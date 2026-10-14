#!/usr/bin/env python3
"""Verify release archives, hashes and the source-only distribution boundary."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from build_release import ALLOWED_SUFFIXES, DENIED_PARTS, ROOT_FILES, SECRET_PATTERNS


def safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or "\\" in name or ":" in name or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe archive member: {name}")
    return path


def source_allowed(name: str) -> bool:
    path = safe_name(name)
    if any(part in DENIED_PARTS for part in path.parts):
        return False
    if name in ROOT_FILES:
        return True
    return len(path.parts) > 1 and path.suffix.lower() in ALLOWED_SUFFIXES.get(path.parts[0], set())


def check_payload(name: str, payload: bytes) -> None:
    if any(pattern.search(payload) for pattern in SECRET_PATTERNS):
        raise ValueError(f"Possible credential in {name}; review before publishing")
    # A shared release must not disclose the developer's private home paths.
    if re.search(rb"(?:/home/|/Users/)[A-Za-z0-9_.-]+/", payload):
        raise ValueError(f"Personal absolute home path in {name}")


def check_checksum(path: Path) -> None:
    checksum_file = path.with_suffix(path.suffix + ".sha256")
    fields = checksum_file.read_text(encoding="ascii").strip().split()
    if len(fields) != 2 or fields[1] != path.name:
        raise ValueError(f"Malformed checksum: {checksum_file.name}")
    if fields[0] != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError(f"Checksum mismatch: {path.name}")


def read_archive(path: Path) -> dict[str, bytes]:
    payloads = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            safe_name(info.filename)
            if info.filename in payloads or info.is_dir():
                raise ValueError(f"Duplicate or unexpected directory member: {info.filename}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"Symlink in archive: {info.filename}")
            payloads[info.filename] = archive.read(info)
    return payloads


def check_source(path: Path) -> tuple[str, dict[str, bytes]]:
    check_checksum(path)
    prefix = path.stem + "/"
    payloads = read_archive(path)
    if any(not name.startswith(prefix) for name in payloads):
        raise ValueError("Source archive must have one named release directory")
    payloads = {name[len(prefix):]: payload for name, payload in payloads.items()}
    manifest_bytes = payloads.pop("MANIFEST.json")
    manifest = json.loads(manifest_bytes)
    version = manifest["version"]
    if path.stem != f"Lite_Recommender_v{version}":
        raise ValueError("Archive name and manifest version differ")
    if path.with_suffix(".MANIFEST.json").read_bytes() != manifest_bytes:
        raise ValueError("External and bundled manifests differ")
    records = {record["path"]: record for record in manifest["files"]}
    if len(records) != len(manifest["files"]) or records.keys() != payloads.keys():
        raise ValueError("Archive content does not exactly match its manifest")
    if not ROOT_FILES.issubset(payloads):
        raise ValueError("Source release is missing required project documentation")
    for name, payload in payloads.items():
        if not source_allowed(name):
            raise ValueError(f"Non-source or runtime file in ZIP: {name}")
        if records[name]["bytes"] != len(payload) or records[name]["sha256"] != hashlib.sha256(payload).hexdigest():
            raise ValueError(f"Manifest mismatch: {name}")
        check_payload(name, payload)
    return version, payloads


def check_wheel(path: Path, version: str, source: dict[str, bytes]) -> None:
    expected_name = f"lite_literature_recommender-{version}-py3-none-any.whl"
    if path.name != expected_name:
        raise ValueError(f"Unexpected wheel name: {path.name}")
    check_checksum(path)
    payloads = read_archive(path)
    metadata_prefix = f"lite_literature_recommender-{version}.dist-info/"
    app = {}
    for name, payload in payloads.items():
        if name.startswith("lite_recommender/"):
            if not source_allowed(name) or name not in source or source[name] != payload:
                raise ValueError(f"Wheel app file differs from allowlisted source: {name}")
            app[name] = payload
        elif not name.startswith(metadata_prefix):
            raise ValueError(f"Unexpected wheel payload: {name}")
        check_payload(name, payload)
    if app.keys() != {name for name in source if name.startswith("lite_recommender/")}:
        raise ValueError("Wheel is missing an allowlisted app file")
    metadata = BytesParser().parsebytes(payloads[metadata_prefix + "METADATA"])
    if metadata["Name"] != "lite-literature-recommender" or metadata["Version"] != version:
        raise ValueError("Wheel project metadata does not match source release")
    requirements = metadata.get_all('Requires-Dist') or []
    extras = {'semantic': 'sentence-transformers', 'browser': 'playwright'}
    for requirement in requirements:
        if not any(requirement.lower().startswith(package) and (f'extra == "{extra}"' in requirement or f"extra == '{extra}'" in requirement) for extra, package in extras.items()):
            raise ValueError('Base installation must not require optional dependencies')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path(__file__).resolve().parents[1] / "dist")
    parser.add_argument("--require-wheel", action="store_true")
    args = parser.parse_args()
    archives = sorted(args.dist.glob("Lite_Recommender_v*.zip"))
    if len(archives) != 1:
        parser.error("Expected exactly one source ZIP; use a fresh output directory")
    try:
        version, source = check_source(archives[0])
        wheels = sorted(args.dist.glob("*.whl"))
        if len(wheels) > 1 or (args.require_wheel and len(wheels) != 1):
            raise ValueError("Expected exactly one wheel in the release directory")
        for wheel in wheels:
            check_wheel(wheel, version, source)
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        print(f"Release verification failed: {error}")
        return 1
    print(f"Verified v{version}: {len(source)} allowlisted source files, {len(wheels)} wheel, manifests and SHA-256 checksums.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
