import datetime as dt
import unittest

from lite_recommender.concepts import normalize_concepts
from lite_recommender.ranking import compute_policy, score, select


class ConceptRankingTests(unittest.TestCase):
    def setUp(self):
        self.profile = {"topic_id": "HEA_mobility", "title": "HEA mobility", "profile_version": "1", "stage": "focus", "keywords": ["high entropy alloy", "dislocation", "mobility"], "method_terms": ["molecular dynamics", "drag coefficient"], "questions": ["What controls dislocation velocity?"]}
        self.date = dt.date(2026, 10, 14)

    def paper(self, title, abstract="A study with experimental evidence.", **kwargs):
        return {"paper_id": title, "title": title, "abstract": abstract, "doi": "10.1234/test", "published": "2025-10-01", "kind": "research", **kwargs}

    def ranked(self, title, abstract="A study with experimental evidence.", **kwargs):
        return score(self.profile, self.paper(title, abstract, **kwargs), {}, self.date)

    def test_legacy_mobility_forms_two_concepts_and_object_anchor(self):
        groups = normalize_concepts(self.profile)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0][0], "high entropy alloy")
        self.assertIn("dislocation velocity", groups[1])
        reverse = {**self.profile, "keywords": ["dislocation mobility", "high entropy alloy", "dislocation"]}
        self.assertEqual(len(normalize_concepts(reverse)), 2)
        self.assertEqual(normalize_concepts(reverse)[0][0], "high entropy alloy")

    def test_explicit_concepts_and_within_group_or(self):
        profile = {**self.profile, "core_concepts": [["neural network", "machine learning"], ["fracture toughness", "crack resistance"]]}
        yes = score(profile, self.paper("Machine learning predicts crack resistance"), {}, self.date)
        self.assertTrue(yes["eligible"])
        no = score(profile, self.paper("Machine learning predicts corrosion"), {}, self.date)
        self.assertFalse(no["eligible"])
        self.assertEqual(no["missing_concepts"], ["fracture toughness"])

    def test_related_expression_and_no_method_still_core(self):
        value = self.ranked("Dislocation velocity in high-entropy alloys")
        self.assertEqual(value["classification"], "core")
        self.assertTrue(value["eligible"])
        self.assertEqual(value["components"]["method"], 0)
        self.assertGreaterEqual(value["score"], 65)
        self.assertEqual(value["components"]["complement"], 0)

    def test_method_bonus_never_substitutes_missing_core(self):
        value = self.ranked("Molecular dynamics of high entropy alloy corrosion with drag coefficient")
        self.assertFalse(value["eligible"])
        self.assertEqual(value["classification"], "rejected")
        self.assertIn("missing_core_concept", value["rejection_reasons"])
        method = self.ranked("Dislocation mobility in high entropy alloys using molecular dynamics and drag coefficient")
        plain = self.ranked("Dislocation mobility in high entropy alloys")
        self.assertEqual(method["base_score"] - plain["base_score"], 10)

    def test_related_problem_other_object_is_rejected_even_with_methods(self):
        value = self.ranked("Dislocation velocity in copper using molecular dynamics and drag coefficient")
        self.assertEqual(value["classification"], "rejected")
        self.assertFalse(value["exploration_eligible"])

    def test_no_abstract_retained_pending_never_formal(self):
        value = self.ranked("Dislocation mobility in high entropy alloys", abstract="")
        self.assertEqual(value["classification"], "pending")
        self.assertIn("missing_abstract", value["rejection_reasons"])
        self.assertEqual(select({"HEA_mobility": [value]}, 10), [])

    def test_future_and_exclusion_hard_rejections(self):
        future = self.ranked("Dislocation mobility in HEAs", published="2026-10-15")
        self.assertEqual(future["classification"], "rejected")
        self.assertIn("future_date", future["rejection_reasons"])
        profile = {**self.profile, "exclude_terms": ["corrosion"]}
        excluded = score(profile, self.paper("HEA dislocation mobility and corrosion", abstract=""), {}, self.date)
        self.assertEqual(excluded["classification"], "rejected")
        self.assertIn("excluded_term", excluded["rejection_reasons"])

    def test_final_feedback_score_controls_gate(self):
        profile = {**self.profile, "core_min_score": 91}
        paper = self.paper("High entropy alloy dislocation mobility")
        first = score(profile, paper, {}, self.date)
        self.assertFalse(first["eligible"])
        adjustment = {term: 8 for term in first["matched_terms"]}
        second = score(profile, paper, {"adjustments": adjustment}, self.date)
        self.assertLess(second["base_score"], 91)
        self.assertGreaterEqual(second["score"], 91)
        self.assertTrue(second["eligible"])

    def test_at_most_one_exploration_each_independent_topic(self):
        core = [self.ranked("Dislocation mobility in HEA " + str(i)) for i in range(5)]
        adjacent = [self.ranked("High entropy alloy dislocation deformation " + str(i)) for i in range(4)]
        values = {"one": [{**paper, "topic_id": "one"} for paper in core + adjacent], "two": [{**paper, "topic_id": "two"} for paper in core + adjacent]}
        selected = select(values, {"one": 3, "two": 4})
        self.assertEqual(len(selected), 7)
        for topic, count in [("one", 3), ("two", 4)]:
            topic_values = [paper for paper in selected if paper["topic_id"] == topic]
            self.assertEqual(len(topic_values), count)
            self.assertEqual(sum(paper.get("exploration", False) for paper in topic_values), 1)

    def test_novelty_actual_overlap_differs(self):
        values = [self.ranked("Dislocation mobility in HEA experimental stress temperature"), self.ranked("Dislocation mobility in HEA experimental stress temperature second"), self.ranked("Dislocation mobility in HEA atomistic drag phonon simulation")]
        result = select({"HEA_mobility": values}, 3)
        self.assertEqual(result[0]["selection_novelty"], 1)
        self.assertNotEqual(result[1]["selection_novelty"], result[2]["selection_novelty"])

    def test_concept_feedback_works_without_legacy_keyword_tag(self):
        profile = {**self.profile, "core_concepts": [["alloy"], ["velocity"]]}
        feedback = {str(i): {"rating": 5, "reason": "question_relevant", "tags": ["velocity"]} for i in range(3)}
        policy = compute_policy(profile, feedback, {})
        self.assertEqual(policy["adjustments"]["velocity"], 2)
        self.assertEqual(compute_policy(profile, feedback, policy), policy)


if __name__ == "__main__":
    unittest.main()
