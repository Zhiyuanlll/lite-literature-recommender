import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lite_recommender.service import Service, week_key
from lite_recommender.storage import Store
from lite_recommender.ranking import compute_policy, score


def profile(tid="topic"):
    return {"topic_id": tid, "title": tid, "group": "通用", "questions": ["How do mechanical robots move?"], "keywords": ["robot", "mechanical", "movement"], "method_terms": ["model", "simulation"], "stage": "focus", "active": True}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lite-core-")
        self.s = Service(Path(self.temp.name) / "中文 空格")
        self.p = self.s.save_topic(profile())

    def tearDown(self):
        self.temp.cleanup()

    def paper(self, idx=0, demo=False):
        return self.s.store.upsert_paper({"title": "Robot mechanical movement model simulation " + str(idx), "doi": "10.1234/robot-" + str(idx), "abstract": "A robot mechanical movement model simulation.", "authors": ["Example"], "published": "2025-01-01", "year": 2025, "source": "fixture", "is_demo": demo})

    def test_feedback_idempotence_and_withdrawal(self):
        for i in range(3):
            p = self.paper(i)
            f = {"topic_id": "topic", "paper_id": p["paper_id"], "rating": 5, "reason": "method_useful", "tags": ["model"], "read_status": "read"}
            self.assertTrue(self.s.feedback(f)["changed"])
            self.assertFalse(self.s.feedback(f)["changed"])
        latest = self.s.store.latest_feedback("topic", "1")
        policy = compute_policy(self.p, latest, {})
        self.assertEqual(policy["adjustments"]["model"], 2)
        self.assertEqual(policy, compute_policy(self.p, latest, policy))
        f["rating"] = None
        self.s.feedback(f)
        updated = compute_policy(self.p, self.s.store.latest_feedback("topic", "1"), policy)
        self.assertEqual(updated["adjustments"]["model"], 0)

    def test_read_status_does_not_override_low_rating(self):
        latest = {}
        for i in range(3):
            latest[str(i)] = {"rating": 1, "read_status": "read", "reason": "method_mismatch", "tags": ["model"]}
        p = compute_policy(self.p, latest, {})
        self.assertLess(p["adjustments"]["model"], 0)

    def test_notes_and_time_do_not_train_interest(self):
        latest = {str(i): {"rating": 1, "reason": "no_time", "note": "no time", "tags": ["model"]} for i in range(3)}
        self.assertEqual(compute_policy(self.p, latest, {})["adjustments"]["model"], 0)

    def test_topic_isolation_and_canonical_identity(self):
        self.s.save_topic(profile("other"))
        p = self.paper()
        again = self.s.store.upsert_paper({"title": p["title"], "doi": "https://doi.org/10.1234/robot-0", "arxiv_id": "2401.12345v2"})
        self.assertEqual(p["paper_id"], again["paper_id"])
        self.s.feedback({"topic_id": "topic", "paper_id": p["paper_id"], "rating": 1})
        self.s.feedback({"topic_id": "other", "paper_id": p["paper_id"], "rating": 5})
        self.assertEqual(self.s.store.latest_feedback("topic", "1")[p["paper_id"]]["rating"], 1)
        self.assertEqual(self.s.store.latest_feedback("other", "1")[p["paper_id"]]["rating"], 5)

    def test_date_unknown_and_future(self):
        p = self.paper()
        unknown = score(self.p, {**p, "published": "", "year": 2026}, {}, dt.date(2026, 10, 14))
        future = score(self.p, {**p, "published": "2026-10-15"}, {}, dt.date(2026, 10, 14))
        self.assertEqual(unknown["date_bucket"], "unknown")
        self.assertFalse(future["eligible"])

    def test_preview_does_not_consume_budget_or_hide_formal_candidates(self):
        fixture = [self.paper(i) for i in range(25)]
        week = week_key(self.s.now())
        with patch("lite_recommender.sources.search", return_value=(fixture, [])):
            preview = self.s.run(run_id="preview", preview=True)
            self.assertEqual(preview["mode"], "preview")
            self.assertEqual(preview["count"], 10)
            self.assertEqual(self.s.store.used_budget(week), 0)
            self.assertEqual(self.s.store.seen("topic", "real"), set())
            self.assertEqual(self.s.store.get_policy("topic", "1"), {"digest": None, "adjustments": {}})
            formal = self.s.run(run_id="formal")
            self.assertEqual(formal["count"], 10)
            again = self.s.run(run_id="preview-again", preview=True)
            self.assertEqual(again["count"], 10)
            self.assertEqual(self.s.store.used_budget(week), 10)
            self.assertEqual(self.s.run(run_id="exhausted")["count"], 0)
            self.assertTrue(self.s.run(run_id="preview", preview=True)["replayed"])
        with self.assertRaises(ValueError):
            self.s.run(demo=True, preview=True)

    def test_each_topic_has_independent_weekly_quota(self):
        fixture = [self.paper(i) for i in range(25)]
        week = week_key(self.s.now())
        with patch("lite_recommender.sources.search", return_value=(fixture, [])) as search:
            self.assertEqual(self.s.run(topics=["topic"], run_id="first-topic")["count"], 10)
            self.s.save_topic(profile("other"))
            second = self.s.run(run_id="new-topic")
            self.assertEqual(second["count"], 10)
            self.assertEqual(second["topic_budgets"], {"other": 10, "topic": 0})
            self.assertEqual(search.call_count, 2)
            self.assertEqual(self.s.store.used_budget(week, "topic"), 10)
            self.assertEqual(self.s.store.used_budget(week, "other"), 10)
            preview = self.s.run(run_id="two-topic-preview", preview=True)
            self.assertEqual(preview["count"], 20)
            self.assertEqual(self.s.store.used_budget(week), 20)
            self.s.save_settings({"budget": 12})
            topup = self.s.run(run_id="topup")
            self.assertEqual(topup["count"], 4)
            self.assertEqual(self.s.store.used_budget(week, "topic"), 12)
            self.assertEqual(self.s.store.used_budget(week, "other"), 12)
            self.s.save_settings({"budget": 5})
            self.assertEqual(self.s.run(run_id="lowered")["count"], 0)

    def test_sparse_topic_does_not_transfer_quota(self):
        self.s.save_topic(profile("other"))
        fixture = [self.paper(i) for i in range(25)]
        def search(profile, **kwargs):
            return (fixture[:2] if profile["topic_id"] == "topic" else fixture, [])
        with patch("lite_recommender.sources.search", side_effect=search):
            result = self.s.run(run_id="sparse")
        papers = self.s.run_detail(result["run_id"])["papers"]
        self.assertEqual(sum(p["topic_id"] == "topic" for p in papers), 2)
        self.assertEqual(sum(p["topic_id"] == "other" for p in papers), 10)

    def test_higher_budget_and_validation(self):
        self.assertEqual(self.s.settings["budget"], 10)
        self.s.save_settings({"budget": 20})
        fixture = [self.paper(i) for i in range(30)]
        with patch("lite_recommender.sources.search", return_value=(fixture, [])):
            self.assertEqual(self.s.run(run_id="twenty")["count"], 20)
        for invalid in [0, -1, 1.5, True, "abc"]:
            with self.assertRaises(ValueError):
                self.s.save_settings({"budget": invalid})

    def test_real_weekly_budget_and_batch_replay(self):
        self.s.save_topic(profile("other"))
        fixture = [{**self.paper(i), "doi": "10.1234/real-" + str(i)} for i in range(25)]
        with patch("lite_recommender.sources.search", return_value=(fixture, [])):
            first = self.s.run(run_id="first")
            self.assertEqual(first["count"], 20)
            second = self.s.run(run_id="second")
            self.assertEqual(second["count"], 0)
            self.assertTrue(self.s.run(run_id="first")["replayed"])
        self.assertEqual(self.s.store.used_budget(week_key(self.s.now())), 20)

    def test_demo_isolated_and_exports_backup(self):
        result = self.s.run(demo=True, run_id="demotest")
        self.assertLessEqual(result["count"], 10)
        self.assertGreater(result["count"], 0)
        self.assertEqual(self.s.store.used_budget(week_key(self.s.now())), 0)
        for p in self.s.run_detail("demotest")["papers"]:
            self.assertTrue(p["is_demo"])
        self.assertIn("SYNTHETIC", self.s.export_run("demotest", "bib"))
        self.assertTrue(self.s.backup().is_file())

    def test_profile_version_changes_on_research_change(self):
        p = self.s.save_topic({**self.p, "questions": ["Another research question"]})
        self.assertEqual(p["profile_version"], "2")
        self.assertEqual(self.s.store.latest_feedback("topic", "2"), {})

    def test_windows_reserved_identifiers_are_rejected_on_all_platforms(self):
        for invalid in ["CON", "nul", "COM1", "aux.topic", "topic.", "LPT²"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.s.save_topic({**profile(), "topic_id": invalid})
            with self.subTest(group=invalid), self.assertRaises(ValueError):
                self.s.save_topic({**profile(), "group": invalid})

    def test_path_traversal_and_validation(self):
        with self.assertRaises(ValueError):
            self.s.save_topic({**profile(), "group": "../outside"})
        with self.assertRaises(ValueError):
            self.s.save_settings({"budget": 0})
        p = self.paper()
        with self.assertRaises(ValueError):
            self.s.feedback({"topic_id": "topic", "paper_id": p["paper_id"], "rating": 6})

    def test_legacy_import_is_repeatable_and_does_not_trust_weights(self):
        legacy = Path(self.temp.name) / "legacy"
        (legacy / "20261012").mkdir(parents=True)
        p = {"title": "Robot mechanical movement model", "doi": "10.1234/legacy", "abstract": "robot mechanical model"}
        (legacy / "20261012/paper_candidates.json").write_text(json.dumps([p]))
        record = {"date": "20261012", "paper_id": "TOP1", "title": p["title"], "rating": 1, "status": "read", "note": "Less relevant"}
        (legacy / "feedback_history.json").write_text(json.dumps({"feedback": [record]}))
        (legacy / "research_interest_model.json").write_text(json.dumps({"topics": {"model": 1.0}}))
        first = self.s.import_legacy(legacy, "topic")
        second = self.s.import_legacy(legacy, "topic")
        self.assertEqual(first["feedback_imported"], 1)
        self.assertEqual(second["feedback_imported"], 0)
        policy = self.s.store.get_policy("topic", "1")
        self.assertEqual(policy["adjustments"], {})

    def test_process_lock_rejects_parallel_recommendation(self):
        from lite_recommender.storage import FileLock
        with FileLock(self.s.workspace / "_shared/run.lock"):
            with self.assertRaises(RuntimeError):
                self.s.run(demo=True)

    def test_feedback_changes_retrieval_priority_without_changing_topic_anchor(self):
        papers = []
        for i in range(3):
            p = self.paper(i)
            papers.append(p)
            self.s.feedback({"topic_id": "topic", "paper_id": p["paper_id"], "rating": 5, "reason": "question_relevant", "tags": ["movement"]})
        with patch("lite_recommender.sources.search", return_value=(papers, [])) as retrieval:
            result = self.s.run(run_id="query-adaptation")
        used = retrieval.call_args.args[0]
        self.assertEqual(used["keywords"], ["robot", "movement", "mechanical"])
        self.assertEqual(self.p["keywords"], ["robot", "mechanical", "movement"])
        self.assertIn("检索词优先次序", self.s.export_run(result["run_id"], "markdown"))


if __name__ == "__main__":
    unittest.main()
