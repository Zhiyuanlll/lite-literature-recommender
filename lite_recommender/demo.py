"""Clearly synthetic metadata for offline demonstrations and smoke tests."""
from __future__ import annotations

import hashlib
from datetime import date, timedelta


def _terms(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(term).strip() for term in value if str(term).strip()]


def candidates(profile: dict, limit: int = 36) -> list[dict]:
    """Generate discipline-neutral examples, never purported real articles.

    All titles/abstracts explicitly mark synthetic data, every identity is in
    the ``demo:`` namespace, and all links use the reserved example.invalid
    domain.  Neither a real DOI nor an arXiv identifier is invented.
    """
    if not isinstance(profile, dict):
        raise TypeError("profile must be a dictionary")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if limit <= 0:
        return []
    keywords = _terms(profile.get("keywords") or profile.get("include_terms"))
    methods = _terms(profile.get("method_terms"))
    questions = _terms(profile.get("questions") or profile.get("current_questions"))
    topic = str(profile.get("topic_id") or profile.get("title") or "new-topic")
    keywords = keywords or [str(profile.get("title") or topic)]
    methods = methods or ["reproducible modelling", "controlled experiment", "data analysis"]
    run_date = profile.get("_run_date")
    if isinstance(run_date, date):
        today = run_date
    else:
        today = date.fromisoformat(str(run_date)[:10]) if run_date else date.today()
    digest = hashlib.sha256(topic.encode("utf-8")).hexdigest()[:12]
    forms = (
        ("review", "Review and research landscape"),
        ("research", "Mechanism and parameter sensitivity"),
        ("method", "Method validation and reproducible workflow"),
        ("research", "Comparison and independent evidence"),
        ("review", "Survey of assumptions and unresolved questions"),
        ("research", "Robustness across conditions"),
    )
    papers = []
    for index in range(limit):
        kind, form = forms[index % len(forms)]
        term = keywords[index % len(keywords)]
        method = methods[(index // len(keywords)) % len(methods)]
        # Include both recent and background papers to exercise weekly allocation.
        age = index % 7 if index % 3 else 35 + index * 17
        published = today - timedelta(days=age)
        identity = f"demo:{digest}:{index + 1:03d}"
        url = f"https://example.invalid/synthetic/{digest}/{index + 1:03d}"
        question = questions[index % len(questions)] if questions else f"Research question about {term}"
        title = f"[SYNTHETIC/演示] {form}: {term} using {method} ({index + 1:02d})"
        abstract = (
            "SYNTHETIC DEMO DATA / 合成演示数据，非真实论文，不得引用。 "
            f"Topic: {'; '.join(keywords)}. Method: {method}. Question: {question}. "
            f"This example tests {kind} allocation, ranking, reading-state feedback and version-safe storage. "
            "The apparent result is illustrative only; no scientific claim is supported."
        )
        papers.append({
            "paper_id": identity, "title": title, "authors": ["Synthetic Demo Author"],
            "doi": "", "arxiv_id": "", "url": url, "abstract": abstract,
            "published": published.isoformat(), "year": published.year,
            "source": "demo", "sources": ["demo"], "kind": kind,
            "is_demo": True, "pdf_url": "", "tags": [term, method],
            "versions": [{"source": "demo", "doi": "", "arxiv_id": "", "url": url,
                          "pdf_url": "", "published": published.isoformat(), "year": published.year}],
        })
    return papers
