#!/usr/bin/env python3
"""Build a source ZIP from an explicit allowlist, never from a workspace."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile
import shutil
import subprocess
import sys

ROOT_FILES = {"README.md", "LICENSE", "NOTICE", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "pyproject.toml", ".gitignore"}
ALLOWED_SUFFIXES = {
    "lite_recommender": {".py", ".html", ".css", ".js", ".svg", ".json"},
    "tests": {".py", ".json"},
    "docs": {".md"},
    "examples": {".md", ".json"},
    "scripts": {".py", ".sh", ".command", ".cmd", ".ps1", ".md"},
    ".github": {".yml", ".yaml", ".md", ".json"},
}
DENIED_PARTS = {"workspace", "data", "runs", "pdfs", "cache", "backups", "__pycache__", ".git", ".agents", ".codex", ".venv", "venv", "node_modules", "dist", "build"}
SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
]


def source_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for filename in sorted(ROOT_FILES):
        path = root / filename
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or symlinked release file: {filename}")
        result.append(path)
    for folder, suffixes in ALLOWED_SUFFIXES.items():
        base = root / folder
        if not base.is_dir():
            raise ValueError(f"Missing source directory: {folder}")
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root)
            if any(part in DENIED_PARTS for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f"Symlinks cannot be released: {relative}")
            if path.is_file() and path.suffix.lower() in suffixes:
                result.append(path)
    for path in result:
        if any(pattern.search(path.read_bytes()) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Possible embedded credential in {path.relative_to(root)}; review before releasing")
    return sorted(result, key=lambda path: path.relative_to(root).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--version", help="Defaults to project version in pyproject.toml")
    parser.add_argument("--tag", help="Optional release tag, required to equal v<project-version>")
    parser.add_argument("--wheel", action="store_true", help="Also build the wheel (requires pip, setuptools and wheel)")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # Bundle the same offline checks for both source users and wheel installations.
    checks = root / "lite_recommender/_tests"
    checks.mkdir(exist_ok=True)
    (checks / "__init__.py").write_text('"""Bundled isolated verification checks."""\n', encoding="utf-8")
    for test in (root / "tests").glob("test_*.py"):
        shutil.copy2(test, checks / test.name)
    match = re.search(r'^version\s*=\s*"([^\"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE)
    project_version = match.group(1) if match else ""
    version = args.version or project_version
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.-]*)", version):
        parser.error("Expected a safe semantic version, e.g. 2.0.0")
    if version != project_version:
        parser.error("--version must match pyproject.toml; update source version before releasing")
    if args.tag is not None and args.tag != f"v{version}":
        parser.error("--tag must match v<project-version>; refusing a mislabeled release")
    package_version = re.search(r'^__version__\s*=\s*"([^\"]+)"', (root / "lite_recommender/__init__.py").read_text(encoding="utf-8"), re.MULTILINE)
    if not package_version or package_version.group(1) != version:
        parser.error("lite_recommender.__version__ must match pyproject.toml")
    files = source_files(root)
    records = []
    payloads: dict[str, bytes] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        payloads[relative] = payload
        records.append({"path": relative, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    manifest = {
        "product": "Lite Recommender", "version": version,
        "manifest_version": 1, "license": "MIT", "python_requires": ">=3.10",
        "release_policy": "explicit source allowlist; no runtime workspace or personal data",
        "files": records,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output = (args.output_dir or root / "dist").expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    basename = f"Lite_Recommender_v{version}"
    archive = output / f"{basename}.zip"
    prefix = f"{basename}/"
    # Fixed ZIP dates avoid nondeterminism from filesystem modification times.
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        for relative, payload in [*payloads.items(), ("MANIFEST.json", manifest_bytes)]:
            info = zipfile.ZipInfo(prefix + relative, date_time=(2026, 10, 14, 0, 0, 0))
            info.create_system = 3
            executable = relative.endswith((".sh", ".command"))
            info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            handle.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / f"{basename}.MANIFEST.json").write_bytes(manifest_bytes)
    (output / f"{basename}.zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    wheel_path = None
    if args.wheel:
        # Build only from the clean allowlisted snapshot, never from runtime data
        # that could otherwise be picked up by setuptools package discovery.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="lite-release-source-") as temporary:
            clean = Path(temporary) / "source"
            clean.mkdir()
            for relative, payload in payloads.items():
                target = clean / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--wheel-dir", str(output), str(clean)], check=True)
        wheel_path = output / f"lite_literature_recommender-{version}-py3-none-any.whl"
        if not wheel_path.is_file():
            raise ValueError("Expected portable Python wheel was not built")
        wheel_checksum = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        wheel_path.with_suffix(wheel_path.suffix + ".sha256").write_text(f"{wheel_checksum}  {wheel_path.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(archive), "sha256": checksum, "manifest": str(output / f"{basename}.MANIFEST.json"), "source_files": len(files), "wheel": str(wheel_path) if wheel_path else None}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
