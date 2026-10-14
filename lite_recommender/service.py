from __future__ import annotations
import datetime as dt
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from . import __version__
from .storage import Store, FileLock, atomic_json, dump, digest, normalized
from .ranking import score, select, compute_policy, REASONS

DEFAULT_SETTINGS = {"budget": 10, "sources": ["crossref", "openalex", "arxiv"], "schedule": {"enabled": False, "day": 0, "time": "09:00", "timezone": "Asia/Shanghai"}}


def week_key(value):
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


def timezone(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return dt.timezone(dt.timedelta(hours=8))
        if name == "UTC":
            return dt.timezone.utc
        raise ValueError("时区无效；Windows 无时区数据库时请选择 Asia/Shanghai 或 UTC")


def identifier(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"[\w-][\w.-]{0,79}", value) or value in (".", ".."):
        raise ValueError("标识只能包含字母、数字、中文、下划线、短横线和点，长度1–80")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³"}
    if value.endswith(".") or value.split(".", 1)[0].upper() in reserved:
        raise ValueError("为兼容Windows，请勿使用设备保留名称或以点结尾的标识")
    return value


def terms(value):
    if isinstance(value, str):
        value = re.split(r"[,，;；\n]", value)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("关键词或问题必须为列表或分行文字")
    return list(dict.fromkeys(str(v).strip() for v in value if str(v).strip()))[:60]


class Service:
    def __init__(self, workspace):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.workspace / "_shared/library.sqlite")
        self.settings_path = self.workspace / "settings.json"
        if not self.settings_path.exists():
            atomic_json(self.settings_path, DEFAULT_SETTINGS)
        self.settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.settings = {**DEFAULT_SETTINGS, **self.settings, "schedule": {**DEFAULT_SETTINGS["schedule"], **self.settings.get("schedule", {})}}
        self.save_settings(self.settings)
        self._sync_profiles()

    def now(self):
        return dt.datetime.now(timezone(self.settings["schedule"]["timezone"]))

    def _sync_profiles(self):
        registry = self.workspace / "topics.json"
        if not registry.exists():
            return
        data = json.loads(registry.read_text(encoding="utf-8"))
        for item in data.get("topics", []):
            relative = item.get("relative_path", "")
            path = (self.workspace / relative / "profile.json").resolve()
            if self.workspace not in path.parents:
                raise ValueError("专题路径必须位于工作目录内")
            if not path.exists():
                continue
            p = json.loads(path.read_text(encoding="utf-8"))
            tid = identifier(p.get("topic_id") or item.get("id"))
            profile = {**p, "topic_id": tid, "id": tid, "title": p.get("title") or p.get("research_topic") or tid, "group": p.get("group") or item.get("group") or "其他", "questions": terms(p.get("questions", p.get("current_questions", []))), "keywords": terms(p.get("keywords", p.get("include_terms", []))), "method_terms": terms(p.get("method_terms", [])), "exclude_terms": terms(p.get("exclude_terms", p.get("exclude_terms_confirmed_by_user", []))), "stage": p.get("stage") if p.get("stage") in ("survey", "gap", "focus") else "survey", "active": bool(p.get("active", False)), "profile_version": str(p.get("profile_version", "1")), "relative_path": relative, "priority": int(p.get("priority", 1)), "example": bool(p.get("example", False))}
            if profile["active"] and (not profile["keywords"] or not profile["questions"]):
                raise ValueError(f"已启用方向 {tid} 缺少研究问题或关键词，请检查 profile.json")
            existing = next((v for v in self.store.topics() if v["topic_id"] == tid), None)
            if existing and existing["profile_version"] == profile["profile_version"] and any(existing.get(k) != profile.get(k) for k in ("questions", "keywords", "method_terms", "exclude_terms", "stage")):
                try:
                    profile["profile_version"] = str(int(existing["profile_version"]) + 1)
                except ValueError:
                    profile["profile_version"] = "v_" + digest(profile)[:12]
                atomic_json(path, profile)
            self.store.save_topic(profile)

    def _topic(self, tid):
        for p in self.store.topics():
            if p["topic_id"] == tid:
                return p
        raise ValueError("未找到方向：" + str(tid))

    def save_topic(self, payload):
        tid = identifier(payload.get("topic_id") or payload.get("id"))
        existing = next((p for p in self.store.topics() if p["topic_id"] == tid), None)
        profile = {**(existing or {}), **payload, "topic_id": tid, "id": tid}
        profile["title"] = str(profile.get("title") or tid).strip()[:200]
        profile["group"] = identifier(profile.get("group") or "其他")
        for key in ("questions", "keywords", "method_terms", "exclude_terms"):
            profile[key] = terms(profile.get(key, []))
        profile["stage"] = profile.get("stage") or "survey"
        if profile["stage"] not in ("survey", "gap", "focus"):
            raise ValueError("阶段必须为survey、gap或focus")
        profile["active"] = bool(profile.get("active", False))
        if not profile["questions"] or not profile["keywords"]:
            raise ValueError("请至少填写一个当前研究问题和一个英文检索关键词")
        profile["priority"] = max(1, min(10, int(profile.get("priority", 1))))
        significant = ("questions", "keywords", "method_terms", "exclude_terms", "stage")
        version = str(existing.get("profile_version", "1")) if existing else "1"
        if existing and any(existing.get(k) != profile.get(k) for k in significant):
            try:
                version = str(int(version) + 1)
            except ValueError:
                version = "v_" + digest([version, profile])[:12]
        profile["profile_version"] = version
        profile["relative_path"] = str(Path(profile["group"]) / tid)
        topic_path = self.workspace / profile["relative_path"]
        for directory in ("data", "runs", "notes", "inputs"):
            (topic_path / directory).mkdir(parents=True, exist_ok=True)
        atomic_json(topic_path / "profile.json", profile)
        self.store.save_topic(profile)
        self._write_registry()
        return profile

    def _write_registry(self):
        atomic_json(self.workspace / "topics.json", {"schema_version": 2, "topics": [{"id": p["topic_id"], "topic_id": p["topic_id"], "title": p["title"], "group": p["group"], "relative_path": p["relative_path"], "active": p["active"]} for p in self.store.topics()]})

    def seed_examples(self):
        folder = Path(__file__).resolve().parent / "examples"
        for path in sorted(folder.glob("*.json")):
            p = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(p, dict) or not p.get("topic_id"):
                continue
            if not any(t["topic_id"] == p["topic_id"] for t in self.store.topics()):
                self.save_topic({**p, "active": False, "example": True})

    def _validate_settings(self, payload):
        settings = {**self.settings, **payload, "schedule": {**self.settings["schedule"], **payload.get("schedule", {})}}
        raw_budget = settings.get("budget", 10)
        if isinstance(raw_budget, bool) or not re.fullmatch(r"[0-9]+", str(raw_budget)):
            raise ValueError("每周推荐上限须为正整数")
        budget = int(raw_budget)
        if budget < 1:
            raise ValueError("每周推荐上限须为正整数")
        settings["budget"] = budget
        day = settings["schedule"].get("day", 0)
        if isinstance(day, str) and not day.isdigit():
            day = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].index(day)
        settings["schedule"]["day"] = int(day)
        if not 0 <= int(day) <= 6 or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", settings["schedule"].get("time", "")):
            raise ValueError("每周日期或时间无效")
        timezone(settings["schedule"]["timezone"])
        settings["schedule"]["enabled"] = bool(settings["schedule"].get("enabled"))
        allowed = {"crossref", "openalex", "arxiv"}
        if not isinstance(settings.get("sources"), list) or not settings["sources"] or not set(settings["sources"]) <= allowed:
            raise ValueError("检索源必须选crossref、openalex或arxiv")
        return settings

    def save_settings(self, payload):
        settings = self._validate_settings(payload)
        atomic_json(self.settings_path, settings)
        self.settings = settings
        return settings

    def feedback(self, payload, source_key=None):
        p = self._topic(payload.get("topic_id"))
        version = str(payload.get("profile_version") or p["profile_version"])
        if version != str(p["profile_version"]):
            frozen = self.store.profile_version(p["topic_id"], version)
            if not frozen:
                raise ValueError("旧研究配置版本不存在；请在对应历史批次提交评分")
            p = frozen
        pid = payload.get("paper_id")
        paper = self.store.paper(pid)
        if not paper:
            raise ValueError("论文标识不存在")
        pid = paper["paper_id"]
        rating = payload.get("rating")
        if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
            raise ValueError("评分必须是整数1–5，撤回请用null")
        reason = str(payload.get("reason") or "")
        if reason not in REASONS:
            raise ValueError("反馈原因无效")
        status = payload.get("read_status", payload.get("status", "unread")) or "unread"
        if status not in ("unread", "reading", "skimmed", "read", "cited", "ignore"):
            raise ValueError("阅读状态无效")
        supplied = payload.get("tags", [])
        tags = terms(supplied)
        known = set(p["keywords"] + p["method_terms"])
        if not set(tags) <= known:
            raise ValueError("偏好标签必须来自当前方向的关键词或方法词")
        clean = {"topic_id": p["topic_id"], "paper_id": pid, "profile_version": str(p["profile_version"]), "rating": rating, "reason": reason, "read_status": status, "note": str(payload.get("note") or "")[:6000], "tags": tags}
        changed = self.store.append_feedback(clean, self.now().isoformat(), source_key)
        return {**clean, "changed": changed, "message": "反馈已保存；下一轮推荐使用最新记录。" if changed else "记录未改变，没有重复学习。"}

    def overview(self):
        self.settings = self._validate_settings(json.loads(self.settings_path.read_text(encoding="utf-8")))
        stats = {"rated": 0, "high_rated": 0, "low_rated": 0, "topics": len(self.store.topics())}
        for topic in self.store.topics():
            values = self.store.latest_feedback(topic["topic_id"], topic["profile_version"])
            for pid, item in values.items():
                if item.get("rating") is not None and not (self.store.paper(pid) or {}).get("is_demo"):
                    stats["rated"] += 1
                    stats["high_rated"] += item["rating"] >= 4
                    stats["low_rated"] += item["rating"] <= 2
        stats["satisfaction"] = round(stats["high_rated"] / stats["rated"], 3) if stats["rated"] else None
        stats["used_this_week"] = self.store.used_budget(week_key(self.now()))
        return {"topics": self.store.topics(), "settings": self.settings, "runs": self.store.runs(), "stats": stats, "version": __version__, "workspace": str(self.workspace)}

    def run(self, topics=None, demo=False, run_id=None, preview=False):
        from . import sources
        from .demo import candidates
        now = self.now()
        week = week_key(now)
        if demo and preview:
            raise ValueError("离线演示和真实预览不能同时启用")
        mode = "demo" if demo else ("preview" if preview else "real")
        rid = identifier(run_id or week + "-" + mode)
        with FileLock(self.workspace / "_shared/run.lock"):
            old = self.store.run(rid)
            if old and old["mode"] != mode:
                raise ValueError("批次标识已被另一运行模式使用")
            if old and old["status"] in ("complete", "partial"):
                return {**old, "replayed": True}
            profiles = [p for p in self.store.topics() if (not topics or p["topic_id"] in topics) and (demo or preview or p.get("active"))]
            profiles.sort(key=lambda p: (-p.get("priority", 1), p["topic_id"]))
            if not profiles:
                raise ValueError("请先新增并启用一个研究方向。演示可使用未启用的示例方向。")
            for p in profiles:
                if not p["questions"] or not p["keywords"]:
                    raise ValueError("请先填写方向的研究问题和英文关键词：" + p["topic_id"])
            remaining = self.settings["budget"] if demo or preview else max(0, self.settings["budget"] - self.store.used_budget(week))
            out = self.workspace / "_shared/runs" / rid
            out.mkdir(parents=True, exist_ok=True)
            result = {"run_id": rid, "id": rid, "mode": mode, "status": "running", "created_at": now.isoformat(), "last_attempt_at": now.isoformat(), "attempts": (old or {}).get("attempts", 0) + 1, "count": 0, "topics": [p["topic_id"] for p in profiles], "profiles": profiles, "warnings": [], "report_path": str(out / "report.md"), "candidate_count": 0, "budget": remaining}
            self.store.save_run(result, week)
            try:
                warnings = result["warnings"]
                ranked = {}
                snapshots = []
                for p in profiles:
                    topicid = p["topic_id"]
                    feedback = self.store.latest_feedback(topicid, p["profile_version"])
                    # Synthetic ratings are confined to synthetic runs; they never train real preferences.
                    feedback = {pid: value for pid, value in feedback.items() if bool((self.store.paper(pid) or {}).get("is_demo")) == demo}
                    policy_key = str(p["profile_version"]) + (":demo" if demo else "")
                    policy = compute_policy(p, feedback, self.store.get_policy(topicid, policy_key))
                    frozen_path = out / (topicid + ".snapshot.json")
                    if old and frozen_path.exists():
                        snapshot = json.loads(frozen_path.read_text(encoding="utf-8"))
                        p, feedback, policy = snapshot["profile"], snapshot["feedback"], snapshot["policy"]
                    else:
                        atomic_json(frozen_path, {"profile": p, "feedback": feedback, "policy": policy})
                    snapshots.append({"profile": p, "feedback": feedback, "policy": policy})
                    if remaining == 0:
                        ranked[topicid] = []
                        continue
                    adjustments = policy.get("adjustments", {})
                    search_keywords = p["keywords"][:1] + sorted(p["keywords"][1:], key=lambda word: -adjustments.get(word, 0))
                    search_methods = sorted(p["method_terms"], key=lambda word: -adjustments.get(word, 0))
                    request_profile = {**p, "keywords": search_keywords, "method_terms": search_methods, "_cache_dir": str(self.workspace / "_shared/cache"), "_run_date": now.date().isoformat()}
                    snapshots[-1]["search_keywords"] = search_keywords
                    snapshots[-1]["search_methods"] = search_methods
                    papers, errors = (candidates(request_profile, 36), []) if demo else sources.search(request_profile, limit=min(1000, max(60, remaining * 6)), sources=self.settings["sources"])
                    warnings.extend(topicid + ": " + e for e in errors)
                    raw_ids = []
                    for paper in papers:
                        merged = self.store.upsert_paper(paper)
                        raw_ids.append(merged["paper_id"])
                    seen = set() if demo or preview else self.store.seen(topicid, mode)
                    values = {}
                    for pid in raw_ids:
                        merged = self.store.paper(pid)
                        if merged["paper_id"] not in seen:
                            values[merged["paper_id"]] = score(p, merged, policy, now.date())
                    ranked[topicid] = list(values.values())
                    result["candidate_count"] += len(papers)
                    atomic_json(out / (topicid + ".candidates.json"), ranked[topicid])
                chosen = select(ranked, remaining)
                if remaining == 0:
                    warnings.append("本周真实推荐总预算已用完；没有再次检索或增加文章。")
                elif not chosen:
                    warnings.append("没有达到摘要依据与相关性门槛的文献；本轮没有凑数。检查检索词或稍后重试来源。")
                result["count"] = len(chosen)
                result["status"] = "failed" if remaining and result["candidate_count"] == 0 and warnings else ("partial" if warnings else "complete")
                if preview:
                    warnings.append("真实检索预览：不占周配额，不写入正式推送去重记录；可再次出现已推荐论文。仅供调试检索，不提交评分。")
                result["strategy_changes"] = [{"topic_id": s["profile"]["topic_id"], "rated_count": s["policy"].get("rated_count", 0), "tag_adjustments": s["policy"].get("adjustments", {}), "search_keywords": s.get("search_keywords", s["profile"]["keywords"]), "search_methods": s.get("search_methods", s["profile"]["method_terms"])} for s in snapshots]
                result["profiles"] = [s["profile"] for s in snapshots]
                self.store.save_run(result, week, chosen)
                for s in snapshots:
                    key = str(s["profile"]["profile_version"]) + (":demo" if demo else "")
                    if not preview:
                        self.store.save_policy(s["profile"]["topic_id"], key, s["policy"])
                for p in profiles:
                    dest = self.workspace / p["relative_path"] / "runs" / rid
                    atomic_json(dest / "recommendations.json", [v for v in chosen if v["topic_id"] == p["topic_id"]])
                atomic_json(out / "result.json", result)
                (out / "report.md").write_text(self.export_run(rid, "markdown"), encoding="utf-8")
                (out / "papers.bib").write_text(self.export_run(rid, "bib"), encoding="utf-8")
                return result
            except Exception as exc:
                result["status"] = "failed"
                result["warnings"].append(str(exc))
                self.store.save_run(result, week, [])
                atomic_json(out / "result.json", result)
                raise

    def run_detail(self, run_id):
        result = self.store.run(run_id)
        if not result:
            raise ValueError("批次不存在")
        papers = self.store.run_papers(run_id)
        cache = {}
        for paper in papers:
            tid, version = paper["topic_id"], paper["profile_version"]
            key = (tid, version)
            if key not in cache:
                cache[key] = self.store.latest_feedback(tid, version)
            canonical = (self.store.paper(paper["paper_id"]) or paper)["paper_id"]
            paper["canonical_paper_id"] = canonical
            paper["feedback"] = cache[key].get(canonical, {"rating": None, "reason": "", "read_status": "unread", "note": "", "tags": []})
            paper["other_topics"] = [p["title"] for p in self.store.topics() if p["topic_id"] != tid and canonical in self.store.latest_feedback(p["topic_id"], p["profile_version"])]
        return {**result, "papers": papers}

    def export_run(self, run_id, format="bib"):
        result = self.run_detail(run_id)
        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        if format == "bib":
            escape = lambda x: str(x).replace("\\", "\\textbackslash{} ").replace("{", "\\{").replace("}", "\\}")
            entries = []
            for p in result["papers"]:
                key = p["paper_id"] + "_" + p["topic_id"]
                fields = {"title": p["title"], "author": " and ".join(p.get("authors", [])), "year": p.get("year") or "", "doi": p.get("doi", ""), "url": p.get("url", ""), "note": "SYNTHETIC DEMO - NOT A REAL PAPER" if p.get("is_demo") else ""}
                entries.append("@article{" + key + ",\n" + ",\n".join("  " + k + " = {" + escape(v) + "}" for k, v in fields.items() if v) + "\n}")
            return "\n\n".join(entries) + "\n"
        if format != "markdown":
            raise ValueError("导出格式须为bib、json或markdown")
        lines = ["# 每周文献推荐 " + result["run_id"], "", "模式：" + ("离线演示（全部为合成资料，不能引用）" if result["mode"] == "demo" else "真实学术来源"), "数量：" + str(result["count"]) + "；状态：" + result["status"], "", "## 策略与依据", "", "本报告采用可解释词项匹配与反馈修正。依据为来源摘要；未进行全文精读或新颖性认证。"]
        for s in result.get("strategy_changes", []):
            lines.append("- " + s["topic_id"] + "：有效评分 " + str(s["rated_count"]) + "；标签修正 " + json.dumps({k: v for k, v in s["tag_adjustments"].items() if v}, ensure_ascii=False))
            if s.get("search_keywords"):
                lines.append("  检索词优先次序：" + "、".join(s["search_keywords"]) + "；方法词：" + "、".join(s.get("search_methods", [])))
        for warning in result["warnings"]:
            lines.append("- 运行说明：" + warning)
        for i, p in enumerate(result["papers"], 1):
            lines += ["", "## " + str(i) + ". " + p["title"], "", "方向：" + p["topic_title"] + "；标识：" + p["paper_id"], "来源：" + p.get("source", "") + "；发表日期：" + (p.get("published") or "精确日期待核验") + "；阅读依据：摘要", "基础分：" + str(p["base_score"]) + "；偏好修正：" + str(p["adjustment"]) + "；排序分：" + str(p["score"]), "推荐理由：" + p["reason"], "链接：" + p.get("url", ""), "", "### 原始摘要", "", p.get("abstract", ""), "", "本人评分：" + str(p["feedback"].get("rating")) + "；原因：" + p["feedback"].get("reason", "")]
        gap_topics = [p for p in result.get("profiles", []) if p["stage"] == "gap"]
        if gap_topics:
            lines += ["", "## 本人候选问题与证据核查入口", "", "以下问题来自方向配置，均为待核查问题；匹配文献仅提供摘要线索，尚不能据此宣称研究空白。"]
            for topic in gap_topics:
                lines += ["- " + topic["title"] + "：" + q for q in topic["questions"]]
                lines.append("  摘要线索：" + ", ".join(p["paper_id"] for p in result["papers"] if p["topic_id"] == topic["topic_id"]))
        return "\n\n".join(lines) + "\n"

    def backup(self):
        folder = self.workspace / "_shared/backups"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("lite-private-backup-" + self.now().strftime("%Y%m%d-%H%M%S-%f") + ".zip")
        with tempfile.TemporaryDirectory(prefix="lite-backup-") as temp:
            import sqlite3
            database = Path(temp) / "library.sqlite"
            with self.store.connect() as src:
                dest = sqlite3.connect(database)
                src.backup(dest)
                dest.close()
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(database, "_shared/library.sqlite")
                for p in [self.settings_path, self.workspace / "topics.json"]:
                    if p.exists():
                        z.write(p, p.relative_to(self.workspace))
                for topic in self.store.topics():
                    base = self.workspace / topic["relative_path"]
                    for file in base.rglob("*"):
                        if file.is_file() and file.suffix in (".json", ".jsonl", ".md", ".txt"):
                            z.write(file, file.relative_to(self.workspace))
                for file in (self.workspace / "_shared/runs").rglob("*"):
                    if file.is_file():
                        z.write(file, file.relative_to(self.workspace))
        return path

    def schedule_tick(self):
        schedule = self.settings["schedule"]
        if not schedule.get("enabled"):
            return
        now = self.now()
        hh, mm = map(int, schedule["time"].split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0) - dt.timedelta(days=(now.weekday() - schedule["day"]) % 7)
        if now < target:
            target -= dt.timedelta(days=7)
        # Do not backfill the previous week on first activation. Catch up only this week's occurrence.
        if week_key(target) != week_key(now):
            return
        rid = week_key(target) + "-real"
        previous = self.store.run(rid)
        if previous:
            if previous["status"] in ("complete", "partial") or previous.get("attempts", 0) >= 3:
                return
            try:
                elapsed = (now - dt.datetime.fromisoformat(previous.get("last_attempt_at", previous["created_at"]))).total_seconds()
            except (ValueError, KeyError):
                elapsed = 0
            if elapsed < 900:
                return
        self.run(run_id=rid)

    def import_legacy(self, path, topic_id):
        from .migration import import_legacy
        return import_legacy(self, Path(path), topic_id)
