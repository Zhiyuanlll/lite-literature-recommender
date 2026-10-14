"""Optional CPU semantic ranking; ordinary runs never install or download.

Only the explicit setup operation contacts Hugging Face. Inference uses a
verified local, pinned E5 model with safetensors and no remote model code.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import shutil
import tempfile
from pathlib import Path

from .storage import atomic_json, digest
from .ranking import INTEREST_REASONS

MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MANIFEST = "lite-semantic-model.json"
ALLOWED_MODULES = {
    "sentence_transformers.models.Transformer",
    "sentence_transformers.models.Pooling",
    "sentence_transformers.models.Normalize",
    # Official Sentence Transformers 5.x save paths; legacy exports above
    # remain accepted. No arbitrary custom module import is permitted.
    "sentence_transformers.base.modules.transformer.Transformer",
    "sentence_transformers.sentence_transformer.modules.pooling.Pooling",
    "sentence_transformers.sentence_transformer.modules.normalize.Normalize",
}
_MODEL_CACHE = {}


def _dependency_available():
    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        return False


def _manifest(model_dir):
    path = Path(model_dir)
    try:
        value = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("未找到本程序安装的语义模型；请先显式下载模型。") from exc
    if value.get("model_id") != MODEL_ID or value.get("revision") != MODEL_REVISION or not isinstance(value.get("files"), dict):
        raise ValueError("本地语义模型版本不受支持；请使用语义模型安装命令。")
    return value


def _verify_layout(model_dir):
    root = Path(model_dir).resolve()
    modules = json.loads((root / "modules.json").read_text(encoding="utf-8"))
    if not isinstance(modules, list) or not modules or any(not isinstance(module, dict) or module.get("type") not in ALLOWED_MODULES for module in modules):
        raise ValueError("本地模型含未获准的模块类型。")
    for module in modules:
        directory = module.get("path", "")
        if not isinstance(directory, str) or (root / directory).resolve() != root and root not in (root / directory).resolve().parents:
            raise ValueError("本地模型模块路径不安全。")
    if any(path.suffix in {".py", ".bin", ".pt", ".pth", ".pkl", ".pickle"} for path in root.rglob("*") if path.is_file()):
        raise ValueError("本地模型必须使用 safetensors，并且不能包含可执行模型代码。")
    if not any(root.rglob("*.safetensors")):
        raise ValueError("本地模型缺少 safetensors 权重。")


def _file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _verify_files(model_dir, manifest):
    root = Path(model_dir).resolve()
    for filename, expected in manifest["files"].items():
        path = (root / filename).resolve()
        if root not in path.parents or not isinstance(expected, str) or _file_hash(path) != expected:
            raise ValueError("本地模型文件校验失败；请重新安装模型。")
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file() and path.name != MANIFEST}
    if actual != set(manifest["files"]):
        raise ValueError("本地模型文件清单发生变化；请重新安装模型。")
    _verify_layout(root)


def status(settings):
    """Cheap UI capability check; inference verifies all hashes before load."""
    available = _dependency_available()
    directory = settings.get("model_dir", "")
    ready, message = False, "请先安装可选语义依赖，再下载本地模型。"
    if available:
        message = "语义依赖已安装；请显式下载本地模型。"
        if directory:
            try:
                manifest = _manifest(directory)
                ready = bool(manifest["files"]) and all((Path(directory) / name).is_file() for name in manifest["files"])
                message = "本地模型已就绪；启用后只在 CPU 计算。" if ready else "本地模型文件不完整。"
            except (ValueError, OSError):
                message = "本地模型尚未安装或版本不受支持。"
    return {"available": available, "ready": ready, "enabled": bool(settings.get("enabled")), "message": message, "model_id": MODEL_ID, "revision": MODEL_REVISION}


def setup_model(model_dir):
    """Explicitly download the pinned model and atomically save a local copy.

    This does not install Python packages, activate semantics, or access any
    personal research data. An unknown nonempty directory is never replaced.
    """
    if not _dependency_available():
        raise RuntimeError("缺少可选依赖。请执行 python -m pip install 'sentence-transformers>=3.0,<6'，或按语义增强说明安装。")
    target = Path(model_dir).expanduser().resolve()
    if target.exists():
        if any(target.iterdir()):
            manifest = _manifest(target)
            _verify_files(target, manifest)
            return {**manifest, "model_dir": str(target), "already_installed": True}
        target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".lite-semantic-", dir=target.parent))
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(MODEL_ID, revision=MODEL_REVISION, device="cpu", trust_remote_code=False, token=False, model_kwargs={"use_safetensors": True})
        model.save(str(temporary), safe_serialization=True)
        _verify_layout(temporary)
        files = {str(path.relative_to(temporary)): _file_hash(path) for path in temporary.rglob("*") if path.is_file()}
        manifest = {"format": 1, "model_id": MODEL_ID, "revision": MODEL_REVISION, "license": "MIT", "files": files, "max_tokens": 512}
        atomic_json(temporary / MANIFEST, manifest)
        os.replace(temporary, target)
        return {**manifest, "model_dir": str(target), "already_installed": False}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _load_model(model_dir):
    if not _dependency_available():
        raise RuntimeError("未安装 Sentence Transformers 可选依赖")
    manifest = _manifest(model_dir)
    # File sizes/mtimes detect edits and invalidate the in-memory model cache;
    # cryptographic hashes are verified before each new load.
    root = Path(model_dir).resolve()
    signatures = [(name, (root / name).stat().st_size, (root / name).stat().st_mtime_ns) for name in sorted(manifest["files"])]
    key = digest([str(root), manifest, signatures])
    if key not in _MODEL_CACHE:
        _verify_files(root, manifest)
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(str(root), device="cpu", local_files_only=True, trust_remote_code=False, token=False, model_kwargs={"use_safetensors": True})
        model.max_seq_length = 512
        _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = model
    return _MODEL_CACHE[key], digest([MODEL_ID, MODEL_REVISION, manifest["files"]])


def _unit(vector):
    values = [float(value) for value in vector]
    if not values or len(values) > 4096 or any(not math.isfinite(value) for value in values):
        raise ValueError("语义模型返回无效向量")
    length = math.sqrt(sum(value * value for value in values))
    if length == 0:
        raise ValueError("语义模型返回零向量")
    return [value / length for value in values]


def _dot(one, two):
    if len(one) != len(two):
        raise ValueError("语义向量维度不一致")
    return sum(a * b for a, b in zip(one, two))


def _vectors(model, identity, texts, cache_dir):
    cache = Path(cache_dir) / "embeddings"
    result, missing, missing_keys = {}, [], []
    keys = [digest([identity, text]) for text in texts]
    for key, text in zip(keys, texts):
        if key in result or key in missing_keys:
            continue
        try:
            value = json.loads((cache / (key + ".json")).read_text(encoding="utf-8"))
            if value.get("model") != identity:
                raise ValueError("wrong model")
            result[key] = _unit(value["vector"])
        except (OSError, ValueError, TypeError, KeyError):
            missing.append(text)
            missing_keys.append(key)
    if missing:
        vectors = model.encode(missing, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False, batch_size=16)
        if len(vectors) != len(missing):
            raise ValueError("语义模型返回向量数量不一致")
        for key, vector in zip(missing_keys, vectors):
            result[key] = _unit(vector)
            atomic_json(cache / (key + ".json"), {"model": identity, "vector": result[key]})
    return [result[key] for key in keys], len(missing)


def _passage(paper):
    return "passage: " + paper.get("title", "") + "\n" + paper.get("abstract", "")


def rerank(profile, papers, settings, cache_dir, feedback=None, rated_papers=None):
    """Return rescored candidates, warnings, and reproducible run metadata.

    Similarity supplies at most +/-10 points at weight .25. It never invents a
    missing core-concept hit or promotes title-only evidence into formal work.
    Relevant rated-paper centroids can slightly move the query, without model
    fine-tuning. Free notes, no-time and access-only feedback are not encoded.
    """
    metadata = {"enabled": bool(settings.get("enabled")), "used": False, "model_id": MODEL_ID, "revision": MODEL_REVISION}
    if not settings.get("enabled") or not papers:
        return papers, [], metadata
    try:
        model, identity = _load_model(settings.get("model_dir", ""))
        from .concepts import normalize_concepts
        questions = profile.get("questions", [])
        question = "query: " + "\n".join(questions + ["; ".join(group[0] for group in normalize_concepts(profile))])
        query = _unit(model.encode([question], normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False, batch_size=1)[0])
        valid = [(pid, value) for pid, value in (feedback or {}).items() if value.get("rating") in {1, 2, 3, 4, 5} and value.get("reason") in INTEREST_REASONS]
        rated_papers = rated_papers or {}
        if not isinstance(rated_papers, dict):
            rated_papers = {paper.get("paper_id"): paper for paper in rated_papers if paper}
        positives = [rated_papers[pid] for pid, value in valid if value["rating"] >= 4 and rated_papers.get(pid, {}).get("abstract") and not rated_papers[pid].get("is_demo")]
        centroid_count = 0
        new_vectors = 0
        if len(valid) >= 3 and len(positives) >= 2:
            vectors, added = _vectors(model, identity, [_passage(paper) for paper in positives], cache_dir)
            centroid = _unit([sum(values) / len(vectors) for values in zip(*vectors)])
            query = _unit([.8 * a + .2 * b for a, b in zip(query, centroid)])
            centroid_count, new_vectors = len(positives), added
        vectors, added = _vectors(model, identity, [_passage(paper) for paper in papers], cache_dir)
        similarities = [_dot(query, vector) for vector in vectors]
        # Title-only and hard-rejected records stay visible for inspection,
        # but cannot change the relative scoring of abstract-backed papers.
        rankable = [bool(paper.get("abstract")) and not any(reason in {"excluded_term", "future_date"} for reason in paper.get("rejection_reasons", [])) for paper in papers]
        ranking_similarities = [value for value, allowed in zip(similarities, rankable) if allowed]
        weight = max(0, min(.5, float(settings.get("weight", .25))))
        result = []
        for paper, similarity, allowed in zip(papers, similarities, rankable):
            # E5 similarities are not probabilities. A within-batch percentile
            # avoids pretending an uncalibrated fixed cosine is a probability.
            lower = sum(value < similarity for value in ranking_similarities)
            equal_other = max(0, sum(value == similarity for value in ranking_similarities) - 1)
            percentile = (lower + .5 * equal_other) / (len(ranking_similarities) - 1) if allowed and len(ranking_similarities) > 1 else .5
            adjustment = round((percentile - .5) * 80 * weight, 3)
            final = round(max(0, min(100, paper.get("base_score", paper.get("score", 0)) + paper.get("adjustment", 0) + adjustment)), 2)
            hard = any(reason in {"excluded_term", "future_date"} for reason in paper.get("rejection_reasons", []))
            has_abstract = bool(paper.get("abstract"))
            complete = bool(paper.get("core_concepts")) and not paper.get("missing_concepts")
            eligible = not hard and has_abstract and complete and final >= float(profile.get("core_min_score", 65))
            anchor = any(group.get("index") == 0 for group in paper.get("matched_concepts", []))
            exploration = not hard and has_abstract and len(paper.get("core_concepts", [])) >= 2 and anchor and paper.get("adjacent_core", False) and paper.get("components", {}).get("topic", 0) >= .5 and final >= float(profile.get("exploration_min_score", 50)) and not eligible
            classification = "rejected" if hard else ("pending" if not has_abstract else ("core" if eligible else ("exploration" if exploration else "rejected")))
            reasons = [reason for reason in paper.get("rejection_reasons", []) if reason != "below_core_score"]
            if final < float(profile.get("core_min_score", 65)):
                reasons.append("below_core_score")
            detail = {"model_id": MODEL_ID, "revision": MODEL_REVISION, "cosine": round(similarity, 6), "batch_percentile": round(percentile, 6), "adjustment": adjustment, "weight": weight, "positive_feedback_count": centroid_count, "probability": False, "max_tokens": 512}
            result.append({**paper, "score": final, "eligible": eligible, "exploration_eligible": exploration, "classification": classification, "rejection_reasons": reasons, "semantic": detail})
        metadata.update({"used": True, "embedded_count": len(papers), "ranking_pool_count": len(ranking_similarities), "new_embedding_count": new_vectors + added, "positive_feedback_count": centroid_count, "query_digest": digest(question), "weight": weight})
        return result, [], metadata
    except Exception as exc:
        metadata["fallback"] = True
        return papers, ["语义增强不可用，已使用普通规则排序：" + str(exc)], metadata
