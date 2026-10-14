"""Concept-aware recall, bounded paging and observable source counts."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from lite_recommender import sources


def payload(value):
    return json.dumps(value).encode("utf-8")


def params(url):
    return parse_qs(urlparse(url).query)


class ConceptQueryTests(unittest.TestCase):
    def test_every_variant_keeps_object_and_problem(self):
        groups = [["high entropy alloy", "multi principal element alloy"],
                  ["dislocation mobility", "dislocation velocity", "dislocation kinetics"]]
        queries = sources._queries({"core_concepts": groups, "method_terms": ["molecular dynamics"], "stage": "focus"})
        expanded_groups = sources.normalize_concepts({"core_concepts": groups})
        self.assertLessEqual(len(queries), 6)
        self.assertEqual({query["mode"] for query in queries}, {"relevance", "recent"})
        for query in queries:
            for group in expanded_groups:
                self.assertTrue(any(term in query["terms"] for term in group), query)
        searched = {term for query in queries for term in query["terms"]}
        self.assertIn("dislocation mobility", searched)
        self.assertIn("dislocation velocity", searched)
        self.assertIn("dislocation kinetics", searched)

    def test_method_and_review_are_optional_additions_never_substitutions(self):
        for stage, extra in (("focus", "finite element"), ("survey", "review")):
            queries = sources._queries({"core_concepts": [["tissue"], ["fracture"]],
                                        "method_terms": ["finite element"], "stage": stage})
            self.assertEqual(queries[-1]["terms"], ["tissue", "fracture", extra])
            self.assertTrue(all("tissue" in q["terms"] and "fracture" in q["terms"] for q in queries))

    def test_legacy_third_mobility_term_is_retrieved(self):
        queries = sources._queries({"keywords": ["high entropy alloy", "dislocation", "mobility"],
                                    "method_terms": ["molecular dynamics"], "stage": "focus"})
        for query in queries:
            self.assertTrue(any("dislocation" in term and any(word in term for word in ("mobility", "velocity", "kinetics"))
                                for term in query["terms"]), query)
        self.assertIn("dislocation mobility", queries[0]["terms"])

    def test_legacy_question_supplement_retains_all_core_terms(self):
        queries = sources._queries({"include_terms": ["ecosystem", "carbon"],
                                    "current_questions": ["land use effects"], "stage": "gap"})
        self.assertEqual(queries[2]["terms"], ["ecosystem", "carbon", "land use effects"])

    def test_large_synonym_groups_cannot_cause_request_explosion(self):
        groups = [[f"object {i}" for i in range(30)], [f"problem {i}" for i in range(30)]]
        self.assertEqual(len(sources._queries({"core_concepts": groups})), 6)


class RetrievalPagingTests(unittest.TestCase):
    query = {"terms": ["high entropy alloy", "dislocation mobility"], "mode": "relevance"}

    def test_source_paging_parameters_and_core_terms(self):
        crossref = params(sources._request("crossref", self.query, 22, page=2))
        self.assertEqual(crossref["offset"], ["44"])
        self.assertEqual(crossref["rows"], ["22"])
        self.assertIn("dislocation mobility", crossref["query"][0])
        openalex = params(sources._request("openalex", self.query, 22, page=2))
        self.assertEqual(openalex["page"], ["3"])
        self.assertEqual(openalex["per_page"], ["22"])
        self.assertIn("high entropy alloy", openalex["search"][0])
        arxiv = params(sources._request("arxiv", self.query, 22, page=2))
        self.assertEqual(arxiv["start"], ["44"])
        self.assertEqual(arxiv["max_results"], ["22"])
        self.assertEqual(arxiv["search_query"], ['all:"high entropy alloy" AND all:"dislocation mobility"'])

    def test_recent_order_preserved_on_later_pages(self):
        query = dict(self.query, mode="recent")
        self.assertEqual(params(sources._request("crossref", query, 10, 1))["sort"], ["published"])
        self.assertEqual(params(sources._request("openalex", query, 10, 1))["sort"], ["publication_date:desc"])
        self.assertEqual(params(sources._request("arxiv", query, 10, 1))["sortBy"], ["submittedDate"])

    def test_default_candidate_budget_is_200_without_a_display_quota(self):
        requested = []
        def response(url, **kwargs):
            values = params(url)
            requested.append(int(values["rows"][0]))
            return payload({"message": {"items": []}})
        profile = {"core_concepts": [["tissue"], ["fracture"]]}
        with patch.object(sources, "_http_get", side_effect=response):
            sources.search(profile, sources=["crossref"])
        self.assertEqual(sum(requested), 200)
        self.assertEqual(profile["_search_diagnostics"]["limit"], 200)

    def test_continuation_fetches_different_ranges_and_distinct_cache_entries(self):
        calls = []
        def response(url, **kwargs):
            calls.append(url)
            values = params(url)
            page = int(values["page"][0])
            key = "review" if "review" in values["search"][0] else "ordinary"
            sort = "recent" if "sort" in values else "relevant"
            return payload({"results": [{"doi": f"https://doi.org/10.1234/{page}-{key}-{sort}",
                                          "display_name": f"Paper {page} {key} {sort}"}]})
        with tempfile.TemporaryDirectory() as temporary:
            profile = {"core_concepts": [["tissue"], ["fracture"]], "stage": "survey", "_cache_dir": temporary}
            with patch.object(sources, "_http_get", side_effect=response):
                initial, _ = sources.search(profile, 12, ["openalex"])
                repeated, _ = sources.search(profile, 12, ["openalex"])
                profile["_search_page"] = 1
                continued, _ = sources.search(profile, 12, ["openalex"])
            self.assertEqual(initial, repeated)
            self.assertFalse({p["doi"] for p in initial} & {p["doi"] for p in continued})
            self.assertEqual(len(calls), 6)
            self.assertEqual(len(list(Path(temporary).rglob("*.json"))), 6)
            self.assertEqual(profile["_search_diagnostics"]["page"], 1)

    def test_invalid_pages_and_bounded_basic_paging(self):
        for page in (-1, True, "1"):
            with self.assertRaises(ValueError):
                sources.search({"keywords": ["tissue"], "_search_page": page}, 200)
        with self.assertRaises(ValueError):
            sources._request("crossref", self.query, 200, 51)
        with self.assertRaises(ValueError):
            sources._request("openalex", self.query, 200, 50)
        self.assertEqual(params(sources._request("openalex", self.query, 500))["per_page"], ["200"])


class SourceDiagnosticsTests(unittest.TestCase):
    def test_counts_before_deduplication_and_query_attribution(self):
        record = {"title": ["Tissue fracture"], "DOI": "10.1234/same"}
        profile = {"core_concepts": [["tissue"], ["fracture"]]}
        diagnostic_target = {}
        profile["_search_diagnostics"] = diagnostic_target
        with patch.object(sources, "_http_get", return_value=payload({"message": {"items": [record]}})):
            papers, warnings = sources.search(profile, 200, ["crossref"])
        self.assertEqual(warnings, [])
        self.assertEqual(len(papers), 1)
        self.assertIs(profile["_search_diagnostics"], diagnostic_target)
        self.assertEqual(diagnostic_target["raw_count"], 2)
        self.assertEqual(diagnostic_target["deduplicated_count"], 1)
        self.assertEqual(diagnostic_target["duplicate_count"], 1)
        self.assertEqual(diagnostic_target["source_counts"], {"crossref": 2})
        self.assertEqual(len(diagnostic_target["query_summaries"]), 2)
        self.assertEqual(sum(q["returned"] for q in diagnostic_target["query_summaries"]), 2)

    def test_error_details_cannot_disclose_request_credentials(self):
        profile = {"core_concepts": [["tissue"], ["fracture"]]}
        secret = "https://example.test?api_key=DO-NOT-EXPOSE"
        with patch.object(sources, "_fetch", side_effect=RuntimeError(secret)):
            papers, warnings = sources.search(profile, 200, ["crossref"])
        self.assertEqual(papers, [])
        self.assertNotIn(secret, json.dumps(warnings))
        self.assertNotIn("DO-NOT-EXPOSE", json.dumps(profile["_search_diagnostics"]))
        self.assertEqual(profile["_search_diagnostics"]["query_summaries"][0]["status"], "failed")
        self.assertEqual(profile["_search_diagnostics"]["errors"], warnings)


if __name__ == "__main__":
    unittest.main()
