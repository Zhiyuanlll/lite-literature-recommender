"""Visible, process-scoped PDF browser. Authentication stays in browser memory.

Playwright objects live on one dedicated thread across export jobs. Publisher and
external lookup contexts are isolated; neither is serialized to disk.
"""
import atexit
import base64
from concurrent.futures import Future
import os
from pathlib import Path
import queue
import re
import tempfile
import threading
import time
from urllib.parse import quote, urljoin

from .download import MAX_BYTES, CHUNK, _validate_url, _valid_pdf, _public_addresses
from .sources import _doi

EXTERNAL_DOI_BASE = "https://sci-hub.jp"


class BrowserAccess:
    def __init__(self):
        self.tasks = queue.Queue()
        self.worker = None
        self.worker_lock = threading.Lock()
        self.stopping = threading.Event()
        self.command = queue.Queue()
        self.playwright = self.browser = self.publisher = self.external = None
        self.institution_attempted = False
        self.closed = False
        atexit.register(self.close)

    def _work(self):
        try:
            while not self.stopping.is_set():
                task = self.tasks.get()
                if task is None:
                    break
                future, args = task
                try:
                    future.set_result(self._download(*args))
                except Exception as exc:
                    future.set_exception(exc)
        finally:
            self._dispose()

    def signal(self, action):
        if action not in ("continue", "skip"):
            raise ValueError("下载操作无效")
        self.command.put(action)

    def _start(self):
        if self.browser and self.browser.is_connected():
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ValueError("机构下载浏览器未安装：请安装 .[browser] 并运行 python -m playwright install chromium") from None
        if self.playwright:
            self.playwright.stop()
        bundled = Path(__file__).resolve().parent.parent / ".browser-binaries"
        if bundled.is_dir():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled))
        self.playwright = sync_playwright().start()
        try:
            options = {"headless": False}
            if os.environ.get("LITE_BROWSER_EXECUTABLE"):
                options["executable_path"] = os.environ["LITE_BROWSER_EXECUTABLE"]
            self.browser = self.playwright.chromium.launch(**options)
            self.publisher = self.browser.new_context(accept_downloads=True, service_workers='block')
            self.external = self.browser.new_context(accept_downloads=True, service_workers='block')
            self.institution_attempted = False
            self.publisher.set_default_timeout(15000)
            self.external.set_default_timeout(15000)
        except Exception:
            self.playwright.stop()
            self.playwright = None
            raise ValueError("无法打开机构下载浏览器；请运行 python -m playwright install chromium，并在桌面会话启动程序") from None

    def download(self, paper, destination, progress=lambda **kw: None):
        if self.closed:
            raise ValueError("下载浏览器已关闭")
        doi = _doi(paper.get("doi"))
        if not doi:
            raise ValueError("无有效 DOI，无法自动进行机构或 Sci-Hub 查询")
        future = Future()
        with self.worker_lock:
            if self.closed:
                raise ValueError("下载浏览器已关闭")
            if self.worker is None:
                self.worker = threading.Thread(target=self._work, name="lite-pdf-browser", daemon=True)
                self.worker.start()
            self.tasks.put((future, (doi, Path(destination), progress)))
        return future.result()

    def _download(self, doi, destination, progress):
        self._start()
        while not self.command.empty():
            self.command.get_nowait()
        errors = []
        for context, url, source in (
            (self.publisher, "https://doi.org/" + quote(doi, safe="/"), "institution_browser"),
            (self.external, EXTERNAL_DOI_BASE + "/" + quote(doi, safe="/"), "external_doi_browser"),
        ):
            if self.stopping.is_set():
                raise ValueError("程序已关闭，下载取消")
            try:
                progress(message="正在打开论文原文与机构入口…" if source == "institution_browser" else "机构未取得 PDF，正在自动查询 Sci-Hub…", waiting=False)
                result = self._visit(context, url, destination, source, progress)
                if result:
                    return result
                errors.append("机构未取得PDF" if source == "institution_browser" else "Sci-Hub未找到有效PDF")
            except Exception:
                if self.stopping.is_set():
                    raise ValueError("程序已关闭，下载取消") from None
                # Browser and SSO URLs may include tokens; do not log exception text.
                errors.append("机构页面访问失败" if source == "institution_browser" else "Sci-Hub页面访问失败或不可达")
        raise ValueError("；".join(errors))

    def _visit(self, context, url, destination, source, progress):
        page = context.new_page()
        completed, failures, sessions = [], [], []

        def attach(target):
            session = context.new_cdp_session(target)
            sessions.append(session)

            def paused(event):
                request_id = event['requestId']
                headers = {h['name'].lower(): h['value'] for h in event.get('responseHeaders', [])}
                content_type = headers.get('content-type', '').lower()
                pdf = 'application/pdf' in content_type or (
                    'attachment' in headers.get('content-disposition', '').lower()
                    and '.pdf' in headers.get('content-disposition', '').lower())
                if not pdf or event.get('responseStatusCode') != 200:
                    session.send('Fetch.continueRequest', {'requestId': request_id})
                    return
                handle = None
                try:
                    length = headers.get('content-length')
                    if length is not None and (not length.isdigit() or int(length) > MAX_BYTES):
                        raise ValueError('PDF超过25 MiB下载上限或响应长度无效')
                    handle = session.send('Fetch.takeResponseBodyAsStream', {'requestId': request_id})['stream']
                    count = self._save_stream(session, handle, destination, length)
                    completed.append({'bytes': count, 'source': source, 'identity_verified': False,
                                      'message': '已保存PDF；尚未核对正文题名与 DOI，请核对论文归属及版本'})
                except Exception:
                    failures.append('浏览器PDF超过限制、不完整或无法流式读取')
                finally:
                    if handle:
                        try: session.send('IO.close', {'handle': handle})
                        except Exception: pass
                    try: session.send('Fetch.failRequest', {'requestId': request_id, 'errorReason': 'Aborted'})
                    except Exception: pass

            session.on('Fetch.requestPaused', paused)
            session.send('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Response'}]})
            # Any download not captured by the response interceptor is cancelled;
            # never let save_as buffer an unbounded attachment first.
            target.on('download', lambda download: download.cancel())

        def guard(route):
            try:
                _, host, port = _validate_url(route.request.url)
                _public_addresses(host, port)
                route.fallback()
            except Exception:
                route.abort()

        # Validate DNS before each browser request. Chromium still resolves via
        # its own network stack; this is not the direct downloader's IP pinning.
        try:
            context.route('**/*', guard)
            attach(page)
            page.on('popup', attach)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                # Chromium can report a navigation error when it starts a download.
                if not completed and not failures and page.url == "about:blank":
                    return None
            deadline = time.monotonic() + 300
            clicked = set()
            waiting = False
            resumed_at = time.monotonic() if self.institution_attempted else None
            while time.monotonic() < deadline and not self.stopping.is_set():
                if completed:
                    return completed[0]
                if failures:
                    return None
                if page.is_closed():
                    return None
                if context.pages:
                    page = context.pages[-1]
                try:
                    action = self.command.get_nowait()
                except queue.Empty:
                    action = None
                if action == "skip":
                    return None
                if action == "continue":
                    deadline = time.monotonic() + 300
                    waiting = False
                    clicked.clear()
                    resumed_at = time.monotonic()
                    if source == "institution_browser":
                        self.institution_attempted = True
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                # Once the user has completed an institution attempt, an explicit
                # publisher entitlement failure triggers the external lookup.
                if source == "institution_browser" and resumed_at and time.monotonic() - resumed_at > 8:
                    from urllib.parse import urlsplit
                    host = urlsplit(page.url).hostname or ""
                    if host == "sciencedirect.com" or host.endswith(".sciencedirect.com"):
                        text = page.locator("body").inner_text(timeout=1000)
                        if re.search(r"(?:institution|organization).{0,100}(?:does not have|doesn't have|no access)|purchase (?:pdf|access)|buy (?:pdf|article)", text, re.I):
                            return None
                # Only follow visible PDF and institution controls. The user chooses
                # their institution and completes SSO in the visible browser.
                controls = page.locator("a, button")
                triggered = False
                # Some providers embed the PDF without a visible download button.
                for frame in page.locator("iframe[src], embed[src], object[data]").all()[:20]:
                    src = frame.get_attribute("src") or frame.get_attribute("data") or ""
                    if not re.search(r"\.pdf(?:[?#]|$)|/pdfft(?:[?#]|$)", src, re.I):
                        continue
                    target = urljoin(page.url, src)
                    if target in clicked:
                        continue
                    _validate_url(target)
                    clicked.add(target)
                    try:
                        page.goto(target, wait_until="domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    triggered = True
                    break
                if triggered:
                    page.wait_for_timeout(700)
                    continue
                for control in controls.all()[:200]:
                    try:
                        if not control.is_visible():
                            continue
                        text = control.inner_text(timeout=1000).strip()
                        href = control.get_attribute("href") or ""
                        institution = re.search(r"(?:sign in|log in|login) (?:via|through).*institution|access through.*(?:institution|organization)|institutional (?:login|access)|通过.*机构|机构登录", text, re.I)
                        from urllib.parse import urlsplit
                        host = urlsplit(page.url).hostname or ""
                        if source == "institution_browser" and (host == "sciencedirect.com" or host.endswith(".sciencedirect.com")):
                            # Open the publisher's sign-in chooser, never submit an
                            # identity-provider form or a filled password form.
                            if re.fullmatch(r"sign in|check access|get access|登录|检查访问权限", text, re.I) and not page.locator('input[type="password"]').count():
                                institution = True
                        pdf = re.search(r"view pdf|download pdf|下载.*PDF|查看.*PDF|^PDF$", text, re.I) or re.search(r"\.pdf(?:[?#]|$)|/pdfft(?:[?#]|$)", href, re.I)
                        key = (page.url, text, href)
                        if key in clicked or not (institution or pdf):
                            continue
                        if href and not href.startswith("javascript:"):
                            _validate_url(urljoin(page.url, href))
                        clicked.add(key)
                        control.click(timeout=3000)
                        triggered = True
                        break
                    except Exception:
                        continue
                if not triggered and not waiting:
                    message = ("请在下载浏览器选择机构并完成图书馆登录；完成后点“继续下载”。本次程序运行期间复用登录。若机构无权限，点“机构无法获取”转查 Sci-Hub。" if source == "institution_browser" else "已自动打开 Sci-Hub DOI 查询；可在该浏览器完成验证或打开 PDF，再点“继续下载”；未找到时点“跳过此论文”。")
                    progress(message=message, waiting=True, stage=source)
                    waiting = True
                # Pump browser events while a user handles the institution/SSO page.
                page.wait_for_timeout(700)
            return None
        finally:
            for session in sessions:
                try:
                    session.send('Fetch.disable')
                    session.detach()
                except Exception:
                    pass
            context.unroute("**/*", guard)
            # Close popups too, while keeping the shared session for the next paper.
            for target in list(context.pages):
                try:
                    target.close()
                except Exception:
                    pass
            progress(waiting=False)

    def _save_stream(self, session, handle, destination, length=None):
        """Write only bounded chunks; never obtain a whole response body."""
        fd, name = tempfile.mkstemp(prefix='.browser-pdf-', dir=destination.parent)
        temporary = Path(name)
        count = 0
        try:
            with os.fdopen(fd, 'wb') as output:
                deadline = time.monotonic() + 120
                while not self.stopping.is_set() and time.monotonic() < deadline:
                    part = session.send('IO.read', {'handle': handle, 'size': min(CHUNK, MAX_BYTES - count + 1)})
                    block = base64.b64decode(part.get('data', ''), validate=True) if part.get('base64Encoded') else part.get('data', '').encode('utf-8')
                    if len(block) > CHUNK or count + len(block) > MAX_BYTES:
                        raise ValueError('PDF超过25 MiB下载上限')
                    count += len(block)
                    output.write(block)
                    if part.get('eof'):
                        break
                else:
                    raise ValueError('PDF下载取消或流式读取超时')
            if length is not None and count != int(length):
                raise ValueError('PDF响应不完整')
            if not _valid_pdf(temporary):
                raise ValueError('响应不是PDF')
            with temporary.open('rb') as stream:
                stream.seek(max(0, count - 65536))
                if b'%%EOF' not in stream.read():
                    raise ValueError('PDF缺少结束标记')
            os.replace(temporary, destination)
            return count
        finally:
            temporary.unlink(missing_ok=True)

    def _dispose(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.playwright:
                self.playwright.stop()
        self.browser = self.publisher = self.external = self.playwright = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stopping.set()
        self.tasks.put(None)
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=5)
        atexit.unregister(self.close)
