import datetime as dt
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from lite_recommender import semantic
from lite_recommender.ranking import score


class FakeModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))
        result = []
        for text in texts:
            if text.startswith("query:"):
                result.append([1., 0.])
            elif "positive" in text:
                result.append([0., 1.])
            elif "direct" in text:
                result.append([1., 0.])
            else:
                result.append([0., 1.])
        return result


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lite-semantic-test-")
        self.root = Path(self.temp.name)
        self.profile = {"topic_id": "hea", "title": "HEA", "profile_version": "1", "stage": "focus", "questions": ["Dislocation movement"], "keywords": ["high entropy alloy", "dislocation mobility"], "method_terms": []}
        self.settings = {"enabled": True, "model_dir": str(self.root / "model"), "weight": .25}

    def tearDown(self):
        self.temp.cleanup()

    def paper(self, identifier, abstract="measured response", title=None):
        return score(self.profile, {"paper_id": identifier, "title": title or "High entropy alloy dislocation mobility " + identifier, "abstract": abstract, "published": "2025-01-01", "doi": "10.1234/" + identifier}, {}, dt.date(2026, 10, 14))

    def test_disabled_never_loads_or_imports_model(self):
        papers = [self.paper("one")]
        with patch.object(semantic, "_load_model") as load:
            result, warnings, metadata = semantic.rerank(self.profile, papers, {"enabled": False}, self.root)
        load.assert_not_called()
        self.assertIs(result, papers)
        self.assertEqual(warnings, [])
        self.assertFalse(metadata["used"])

    def test_failure_returns_rules_and_clear_warning(self):
        papers = [self.paper("one")]
        with patch.object(semantic, "_load_model", side_effect=RuntimeError("dependency missing")):
            result, warnings, metadata = semantic.rerank(self.profile, papers, self.settings, self.root)
        self.assertIs(result, papers)
        self.assertIn("普通规则", warnings[0])
        self.assertTrue(metadata["fallback"])

    def test_prefixes_rank_cache_and_abstract_change(self):
        model = FakeModel()
        papers = [self.paper("direct"), self.paper("adjacent")]
        with patch.object(semantic, "_load_model", return_value=(model, "model-hash")):
            first, warnings, metadata = semantic.rerank(self.profile, papers, self.settings, self.root)
            second, _, reused = semantic.rerank(self.profile, papers, self.settings, self.root)
            changed = [papers[0], self.paper("adjacent", abstract="changed experimental result")]
            _, _, refreshed = semantic.rerank(self.profile, changed, self.settings, self.root)
        self.assertEqual(warnings, [])
        self.assertGreater(first[0]["score"], first[1]["score"])
        self.assertEqual(first, second)
        self.assertEqual(metadata["new_embedding_count"], 2)
        self.assertEqual(reused["new_embedding_count"], 0)
        self.assertEqual(refreshed["new_embedding_count"], 1)
        self.assertTrue(all(text.startswith(("query: ", "passage: ")) for call in model.calls for text in call))
        self.assertFalse(first[0]["semantic"]["probability"])

    def test_similarity_cannot_supply_missing_object_or_abstract(self):
        model = FakeModel()
        papers = [self.paper("direct", title="Dislocation mobility in copper direct"), self.paper("direct-no-abstract", abstract="")]
        with patch.object(semantic, "_load_model", return_value=(model, "model-hash")):
            result, _, _ = semantic.rerank(self.profile, papers, self.settings, self.root)
        self.assertEqual(result[0]["classification"], "rejected")
        self.assertEqual(result[1]["classification"], "pending")
        self.assertFalse(any(paper["eligible"] for paper in result))

    def test_feedback_centroid_uses_actual_papers_and_ignores_notes(self):
        model = FakeModel()
        feedback = {str(i): {"rating": 5, "reason": "question_relevant", "note": "SECRET free note"} for i in range(3)}
        rated = {str(i): self.paper("positive-" + str(i)) for i in range(3)}
        with patch.object(semantic, "_load_model", return_value=(model, "model-hash")):
            result, _, metadata = semantic.rerank(self.profile, [self.paper("direct"), self.paper("adjacent")], self.settings, self.root, feedback, rated)
        self.assertEqual(metadata["positive_feedback_count"], 3)
        self.assertFalse(any("SECRET" in text for call in model.calls for text in call))
        model.calls.clear()
        feedback = {str(i): {"rating": 5, "reason": "no_time"} for i in range(3)}
        with patch.object(semantic, "_load_model", return_value=(model, "model-hash")):
            _, _, metadata = semantic.rerank(self.profile, [self.paper("direct")], self.settings, self.root, feedback, rated)
        self.assertEqual(metadata.get("positive_feedback_count"), 0)

    def test_local_load_security_arguments_and_hash_verification(self):
        with patch.object(semantic, "_dependency_available", return_value=False):
            self.assertFalse(semantic.status(self.settings)["available"])
            with self.assertRaises(RuntimeError):
                semantic.setup_model(self.root / "download")
        model = self.root / "safe-model"
        model.mkdir()
        (model / "modules.json").write_text(json.dumps([{"type": "sentence_transformers.models.Transformer", "path": ""}]))
        (model / "model.safetensors").write_bytes(b"fake test weights")
        files = {path.name: semantic._file_hash(path) for path in model.iterdir()}
        manifest = {"model_id": semantic.MODEL_ID, "revision": semantic.MODEL_REVISION, "files": files}
        semantic._verify_files(model, manifest)
        (model / "model.safetensors").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            semantic._verify_files(model, manifest)
        (model / "evil.py").write_text("raise Exception('never execute')")
        with self.assertRaises(ValueError):
            semantic._verify_layout(model)

    def test_ties_and_singleton_give_no_unjustified_bonus(self):
        model = FakeModel()
        with patch.object(semantic, "_load_model", return_value=(model, "model-hash")):
            tied, _, _ = semantic.rerank(self.profile, [self.paper("one"), self.paper("two")], self.settings, self.root)
            single, _, _ = semantic.rerank(self.profile, [self.paper("one")], self.settings, self.root)
        self.assertEqual([paper["semantic"]["adjustment"] for paper in tied], [0, 0])
        self.assertEqual(single[0]["semantic"]["adjustment"], 0)

    def test_title_only_and_hard_rejected_do_not_distort_percentiles(self):
        model = FakeModel()
        core = self.paper("one")
        pending = self.paper("direct-pending", abstract="")
        future = {**self.paper("direct-future"), "date_bucket": "future", "classification": "rejected", "eligible": False, "exploration_eligible": False, "rejection_reasons": ["future_date"]}
        with patch.object(semantic, "_load_model", return_value=(model, "model-hash")):
            result, _, metadata = semantic.rerank(self.profile, [core, pending, future], self.settings, self.root)
        self.assertEqual(metadata["ranking_pool_count"], 1)
        self.assertEqual([paper["semantic"]["adjustment"] for paper in result], [0, 0, 0])
        self.assertEqual(result[0]["score"], core["score"])

    def test_official_new_module_paths_and_reject_custom_code(self):
        model = self.root / "st-five-model"
        model.mkdir()
        (model / "model.safetensors").write_bytes(b"fake test weights")
        modules = [
            {"type": "sentence_transformers.base.modules.transformer.Transformer", "path": ""},
            {"type": "sentence_transformers.sentence_transformer.modules.pooling.Pooling", "path": "1_Pooling"},
            {"type": "sentence_transformers.sentence_transformer.modules.normalize.Normalize", "path": "2_Normalize"},
        ]
        (model / "modules.json").write_text(json.dumps(modules))
        semantic._verify_layout(model)
        modules[0]["type"] = "custom_module.UntrustedModel"
        (model / "modules.json").write_text(json.dumps(modules))
        with self.assertRaises(ValueError):
            semantic._verify_layout(model)

    def test_setup_is_explicit_pinned_and_local_load_never_downloads(self):
        constructor_calls = []
        class SavedModel(FakeModel):
            def __init__(self, name, **kwargs):
                super().__init__()
                constructor_calls.append((name, kwargs))

            def save(self, directory, **kwargs):
                if kwargs != {"safe_serialization": True}:
                    raise AssertionError("unsafe serialization")
                path = Path(directory)
                (path / "modules.json").write_text(json.dumps([{"type": "sentence_transformers.models.Transformer", "path": ""}]))
                (path / "model.safetensors").write_bytes(b"mock local weights")
        module = types.ModuleType("sentence_transformers")
        module.SentenceTransformer = SavedModel
        destination = self.root / "installed-model"
        with patch.object(semantic, "_dependency_available", return_value=True), patch.dict("sys.modules", {"sentence_transformers": module}):
            installed = semantic.setup_model(destination)
            model, identity = semantic._load_model(destination)
            semantic.setup_model(destination)
        self.assertEqual(len(constructor_calls), 2)
        remote_name, remote_options = constructor_calls[0]
        self.assertEqual(remote_name, semantic.MODEL_ID)
        self.assertEqual(remote_options["revision"], semantic.MODEL_REVISION)
        self.assertFalse(remote_options["trust_remote_code"])
        self.assertFalse(remote_options["token"])
        self.assertEqual(remote_options["model_kwargs"], {"use_safetensors": True})
        local_name, local_options = constructor_calls[1]
        self.assertEqual(Path(local_name).resolve(), destination.resolve())
        self.assertTrue(local_options["local_files_only"])
        self.assertEqual(local_options["device"], "cpu")
        self.assertFalse(local_options["trust_remote_code"])
        self.assertEqual(model.max_seq_length, 512)
        self.assertEqual(installed["revision"], semantic.MODEL_REVISION)
        self.assertTrue(identity)


if __name__ == "__main__":
    unittest.main()
