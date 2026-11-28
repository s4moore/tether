#!/usr/bin/env bash
# Regenerates docs/tether-help.txt from `tether --help`. The test suite fails when that file is stale, so run this after changing
# the help text in lib/tetherlib/cli.py.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$HERE/bin/tether" --help > "$HERE/docs/tether-help.txt" 2>&1 || true
echo "regenerated docs/tether-help.txt"
