"""Sources use recorded fixtures and mock HTTP; the test suite is offline."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from lite_recommender import demo, sources


def json_bytes(value):
    return json.dumps(value).encode("utf-8")


CROSSREF = json_bytes({"message": {"items": [{
    "DOI": "https://doi.org/10.1234/ABC", "title": ["A mechanism &amp; method"],
    "author": [{"given": "Ada", "family": "Smith"}],
    "abstract": "<jats:p>Evidence <jats:italic>with</jats:italic> &amp; text.</jats:p>",
    "issued": {"date-parts": [[2024, 1, 10]]},
    "published-print": {"date-parts": [[2024, 2, 5]]},
    "published-online": {"date-parts": [[2023, 12, 20]]},
    "created": {"date-parts": [[2026, 10, 14]]},
    "URL": "https://doi.org/10.1234/ABC", "type": "journal-article",
    "container-title": ["Test Journal"],
    "link": [{"URL": "https://publisher.example/test.pdf", "content-type": "application/pdf"}],
}]}})

OPENALEX = json_bytes({"results": [{
    "id": "https://openalex.org/W1234", "doi": "https://doi.org/10.1234/abc",
    "display_name": "A mechanism & method", "publication_date": "2023-12-20",
    "publication_year": 2023, "type": "article",
    "authorships": [{"author": {"display_name": "Ada Smith"}}],
    "abstract_inverted_index": {"Evidence": [0], "with": [1], "text.": [2]},
    "primary_location": {"landing_page_url": "https://publisher.example/test"},
    "best_oa_location": {"landing_page_url": "https://arxiv.org/abs/2308.12345v2", "pdf_url": "https://arxiv.org/pdf/2308.12345"},
}]})

ARXIV = b'''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
 <entry><id>http://arxiv.org/abs/2308.12345v2</id><title>A mechanism &amp; method</title>
 <published>2023-08-25T15:00:00Z</published><updated>2026-10-12T12:00:00Z</updated>
 <summary>Original preprint abstract.</summary><author><name>Ada Smith</name></author>
 <arxiv:doi>10.1234/abc</arxiv:doi>
 <link href="http://arxiv.org/pdf/2308.12345v2" title="pdf" type="application/pdf"/>
 </entry>
</feed>'''


class MetadataNormalizationTests(unittest.TestCase):
    def test_crossref_earliest_publication_not_registration_date(self):
        paper = sources._crossref_items(CROSSREF)[0]
        self.assertEqual(paper["published"], "2023-12-20")
        self.assertEqual(paper["year"], 2023)
        self.assertEqual(paper["doi"], "10.1234/abc")
        self.assertEqual(paper["abstract"], "Evidence with & text.")
        self.assertEqual(paper["authors"], ["Ada Smith"])
        self.assertEqual(paper["kind"], "method")
        self.assertEqual(paper["source"], "crossref")
        self.assertFalse(paper["is_demo"])

    def test_crossref_partial_date_does_not_become_january_first(self):
        record = {"title": ["Year only"], "issued": {"date-parts": [[2024]]},
                  "published-print": {"date-parts": [[2025, 1, 1]]}}
        paper = sources._crossref_items(json_bytes({"message": {"items": [record]}}))[0]
        self.assertEqual(paper["published"], "")
        self.assertEqual(paper["year"], 2024)

    def test_crossref_invalid_day_preserves_known_year(self):
        self.assertEqual(sources._date_parts({"date-parts": [[2024, 2, 30]]}), ("", 2024))
        self.assertEqual(sources._date_parts({"date-parts": [[0]]}), ("", None))

    def test_crossref_empty_title_and_null_author_are_safe(self):
        records = [{"title": []}, {"title": ["Valid"], "author": None, "link": None, "relation": []}]
        papers = sources._crossref_items(json_bytes({"message": {"items": records}}))
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["authors"], [])

    def test_openalex_reconstructs_abstract_and_retains_repository(self):
        paper = sources._openalex_items(OPENALEX)[0]
        self.assertEqual(paper["abstract"], "Evidence with text.")
        self.assertEqual(paper["arxiv_id"], "2308.12345")
        self.assertEqual(paper["url"], "https://doi.org/10.1234/abc")
        self.assertEqual(paper["pdf_url"], "https://arxiv.org/pdf/2308.12345")
        self.assertEqual(len(paper["locations"]), 2)

    def test_arxiv_first_submission_and_update_remain_distinct(self):
        paper = sources._arxiv_items(ARXIV)[0]
        self.assertEqual(paper["published"], "2023-08-25")
        self.assertEqual(paper["updated"], "2026-10-12")
        self.assertEqual(paper["arxiv_id"], "2308.12345")
        self.assertTrue(paper["is_preprint"])
        self.assertTrue(paper["versions"][0]["url"].endswith("v2"))

    def test_arxiv_error_feed_is_not_a_paper(self):
        payload = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#incorrect_id_format</id><title>Error</title></entry></feed>'
        with self.assertRaises(ValueError):
            sources._arxiv_items(payload)

    def test_merge_preserves_all_source_versions_and_journal_date(self):
        papers = sources._arxiv_items(ARXIV) + sources._openalex_items(OPENALEX) + sources._crossref_items(CROSSREF)
        merged = sources.merge_candidates(papers)
        self.assertEqual(len(merged), 1)
        paper = merged[0]
        self.assertEqual(paper["source"], "crossref")
        self.assertEqual(set(paper["sources"]), {"crossref", "openalex", "arxiv"})
        self.assertEqual(len(paper["versions"]), 3)
        self.assertEqual(paper["published"], "2023-12-20")
        self.assertEqual(paper["arxiv_id"], "2308.12345")
        self.assertNotIn("is_preprint", paper)

    def test_same_title_with_distinct_dois_is_not_merged(self):
        paper = sources._crossref_items(CROSSREF)[0]
        other = dict(paper, doi="10.1234/different")
        self.assertEqual(len(sources.merge_candidates([paper, other])), 2)

    def test_title_match_requires_first_author(self):
        paper = sources._crossref_items(CROSSREF)[0]
        other = dict(paper, doi="", authors=["Other Person"])
        self.assertEqual(len(sources.merge_candidates([paper, other])), 2)

    def test_explicit_preprint_relation_merges_distinct_dois(self):
        paper = sources._crossref_items(CROSSREF)[0]
        other = dict(paper, doi="10.1234/preprint", related_dois=["10.1234/abc"])
        self.assertEqual(len(sources.merge_candidates([paper, other])), 1)

    def test_crossref_only_identity_relations_participate_in_merging(self):
        item = {"title": ["A preprint"], "DOI": "10.1234/preprint", "type": "posted-content",
                "relation": {"is-preprint-of": [{"id-type": "doi", "id": "10.1234/journal"}],
                             "references": [{"id-type": "doi", "id": "10.1234/reference"}]}}
        paper = sources._crossref_items(json_bytes({"message": {"items": [item]}}))[0]
        self.assertEqual(paper["related_dois"], ["10.1234/journal"])
        journal = sources._crossref_items(json_bytes({"message": {"items": [{
            "title": ["Journal version"], "DOI": "10.1234/journal", "type": "journal-article",
            "URL": "https://doi.org/10.1234/journal", "issued": {"date-parts": [[2025, 2, 3]]},
        }]}}))[0]
        merged = sources.merge_candidates([paper, journal])[0]
        self.assertEqual(merged["doi"], "10.1234/journal")
        self.assertEqual(merged["published"], "2025-02-03")
        self.assertEqual(len(merged["versions"]), 2)

    def test_malformed_response_shape_is_a_parse_error(self):
        for payload in (b'[]', b'{"message":null}', b'{"message":{"items":null}}'):
            with self.assertRaises(ValueError):
                sources._crossref_items(payload)
        with self.assertRaises(ValueError):
            sources._openalex_items(b'[]')


class SearchAndTransportTests(unittest.TestCase):
    profile = {"topic_id": "soft-robotics", "title": "Soft robotics", "keywords": ["soft robotics", "embodied intelligence"],
               "method_terms": ["finite element method"], "stage": "review"}

    def test_search_deduplicates_without_filling_with_demo(self):
        def response(url, **kwargs):
            return CROSSREF if "crossref.org" in url else OPENALEX if "openalex.org" in url else ARXIV
        with patch.object(sources, "_http_get", side_effect=response), patch.object(sources, "_respect_rate_limit"):
            papers, warnings = sources.search(self.profile, 18)
        self.assertEqual(len(papers), 1)
        self.assertEqual(warnings, [])
        self.assertFalse(papers[0]["is_demo"])

    def test_fair_source_and_query_budgets(self):
        calls = []
        def response(url, **kwargs):
            calls.append(url)
            if "crossref.org" in url:
                return json_bytes({"message": {"items": []}})
            if "openalex.org" in url:
                return json_bytes({"results": []})
            return b'<feed xmlns="http://www.w3.org/2005/Atom" />'
        with patch.object(sources, "_http_get", side_effect=response), patch.object(sources, "_respect_rate_limit"):
            sources.search(self.profile, 20)
        self.assertEqual(len(calls), 9)
        budgets = {"crossref": [], "openalex": [], "arxiv": []}
        for url in calls:
            values = parse_qs(urlparse(url).query)
            name, field = ("crossref", "rows") if "crossref.org" in url else ("openalex", "per_page") if "openalex.org" in url else ("arxiv", "max_results")
            budgets[name].append(int(values[field][0]))
        self.assertEqual([sum(budgets[name]) for name in sources.DEFAULT_SOURCES], [7, 7, 6])
        self.assertTrue(all(max(values) - min(values) <= 1 for values in budgets.values()))
        self.assertTrue(all("dislocation" not in url for url in calls))

    def test_failure_degrades_to_other_sources(self):
        def response(url, **kwargs):
            if "crossref.org" in url:
                raise URLError("unavailable")
            return OPENALEX
        with patch.object(sources, "_http_get", side_effect=response) as get, patch.object(sources.time, "sleep"):
            papers, warnings = sources.search(self.profile, 12, ["crossref", "openalex"])
        self.assertEqual(len(papers), 1)
        self.assertTrue(any("crossref" in warning and "失败" in warning for warning in warnings))
        self.assertEqual(sum("crossref.org" in call.args[0] for call in get.call_args_list), 2)

    def test_http_authentication_failure_does_not_retry(self):
        error = HTTPError("https://api.openalex.org/works", 401, "unauthorized", {}, None)
        with patch.object(sources, "_http_get", side_effect=error) as get, patch.object(sources.time, "sleep"):
            papers, warnings = sources.search(self.profile, 12, ["openalex"])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(papers, [])
        self.assertTrue(any("HTTP 401" in warning for warning in warnings))

    def test_api_key_sent_as_header_not_query_or_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = dict(self.profile, _cache_dir=temporary)
            with patch.dict(sources.os.environ, {"OPENALEX_API_KEY": "secret-example-key"}), patch.object(sources, "_http_get", return_value=OPENALEX) as get:
                sources.search(profile, 6, ["openalex"])
            for call in get.call_args_list:
                self.assertNotIn("secret-example-key", call.args[0])
                self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer secret-example-key")
            for file in Path(temporary).rglob("*.json"):
                self.assertNotIn("secret-example-key", file.read_text())

    def test_cache_reuses_response_then_degrades_to_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = dict(self.profile, _cache_dir=temporary)
            url = "https://api.openalex.org/works?search=test"
            with patch.object(sources, "_http_get", return_value=OPENALEX) as get:
                self.assertEqual(sources._fetch(url, profile, "openalex", []), OPENALEX)
                self.assertEqual(sources._fetch(url, profile, "openalex", []), OPENALEX)
                self.assertEqual(get.call_count, 1)
            file = sources._cache_path(profile, url)
            stored = json.loads(file.read_text())
            stored["fetched_at"] = time.time() - 2 * 86400
            file.write_text(json.dumps(stored))
            warnings = []
            with patch.object(sources, "_http_get", side_effect=URLError("offline")), patch.object(sources.time, "sleep"):
                self.assertEqual(sources._fetch(url, profile, "openalex", warnings), OPENALEX)
            self.assertTrue(any("缓存" in warning and "最新" in warning for warning in warnings))

    def test_arxiv_spacing_applies_between_requests(self):
        with patch.object(sources, "_ARXIV_LAST_REQUEST", 10.0), patch.object(sources.time, "monotonic", side_effect=[11.0, 13.0]), patch.object(sources.time, "sleep") as sleep:
            sources._respect_rate_limit("arxiv")
        sleep.assert_called_once_with(2.0)

    def test_legacy_profile_terms_remain_supported(self):
        queries = sources._queries({"include_terms": ["ecosystem", "carbon"], "current_questions": ["land use effects"], "stage": "gap"})
        self.assertEqual(queries[0]["terms"], ["ecosystem", "carbon"])
        self.assertEqual(queries[2]["terms"], ["ecosystem", "land use effects"])

    def test_unknown_sources_and_empty_profile_report_no_results(self):
        papers, warnings = sources.search({}, 10, ["unknown"])
        self.assertEqual(papers, [])
        self.assertEqual(len(warnings), 2)
        self.assertEqual(sources.search(self.profile, 0), ([], []))


class DemoTests(unittest.TestCase):
    def test_arbitrary_direction_demo_is_marked_and_date_controlled(self):
        profile = {"topic_id": "ecology", "keywords": ["ecosystem", "carbon"], "method_terms": ["remote sensing"],
                   "questions": ["How does land use affect carbon?"], "_run_date": "2026-10-14"}
        papers = demo.candidates(profile, 18)
        self.assertEqual(len(papers), 18)
        self.assertEqual({paper["kind"] for paper in papers}, {"review", "research", "method"})
        for paper in papers:
            self.assertTrue(paper["is_demo"])
            self.assertTrue(paper["paper_id"].startswith("demo:"))
            self.assertIn("SYNTHETIC", paper["title"])
            self.assertIn("非真实论文", paper["abstract"])
            self.assertIn("ecosystem", paper["abstract"])
            self.assertEqual(urlparse(paper["url"]).hostname, "example.invalid")
            self.assertEqual(paper["doi"], "")
            self.assertEqual(paper["arxiv_id"], "")
            self.assertLessEqual(paper["published"], "2026-10-14")
        self.assertEqual(papers, demo.candidates(profile, 18))

    def test_demo_id_isolated_across_topics(self):
        one = demo.candidates({"topic_id": "one", "title": "Same"}, 1)[0]
        two = demo.candidates({"topic_id": "two", "title": "Same"}, 1)[0]
        self.assertNotEqual(one["paper_id"], two["paper_id"])
        self.assertEqual(demo.candidates({}, 0), [])


if __name__ == "__main__":
    unittest.main()
