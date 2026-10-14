from __future__ import annotations
import json
from pathlib import Path
from .storage import normalized, digest, atomic_json


def import_legacy(service, path: Path, topic_id: str):
    profile = service._topic(topic_id)
    if (path / "data").is_dir():
        path = path / "data"
    if not path.is_dir():
        raise ValueError("旧数据目录不存在")
    report = {"papers_imported": 0, "feedback_imported": 0, "feedback_unchanged": 0, "unresolved": [], "source": str(path.resolve()), "topic_id": topic_id, "note": "仅导入原始记录，不导入可能被重复累加的旧兴趣权重。"}
    titles = {}
    for file in sorted(path.rglob("paper_candidates.json")):
        data = json.loads(file.read_text(encoding="utf-8"))
        values = data if isinstance(data, list) else data.get("papers", [])
        for paper in values:
            mapped = {**paper, "url": paper.get("url") or paper.get("link", ""), "source": paper.get("source") or paper.get("journal_or_source") or "legacy", "is_demo": False}
            saved = service.store.upsert_paper(mapped)
            titles[normalized(saved["title"])] = saved["paper_id"]
            report["papers_imported"] += 1
    history_path = path / "feedback_history.json"
    if history_path.exists():
        data = json.loads(history_path.read_text(encoding="utf-8"))
        records = data if isinstance(data, list) else data.get("feedback", [])
        for record in records:
            pid = titles.get(normalized(record.get("title", "")))
            if not pid:
                report["unresolved"].append({"title": record.get("title", ""), "date": record.get("date", ""), "paper_id": record.get("paper_id", ""), "reason": "原候选清单无法确定稳定论文标识"})
                continue
            rating = record.get("rating")
            try:
                rating = int(rating) if rating not in (None, "") else None
            except (ValueError, TypeError):
                report["unresolved"].append({"title": record.get("title", ""), "reason": "评分格式无效"})
                continue
            if rating is not None and not 1 <= rating <= 5:
                report["unresolved"].append({"title": record.get("title", ""), "reason": "评分超出1–5"})
                continue
            tag_values = record.get("follow_up_keywords") or []
            tags = [v for v in tag_values if v in profile["keywords"] + profile["method_terms"]]
            payload = {"topic_id": topic_id, "paper_id": pid, "rating": rating, "read_status": record.get("status") or "unread", "note": record.get("note", ""), "reason": "relevant" if rating and rating >= 4 and tags else "other", "tags": tags}
            try:
                result = service.feedback(payload, source_key="legacy_" + digest([topic_id, str(history_path.resolve()), record]))
                report["feedback_imported" if result["changed"] else "feedback_unchanged"] += 1
            except ValueError as exc:
                report["unresolved"].append({"title": record.get("title", ""), "reason": str(exc)})
    if not report["papers_imported"] and not history_path.exists():
        report["note"] += " 当前分享包不含原机data，请从原电脑另行导出。"
    out = service.workspace / "_shared/migrations" / ("legacy-" + digest([str(path.resolve()), topic_id])[:16] + ".json")
    atomic_json(out, report)
    report["report_path"] = str(out)
    return report
