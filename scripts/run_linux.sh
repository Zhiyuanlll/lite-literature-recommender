#!/usr/bin/env bash
set -euo pipefail

# Usage: ./scripts/run_linux.sh [workspace-path] [serve options]
# Relative workspace paths resolve from the caller's current directory.
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_CMD=""
for candidate in "$PROJECT_DIR/.venv/bin/python" python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON_CMD="$candidate"
    break
  fi
done
if [[ -z "$PYTHON_CMD" ]]; then
  echo "Python 3.10 or newer is required. Install Python, then run this script again." >&2
  exit 1
fi
WORKSPACE_PATH="$PROJECT_DIR/workspace"
if [[ $# -gt 0 && "$1" != --* ]]; then
  WORKSPACE_PATH="$($PYTHON_CMD -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$1")"
  shift
fi
cd -- "$PROJECT_DIR"
"$PYTHON_CMD" -m lite_recommender --workspace "$WORKSPACE_PATH" init
exec "$PYTHON_CMD" -m lite_recommender --workspace "$WORKSPACE_PATH" serve --open "$@"
