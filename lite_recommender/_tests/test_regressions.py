"""Regression coverage for identity, feedback versions and failed schedules.

All metadata is a fixture; requests are mocked and private files live only in
temporary test workspaces.
"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lite_recommender.ranking import compute_policy
from lite_recommender.service import Service, week_key


def profile(topic_id="regression"):
    return {
        "topic_id": topic_id, "title": "Regression direction", "group": "通用",
        "questions": ["Old question: how does mechanical robot movement work?"],
        "keywords": ["robot", "mechanical", "movement"],
        "method_terms": ["model", "simulation"], "stage": "gap", "active": True,
    }


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lite-regressions-")
        self.workspace = Path(self.temporary.name) / "中文 空格"
        self.service = Service(self.workspace)
        self.profile = self.service.save_topic(profile())

    def tearDown(self):
        self.temporary.cleanup()

    def test_canonical_identity_bridges_existing_doi_and_arxiv_records(self):
        store = self.service.store
        preprint = store.upsert_paper({
            "title": "Robot preprint title", "authors": ["Ada Smith"],
            "arxiv_id": "2401.12345v1", "source": "arxiv", "is_demo": False,
        })
        journal = store.upsert_paper({
            "title": "Robot journal title", "authors": ["Ada Smith"],
            "doi": "10.1234/journal-identity", "source": "crossref", "is_demo": False,
        })
        self.assertNotEqual(preprint["paper_id"], journal["paper_id"])
        self.service.feedback({"topic_id": self.profile["topic_id"], "paper_id": preprint["paper_id"],
                               "rating": 2, "reason": "method_mismatch", "tags": ["model"]})
        self.service.feedback({"topic_id": self.profile["topic_id"], "paper_id": journal["paper_id"],
                               "rating": 5, "reason": "method_useful", "tags": ["model"]})
        combined = store.upsert_paper({
            "title": "Robot journal title", "authors": ["Ada Smith"],
            "doi": "https://doi.org/10.1234/journal-identity", "arxiv_id": "2401.12345v3",
            "source": "crossref", "is_demo": False,
        })
        canonical_id = combined["paper_id"]
        self.assertIn(canonical_id, (preprint["paper_id"], journal["paper_id"]))
        for old_id in (preprint["paper_id"], journal["paper_id"]):
            self.assertEqual(store.paper(old_id)["paper_id"], canonical_id)
        later_arxiv = store.upsert_paper({"title": "Robot preprint title", "arxiv_id": "2401.12345v4"})
        later_doi = store.upsert_paper({"title": "Another metadata spelling", "doi": "10.1234/journal-identity"})
        self.assertEqual(later_arxiv["paper_id"], canonical_id)
        self.assertEqual(later_doi["paper_id"], canonical_id)
        feedback = store.latest_feedback(self.profile["topic_id"], "1")
        self.assertEqual(set(feedback), {canonical_id})
        self.assertEqual(feedback[canonical_id]["rating"], 5)

    def test_demo_all_strong_aliases_are_separate_from_real_aliases(self):
        store = self.service.store
        metadata = {"title": "Same title", "authors": ["Same Author"],
                    "doi": "10.1234/isolation", "arxiv_id": "2402.12345", "source": "fixture"}
        real = store.upsert_paper({**metadata, "is_demo": False})
        synthetic = store.upsert_paper({**metadata, "is_demo": True})
        self.assertNotEqual(real["paper_id"], synthetic["paper_id"])
        self.assertFalse(store.paper(real["paper_id"])["is_demo"])
        self.assertTrue(store.paper(synthetic["paper_id"])["is_demo"])
        for flag, identity in ((False, real["paper_id"]), (True, synthetic["paper_id"])):
            for strong_id in ({"doi": metadata["doi"]}, {"arxiv_id": metadata["arxiv_id"]}):
                again = store.upsert_paper({"title": "Changed title", **strong_id, "is_demo": flag})
                self.assertEqual(again["paper_id"], identity)

    def test_explicit_versions_bridge_preprint_and_journal_dois(self):
        store = self.service.store
        preprint_version = {"source": "crossref", "doi": "10.1234/deposited-preprint",
                            "arxiv_id": "", "url": "https://doi.org/10.1234/deposited-preprint"}
        journal_version = {"source": "crossref", "doi": "10.1234/published-version",
                           "arxiv_id": "", "url": "https://doi.org/10.1234/published-version"}
        preprint = store.upsert_paper({
            "title": "Original preprint title", "authors": ["Ada Smith"],
            "doi": preprint_version["doi"], "source": "crossref", "is_demo": False,
            "versions": [preprint_version],
        })
        journal = store.upsert_paper({
            "title": "Revised journal title", "authors": ["Ada Smith"],
            "doi": journal_version["doi"], "source": "crossref", "is_demo": False,
            "versions": [journal_version],
        })
        self.assertNotEqual(preprint["paper_id"], journal["paper_id"])
        self.service.feedback({"topic_id": self.profile["topic_id"], "paper_id": preprint["paper_id"],
                               "rating": 5, "reason": "method_useful", "tags": ["model"]})
        # Different titles and different DOIs leave the explicit version chain
        # as the only available identity bridge; there is no arXiv identifier.
        joined = store.upsert_paper({
            "title": "Revised journal title", "authors": ["Ada Smith"],
            "doi": journal_version["doi"], "source": "crossref", "is_demo": False,
            "versions": [preprint_version, journal_version],
        })
        canonical_id = joined["paper_id"]
        self.assertEqual(joined["doi"], journal_version["doi"])
        self.assertEqual({version["doi"] for version in joined["versions"]},
                         {preprint_version["doi"], journal_version["doi"]})
        self.assertEqual(store.paper(preprint["paper_id"])["paper_id"], canonical_id)
        self.assertEqual(store.paper(journal["paper_id"])["paper_id"], canonical_id)
        later = store.upsert_paper({"title": "Original preprint title", "doi": preprint_version["doi"], "is_demo": False})
        self.assertEqual(later["paper_id"], canonical_id)
        feedback = store.latest_feedback(self.profile["topic_id"], "1")
        self.assertEqual(set(feedback), {canonical_id})
        self.assertEqual(feedback[canonical_id]["rating"], 5)

    def test_identity_union_keeps_published_report_and_weekly_budget_frozen(self):
        common = {
            "authors": ["Ada Smith"], "abstract": "A robot mechanical movement model simulation.",
            "published": "2025-01-01", "year": 2025, "kind": "research", "is_demo": False,
        }
        preprint = {**common, "title": "Robot mechanical movement model simulation preprint",
                    "arxiv_id": "2401.45678", "source": "arxiv"}
        journal = {**common, "title": "Robot mechanical movement model simulation journal version",
                   "doi": "10.1234/frozen-history", "source": "crossref"}
        with patch("lite_recommender.sources.search", return_value=([preprint, journal], [])):
            result = self.service.run(run_id="identity-history")
        self.assertEqual(result["count"], 2)
        week = week_key(self.service.now())
        self.assertEqual(self.service.store.used_budget(week), 2)
        before = self.service.export_run(result["run_id"], "markdown")
        frozen_rows = self.service.store.run_papers(result["run_id"])
        joined = self.service.store.upsert_paper({**journal, "arxiv_id": preprint["arxiv_id"]})
        self.assertEqual(self.service.store.used_budget(week), 2)
        self.assertEqual(len(self.service.store.run_papers(result["run_id"])), 2)
        self.assertEqual(self.service.store.run_papers(result["run_id"]), frozen_rows)
        self.assertEqual(self.service.export_run(result["run_id"], "markdown"), before)
        self.assertIn(joined["paper_id"], self.service.store.seen(self.profile["topic_id"], "real"))

    def test_title_alone_does_not_merge_different_first_authors(self):
        store = self.service.store
        first = store.upsert_paper({"title": "An introduction", "authors": ["Ada Smith"], "source": "fixture"})
        different = store.upsert_paper({"title": "An introduction", "authors": ["Lin Jones"], "source": "fixture"})
        repeated = store.upsert_paper({"title": "An introduction", "authors": ["Ada Smith"], "source": "other-fixture"})
        self.assertNotEqual(first["paper_id"], different["paper_id"])
        self.assertEqual(first["paper_id"], repeated["paper_id"])
        self.assertEqual(store.paper(first["paper_id"])["authors"], ["Ada Smith"])

    def test_old_batch_feedback_uses_frozen_profile_version_and_tags(self):
        self.service.run(demo=True, run_id="old-profile")
        paper = self.service.run_detail("old-profile")["papers"][0]
        self.assertEqual(paper["profile_version"], "1")
        current = self.service.save_topic({**self.profile, "questions": ["A different research question"],
                                           "keywords": ["new direction"], "method_terms": ["new method"]})
        self.assertEqual(current["profile_version"], "2")
        saved = self.service.feedback({
            "topic_id": paper["topic_id"], "paper_id": paper["paper_id"],
            "profile_version": paper["profile_version"], "rating": 5,
            "reason": "method_useful", "tags": ["model"], "read_status": "read",
        })
        self.assertEqual(saved["profile_version"], "1")
        old_feedback = self.service.store.latest_feedback(paper["topic_id"], "1")
        self.assertEqual(old_feedback[paper["paper_id"]]["rating"], 5)
        self.assertEqual(self.service.store.latest_feedback(paper["topic_id"], "2"), {})
        refreshed = next(p for p in self.service.run_detail("old-profile")["papers"] if p["paper_id"] == paper["paper_id"])
        self.assertEqual(refreshed["feedback"]["rating"], 5)
        self.assertFalse(self.service.feedback({
            "topic_id": paper["topic_id"], "paper_id": paper["paper_id"],
            "profile_version": "1", "rating": 5, "reason": "method_useful", "tags": ["model"], "read_status": "read",
        })["changed"])

    def test_unrecognized_profile_version_is_rejected(self):
        paper = self.service.store.upsert_paper({"title": "Robot mechanical movement model", "doi": "10.1234/version-check"})
        with self.assertRaises(ValueError):
            self.service.feedback({"topic_id": self.profile["topic_id"], "paper_id": paper["paper_id"],
                                   "profile_version": "unknown-version", "rating": 5})

    def test_note_and_read_state_changes_do_not_advance_policy(self):
        feedback = {str(i): {"rating": 5, "reason": "method_useful", "tags": ["model"],
                             "read_status": "unread", "note": ""} for i in range(3)}
        original = compute_policy(self.profile, feedback, {})
        self.assertEqual(original["adjustments"]["model"], 2)
        feedback["0"]["note"] = "A note about the reading plan"
        feedback["1"]["read_status"] = "read"
        unchanged = compute_policy(self.profile, feedback, original)
        self.assertEqual(unchanged, original)
        # A rating correction must still produce a new preference state.
        feedback["0"]["rating"] = 1
        changed = compute_policy(self.profile, feedback, unchanged)
        self.assertNotEqual(changed["digest"], original["digest"])
        self.assertLess(changed["adjustments"]["model"], original["adjustments"]["model"])

    def test_tag_order_does_not_advance_policy(self):
        feedback = {str(i): {"rating": 5, "reason": "method_useful", "tags": ["model", "simulation"]} for i in range(10)}
        original = compute_policy(self.profile, feedback, {})
        feedback["0"]["tags"].reverse()
        self.assertEqual(compute_policy(self.profile, feedback, original), original)

    def test_historical_gap_export_uses_frozen_questions_and_stage(self):
        result = self.service.run(demo=True, run_id="frozen-gap")
        before = self.service.export_run(result["run_id"], "markdown")
        self.assertIn(self.profile["questions"][0], before)
        self.service.save_topic({**self.profile, "stage": "focus", "questions": ["New question should belong to the next version"]})
        after = self.service.export_run(result["run_id"], "markdown")
        self.assertEqual(after, before)
        self.assertNotIn("New question should belong", after)

    def test_failed_schedule_retries_after_fifteen_minutes_at_most_three_attempts(self):
        self.service.save_settings({"schedule": {"enabled": True, "day": 0, "time": "09:00"}})
        start = dt.datetime(2026, 10, 14, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        attempts_at = ((0, 1), (14 * 60 + 59, 1), (15 * 60, 2),
                       (29 * 60 + 59, 2), (30 * 60, 3), (45 * 60, 3), (60 * 60, 3))
        with patch("lite_recommender.sources.search", return_value=([], ["fixture HTTP 503"])) as requests:
            for seconds, expected in attempts_at:
                with patch.object(self.service, "now", return_value=start + dt.timedelta(seconds=seconds)):
                    self.service.schedule_tick()
                self.assertEqual(requests.call_count, expected, f"schedule at +{seconds} seconds")
            self.assertEqual(self.service.store.run("2026-W42-real")["status"], "failed")
            following_week = dt.datetime(2026, 10, 19, 9, 0, tzinfo=start.tzinfo)
            with patch.object(self.service, "now", return_value=following_week):
                self.service.schedule_tick()
            self.assertEqual(requests.call_count, 4)
        self.assertEqual(self.service.store.used_budget("2026-W42"), 0)

    def test_invalid_manually_written_budget_is_rejected_on_load(self):
        settings = json.loads((self.workspace / "settings.json").read_text(encoding="utf-8"))
        settings["budget"] = 0
        (self.workspace / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        with self.assertRaises(ValueError):
            Service(self.workspace)


if __name__ == "__main__":
    unittest.main()
