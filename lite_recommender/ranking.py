from __future__ import annotations
import datetime as dt
import re
from .storage import normalized, digest
from .concepts import normalize_concepts

# Configuration-match points, not a judgment of scientific quality.
WEIGHTS = {"topic": 70, "method": 10, "stage": 15, "utility": 0, "complement": 0, "evidence": 5}
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
    return bool(term.isascii() and re.search(r" " + re.escape(term) + r"(?:s|es) ", hay))


def adjacent_match(text, concepts, concept_hits, profile):
    """Evidence of a neighboring problem, beyond the broad object alone."""
    if any(group["terms"] for group in concept_hits[1:]):
        return True
    if any(matches(text, term) for term in profile.get("exploration_terms", [])):
        return True
    generic = {"model", "models", "method", "approach", "property", "properties", "alloy", "alloys", "high", "entropy", "research", "study", "with", "from", "that"}
    fragments = {word for group in concepts[1:] for term in group for word in normalized(term).split() if len(word) >= 4 and word not in generic}
    return any(matches(text, word) for word in fragments)


def compute_policy(profile, feedback, previous):
    interest_input = {pid: {"rating": v.get("rating"), "reason": v.get("reason") if v.get("reason") in INTEREST_REASONS else None, "tags": sorted(v.get("tags", [])) if v.get("reason") in INTEREST_REASONS else []} for pid, v in feedback.items()}
    current_digest = digest(interest_input)
    if previous.get("digest") == current_digest:
        return previous
    rated = [v for v in feedback.values() if v.get("rating") is not None]
    terms = list(dict.fromkeys(profile.get("keywords", []) + profile.get("method_terms", []) + [term for group in normalize_concepts(profile) for term in group]))
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


def score(profile, paper, policy, run_date, semantic=None):
    text = paper.get("title", "") + " " + paper.get("abstract", "")
    matched = [k for k in profile.get("keywords", []) if matches(text, k)]
    methods = [k for k in profile.get("method_terms", []) if matches(text, k)]
    concepts = normalize_concepts(profile)
    concept_hits = [{"index": index, "label": group[0], "terms": [term for term in group if matches(text, term)]} for index, group in enumerate(concepts)]
    matched_concepts = [group for group in concept_hits if group["terms"]]
    missing_concepts = [group["label"] for group in concept_hits if not group["terms"]]
    topic = len(matched_concepts) / max(1, len(concepts))
    method = min(1.0, len(methods) / max(1, min(2, len(profile.get("method_terms", []))))) if profile.get("method_terms") else 0.0
    kind = paper.get("kind", "research")
    stage = profile.get("stage", "survey")
    stage_fit = {"survey": {"review": 1.0, "method": .85, "research": .7}, "gap": {"review": .8, "method": 1.0, "research": 1.0}, "focus": {"review": .6, "method": .95, "research": 1.0}}.get(stage, {}).get(kind, .7)
    components = {"topic": topic, "method": method, "stage": stage_fit, "utility": 0, "complement": 0, "evidence": 1 if paper.get("abstract") and (paper.get("doi") or paper.get("arxiv_id")) else (.7 if paper.get("abstract") else .2)}
    base = round(sum(WEIGHTS[k] * v for k, v in components.items()), 2)
    matched_terms = list(dict.fromkeys(matched + methods + [term for group in matched_concepts for term in group["terms"]]))
    adjustments = [policy.get("adjustments", {}).get(k, 0) for k in matched_terms]
    correction = round(sum(adjustments) / len(adjustments), 3) if adjustments else 0
    published = paper.get("published", "")
    try:
        age = (run_date - dt.date.fromisoformat(published[:10])).days
        date_bucket = "new" if 0 <= age < 7 else ("future" if age < 0 else "foundational")
    except (ValueError, TypeError):
        date_bucket = "unknown"
    excluded = [term for term in profile.get("exclude_terms", []) if matches(text, term)]
    semantic_adjustment = float((semantic or {}).get("adjustment", 0))
    final = round(max(0, min(100, base + correction + semantic_adjustment)), 2)
    core_threshold = float(profile.get("core_min_score", 65))
    exploration_threshold = float(profile.get("exploration_min_score", 50))
    hard_reasons = (["excluded_term"] if excluded else []) + (["future_date"] if date_bucket == "future" else [])
    reasons = list(hard_reasons)
    if not paper.get("abstract"):
        reasons.append("missing_abstract")
    if missing_concepts or not concepts:
        reasons.append("missing_core_concept")
    if final < core_threshold:
        reasons.append("below_core_score")
    anchor_match = bool(concept_hits and concept_hits[0]["terms"])
    adjacent = adjacent_match(text, concepts, concept_hits, profile)
    eligible = not hard_reasons and bool(paper.get("abstract")) and bool(concepts) and not missing_concepts and final >= core_threshold
    exploration = not hard_reasons and bool(paper.get("abstract")) and len(concepts) >= 2 and anchor_match and adjacent and topic >= .5 and final >= exploration_threshold and not eligible
    if not eligible and paper.get("abstract") and not adjacent:
        reasons.append("not_adjacent")
    classification = "rejected" if hard_reasons else ("pending" if not paper.get("abstract") else ("core" if eligible else ("exploration" if exploration else "rejected")))
    reason = "核心概念：" + "、".join(group["label"] for group in matched_concepts) + ("；待核验概念：" + "、".join(missing_concepts) if missing_concepts else "") + "；方法偏好加分：" + "、".join(methods or ["未命中，不作为必要条件"]) + "。分数仅表示配置匹配程度。"
    result = {**paper, "topic_id": profile["topic_id"], "topic_title": profile["title"], "stage": stage, "base_score": base, "adjustment": correction, "score": final, "components": components, "matched_terms": matched_terms, "matched_concepts": matched_concepts, "missing_concepts": missing_concepts, "core_concepts": concepts, "adjacent_core": adjacent, "reason": reason, "evidence": "abstract" if paper.get("abstract") else "title_only", "eligible": eligible, "exploration_eligible": exploration, "classification": classification, "rejection_reasons": reasons, "date_bucket": date_bucket, "excluded_terms": excluded, "profile_version": profile["profile_version"], "scoring_version": "2.1"}
    if semantic:
        result["semantic"] = semantic
    return result


def _features(paper):
    stop = {"the", "of", "and", "in", "on", "for", "a", "an", "with", "using", "study", "model"}
    return {term for term in normalized(paper.get("title", "")).split() if term not in stop and not term.isdigit()} | {normalized(term) for term in paper.get("matched_terms", [])}


def _novelty(paper, selected):
    if not selected:
        return 1.0
    features = _features(paper)
    return 1 - max((len(features & _features(other)) / max(1, len(features | _features(other))) for other in selected), default=0)


def _select_topic(papers, budget):
    if budget <= 0:
        return []
    core = sorted([paper for paper in papers if paper.get("eligible")], key=lambda paper: paper["score"], reverse=True)
    adjacent = sorted([paper for paper in papers if paper.get("exploration_eligible") and not paper.get("eligible")], key=lambda paper: paper["score"], reverse=True)
    reserve = 1 if adjacent and budget > 1 else 0
    result = []
    for _ in range(min(len(core), budget - reserve)):
        chosen = max(core, key=lambda paper: paper["score"] + 5 * _novelty(paper, result))
        novelty = round(_novelty(chosen, result), 3)
        result.append({**chosen, "selection_novelty": novelty, "components": {**chosen.get("components", {}), "complement": novelty}})
        core.remove(chosen)
    if reserve:
        chosen = max(adjacent, key=lambda paper: paper["score"] + 5 * _novelty(paper, result))
        novelty = round(_novelty(chosen, result), 3)
        result.append({**chosen, "exploration": True, "selection_novelty": novelty, "components": {**chosen.get("components", {}), "complement": novelty}})
    return result


def select(ranked_by_topic, budget):
    """Select actual lexical diversity, with at most one exploration per topic.

    A dict means independent per-topic quotas. An integer retains the global
    round-robin API for older callers; Service normally calls one topic at a
    time, so no direction silently borrows another direction's weekly quota.
    """
    if isinstance(budget, dict):
        return [paper for topic, papers in ranked_by_topic.items() for paper in _select_topic(papers, max(0, int(budget.get(topic, 0))))]
    if budget <= 0:
        return []
    queues = {topic: _select_topic(papers, budget) for topic, papers in ranked_by_topic.items()}
    result = []
    while len(result) < budget and any(queues.values()):
        for queue in queues.values():
            if queue and len(result) < budget:
                result.append(queue.pop(0))
    return result
