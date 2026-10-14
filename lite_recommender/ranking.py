from __future__ import annotations
import datetime as dt
from .storage import normalized, digest

WEIGHTS = {"topic": 40, "method": 25, "stage": 15, "utility": 10, "complement": 5, "evidence": 5}
INTEREST_REASONS = {"relevant", "method_useful", "mechanism_relevant", "reproducible", "question_relevant", "topic_mismatch", "scale_mismatch", "method_mismatch", "duplicate"}
REASONS = INTEREST_REASONS | {"stage_mismatch", "too_difficult", "no_time", "no_fulltext", "evidence_insufficient", "other", ""}


def matches(text, term):
    term = normalized(term)
    hay = " " + normalized(text) + " "
    if not term:
        return False
    if (" " + term + " ") in hay:
        return True
    # Conservative plural handling for English search phrases (alloy/alloys, model/models).
    import re
    return bool(term.isascii() and re.search(r" " + re.escape(term) + r"(?:s|es) ", hay))


def compute_policy(profile, feedback, previous):
    interest_input = {pid: {"rating": v.get("rating"), "reason": v.get("reason") if v.get("reason") in INTEREST_REASONS else None, "tags": sorted(v.get("tags", [])) if v.get("reason") in INTEREST_REASONS else []} for pid, v in feedback.items()}
    current_digest = digest(interest_input)
    if previous.get("digest") == current_digest:
        return previous
    rated = [v for v in feedback.values() if v.get("rating") is not None]
    terms = list(dict.fromkeys(profile.get("keywords", []) + profile.get("method_terms", [])))
    adjustments = {}
    for tag in terms:
        attributed = []
        for item in rated:
            if item.get("reason") not in INTEREST_REASONS:
                continue
            tags = item.get("tags", [])
            if tag in tags:
                attributed.append(((item["rating"] - 3) / 2, 1 / max(1, len(tags))))
        if len(rated) < 3 or len(attributed) < 2:
            adjustments[tag] = 0.0
            continue
        weight = sum(c for _, c in attributed)
        target = max(-8, min(8, 8 * sum(r * c for r, c in attributed) / (5 + weight)))
        before = previous.get("adjustments", {}).get(tag, 0)
        adjustments[tag] = round(max(before - 2, min(before + 2, target)), 3)
    return {"digest": current_digest, "adjustments": adjustments, "rated_count": len(rated)}


def score(profile, paper, policy, run_date):
    text = paper.get("title", "") + " " + paper.get("abstract", "")
    matched = [k for k in profile.get("keywords", []) if matches(text, k)]
    methods = [k for k in profile.get("method_terms", []) if matches(text, k)]
    topic = min(1.0, len(matched) / max(1, min(3, len(profile.get("keywords", [])))))
    method = min(1.0, len(methods) / max(1, min(2, len(profile.get("method_terms", []))))) if profile.get("method_terms") else 0.6
    kind = paper.get("kind", "research")
    stage = profile.get("stage", "survey")
    stage_fit = {"survey": {"review": 1.0, "method": .85, "research": .7}, "gap": {"review": .8, "method": 1.0, "research": 1.0}, "focus": {"review": .6, "method": .95, "research": 1.0}}[stage].get(kind, .7)
    components = {"topic": topic, "method": method, "stage": stage_fit, "utility": .7 if methods else .5, "complement": .8, "evidence": 1 if paper.get("abstract") and (paper.get("doi") or paper.get("arxiv_id")) else .7}
    base = round(sum(WEIGHTS[k] * v for k, v in components.items()), 2)
    matched_terms = list(dict.fromkeys(matched + methods))
    adjustments = [policy.get("adjustments", {}).get(k, 0) for k in matched_terms]
    correction = round(sum(adjustments) / len(adjustments), 3) if adjustments else 0
    published = paper.get("published", "")
    try:
        age = (run_date - dt.date.fromisoformat(published[:10])).days
        date_bucket = "new" if 0 <= age < 7 else ("future" if age < 0 else "foundational")
    except (ValueError, TypeError):
        date_bucket = "unknown"
    excluded = [term for term in profile.get("exclude_terms", []) if matches(text, term)]
    eligible = bool(paper.get("abstract")) and topic >= .4 and base >= 65 and not excluded and date_bucket != "future"
    exploration = bool(paper.get("abstract")) and topic >= .25 and method >= .7 and base >= 55 and not excluded and date_bucket != "future"
    reason = "命中研究词：" + "、".join(matched or ["不足"]) + "；方法词：" + "、".join(methods or ["未命中，需人工判断方法适用性"]) + "。当前阶段：" + stage + "。"
    return {**paper, "topic_id": profile["topic_id"], "topic_title": profile["title"], "stage": stage, "base_score": base, "adjustment": correction, "score": round(max(0, min(100, base + correction)), 2), "components": components, "matched_terms": matched_terms, "reason": reason, "evidence": "abstract" if paper.get("abstract") else "metadata", "eligible": eligible, "exploration_eligible": exploration, "date_bucket": date_bucket, "excluded_terms": excluded, "profile_version": profile["profile_version"]}


def select(ranked_by_topic, budget):
    """Allocate globally, round-robin by priority; never backfill with rejected work."""
    queues = {key: sorted([p for p in value if p["eligible"]], key=lambda p: p["score"], reverse=True) for key, value in ranked_by_topic.items()}
    kind_sequences = {"survey": ["review"] * 4 + ["research"] * 3 + ["method"] * 2, "gap": ["review"] * 2 + ["research"] * 5 + ["method"] * 2, "focus": ["research"] * 6 + ["method"] * 2 + ["review"]}
    counts = {key: 0 for key in queues}
    selected = []
    while len(selected) < budget:
        progressed = False
        for key, queue in queues.items():
            if queue and len(selected) < budget:
                sequence = kind_sequences.get(queue[0].get("stage", "focus"), kind_sequences["focus"])
                desired = sequence[counts[key]] if counts[key] < len(sequence) else None
                pick = next((i for i, p in enumerate(queue) if p.get("kind") == desired), 0)
                selected.append(queue.pop(pick))
                counts[key] += 1
                progressed = True
        if not progressed:
            break
    # Reserve one slot for a qualified adjacent method; it is inside the global budget.
    exploration = sorted([p for value in ranked_by_topic.values() for p in value if p["exploration_eligible"] and not p["eligible"]], key=lambda p: p["score"], reverse=True)
    if exploration and budget > 1:
        item = {**exploration[0], "exploration": True}
        if len(selected) >= budget:
            selected[-1] = item
        else:
            selected.append(item)
    return selected[:budget]
