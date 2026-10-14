"""Local browser interface. Deliberately bound to IPv4 loopback only."""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

STATIC = Path(__file__).with_name("static")
MAX_BODY = 256 * 1024


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, workspace: Path, port: int = 8765, service=None):
        if service is None:
            from .service import Service
            service = Service(workspace)
        self.service = service
        self.token = secrets.token_urlsafe(32)
        self.jobs: dict[str, dict] = {}
        self.jobs_lock = threading.Lock()
        self.stop_event = threading.Event()
        super().__init__(("127.0.0.1", port), Handler)
        from .browser_access import BrowserAccess
        self.pdf_browser = BrowserAccess()

    def submit(self, payload: dict) -> str:
        with self.jobs_lock:
            if any(j["status"] == "running" for j in self.jobs.values()):
                raise RuntimeError("已有推荐任务正在运行，请等待完成。")
            # Keep polling state bounded; a running job is never discarded.
            while len(self.jobs) >= 50:
                self.jobs.pop(next(iter(self.jobs)))
            job_id = secrets.token_hex(12)
            self.jobs[job_id] = {"job_id": job_id, "status": "running"}

        def work():
            try:
                if payload.get("action") == "pdf-download":
                    from .download import download_run, pdf_archive, download_summary
                    def progress(**fields):
                        with self.jobs_lock:
                            self.jobs[job_id].update(fields)
                    report = download_run(self.service, payload.get("run_id"), select_all=True,
                                          browser=self.pdf_browser, progress=progress)
                    has_pdfs = bool(report["downloaded"] + report["already_exists"])
                    if has_pdfs:
                        pdf_archive(self.service, payload.get("run_id"))
                    result = {"pdf_export": True, "has_pdfs": has_pdfs, "report": download_summary(self.service, payload["run_id"]), "run_id": payload["run_id"], "downloaded": report["downloaded"], "already_exists": report["already_exists"], "no_link": report["no_link"], "failed": report["failed"]}
                elif payload.get('action') == 'semantic-setup':
                    result = self.service.setup_semantic(payload.get('model_dir'))
                else:
                    result = self.service.run(
                        topics=payload.get("topics"), demo=payload.get("demo", False),
                        run_id=payload.get("run_id"), preview=payload.get("preview", False), search_page=payload.get("search_page",0), parent_run_id=payload.get("parent_run_id"),
                    )
                value = {"job_id": job_id, "status": "done", "result": result}
            except Exception as exc:
                value = {"job_id": job_id, "status": "error", "error": str(exc)[:1600]}
            with self.jobs_lock:
                self.jobs[job_id] = value

        threading.Thread(target=work, name="lite-recommend", daemon=True).start()
        return job_id

    def scheduler(self):
        # The event makes shutdown immediate, even during the 60-second interval.
        while not self.stop_event.is_set():
            try:
                self.service.schedule_tick()
            except Exception as exc:
                print(f"[scheduler] {str(exc)[:400]}")
            self.stop_event.wait(60)

    def shutdown(self):
        self.stop_event.set()
        return super().shutdown()

    def server_close(self):
        self.stop_event.set()
        self.pdf_browser.close()
        return super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: LocalServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Do not log query strings, personal reading notes, or token headers.
        pass

    def _authority_ok(self, authority: str) -> bool:
        try:
            parsed = urlsplit("//" + authority)
            return (
                parsed.hostname in {"localhost", "127.0.0.1"}
                and parsed.port == self.server.server_port
                and parsed.username is None and parsed.password is None
                and not parsed.path and not parsed.query and not parsed.fragment
            )
        except (ValueError, AttributeError):
            return False

    def _safe_host(self) -> bool:
        if len(self.headers.get_all("Host", [])) != 1 or not self._authority_ok(self.headers.get("Host", "")):
            self._json({"error": "仅允许通过本机地址访问。"}, 403)
            return False
        return True

    def _send(self, body: bytes, status=200, content_type="application/json; charset=utf-8", download=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data, status=200):
        self._send(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"), status)

    def _exception(self, exc):
        status = 404 if isinstance(exc, (KeyError, FileNotFoundError)) else 400 if isinstance(exc, (ValueError, TypeError)) else 409 if isinstance(exc, RuntimeError) else 500
        self._json({"error": str(exc)[:1600]}, status)

    def do_GET(self):
        if not self._safe_host():
            return
        try:
            request_url = urlsplit(self.path)
            path = request_url.path
            query = parse_qs(request_url.query)
            arg = lambda name: query.get(name, [""])[0]
            if path in {"/", "/index.html"}:
                body = (STATIC / "index.html").read_text(encoding="utf-8").replace("__LITE_TOKEN__", self.server.token)
                self._send(body.encode("utf-8"), content_type="text/html; charset=utf-8")
            elif path in {"/app.js", "/style.css"}:
                file = STATIC / path[1:]
                content_type = "text/javascript; charset=utf-8" if file.suffix == ".js" else "text/css; charset=utf-8"
                self._send(file.read_bytes(), content_type=content_type)
            elif path == "/health":
                self._json({"status": "ok", "local_only": True})
            elif path == "/api/state":
                self._json(self.server.service.overview())
            elif path == "/api/run":
                self._json(self.server.service.run_detail(arg("id")))
            elif path == "/api/job":
                with self.server.jobs_lock:
                    job = self.server.jobs.get(arg("id"))
                    if job is None:
                        raise KeyError("推荐任务不存在或已过期。")
                    self._json(dict(job))
            elif path == '/api/candidates':
                self._json(self.server.service.candidate_detail(arg('id')))
            elif path == "/api/pdf-report":
                from .download import download_summary
                self._json(download_summary(self.server.service, arg("id")))
            elif path == "/api/pdf-archive":
                from .download import pdf_archive
                file = pdf_archive(self.server.service, arg("id"))
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="literature-pdfs.zip"')
                self.send_header("Content-Length", str(file.stat().st_size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with file.open("rb") as stream:
                    while block := stream.read(64 * 1024):
                        self.wfile.write(block)
            elif path == "/api/export":
                fmt = arg("format") or "bib"
                if fmt not in {"bib", "json", "markdown"}:
                    raise ValueError("导出格式只能是 bib、json 或 markdown。")
                result = self.server.service.export_run(arg("id"), format=fmt)
                extensions = {"bib": "bib", "json": "json", "markdown": "md"}
                self._send(result.encode("utf-8"), content_type="text/plain; charset=utf-8", download=f"literature-report.{extensions[fmt]}")
            else:
                self._json({"error": "页面不存在。"}, 404)
        except Exception as exc:
            self._exception(exc)

    def do_POST(self):
        # Failed preflight validation closes the connection instead of leaving an
        # unread request body available for HTTP request smuggling.
        self.close_connection = True
        if not self._safe_host():
            return
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                allowed = parsed.scheme == "http" and self._authority_ok(parsed.netloc) and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
            except ValueError:
                allowed = False
            if not allowed:
                self._json({"error": "不允许来自其他网站的写入。"}, 403)
                return
        token = self.headers.get("X-Lite-Token", "")
        if not hmac.compare_digest(token.encode("utf-8"), self.server.token.encode("utf-8")):
            self._json({"error": "页面令牌失效，请刷新页面后再试。"}, 403)
            return
        if self.headers.get("Transfer-Encoding"):
            self._json({"error": "不支持分块请求。"}, 400)
            return
        if self.headers.get_content_type() != "application/json":
            self._json({"error": "请求必须使用 application/json。"}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0:
                raise ValueError("请求缺少有效长度。")
            path = urlsplit(self.path).path
            body_limit = 36 * 1024 * 1024 if path == "/api/pdf-import" else MAX_BODY
            if length > body_limit:
                self._json({"error": "请求超过接口大小限制。"}, 413)
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求必须是 JSON 对象。")
            path = urlsplit(self.path).path
            if path == "/api/topic":
                self._json(self.server.service.save_topic(payload))
            elif path == "/api/feedback":
                self._json(self.server.service.feedback(payload))
            elif path == "/api/settings":
                self._json(self.server.service.save_settings(payload))
            elif path == "/api/run":
                if "demo" in payload and not isinstance(payload["demo"], bool):
                    raise ValueError("demo 必须是布尔值。")
                if "preview" in payload and not isinstance(payload["preview"], bool):
                    raise ValueError("preview 必须是布尔值。")
                topics = payload.get("topics")
                if topics is not None and (not isinstance(topics, list) or any(not isinstance(t, str) for t in topics)):
                    raise ValueError("topics 必须是方向 ID 数组。")
                if payload.get("run_id") is not None and not isinstance(payload["run_id"], str):
                    raise ValueError("run_id 必须是字符串。")
                self._json({"job_id": self.server.submit(payload), "status": "running"}, 202)
            elif path == '/api/semantic-setup':
                model_dir = payload.get('model_dir')
                if model_dir is not None and not isinstance(model_dir,str):
                    raise ValueError('模型目录须为字符串')
                self._json({'job_id':self.server.submit({'action':'semantic-setup','model_dir':model_dir}),'status':'running'},202)
            elif path == "/api/pdf-import":
                from .access import import_pdf
                self._json(import_pdf(self.server.service, payload))
            elif path == "/api/pdf-abstract":
                from .access import confirm_abstract
                self._json(confirm_abstract(self.server.service, payload))
            elif path == "/api/pdf-control":
                with self.server.jobs_lock:
                    job = self.server.jobs.get(payload.get("job_id"))
                    if not job or job.get("status") != "running" or not job.get("waiting"):
                        raise ValueError("没有正在等待操作的 PDF 下载")
                    self.server.pdf_browser.signal(payload.get("action"))
                    job.update(waiting=False, message="正在继续获取 PDF…")
                self._json({"ok": True})
            elif path == "/api/pdf-download":
                run_id = payload.get("run_id")
                if not isinstance(run_id, str):
                    raise ValueError("请选择论文批次")
                self.server.service.run_detail(run_id)
                self._json({"job_id": self.server.submit({"action": "pdf-download", "run_id": run_id}), "status": "running"}, 202)
            elif path == "/api/backup":
                result = Path(self.server.service.backup())
                self._send(result.read_bytes(), content_type="application/zip", download="lite-recommender-backup.zip")
            else:
                self._json({"error": "接口不存在。"}, 404)
        except Exception as exc:
            self._exception(exc)


def create_server(workspace: Path, port: int = 8765, service=None) -> LocalServer:
    """Create a loopback server. The caller owns its lifecycle."""
    return LocalServer(Path(workspace), port, service)


def start(workspace: Path, port: int = 8765, open_browser: bool = False):
    """Run the local UI and its scheduler until Ctrl+C or termination."""
    server = create_server(workspace, port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Lite Recommender: {url}（仅本机可访问，Ctrl+C 退出）", flush=True)
    threading.Thread(target=server.scheduler, name="lite-scheduler", daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop_event.set()
        server.server_close()
