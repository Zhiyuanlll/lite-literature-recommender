"""PDF retrieval for user-selected papers, with an optional isolated browser.

Connections pin a validated public IP while retaining TLS hostname verification.
Redirects are separately checked. Direct requests never send cookies or credentials;
the optional browser handles its own authentication within its separate contexts.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile
from urllib.parse import urljoin, urlsplit, urlunsplit, urlencode

from .storage import FileLock, atomic_json

MAX_BYTES = 25 * 1024 * 1024
TIMEOUT = 15
MAX_REDIRECTS = 5
CHUNK = 64 * 1024


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.is_global and not address.is_multicast and not address.is_reserved
                and not address.is_loopback and not address.is_unspecified)


def _validate_url(url: str):
    if not isinstance(url, str) or not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError("PDF 链接无效或包含未转义空白")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("只接受公开 HTTPS PDF 直链")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PDF 链接不能包含账户凭据")
    host = parsed.hostname.rstrip(".").lower()
    if not host or "%" in host or host == "localhost" or host.endswith((".localhost", ".local")):
        raise ValueError("拒绝本机或局域网地址")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not _public_ip(host):
        raise ValueError("拒绝私有、保留或本机 IP 地址")
    # Accessing .port validates malformed ports; public HTTPS ports are permitted.
    port = parsed.port or 443
    if not 1 <= port <= 65535:
        raise ValueError("PDF 链接端口无效")
    return parsed, host.encode("idna").decode("ascii"), port


def _public_addresses(host: str, port: int):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("PDF 主机没有可用地址")
    unsafe = [item[4][0] for item in addresses if not _public_ip(item[4][0])]
    if unsafe:
        # Recognize only the benchmarking range used by Fake-IP DNS. Never
        # connect to those addresses or allow ordinary private/loopback targets.
        fake_range = ipaddress.ip_network("198.18.0.0/15")
        if all(ipaddress.ip_address(value) in fake_range for value in unsafe):
            try:
                return _doh_addresses(host, port)
            except Exception as exc:
                raise ValueError("检测到Fake-IP解析，公网DNS解析失败；请检查网络代理或稍后重试：" + str(exc)[:300]) from exc
        raise ValueError("PDF 主机解析到非公开 IP，已拒绝连接")
    unique = []
    seen = set()
    for item in addresses:
        key = (item[0], item[4])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, addresses):
        super().__init__(host, port, timeout=TIMEOUT, context=ssl.create_default_context())
        self.public_addresses = addresses

    def connect(self):
        last_error = None
        for family, kind, protocol, _, address in self.public_addresses:
            raw = socket.socket(family, kind, protocol)
            try:
                raw.settimeout(TIMEOUT)
                # Connect to the already-validated sockaddr, without a second DNS lookup.
                raw.connect(address)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except (OSError, ssl.SSLError) as exc:
                raw.close()
                last_error = exc
        raise OSError("无法建立公开 HTTPS 连接") from last_error


def _doh_addresses(host: str, port: int):
    """Resolve Fake-IP names over authenticated TLS to a fixed public resolver.

    The publisher hostname is the only information sent to the resolver. PDF
    connections remain pinned to verified global addresses and verify TLS names.
    """
    addresses = []
    for record_type in (1, 28):
        connection = _PinnedHTTPSConnection("cloudflare-dns.com", 443, [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.0.0.1", 443)),
        ])
        try:
            connection.request("GET", "/dns-query?" + urlencode({"name": host, "type": record_type}),
                               headers={"Accept": "application/dns-json", "Connection": "close"})
            response = connection.getresponse()
            try:
                if response.status != 200:
                    raise ValueError("公网DNS返回HTTP " + str(response.status))
                body = response.read(64 * 1024 + 1)
                if len(body) > 64 * 1024:
                    raise ValueError("公网DNS响应过大")
                result = json.loads(body)
                question = result.get("Question") or []
                if result.get("Status") != 0 or result.get("TC") or not any(
                    q.get("name", "").rstrip(".").lower() == host.lower() and q.get("type") == record_type for q in question
                ):
                    raise ValueError("公网DNS响应无效或域名不存在")
                for item in result.get("Answer", []):
                    if item.get("type") != record_type:
                        continue
                    ip = str(ipaddress.ip_address(item.get("data", "")))
                    if not _public_ip(ip):
                        raise ValueError("公网DNS结果含非公开IP，已拒绝连接")
                    family = socket.AF_INET if record_type == 1 else socket.AF_INET6
                    sockaddr = (ip, port) if record_type == 1 else (ip, port, 0, 0)
                    addresses.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
            finally:
                response.close()
        finally:
            connection.close()
        if addresses:
            return addresses
    raise ValueError("公网DNS没有返回可用IP")


def _open_response(url: str):
    parsed, host, port = _validate_url(url)
    connection = _PinnedHTTPSConnection(host, port, _public_addresses(host, port))
    path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.request("GET", path, headers={
            "User-Agent": "LiteRecommender/2.0 (optional public PDF retrieval)",
            "Accept": "application/pdf", "Accept-Encoding": "identity", "Connection": "close",
        })
        return connection, connection.getresponse()
    except Exception:
        connection.close()
        raise


def _valid_pdf(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    with path.open("rb") as stream:
        return stream.read(5) == b"%PDF-" and path.stat().st_size > 5


def _safe_report_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        # Signed query parameters are not copied into the public-looking report.
        return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))[:2000]
    except ValueError:
        return "invalid URL"


def _download_url(url: str, destination: Path) -> dict:
    current = url
    temporary = None
    try:
        for hop in range(MAX_REDIRECTS + 1):
            _validate_url(current)
            connection, response = _open_response(current)
            try:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location or hop == MAX_REDIRECTS:
                        raise ValueError("PDF 重定向缺少目标或超过上限")
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    raise ValueError(f"公开 PDF 请求返回 HTTP {response.status}")
                if "text/html" in (response.getheader("Content-Type") or "").lower():
                    raise ValueError("出版商返回网页而非PDF，可能需要浏览器验证或登录；请从论文原文页面按权限获取")
                length = response.getheader("Content-Length")
                if length is not None:
                    try:
                        count = int(length)
                    except ValueError as exc:
                        raise ValueError("PDF Content-Length 无效") from exc
                    if count < 0 or count > MAX_BYTES:
                        raise ValueError("PDF 超过 25 MiB 下载上限")
                fd, temp_name = tempfile.mkstemp(prefix=".pdf-", suffix=".part", dir=destination.parent)
                temporary = Path(temp_name)
                total = 0
                with os.fdopen(fd, "wb") as stream:
                    while True:
                        block = response.read(min(CHUNK, MAX_BYTES - total + 1))
                        if not block:
                            break
                        total += len(block)
                        if total > MAX_BYTES:
                            raise ValueError("PDF 超过 25 MiB 下载上限")
                        stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not _valid_pdf(temporary):
                    raise ValueError("响应不是有效 %PDF 文件；可能为登录页或错误页")
                if length is not None and total != count:
                    raise ValueError("PDF 响应不完整，与 Content-Length 不一致")
                os.replace(temporary, destination)
                temporary = None
                return {"bytes": total, "url": _safe_report_url(current)}
            finally:
                response.close()
                connection.close()
        raise ValueError("PDF 重定向超过上限")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _links(paper: dict) -> list[str]:
    values = [paper.get("pdf_url")]
    versions = paper.get("versions")
    if isinstance(versions, list):
        values.extend(item.get("pdf_url") for item in versions if isinstance(item, dict))
    return list(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))


def download_run(service, run_id: str, min_rating: int = 4, select_all: bool = False,
                 browser=None, progress=lambda **kw: None) -> dict:
    """Download only real papers with user rating >= threshold, or cited status.

    Outcomes are saved separately; recommendations, scores and feedback are unchanged.
    No-link/HTTP/error outcomes are per-paper and do not stop subsequent papers.
    """
    if not isinstance(select_all, bool):
        raise ValueError("select_all 必须是布尔值")
    if isinstance(min_rating, bool) or not isinstance(min_rating, int) or not 1 <= min_rating <= 5:
        raise ValueError("下载评分门槛必须是1–5整数")
    if not isinstance(run_id, str) or not re.fullmatch(r"[\w-][\w.-]{0,79}", run_id) or run_id in (".", ".."):
        raise ValueError("下载批次标识无效")
    result = service.run_detail(run_id)
    workspace = Path(service.workspace).resolve()
    folder = workspace / "_shared/pdfs"
    if workspace not in folder.resolve().parents:
        raise ValueError("PDF 目录必须位于 workspace 内")
    folder.mkdir(parents=True, exist_ok=True)
    report_path = workspace / "_shared/runs" / run_id / "download_report.json"
    if workspace not in report_path.resolve().parents:
        raise ValueError("下载报告目录必须位于 workspace 内")
    report = {"run_id": run_id, "min_rating": min_rating, "select_all": select_all, "created_at": service.now().isoformat(),
              "downloaded": 0, "already_exists": 0, "failed": 0, "no_link": 0,
              "not_selected": 0, "demo_skipped": 0, "items": [], "report_path": str(report_path)}
    lock_path = workspace / "_shared/download.lock"
    if lock_path.is_symlink():
        raise ValueError("下载锁不能是符号链接")
    with FileLock(lock_path):
        for paper in result.get("papers", []):
            progress(message="正在获取：" + str(paper.get("title", "")), waiting=False)
            snapshot_pid = str(paper.get("paper_id", ""))
            pid = str(paper.get("canonical_paper_id") or snapshot_pid)
            item = {"paper_id": pid, "snapshot_paper_id": snapshot_pid,
                    "topic_id": paper.get("topic_id", ""), "title": paper.get("title", "")}
            report["items"].append(item)
            feedback = paper.get("feedback") or {}
            rating = feedback.get("rating")
            selected = select_all or (isinstance(rating, int) and not isinstance(rating, bool) and rating >= min_rating) or feedback.get("read_status") == "cited"
            if result.get("mode") == "demo" or paper.get("is_demo"):
                item["status"] = "demo_skipped"
            elif not selected:
                item["status"] = "not_selected"
            else:
                try:
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", pid):
                        raise ValueError("论文标识不能用作安全文件名")
                    destination = folder / (pid + ".pdf")
                    if destination.is_symlink():
                        raise ValueError("PDF 目标不能是符号链接")
                    item["path"] = str(destination)
                    if _valid_pdf(destination):
                        item["status"] = "already_exists"
                    else:
                        from .access import configuration, is_elsevier, official_pdf
                        if getattr(service, "settings", {}).get("publisher_access", {}).get("enabled") and configuration()["elsevier_key_configured"] and is_elsevier(paper):
                            try:
                                item.update(official_pdf(paper, destination))
                                item["status"] = "downloaded"
                            except Exception:
                                item["publisher_attempt"] = "Elsevier API 未取得有效PDF，已尝试后续下载来源"
                        urls = _links(paper)
                        if item.get("status") == "downloaded":
                            pass
                        elif not urls:
                            item["status"] = "no_link"
                            item["message"] = "快照没有公开 PDF 直链，请从论文原始页面按权限获取"
                        else:
                            errors = []
                            for url in urls:
                                try:
                                    item.update(_download_url(url, destination))
                                    item["status"] = "downloaded"
                                    break
                                except Exception as exc:
                                    errors.append({"url": _safe_report_url(url), "error": str(exc)[:800]})
                            if errors:
                                item["attempt_errors"] = errors
                            if "status" not in item:
                                item["status"] = "failed"
                        if item.get("status") != "downloaded" and browser is not None:
                            try:
                                item.pop("message", None)
                                item.update(browser.download(paper, destination, progress))
                                item["status"] = "downloaded"
                            except Exception as exc:
                                item.update(status="failed", message=str(exc)[:800])
                except Exception as exc:
                    item.update(status="failed", message=str(exc)[:800])
            if item["status"] == "failed" and not item.get("message"):
                item["message"] = "；".join(dict.fromkeys(e["error"] for e in item.get("attempt_errors", [])))[:1000] or "PDF获取失败"
            report[item["status"]] += 1
        atomic_json(report_path, report)
    return report


def pdf_archive(service, run_id: str) -> Path:
    """Package validated saved PDFs and a manifest; never substitute a web page."""
    import json
    import zipfile
    if not isinstance(run_id, str) or not re.fullmatch(r"[\w-][\w.-]{0,79}", run_id) or run_id in (".", ".."):
        raise ValueError("下载批次标识无效")
    service.run_detail(run_id)
    workspace = Path(service.workspace).resolve()
    out = workspace / "_shared/runs" / run_id
    folder = workspace / "_shared/pdfs"
    if not out.resolve().is_relative_to(workspace) or not folder.resolve().is_relative_to(workspace):
        raise ValueError("PDF 目录必须位于 workspace 内")
    report_file = out / "download_report.json"
    if not report_file.is_file() or report_file.is_symlink():
        raise ValueError("请先执行本批次的PDF下载")
    report = json.loads(report_file.read_text(encoding="utf-8"))
    archive = out / "papers.zip"
    if archive.is_symlink():
        raise ValueError("导出文件不能是符号链接")
    if not report.get("downloaded", 0) + report.get("already_exists", 0):
        raise ValueError("本批次未获取到PDF，不生成空ZIP；请查看下载结果报告")
    included = set()
    with FileLock(workspace / "_shared/download.lock"):
        fd, temporary_name = tempfile.mkstemp(prefix=".pdf-archive-", suffix=".zip", dir=out)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as z:
                # Report is a shareable summary: exclude local paths and internal diagnostics.
                summary = {k: report[k] for k in ["run_id", "created_at", "downloaded", "already_exists", "failed", "no_link", "not_selected", "demo_skipped"]}
                summary["items"] = []
                for item in report.get("items", []):
                    entry = {k: item[k] for k in ["paper_id", "title", "status", "message", "source", "identity_verified"] if k in item}
                    summary["items"].append(entry)
                    pid = item.get("paper_id", "")
                    if item.get("status") not in {"downloaded", "already_exists"} or pid in included:
                        continue
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", pid):
                        continue
                    file = folder / (pid + ".pdf")
                    if not _valid_pdf(file):
                        entry["status"] = "missing_saved_file"
                        continue
                    included.add(pid)
                    name = f"{len(included):03d}_{pid}.pdf"
                    z.write(file, name)
                    entry["filename"] = name
                if not included:
                    raise ValueError("本地PDF文件已缺失或损坏，不生成空ZIP；请重新下载")
                summary["pdf_files"] = len(included)
                z.writestr("download_report.json", json.dumps(summary, ensure_ascii=False, indent=2))
                z.writestr("README.txt", "此包包含已获取的论文PDF及逐篇下载结果。未获取的论文见download_report.json。可能包含仓储或预印本版本，请核对论文版本。没有获取的正文不会以摘要或网页替代。")
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
    return archive


def download_summary(service, run_id: str) -> dict:
    """Return actionable per-paper outcomes without disclosing local paths."""
    service.run_detail(run_id)
    if not isinstance(run_id, str) or not re.fullmatch(r"[\w-][\w.-]{0,79}", run_id) or run_id in (".", ".."):
        raise ValueError("下载批次标识无效")
    workspace = Path(service.workspace).resolve()
    path = workspace / "_shared/runs" / run_id / "download_report.json"
    if not path.resolve().is_relative_to(workspace) or path.is_symlink():
        raise ValueError("下载报告路径无效")
    if not path.exists():
        return {"run_id": run_id, "items": []}
    report = json.loads(path.read_text(encoding="utf-8"))
    summary = {key: report.get(key, 0) for key in ["run_id", "downloaded", "already_exists", "no_link", "failed"]}
    summary["items"] = []
    for item in report.get("items", []):
        entry = {key: item[key] for key in ["title", "status", "message", "source", "identity_verified"] if key in item}
        if entry.get("status") == "failed" and not entry.get("message"):
            entry["message"] = "；".join(dict.fromkeys(e.get("error", "") for e in item.get("attempt_errors", [])))[:1000]
        summary["items"].append(entry)
    return summary
