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
from .access import configuration
from .storage import Store, FileLock, atomic_json, dump, digest, normalized
from .ranking import score, select, compute_policy, REASONS

DEFAULT_SETTINGS = {"recommendation_counts": None, "candidate_limit": 200, "semantic": {"enabled": False, "model_dir": "_shared/models/multilingual-e5-small", "weight": 0.25}, "publisher_access": {"enabled": False}, "budget": 10, "sources": ["crossref", "openalex", "arxiv"], "schedule": {"enabled": False, "day": 0, "time": "09:00", "timezone": "Asia/Shanghai"}}


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
        from .concepts import normalize_concepts
        if 'core_concepts' in payload:
            raw = payload['core_concepts']
            if not isinstance(raw, list) or len(raw) > 12 or any(not isinstance(group,list) or not group or len(group)>60 or any(not isinstance(term,str) or not term.strip() or len(term)>200 for term in group) for group in raw):
                raise ValueError('核心概念须为最多12组词语列表；组内可列同义或关联检索表达')
            normalized_groups = normalize_concepts({'core_concepts':raw})
            if existing and not existing.get('core_concepts') and normalized_groups == normalize_concepts(existing):
                profile.pop('core_concepts',None)
            elif not raw:
                profile.pop('core_concepts',None)
            else:
                profile['core_concepts'] = normalized_groups
        significant = ("questions", "keywords", "method_terms", "exclude_terms", "stage", "core_concepts")
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
        access = {**DEFAULT_SETTINGS["publisher_access"], **self.settings.get("publisher_access", {}), **payload.get("publisher_access", {})}
        if not isinstance(access.get("enabled"), bool):
            raise ValueError("出版商接口开关须为布尔值")
        access.pop("library_url", None)
        access.pop("external_doi_base", None)
        settings["publisher_access"] = access
        counts = settings.get('recommendation_counts')
        if counts is not None:
            if not isinstance(counts,dict) or any(isinstance(counts.get(k),bool) or not isinstance(counts.get(k),int) or not 0 <= counts[k] <= 100 for k in ('core','related')) or counts['core']+counts['related'] < 1:
                raise ValueError('核心和相关候选数量须为0–100整数，合计至少1篇')
            settings['recommendation_counts'] = {k:counts[k] for k in ('core','related')}
        limit = settings.get('candidate_limit',200)
        if isinstance(limit,bool) or not re.fullmatch(r'[0-9]+',str(limit)) or not 30 <= int(limit) <= 1000:
            raise ValueError('每方向候选抓取量须为30–1000整数')
        settings['candidate_limit'] = int(limit)
        supplied_semantic = payload.get('semantic', {})
        if not isinstance(supplied_semantic,dict):
            raise ValueError('语义设置须为对象')
        semantic = {**DEFAULT_SETTINGS['semantic'], **self.settings.get('semantic',{}), **supplied_semantic}
        if not isinstance(semantic.get('enabled'),bool) or isinstance(semantic.get('weight'),bool) or not isinstance(semantic.get('weight'),(int,float)) or not 0 <= semantic['weight'] <= .5:
            raise ValueError('语义开关须为布尔值、权重须为0–0.5数值')
        path = Path(str(semantic.get('model_dir') or DEFAULT_SETTINGS['semantic']['model_dir']))
        models = (self.workspace / '_shared/models').resolve()
        target = (self.workspace / path).resolve()
        if target == models or not target.is_relative_to(models) or not target.is_relative_to(self.workspace):
            raise ValueError('模型目录须位于工作区的 _shared/models 下')
        semantic['model_dir'] = str(target.relative_to(self.workspace))
        settings['semantic'] = semantic
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
        if payload.get('run_id'):
            batch=self.store.run(identifier(payload['run_id']))
            if not batch:raise ValueError('反馈批次不存在')
            if batch['mode']=='preview':raise ValueError('下周预览不可评分，请使用可评分的推荐清单')
            if not any(p['topic_id']==payload.get('topic_id') and ((self.store.paper(p['paper_id']) or p)['paper_id']==(self.store.paper(payload.get('paper_id')) or {}).get('paper_id')) for p in self.store.run_papers(batch['run_id'])):
                raise ValueError('论文不在该推荐清单中')
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
        from .concepts import normalize_concepts
        known = set(p['keywords'] + p['method_terms'] + [term for group in normalize_concepts(p) for term in group])
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
        week = week_key(self.now())
        stats["used_this_week"] = self.store.used_budget(week)
        stats["used_by_topic"] = {p["topic_id"]: self.store.used_budget(week, p["topic_id"]) for p in self.store.topics()}
        weekly_counts=self.store.weekly_counts(week)
        if weekly_counts is not None:
            stats['used_by_topic']={p['topic_id']:weekly_counts.get(p['topic_id'],0) for p in self.store.topics()}
            stats['used_this_week']=sum(weekly_counts.values())
        from .concepts import normalize_concepts
        topics = [{**p, 'core_concepts':normalize_concepts(p), 'core_concepts_inferred':not bool(p.get('core_concepts'))} for p in self.store.topics()]
        return {"topics": topics, "settings": self.settings, "runs": self.store.display_runs(), "iteration_count": self.store.iteration_count(), "current_feedback_run_id":(self.store.latest_feedback_run() or {}).get("run_id"), "stats": stats, "version": __version__, "workspace": str(self.workspace), "access_status": configuration(), "semantic_status": self.semantic_status()}

    def run(self, topics=None, demo=False, run_id=None, preview=False, search_page=0, parent_run_id=None, iteration=False, action=None, base_run_id=None):
        from . import sources
        from .demo import candidates
        from .semantic import rerank
        now = self.now()
        week = week_key(now)
        if action not in (None,'weekly','preview_next','reselect','accelerate'):
            raise ValueError('推荐操作无效')
        if action and demo:raise ValueError('每周推荐操作不能使用演示数据')
        if action:
            preview=action=='preview_next';iteration=action=='accelerate'
            if parent_run_id:raise ValueError('新推荐操作由程序自动扩展检索，不使用手动分页')
        if not isinstance(iteration, bool):
            raise ValueError("iteration 必须是布尔值")
        if iteration and (demo or preview):
            raise ValueError("测试迭代不能同时启用演示或预览")
        if demo and preview:
            raise ValueError("离线演示和真实预览不能同时启用")
        if isinstance(search_page, bool) or not isinstance(search_page, int) or not 0 <= search_page <= 100:
            raise ValueError("检索页须为0–100整数")
        parent = self.store.run(identifier(parent_run_id)) if parent_run_id else None
        if parent and parent.get('status') not in ('complete','partial'):
            raise ValueError('上一批次尚未完成，不能继续分页')
        if parent_run_id and (not parent or not preview or demo or parent.get('mode') == 'demo' or 'candidate_limit' not in parent):
            raise ValueError("继续检索须从新版真实批次启动预览")
        if parent and search_page != parent.get('next_search_page', parent.get('search_page', 0) + 1):
            raise ValueError("继续检索的页码须为上一批次下一页")
        mode = "demo" if demo else ("preview" if preview else ("iteration" if iteration else ("reselect" if action=="reselect" else "real")))
        rid = identifier(run_id or week + "-" + mode)
        with FileLock(self.workspace / "_shared/run.lock"):
            old = self.store.run(rid)
            if old and old["mode"] != mode:
                raise ValueError("批次标识已被另一运行模式使用")
            if old and old["status"] in ("complete", "partial"):
                return {**old, "replayed": True}
            profiles = parent['profiles'] if parent else [p for p in self.store.topics() if (not topics or p["topic_id"] in topics) and (demo or (preview and not action) or p.get("active"))]
            profiles.sort(key=lambda p: (-p.get("priority", 1), p["topic_id"]))
            if not profiles:
                raise ValueError("请先新增并启用一个研究方向。演示可使用未启用的示例方向。")
            for p in profiles:
                if not p["questions"] or not p["keywords"]:
                    raise ValueError("请先填写研究问题和英文关键词：" + p["topic_id"])
            if action=='accelerate':
                base=self.store.run(identifier(base_run_id)) if base_run_id else self.store.latest_feedback_run()
                if not base or base['mode'] in ('preview','demo') or base['status'] not in ('complete','partial'):
                    raise ValueError('请先生成本周推荐并完成评分，再加速迭代')
                base_papers=[v for v in self.store.run_papers(base['run_id']) if v['topic_id'] in {p['topic_id'] for p in profiles}]
                if not base_papers:raise ValueError('当前清单没有所选方向，请先重选本周推荐')
                missing=[]
                for item in base_papers:
                    p=next(p for p in profiles if p['topic_id']==item['topic_id'])
                    if str(item['profile_version'])!=str(p['profile_version']):raise ValueError('方向配置已变更，请先重选本周推荐')
                    pid=(self.store.paper(item['paper_id']) or item)['paper_id']
                    feedback=self.store.latest_feedback(item['topic_id'],item['profile_version']).get(pid,{})
                    if feedback.get('rating') not in (1,2,3,4,5):missing.append(item)
                if missing:raise ValueError(f'还有{len(missing)}篇未评分，请保存全部反馈后再加速迭代')
                with self.store.connect() as conn:
                    prior=[json.loads(r[0]) for r in conn.execute("SELECT result FROM runs WHERE mode='iteration' AND status IN ('complete','partial')")]
                if any(r.get('action')=='accelerate' and r.get('base_run_id')==base['run_id'] for r in prior):
                    raise ValueError('该清单已完成加速迭代，请评分新清单后继续')
                base_run_id=base['run_id']
            limit = (parent or old or {}).get('candidate_limit', self.settings['candidate_limit'])
            active_sources = (parent or old or {}).get('sources', self.settings['sources'])
            counts = (parent or old or {}).get('recommendation_counts', self.settings.get('recommendation_counts'))
            if action:counts=counts or {'core':5,'related':15}
            topic_budgets = {p["topic_id"]: (counts["core"]+counts["related"]) if counts else self.settings["budget"] if demo or preview or iteration else max(0, self.settings["budget"] - self.store.used_budget(week, p["topic_id"])) for p in profiles}
            remaining = sum(topic_budgets.values())
            out = self.workspace / "_shared/runs" / rid
            out.mkdir(parents=True, exist_ok=True)
            result = {"run_id": rid, "id": rid, "mode": mode, "status": "running", "created_at": now.isoformat(), "last_attempt_at": now.isoformat(), "attempts": (old or {}).get("attempts", 0) + 1, "count": 0, "topics": [p["topic_id"] for p in profiles], "profiles": profiles, "warnings": [], "report_path": str(out / "report.md"), "candidate_count": 0, "budget": remaining, "budget_scope": "per_topic", "topic_budgets": topic_budgets, 'candidate_limit': None if counts else limit, 'sources': active_sources, 'search_page': search_page, 'parent_run_id': parent_run_id, 'diagnostics': {}, 'scoring_version': '2.1', 'recommendation_counts': counts,'action':action,'base_run_id':base_run_id,'week':week,'target_week':week_key(now+dt.timedelta(days=7)) if action=='preview_next' else week}
            self.store.save_run(result, week)
            try:
                warnings, ranked, snapshots = result["warnings"], {}, []
                for p in profiles:
                    topicid = p["topic_id"]
                    topic_remaining = topic_budgets[topicid]
                    feedback = self.store.latest_feedback(topicid, p["profile_version"])
                    feedback = {pid: value for pid, value in feedback.items() if bool((self.store.paper(pid) or {}).get("is_demo")) == demo}
                    policy_key = str(p["profile_version"]) + (":demo" if demo else "")
                    previous_policy=self.store.get_policy(topicid,policy_key)
                    policy = previous_policy if action=='reselect' and previous_policy.get('digest') else compute_policy(p, feedback, previous_policy)
                    frozen_path = out / (topicid + ".snapshot.json")
                    snapshot = {'profile': p, 'feedback': feedback, 'policy': policy}
                    if old and frozen_path.exists():
                        snapshot = json.loads(frozen_path.read_text(encoding='utf-8'))
                        p, feedback, policy = snapshot['profile'], snapshot['feedback'], snapshot['policy']
                    snapshots.append(snapshot)
                    if topic_remaining == 0:
                        warnings.append(topicid + "：本周正式推荐配额已用完；该方向没有再次检索。")
                        ranked[topicid] = []
                        result['diagnostics'][topicid] = {'raw_count': 0, 'deduped_count': 0, 'seen_count': 0, 'pending_count': 0, 'core_count': 0, 'exploration_count': 0, 'rejected_count': 0, 'selected_count': 0, 'budget_remaining': 0}
                        continue
                    adjustments = policy.get('adjustments', {})
                    search_keywords = p['keywords'][:1] + sorted(p['keywords'][1:], key=lambda word: -adjustments.get(word, 0))
                    search_methods = sorted(p['method_terms'], key=lambda word: -adjustments.get(word, 0))
                    query_profile = {**p, 'keywords': search_keywords, 'method_terms': search_methods}
                    if parent:
                        parent_path = self.workspace / '_shared/runs' / parent['run_id'] / (topicid + '.snapshot.json')
                        parent_snapshot = json.loads(parent_path.read_text(encoding='utf-8'))
                        query_profile = parent_snapshot.get('request_profile', query_profile)
                    if old and snapshot.get('request_profile'):
                        query_profile = snapshot['request_profile']
                    query_profile = {k:v for k,v in query_profile.items() if not k.startswith('_')}
                    snapshot.update(request_profile=query_profile, search_keywords=query_profile['keywords'], search_methods=query_profile['method_terms'])
                    atomic_json(frozen_path, snapshot)
                    request_profile = {**query_profile, '_cache_dir': str(self.workspace / '_shared/cache'), '_run_date': now.date().isoformat(), '_search_page': search_page, '_search_diagnostics': {}}
                    exposures=self.store.exposure_papers(topicid) if action else []
                    request_profile.update(_exclude_papers=exposures,_refresh_metadata=action=='reselect')
                    if counts and not demo:
                        from .discovery import search as discover
                        papers, errors = discover(request_profile,counts,policy,now.date(),active_sources,self.workspace/'_shared/homepage_cache')
                    else:
                        papers, errors = (candidates(request_profile, 36), []) if demo else sources.search(request_profile, limit=limit, sources=active_sources)
                    if not demo:
                        from .access import enrich
                        papers, enrichment_errors = enrich(papers, self.settings['publisher_access']['enabled'])
                        errors.extend(enrichment_errors)
                    warnings.extend(topicid + ': ' + e for e in errors)
                    raw_ids = [self.store.upsert_paper(paper)['paper_id'] for paper in papers]
                    historical = {p['paper_id'] for p in exposures} if action else self.store.seen(topicid, 'real') if not demo else set()
                    values = {}
                    for pid in raw_ids:
                        merged = self.store.paper(pid)
                        values[merged['paper_id']] = score(p, merged, policy, now.date())
                    scored = list(values.values())
                    if not demo:
                        scored, semantic_warnings, semantic_info = rerank(p, scored, self.semantic_settings(), self.workspace / '_shared/semantic_cache', feedback=feedback, rated_papers={pid:self.store.paper(pid) for pid in feedback})
                        warnings.extend(topicid + ': ' + e for e in semantic_warnings)
                    else:
                        semantic_info = {'enabled': False, 'mode': 'demo'}
                    for item in scored:
                        item['previously_recommended'] = item['paper_id'] in historical
                        item['seen'] = bool(item['previously_recommended'] and (action or (not preview and not demo and not iteration)))
                        if item['seen']:
                            item.update(eligible=False, exploration_eligible=False)
                            item['rejection_reasons'] = list(item.get('rejection_reasons', [])) + ['already_recommended']
                    available = [p for p in scored if not p['seen']]
                    ranked[topicid] = available
                    category_counts = {name: sum(p.get('classification') == name for p in available) for name in ('core', 'exploration', 'pending', 'rejected')}
                    source_info = request_profile['_search_diagnostics']
                    diagnostics = {**source_info, 'raw_count': source_info.get('raw_count', len(papers)), 'deduped_count': len(scored), 'seen_count': sum(p['seen'] for p in scored), 'previously_recommended_count': sum(p['previously_recommended'] for p in scored), **{name+'_count':value for name,value in category_counts.items()}, 'selected_count': 0, 'budget_remaining': topic_remaining, 'semantic': semantic_info}
                    result['diagnostics'][topicid] = diagnostics
                    result['candidate_count'] += len(papers)
                    atomic_json(out / (topicid + '.candidates.json'), scored)
                if counts:
                    from .ranking import select_targets
                    chosen = [paper for values in ranked.values() for paper in select_targets(values,counts['core'],counts['related'])]
                else:
                    chosen = [paper for topicid, values in ranked.items() for paper in select({topicid: values}, topic_budgets[topicid])]
                for topicid in ranked:
                    result['diagnostics'][topicid]['selected_count'] = sum(p['topic_id'] == topicid for p in chosen)
                    if counts:
                        result['diagnostics'][topicid].update(selected_core_count=sum(p['topic_id']==topicid and p['recommendation_group']=='core' for p in chosen),selected_related_count=sum(p['topic_id']==topicid and p['recommendation_group']=='related' for p in chosen),selected_title_only_count=sum(p['topic_id']==topicid and not p.get('abstract') for p in chosen))
                for topicid in ranked:
                    d = result['diagnostics'][topicid]
                    if d['selected_count'] < topic_budgets[topicid] and topic_budgets[topicid]:
                        if counts:
                            selected = [p for p in chosen if p['topic_id']==topicid]
                            ncore = sum(p['recommendation_group']=='core' for p in selected)
                            nrelated = len(selected)-ncore
                            warnings.append(f"{topicid}：核心目标{counts['core']}篇、相关目标{counts['related']}篇；实际核心{ncore}篇、相关{nrelated}篇。已扩大检索；标题相关但无摘要条目也可推送，未以无摘要直接剔除。可继续检索补充。")
                        else:
                            warnings.append(f"{topicid}：上限{topic_budgets[topicid]}篇；本轮核心候选{d['core_count']}篇、探索候选{d['exploration_count']}篇（每轮最多展示1篇探索），待核验{d['pending_count']}篇、筛除{d['rejected_count']}篇、已推荐去重{d['seen_count']}篇；实际展示{d['selected_count']}篇。上限不保证篇数，可查看候选或继续检索。")
                if iteration:
                    warnings.append('评分后加速迭代：使用已保存评分更新偏好，提供未展示过的新清单；新论文可评分并参与后续每周推荐。' if action else '手动测试迭代：使用最新评分重新排序，可重复出现已推荐论文，不扣正式周配额；只更新当前结果，不新增每周历史。')
                if remaining == 0:
                    warnings.append('所选方向本周正式推荐配额均已用完；没有再次检索或增加文章。')
                elif not chosen:
                    warnings.append('本轮没有可正式推荐的文献；请查看候选与排除原因，缺少摘要的相关条目保留在待核验区。')
                if counts:
                    result['next_search_page'] = max((d.get('next_search_page',search_page+1) for d in result['diagnostics'].values()),default=search_page+1)
                result['count'] = len(chosen)
                result['status'] = 'failed' if remaining and result['candidate_count'] == 0 and warnings else ('partial' if warnings else 'complete')
                if preview:
                    warnings.append('下周预览：按最新已保存反馈提供未展示过的文献，不可评分；使用当前已发表文献，不模拟未来发表。' if action else '真实检索预览：不占周配额，不写入正式推送去重记录；可再次出现已推荐论文。仅供调试检索，不提交评分。')
                result['strategy_changes'] = [{'topic_id':s['profile']['topic_id'], 'rated_count':s['policy'].get('rated_count',0), 'tag_adjustments':s['policy'].get('adjustments',{}), 'search_keywords':s.get('search_keywords',s['profile']['keywords']), 'search_methods':s.get('search_methods',s['profile']['method_terms'])} for s in snapshots]
                result['profiles'] = [s['profile'] for s in snapshots]
                self.store.save_run(result, week, chosen)
                for s in snapshots:
                    key = str(s['profile']['profile_version']) + (':demo' if demo else '')
                    if not preview:
                        self.store.save_policy(s['profile']['topic_id'],key,s['policy'])
                for p in profiles:
                    dest = self.workspace / p['relative_path'] / 'runs' / rid
                    atomic_json(dest / 'recommendations.json',[v for v in chosen if v['topic_id'] == p['topic_id']])
                atomic_json(out / 'result.json',result)
                (out / 'report.md').write_text(self.export_run(rid,'markdown'),encoding='utf-8')
                (out / 'papers.bib').write_text(self.export_run(rid,'bib'),encoding='utf-8')
                return result
            except Exception as exc:
                result['status'] = 'failed'
                result['warnings'].append(str(exc))
                self.store.save_run(result,week,[])
                atomic_json(out / 'result.json',result)
                raise

    def candidate_detail(self, run_id):
        result = self.store.run(identifier(run_id))
        if not result:
            raise ValueError('批次不存在')
        candidates = []
        for topicid in result.get('topics', []):
            path = self.workspace / '_shared/runs' / identifier(run_id) / (identifier(topicid) + '.candidates.json')
            if path.is_file() and not path.is_symlink():
                for paper in json.loads(path.read_text(encoding='utf-8')):
                    canonical = (self.store.paper(paper['paper_id']) or paper)['paper_id']
                    paper['canonical_paper_id'] = canonical
                    candidates.append(paper)
        return {'run_id':run_id, 'candidates':candidates, 'diagnostics':result.get('diagnostics',{})}

    def semantic_status(self):
        from .semantic import status
        return status(self.semantic_settings())

    def semantic_settings(self):
        settings = dict(self.settings['semantic'])
        settings['model_dir'] = str((self.workspace / settings['model_dir']).resolve())
        return settings

    def setup_semantic(self, model_dir=None):
        from .semantic import setup_model
        configured = dict(self.settings['semantic'])
        if model_dir:
            configured['model_dir'] = model_dir
        validated = self._validate_settings({'semantic':configured})['semantic']
        target = (self.workspace / validated['model_dir']).resolve()
        result = setup_model(target)
        self.save_settings({'semantic':validated})
        return {**result, 'semantic_setup':True, 'message':'本地语义模型已准备；在设置中启用语义增强即可使用。'}

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
        lines = ["# 每周文献推荐 " + result["run_id"], "", "模式：" + ("离线演示（全部为合成资料，不能引用）" if result["mode"] == "demo" else "真实学术来源"), "数量：" + str(result["count"]) + "；状态：" + result["status"], "", "## 策略与依据", "", "本报告采用词项匹配、本地语义排序与反馈修正。缺摘要条目依据限于标题和元数据；未进行全文精读或新颖性认证。"]
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
        with self.store.connect() as conn:
            rows=conn.execute("SELECT result FROM runs WHERE week=? AND mode='reselect' AND status IN ('complete','partial')",(week_key(target),))
            if any(json.loads(r[0]).get('action')=='reselect' for r in rows):return
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
        self.run(run_id=rid,action="weekly")

    def import_legacy(self, path, topic_id):
        from .migration import import_legacy
        return import_legacy(self, Path(path), topic_id)
