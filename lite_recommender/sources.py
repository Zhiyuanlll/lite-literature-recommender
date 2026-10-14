"""Small, dependency-free scholarly metadata clients.

The public entry point is ``search(profile, limit=200, sources=None)``.  No
full text is downloaded.  API failures are returned as warnings so that a
partial result is still usable.  Publication dates are never padded with an
invented month/day; ``published`` is empty when only a year is known.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .concepts import normalize_concepts

DEFAULT_SOURCES = ("crossref", "openalex", "arxiv")
USER_AGENT = "LiteRecommender/2.0 (scholarly metadata; Python urllib)"
_ARXIV_LOCK = threading.Lock()
_ARXIV_LAST_REQUEST: float | None = None
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(str(x).strip() for x in value if str(x).strip()))


def _clean(value) -> str:
    """Remove metadata markup, decode entities, and normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(text).split())


def _doi(value) -> str:
    raw = str(value or "").strip()
    raw = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", raw, flags=re.I)
    return raw.lower() if re.match(r"^10\.\d{4,9}/\S+$", raw, re.I) else ""


def _arxiv_id(value) -> str:
    raw = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", str(value or "").strip(), flags=re.I)
    raw = re.sub(r"\.pdf$", "", raw)
    raw = re.sub(r"v\d+$", "", raw)
    return raw if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z.\-]*/\d{7})", raw) else ""


def _url(value) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    return raw if parsed.scheme in ("https", "http") and parsed.netloc and not parsed.username else ""


def _date_parts(value) -> tuple[str, int | None]:
    """Normalize Crossref Partial Date without fabricating precision."""
    parts = value.get("date-parts", []) if isinstance(value, dict) else []
    if not parts or not isinstance(parts[0], list) or not parts[0]:
        return "", None
    year = None
    try:
        year = int(parts[0][0])
        if not 1 <= year <= 9999:
            return "", None
        if len(parts[0]) < 3:
            return "", year
        return date(year, int(parts[0][1]), int(parts[0][2])).isoformat(), year
    except (TypeError, ValueError, OverflowError):
        return "", year if year is not None and 1 <= year <= 9999 else None


def _iso_date(value) -> tuple[str, int | None]:
    raw = str(value or "").strip()
    try:
        # Atom timestamps are first-submission times, not update times.
        normalized = date.fromisoformat(raw[:10]).isoformat()
        return normalized, int(normalized[:4])
    except ValueError:
        return "", int(raw) if re.fullmatch(r"\d{4}", raw) and int(raw) > 0 else None


def _crossref_publication(item: dict) -> tuple[str, int | None]:
    candidates = [_date_parts(item.get(key)) for key in (
        "issued", "published-online", "published-print", "published", "posted"
    )]
    years = [year for _, year in candidates if year is not None]
    exact = [day for day, _ in candidates if day]
    # A year-only earliest record must not be promoted into a precise recent date.
    earliest_year = min(years) if years else None
    exact = [day for day in exact if int(day[:4]) == earliest_year]
    return (min(exact) if exact else ""), earliest_year


def _kind(title: str, metadata_type: str = "") -> str:
    if metadata_type in ("review", "review-article") or re.search(
        r"\b(?:review|survey|overview|perspective)\b|综述|回顾", title, re.I
    ):
        return "review"
    if re.search(r"\b(?:method(?:s|ology)?|algorithm|protocol|framework|toolkit)\b|方法|算法", title, re.I):
        return "method"
    return "research"


def _base_paper(source: str, title: str, authors: list[str], doi: str,
                arxiv_id: str, url: str, abstract: str, published: str,
                year: int | None, kind: str, pdf_url: str = "", **extra) -> dict:
    record = {
        "title": title, "authors": authors, "doi": doi, "arxiv_id": arxiv_id,
        "url": url, "abstract": abstract, "published": published, "year": year,
        "source": source, "kind": kind, "is_demo": False,
        "pdf_url": pdf_url, **extra,
    }
    record["sources"] = [source]
    record["versions"] = [{key: record.get(key, "") for key in (
        "source", "doi", "arxiv_id", "url", "pdf_url", "published", "year"
    )}]
    return record


def _crossref_items(payload: bytes) -> list[dict]:
    raw = json.loads(payload)
    message = raw.get("message") if isinstance(raw, dict) else None
    if not isinstance(message, dict):
        raise ValueError("Crossref response has no message object")
    items = message.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Crossref response has no items array")
    papers = []
    for item in items:
        if not isinstance(item, dict):
            continue
        titles = item.get("title", [])
        title = _clean((titles[0] if titles else "") if isinstance(titles, list) else titles)
        if not title:
            continue
        authors = [_clean(" ".join(str(a.get(k, "")) for k in ("given", "family")))
                   or _clean(a.get("name")) for a in (item.get("author") or []) if isinstance(a, dict)]
        doi = _doi(item.get("DOI"))
        published, year = _crossref_publication(item)
        related = []
        relations = item.get("relation") or {}
        identities = {"is-preprint-of", "has-preprint", "is-version-of", "has-version", "is-identical-to", "is-manuscript-of", "has-manuscript"}
        for name, relation in relations.items() if isinstance(relations, dict) else []:
            if name not in identities:
                continue
            for linked in relation if isinstance(relation, list) else []:
                if isinstance(linked, dict) and linked.get("id-type") == "doi" and _doi(linked.get("id")):
                    related.append(_doi(linked["id"]))
        pdf = next((_url(link.get("URL")) for link in (item.get("link") or [])
                    if isinstance(link, dict) and link.get("content-type") == "application/pdf"), "")
        papers.append(_base_paper(
            "crossref", title, [a for a in authors if a], doi, "",
            _url(item.get("URL")) or ("https://doi.org/" + doi if doi else ""),
            _clean(item.get("abstract")), published, year, _kind(title, item.get("type", "")),
            pdf, related_dois=related, journal=_clean((item.get("container-title") or [""])[0]),
            publication_type=item.get("type", ""), is_preprint=item.get("type") == "posted-content",
        ))
    return papers


def _openalex_abstract(index) -> str:
    if not isinstance(index, dict):
        return ""
    positions = {}
    for word, offsets in index.items():
        if not isinstance(offsets, list):
            continue
        for offset in offsets:
            if isinstance(offset, int) and 0 <= offset < 100000:
                positions[offset] = str(word)
    return " ".join(positions[x] for x in sorted(positions))


def _openalex_items(payload: bytes) -> list[dict]:
    raw = json.loads(payload)
    items = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("OpenAlex response has no results array")
    papers = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("display_name") or item.get("title"))
        if not title:
            continue
        authors = [_clean((author.get("author") or {}).get("display_name"))
                   for author in (item.get("authorships") or []) if isinstance(author, dict)]
        doi = _doi(item.get("doi"))
        published, year = _iso_date(item.get("publication_date"))
        if year is None and isinstance(item.get("publication_year"), int):
            year = item["publication_year"]
        locations = [item.get("primary_location"), item.get("best_oa_location")]
        locations.extend(item.get("locations") or [])
        locations = [location for location in locations if isinstance(location, dict)]
        arxiv_id = next((_arxiv_id(location.get("landing_page_url")) or _arxiv_id(location.get("pdf_url"))
                         for location in locations
                         if _arxiv_id(location.get("landing_page_url")) or _arxiv_id(location.get("pdf_url"))), "")
        landing = next((_url(location.get("landing_page_url")) for location in locations
                        if _url(location.get("landing_page_url"))), "")
        pdf = next((_url(location.get("pdf_url")) for location in locations if _url(location.get("pdf_url"))), "")
        paper = _base_paper(
            "openalex", title, [a for a in authors if a], doi, arxiv_id,
            "https://doi.org/" + doi if doi else landing or _url(item.get("id")),
            _clean(_openalex_abstract(item.get("abstract_inverted_index"))),
            published, year, _kind(title, item.get("type", "")), pdf,
            openalex_id=item.get("id", ""),
            publication_type=item.get("type", ""), is_preprint=item.get("type") == "preprint",
        )
        # Preserve publisher/repository links even when the DOI is the canonical URL.
        paper["locations"] = [{"url": _url(loc.get("landing_page_url")), "pdf_url": _url(loc.get("pdf_url"))}
                              for loc in locations if _url(loc.get("landing_page_url")) or _url(loc.get("pdf_url"))]
        papers.append(paper)
    return papers


def _arxiv_items(payload: bytes) -> list[dict]:
    root = ET.fromstring(payload)
    if root.tag != _ATOM + "feed":
        raise ValueError("arXiv response is not an Atom feed")
    papers = []
    for entry in root.findall(_ATOM + "entry"):
        identity = entry.findtext(_ATOM + "id", "")
        if "api/errors" in identity:
            raise ValueError("arXiv returned an API error entry")
        arxiv_id = _arxiv_id(identity)
        title = _clean(entry.findtext(_ATOM + "title"))
        if not title or not arxiv_id:
            continue
        links = entry.findall(_ATOM + "link")
        pdf = next((_url(link.get("href")) for link in links
                    if link.get("type") == "application/pdf" or link.get("title") == "pdf"), "")
        published, year = _iso_date(entry.findtext(_ATOM + "published"))
        doi = _doi(entry.findtext(_ARXIV + "doi"))
        paper = _base_paper(
            "arxiv", title, [_clean(author.findtext(_ATOM + "name")) for author in entry.findall(_ATOM + "author")],
            doi, arxiv_id, _url(identity) or "https://arxiv.org/abs/" + arxiv_id,
            _clean(entry.findtext(_ATOM + "summary")), published, year, _kind(title), pdf,
            updated=_iso_date(entry.findtext(_ATOM + "updated"))[0], is_preprint=True,
        )
        papers.append(paper)
    return papers


def _http_get(url: str, timeout: float = 12, headers: dict | None = None) -> bytes:
    """Mockable HTTP boundary; callers add retries and caching around it."""
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/atom+xml", **(headers or {})})
    with urlopen(request, timeout=timeout) as response:
        body = response.read(8 * 1024 * 1024 + 1)
        if len(body) > 8 * 1024 * 1024:
            raise ValueError("metadata response exceeds 8 MiB")
        return body


def _respect_rate_limit(source: str) -> None:
    global _ARXIV_LAST_REQUEST
    if source != "arxiv":
        return
    with _ARXIV_LOCK:
        now = time.monotonic()
        if _ARXIV_LAST_REQUEST is not None:
            delay = 3.0 - (now - _ARXIV_LAST_REQUEST)
            if delay > 0:
                time.sleep(delay)
        _ARXIV_LAST_REQUEST = time.monotonic()


def _cache_path(profile: dict, url: str) -> Path | None:
    root = profile.get("_cache_dir")
    if not root:
        return None
    return Path(root) / "metadata" / (hashlib.sha256(url.encode()).hexdigest() + ".json")


def _cached(path: Path | None, max_age: float) -> bytes | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if 0 <= time.time() - float(payload["fetched_at"]) <= max_age:
            return payload["body"].encode("utf-8")
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def _fetch(url: str, profile: dict, source: str, warnings: list[str]) -> bytes:
    cache_path = _cache_path(profile, url)
    body = _cached(cache_path, 24 * 3600)
    if body is not None:
        return body
    timeout = max(2, min(60, float(profile.get("http_timeout", 12))))
    headers = {}
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if source == "openalex" and key:
        headers["Authorization"] = "Bearer " + key
    last_error = "network error"
    for attempt in range(2):
        try:
            _respect_rate_limit(source)
            body = _http_get(url, timeout=timeout, headers=headers)
            if cache_path is not None:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = cache_path.with_suffix(".tmp")
                    temporary.write_text(json.dumps({"fetched_at": time.time(), "body": body.decode("utf-8")}), encoding="utf-8")
                    temporary.replace(cache_path)
                except (OSError, UnicodeError):
                    warnings.append(f"{source}：缓存写入失败，本轮继续使用在线结果。")
            return body
        except HTTPError as error:
            last_error = f"HTTP {error.code}"
            if error.code in (400, 401, 403, 404, 410, 422):
                break
            if attempt == 0:
                # Cap Retry-After to keep a scheduled run bounded.
                try:
                    delay = min(10, max(1, float(error.headers.get("Retry-After", 1))))
                except (TypeError, ValueError, AttributeError):
                    delay = 1
                time.sleep(delay)
        except (OSError, URLError, TimeoutError, ValueError) as error:
            # Do not echo response bodies or request headers into reports.
            last_error = type(error).__name__
            if attempt == 0:
                time.sleep(1)
    stale = _cached(cache_path, 14 * 24 * 3600)
    if stale is not None:
        warnings.append(f"{source}：在线请求失败（{last_error}），使用不超过 14 天的缓存；不能据此保证最新文献完整。")
        return stale
    raise RuntimeError(last_error)


def _queries(profile: dict) -> list[dict]:
    """Bounded lexical expansion, keeping every required concept in each query.

    ``core_concepts`` is an AND of groups; the expressions within a group are
    alternatives.  The APIs differ in Boolean support, so this layer emits
    explicit lexical combinations rather than pretending that Crossref's
    ranked text search implements a strict Boolean filter.  Ranking checks
    concept coverage afterwards.  Legacy keywords are normalized by the same
    helper as the ranker so that a third keyword is never silently omitted.
    """
    groups = normalize_concepts(profile)
    methods = _strings(profile.get("method_terms"))
    questions = _strings(profile.get("questions") or profile.get("current_questions"))
    if not groups:
        fallback = str(profile.get("title") or profile.get("topic_id") or "").strip()
        groups = [[fallback]] if fallback else []
    if not groups:
        return []
    base = [group[0] for group in groups]
    queries = [{"terms": base, "mode": "relevance"},
               {"terms": base, "mode": "recent"}]
    # Expand the problem group first, then the object group, and rotate across
    # groups.  Avoid a combinatorial product and keep all three sources bounded.
    for alternative in range(1, max(len(group) for group in groups)):
        for index in reversed(range(len(groups))):
            if alternative >= len(groups[index]):
                continue
            variant = list(base)
            variant[index] = groups[index][alternative]
            if variant != base and not any(query["terms"] == variant for query in queries):
                queries.append({"terms": variant, "mode": "relevance"})
                if len(queries) == 6:
                    return queries
    # Preferences may narrow a supplemental request, but never replace a
    # required object/problem concept as they did in the previous query builder.
    stage = str(profile.get("stage", "")).lower()
    if stage in ("review", "survey", "orientation", "beginner", "综述入门", "入门"):
        extra = "review"
    elif methods:
        extra = methods[0]
    elif questions:
        extra = questions[0][:180]
    else:
        extra = ""
    if len(queries) < 6 and extra:
        supplemental = list(dict.fromkeys(base + [extra]))
        queries.append({"terms": supplemental, "mode": "relevance"})
    return queries


def _request(source: str, query: dict, count: int, page: int = 0) -> str:
    """Build basic paging requests; use an unchanged page size on continuation.

    Crossref and OpenAlex basic paging cover only their first 10,000 results.
    This is an interactive bounded search, not a bulk metadata harvester.
    """
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        raise ValueError("search page must be a non-negative integer")
    terms = query["terms"]
    recent = query["mode"] == "recent"
    if source == "crossref":
        if page * count > 10000:
            raise ValueError("Crossref basic paging exceeds 10000 records; refine the concepts")
        params = {"query": " ".join(terms), "rows": count,
                  "sort": "published" if recent else "relevance", "order": "desc",
                  "offset": page * count}
        email = os.environ.get("CROSSREF_MAILTO", "").strip()
        if email:
            params["mailto"] = email
        return "https://api.crossref.org/works?" + urlencode(params)
    if source == "openalex":
        count = min(200, count)
        if (page + 1) * count > 10000:
            raise ValueError("OpenAlex basic paging exceeds 10000 records; refine the concepts")
        params = {"search": " ".join(terms), "per_page": count, "page": page + 1}
        if recent:
            params["sort"] = "publication_date:desc"
        return "https://api.openalex.org/works?" + urlencode(params)
    expression = " AND ".join('all:"' + term.replace('"', " ") + '"' for term in terms)
    return "https://export.arxiv.org/api/query?" + urlencode({
        "search_query": expression, "start": page * count, "max_results": count,
        "sortBy": "submittedDate" if recent else "relevance", "sortOrder": "descending",
    })


def _round_robin(groups: list[list[dict]]) -> list[dict]:
    output = []
    for position in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if position < len(group):
                output.append(group[position])
    return output


def _title_key(paper: dict) -> str:
    normalized = unicodedata.normalize("NFKC", paper.get("title", "")).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _same_title_version(left: dict, right: dict) -> bool:
    if not _title_key(left) or _title_key(left) != _title_key(right):
        return False
    if left.get("doi") and right.get("doi") and left["doi"] != right["doi"]:
        # Distinct DOI records are not deduplicated by title alone.
        return False
    authors_left, authors_right = left.get("authors") or [], right.get("authors") or []
    if not authors_left or not authors_right:
        return False
    surname_left = authors_left[0].casefold().split()[-1]
    surname_right = authors_right[0].casefold().split()[-1]
    return surname_left == surname_right


def _merge_pair(left: dict, right: dict) -> dict:
    ranks = {"crossref": 3, "openalex": 2, "arxiv": 1}
    primary, other = (left, right)
    # Prefer journal metadata over deposited preprint metadata, even if both
    # records came from Crossref and carry different explicitly linked DOIs.
    rank = lambda paper: ranks.get(paper.get("source"), 0) - (2 if paper.get("is_preprint") and paper.get("source") != "arxiv" else 0)
    if rank(right) > rank(left):
        primary, other = right, left
    result = dict(primary)
    for key in ("doi", "arxiv_id", "abstract", "pdf_url", "url", "authors"):
        if not result.get(key) and other.get(key):
            result[key] = other[key]
    # A repository PDF does not turn a published record into a preprint.
    if not primary.get("is_preprint"):
        result.pop("is_preprint", None)
    result["sources"] = list(dict.fromkeys(left.get("sources", []) + right.get("sources", [])))
    versions = []
    for version in left.get("versions", []) + right.get("versions", []):
        if version not in versions:
            versions.append(version)
    result["versions"] = versions
    locations = []
    for location in left.get("locations", []) + right.get("locations", []):
        if location not in locations:
            locations.append(location)
    if locations:
        result["locations"] = locations
    result["related_dois"] = list(dict.fromkeys(left.get("related_dois", []) + right.get("related_dois", [])))
    result["retrieved_for"] = list(dict.fromkeys(left.get("retrieved_for", []) + right.get("retrieved_for", [])))
    return result


def merge_candidates(papers: list[dict]) -> list[dict]:
    """Merge explicit identities and conservative title/author matches.

    Keep all source/version links.  No fuzzy title-only match is performed.
    The journal record's publication date remains distinct from an arXiv
    first-submission date retained in ``versions``.
    """
    merged = []
    for paper in papers:
        matches = []
        for index, existing in enumerate(merged):
            same_doi = bool(paper.get("doi") and paper["doi"] == existing.get("doi"))
            same_arxiv = bool(paper.get("arxiv_id") and paper["arxiv_id"] == existing.get("arxiv_id"))
            related = bool(paper.get("doi") and paper["doi"] in existing.get("related_dois", [])) or bool(
                existing.get("doi") and existing["doi"] in paper.get("related_dois", []))
            if same_doi or same_arxiv or related or _same_title_version(paper, existing):
                matches.append(index)
        if not matches:
            merged.append(dict(paper))
            continue
        target = matches[0]
        combined = _merge_pair(merged[target], paper)
        for index in reversed(matches[1:]):
            combined = _merge_pair(combined, merged.pop(index))
        merged[target] = combined
    return merged


def search(profile: dict, limit: int = 200, sources: list | None = None) -> tuple[list[dict], list[str]]:
    """Search multiple sources with balanced candidate/query budgets.

    ``limit`` bounds returned records before cross-query/source deduplication,
    independently of recommendation quotas.  It is not a completeness promise.
    ``_search_page`` is zero-based and advances all requests by one page.  To
    continue without skipping/repeating ranges, keep the profile, source list,
    and limit unchanged.  URLs include paging, so cache entries remain distinct.
    ``_search_diagnostics`` receives safe counts and lexical query summaries;
    API credentials and full request URLs are never written into it.

    ``core_concepts``, ``method_terms`` and ``questions`` are discipline-independent.
    For existing profiles ``include_terms`` and ``current_questions`` are
    accepted.  ``_cache_dir`` enables 24-hour caching.  Keys are optional:
    ``OPENALEX_API_KEY`` (Bearer header), ``CROSSREF_MAILTO`` (polite pool).
    """
    if not isinstance(profile, dict):
        raise TypeError("profile must be a dictionary")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if limit <= 0:
        return [], []
    if limit > 1000:
        raise ValueError("limit must not exceed 1000")
    page = profile.get("_search_page", 0)
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        raise ValueError("search page must be a non-negative integer")
    diagnostics = profile.get("_search_diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = {}
        profile["_search_diagnostics"] = diagnostics
    diagnostics.clear()
    diagnostics.update({"page": page, "limit": limit, "raw_count": 0,
                        "deduplicated_count": 0, "deduped_count": 0, "duplicate_count": 0,
                        "source_counts": {}, "query_summaries": [], "errors": []})
    warnings = []
    selected = _strings(DEFAULT_SOURCES if sources is None else sources)
    selected = [source.lower() for source in selected]
    unknown = [source for source in selected if source not in DEFAULT_SOURCES]
    if unknown:
        warnings.append("未支持的检索来源：" + ", ".join(unknown))
    selected = list(dict.fromkeys(source for source in selected if source in DEFAULT_SOURCES))
    queries = _queries(profile)
    if not selected or not queries:
        warnings.append("未提供有效检索来源或主题关键词，未检索候选文献。")
        diagnostics["errors"] = list(warnings)
        return [], warnings
    parsers = {"crossref": _crossref_items, "openalex": _openalex_items, "arxiv": _arxiv_items}
    source_results = [[] for _ in selected]
    source_budgets = [limit // len(selected) + int(i < limit % len(selected)) for i in range(len(selected))]
    for source_index, source in enumerate(selected):
        budget = source_budgets[source_index]
        query_results = []
        for query_index, query in enumerate(queries):
            count = budget // len(queries) + int(query_index < budget % len(queries))
            # OpenAlex's basic page size is 200.  The limit is a ceiling rather
            # than an instruction to exceed a source's supported request size.
            if source == "openalex":
                count = min(200, count)
            if count <= 0:
                continue
            summary = {"source": source, "terms": list(query["terms"]),
                       "mode": query["mode"], "page": page, "requested": count,
                       "returned": 0, "status": "ok"}
            diagnostics["query_summaries"].append(summary)
            try:
                papers = parsers[source](_fetch(_request(source, query, count, page), profile, source, warnings))[:count]
            except (RuntimeError, ValueError, TypeError, KeyError, ET.ParseError) as error:
                # The transport emits controlled HTTP/type messages; a custom
                # boundary exception must not leak URLs, credentials, or bodies.
                detail = str(error) if isinstance(error, RuntimeError) and re.fullmatch(r"HTTP \d{3}|[A-Za-z]+Error|network error", str(error)) else type(error).__name__
                summary["status"] = "failed"
                summary["error"] = detail
                warnings.append(f"{source} 检索失败（{detail}），本轮继续使用其他来源；结果可能不完整。")
                break
            summary["returned"] = len(papers)
            diagnostics["raw_count"] += len(papers)
            for paper in papers:
                paper["retrieved_for"] = [" ".join(query["terms"])]
                paper["query_mode"] = query["mode"]
            query_results.append(papers)
        source_results[source_index] = _round_robin(query_results)
        diagnostics["source_counts"][source] = len(source_results[source_index])
    combined = merge_candidates(_round_robin(source_results))[:limit]
    diagnostics["deduplicated_count"] = len(combined)
    diagnostics["deduped_count"] = len(combined)
    diagnostics["duplicate_count"] = diagnostics["raw_count"] - len(combined)
    if not combined:
        warnings.append("在线检索未获得候选文献。可检查网络或关键词；演示候选须显式选择 demo 模式。")
    diagnostics["errors"] = list(dict.fromkeys(warnings))
    return combined, diagnostics["errors"]
