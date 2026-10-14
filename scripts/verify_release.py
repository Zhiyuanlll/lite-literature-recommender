#!/usr/bin/env python3
"""Check each source file in an extracted source release against MANIFEST.json."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.is_file():
        parser.error("MANIFEST.json is generated in the release ZIP; extract the ZIP first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = []
    for record in manifest["files"]:
        raw_path = root / record["path"]
        path = raw_path.resolve()
        if not path.is_relative_to(root):
            failures.append(f"Unsafe path: {record['path']}")
            continue
        if not path.is_file() or raw_path.is_symlink():
            failures.append(f"Missing or symlinked file: {record['path']}")
            continue
        contents = path.read_bytes()
        if len(contents) != record["bytes"] or hashlib.sha256(contents).hexdigest() != record["sha256"]:
            failures.append(f"Changed file: {record['path']}")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Verified {len(manifest['files'])} source files for {manifest['product']} {manifest['version']}.")
    print("Runtime workspace files are intentionally outside this source manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
