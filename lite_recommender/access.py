"""Optional authorized publisher access and user-supplied local PDFs.

Secrets are read from the server environment, never from browser settings.
Official requests go only to api.elsevier.com, without credential redirects.
"""
import base64
import json
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit
from .storage import FileLock, atomic_json

MAX_IMPORT = 25 * 1024 * 1024


def configuration():
    return {"elsevier_key_configured": bool(os.environ.get("ELSEVIER_API_KEY")),
            "browser_available": importlib.util.find_spec("playwright") is not None,
            "elsevier_token_configured": bool(os.environ.get("ELSEVIER_INSTTOKEN")),
            "pdf_text_available": bool(shutil.which("pdftotext"))}


def library_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    p = urlsplit(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password or any(ord(c) <= 32 for c in value):
        raise ValueError("学校图书馆入口须为 HTTPS 链接，不能包含账号密码")
    return value


def external_base(value):
    value = library_url(value)
    if value:
        p = urlsplit(value)
        if p.path not in ("", "/") or p.query or p.fragment:
            raise ValueError("外部 DOI 查询入口只填写 HTTPS 网站根地址")
    return value.rstrip("/")


def is_elsevier(paper):
    return str(paper.get("doi", "")).lower().startswith(("10.1016/", "10.1006/"))


def _request(doi, accept, view):
    from .sources import _doi
    from .download import _public_addresses, _PinnedHTTPSConnection
    doi = _doi(doi)
    key = os.environ.get("ELSEVIER_API_KEY", "")
    if not key or not doi:
        raise ValueError("Elsevier API Key 未配置或 DOI 无效")
    if any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("Elsevier API Key 格式无效")
    headers = {"Accept": accept, "X-ELS-APIKey": key, "User-Agent": "LiteRecommender/2.0"}
    token = os.environ.get("ELSEVIER_INSTTOKEN", "")
    if token and any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Elsevier 机构令牌格式无效")
    if token:
        headers["X-ELS-Insttoken"] = token
    connection = _PinnedHTTPSConnection("api.elsevier.com", 443, _public_addresses("api.elsevier.com", 443))
    try:
        connection.request("GET", "/content/article/doi/" + quote(doi, safe="") + "?view=" + view, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Elsevier 接口 HTTP " + str(response.status) + "；请检查 API 授权、学校网络及内容权限")
        limit = MAX_IMPORT if accept == "application/pdf" else 2 * 1024 * 1024
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Elsevier 响应超过大小限制")
        return data
    finally:
        connection.close()


def enrich(papers, enabled):
    if not enabled:
        return papers, []
    if not configuration()["elsevier_key_configured"]:
        return papers, ["已启用 Elsevier 补摘要，但服务未配置 ELSEVIER_API_KEY；继续使用公共来源"]
    warnings = []
    enriched = []
    attempted = 0
    from .sources import _doi, _clean
    for paper in papers:
        p = dict(paper)
        if not p.get("abstract") and is_elsevier(p) and attempted < 20:
            attempted += 1
            try:
                root = json.loads(_request(p["doi"], "application/json", "META_ABS"))["full-text-retrieval-response"]
                core = root.get("coredata", {})
                if _doi(core.get("prism:doi")) != _doi(p["doi"]):
                    raise ValueError("返回 DOI 不匹配，未采用摘要")
                abstract = core.get("dc:description")
                if not isinstance(abstract, str) or not _clean(abstract):
                    raise ValueError("官方接口未返回可用摘要")
                p.update(abstract=_clean(abstract), abstract_source="elsevier_api")
            except Exception as exc:
                warnings.append(p["doi"] + "：" + (str(exc) if isinstance(exc, ValueError) else "Elsevier 接口请求或解析失败"))
        enriched.append(p)
    if attempted == 20:
        warnings.append("本方向 Elsevier 补摘要每轮最多尝试20篇；其余保留公共元数据")
    return enriched, warnings


def official_pdf(paper, destination):
    from .download import _valid_pdf
    data = _request(paper.get("doi"), "application/pdf", "FULL")
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-65536:]:
        raise ValueError("Elsevier 未返回有效 PDF，可能缺少 PDF 格式权限")
    fd, name = tempfile.mkstemp(prefix=".elsevier-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if not _valid_pdf(Path(name)):
            raise ValueError("Elsevier PDF 验证失败")
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)
    return {"bytes": len(data), "source": "elsevier_api"}


def _extract_abstract(path):
    executable = shutil.which("pdftotext")
    if not executable:
        return "", "PDF 已保存；未安装 pdftotext，无法自动提取摘要"
    try:
        with tempfile.TemporaryDirectory(prefix="lite-pdf-text-") as temp:
            textpath = Path(temp) / "text.txt"
            subprocess.run([executable, "-f", "1", "-l", "2", "-enc", "UTF-8", str(path), str(textpath)], check=True, timeout=8, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if textpath.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("extracted text too large")
            text = textpath.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"(?:\babstract\b|摘\s*要)\s*[:：]?\s*(.{80,12000}?)(?=\n\s*(?:key\s*words?\b|index terms\b|关键词|(?:1[.\s]+)?introduction\b|引言)|\Z)", text, re.I | re.S)
            if not match:
                return "", "未找到明确摘要段落；扫描件需要 OCR，可在下次检索使用其他摘要来源"
            return " ".join(match.group(1).split()), "已提取摘要候选；需人工核对后确认用于后续推荐"
    except Exception:
        return "", "PDF 已保存，文本提取失败；不会生成或猜测摘要"


def import_pdf(service, payload):
    run_id, pid = payload.get("run_id"), payload.get("paper_id")
    detail = service.run_detail(run_id)
    paper = next((p for p in detail["papers"] + service.candidate_detail(run_id)["candidates"] if p["paper_id"] == pid), None)
    if not paper or detail["mode"] == "demo" or paper.get("is_demo"):
        raise ValueError("请选择真实批次中的论文")
    pid = paper.get("canonical_paper_id") or pid
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", pid):
        raise ValueError("论文标识无效")
    encoded = payload.get("data", "")
    if not isinstance(encoded, str) or len(encoded) > (MAX_IMPORT * 4 // 3 + 8):
        raise ValueError("PDF 导入大小须不超过25 MB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception:
        raise ValueError("PDF 编码无效") from None
    if len(data) > MAX_IMPORT or not data.startswith(b"%PDF-") or b"%%EOF" not in data[-65536:]:
        raise ValueError("文件不是完整 PDF 或超过25 MB")
    folder = service.workspace / "_shared/pdfs"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / (pid + ".pdf")
    if not folder.resolve().is_relative_to(service.workspace) or destination.is_symlink():
        raise ValueError("PDF 目标路径无效")
    lock = service.workspace / "_shared/download.lock"
    if lock.is_symlink():
        raise ValueError("PDF 锁不能是符号链接")
    with FileLock(lock):
        if destination.exists():
            raise ValueError("这篇论文已有本地 PDF；保留原文件，未覆盖")
        fd, name = tempfile.mkstemp(prefix=".import-", dir=folder)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(name, destination)
        finally:
            Path(name).unlink(missing_ok=True)
        abstract, message = _extract_abstract(destination)
        atomic_json(folder / (pid + ".import.json"), {"paper_id": pid, "doi": paper.get("doi"), "imported_at": service.now().isoformat(), "source": "user_import", "abstract_candidate": abstract})
    return {"paper_id": pid, "message": message, "abstract_candidate": abstract}


def confirm_abstract(service, payload):
    paper = service.store.paper(payload.get("paper_id"))
    if not paper or paper.get("is_demo"):
        raise ValueError("论文不存在")
    pid = paper["paper_id"]
    path = service.workspace / "_shared/pdfs" / (pid + ".import.json")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", pid) or path.is_symlink() or not path.is_file():
        raise ValueError("未找到导入记录")
    record = json.loads(path.read_text(encoding="utf-8"))
    abstract = payload.get("abstract")
    if not isinstance(abstract, str) or not 80 <= len(abstract) <= 12000:
        raise ValueError("请核对并填写80–12000字符的摘要")
    service.store.upsert_paper({**paper, "abstract": abstract.strip(), "abstract_source": "user_confirmed_pdf"})
    record.update(confirmed_abstract=abstract.strip(), confirmed_at=service.now().isoformat())
    atomic_json(path, record)
    return {"message": "摘要已确认，仅影响后续检索评分；历史批次及评分保持原样"}
