#!/bin/bash
set -euo pipefail
# Run from Finder or Terminal; source and workspace paths may contain spaces.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec /bin/bash "$SCRIPT_DIR/run_linux.sh" "$@"
