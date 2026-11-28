#!/usr/bin/env bash
# Everything that can be checked without a screen: unit tests, `herdr-app check` (loads each mod),
# a TypeScript pass over the mods for the mistakes that matter (unknown names, bad imports, wrong
# argument counts). The mod steps need herdr-app and are skipped without it. Set TSC=/path/to/tsc if `tsc`
# is not on PATH.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0
step() { echo "==> $*"; }

step "unit tests"
python3 -W error::ResourceWarning -m unittest discover -s "$HERE/tests" 2>&1 | tail -3 || fail=1

TSC="${TSC:-$(command -v tsc || true)}"
HAVE_HERDR=1; command -v herdr-app >/dev/null || HAVE_HERDR=0
[ "$HAVE_HERDR" = 1 ] || echo "note: herdr-app not found; skipped the mod checks"
for m in "$HERE"/herdr/mods/*/; do
  name="$(basename "$m")"
  [ "$HAVE_HERDR" = 1 ] || continue
  out="$(herdr-app check "$m" 2>&1)"
  # a mod may log a warning after the verdict (e.g. "mod called hc"), so look for the verdict itself
  printf '%s\n' "$out" | grep -q '^check passed' || { echo "FAIL $name: $(printf '%s\n' "$out" | tail -1)"; fail=1; }
  if [ -n "$TSC" ]; then
    bad="$("$TSC" -p "$m/jsconfig.json" 2>&1 | grep -E "TS2304|TS2305|TS2307|TS2554|TS2724|TS2551" || true)"
    [ -n "$bad" ] && { echo "FAIL $name (types):"; echo "$bad" | head -5; fail=1; }
  fi
done
[ -n "$TSC" ] || echo "note: tsc not found; skipped the TypeScript pass (set TSC=...)"
[ "$HAVE_HERDR" = 1 ] && step "mods: herdr-app check${TSC:+ + types} done"

step "layout: every mod it references exists"
python3 - "$HERE" <<'PY' || fail=1
import json, os, sys
here = sys.argv[1]
mods = set()
def walk(n):
    if isinstance(n, dict):
        for k, v in n.items():
            if k == "mod" and isinstance(v, str):
                mods.add(v)
            walk(v)
    elif isinstance(n, list):
        for v in n:
            walk(v)
walk(json.load(open(os.path.join(here, "herdr/layouts/tether.json"))))
ours = set(os.listdir(os.path.join(here, "herdr/mods")))
installed = os.path.expanduser("~/.config/herdr-app/installed.json")
shipped = {k for k in json.load(open(installed)) if not k.startswith("layout:")} if os.path.exists(installed) else None
missing = sorted(m for m in mods if m not in ours | (shipped or set())) if shipped is not None else sorted(m for m in mods if m not in ours and not m.startswith(("agents", "changes", "files", "pane", "workspace")))
print("missing mods: " + ", ".join(missing) if missing else f"ok ({len(mods)} referenced)")
sys.exit(1 if missing else 0)
PY

# the check regenerates these editor aids; they are git-ignored
find "$HERE/herdr/mods" \( -name gpui-kit.d.ts -o -name jsconfig.json \) -delete
[ "$fail" = 0 ] && echo "ALL GOOD" || { echo "SOMETHING FAILED"; exit 1; }
