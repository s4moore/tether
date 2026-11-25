#!/usr/bin/env bash
# This repo is the source of truth; install.sh pushes it to the machine.
#   ./sync.sh meta   regenerate docs/tether-help.txt (the test-suite fails when it is stale)
#   ./sync.sh upstream  show how each cloned mod/layout differs from the shipped one it was cloned from
#                    (run after upgrading herdr-app to see what upstream changed under you)
#   ./sync.sh shipped   verify no shipped Herdr mod was edited in place (also part of `upstream`)
#   ./sync.sh pull   copy edits made directly on this machine back into the repo (then meta).
#                    Refuses if the repo has uncommitted changes, so it can never overwrite work.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERDR_DIR="${HERDR_DIR:-$HOME/.config/herdr-app}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
SKILL_ROOT="${SKILL_ROOT:-$HOME/.claude/skills}"

meta() {
  "$HERE/bin/tether" --help > "$HERE/docs/tether-help.txt" 2>&1 || true
  echo "regenerated docs/tether-help.txt"
}

pull() {
  if [ -n "$(git -C "$HERE" status --porcelain)" ]; then
    echo "error: the repo has uncommitted changes; commit or stash them first (pull would overwrite files)" >&2
    exit 1
  fi
  cp "$BIN_DIR/tether" "$HERE/bin/tether"
  cp "$SKILL_ROOT/tether/SKILL.md" "$HERE/skill/SKILL.md"
  cp "$HERDR_DIR/layouts/tether.json" "$HERE/herdr/layouts/tether.json"
  for src in "$HERE"/herdr/mods/*/; do
    name="$(basename "$src")"
    for f in main.js mod.json trash.svg; do
      [ -f "$HERDR_DIR/mods/$name/$f" ] && cp "$HERDR_DIR/mods/$name/$f" "$src$f"
    done
  done
  meta
  echo "pulled; review with: git -C '$HERE' diff --stat"
}

upstream() {
  # clone -> shipped original (the clone's mod.json / layout also records the version it was cloned from)
  for pair in tether-spaces:spaces tether-menu:menu tether-new-menu:new-menu tether-tabs:tabs; do
    clone="${pair%%:*}"; orig="${pair##*:}"
    echo "=== mod $clone vs shipped $orig (cloned_from: $(python3 -c "import json;print(json.load(open('$HERDR_DIR/mods/$clone/mod.json')).get('cloned_from'))" 2>/dev/null))"
    diff "$HERDR_DIR/mods/$orig/main.js" "$HERDR_DIR/mods/$clone/main.js" || true
  done
  echo "=== layout tether vs shipped main (key order differs, so compared as sorted JSON)"
  diff <(python3 -m json.tool --sort-keys "$HERDR_DIR/layouts/main.json") <(python3 -m json.tool --sort-keys "$HERDR_DIR/layouts/tether.json") || true
  echo "herdr-app version now: $(herdr-app --version)"
  shipped
}

# Herdr records a hash (FNV-1a 64 bit) of every file it ships. A shipped mod edited in place (instead of
# cloned) shows up here, and is at risk of being overwritten by a Herdr upgrade.
shipped() {
  python3 - "$HERDR_DIR" <<'PY'
import json, os, sys
home = sys.argv[1]
def fnv(data):
    h = 0xcbf29ce484222325
    for b in data:
        h = ((h ^ b) * 0x100000001b3) % 2**64
    return f"{h:016x}"
installed = json.load(open(os.path.join(home, "installed.json")))
changed, checked = [], 0
for name, files in installed.items():
    if name.startswith("layout:"):
        continue
    for f, h in files.items():
        path = os.path.join(home, "mods", name, f)
        if not os.path.exists(path):
            changed.append(f"{name}/{f} (missing)")
        elif fnv(open(path, "rb").read()) != h:
            changed.append(f"{name}/{f} (modified)")
        else:
            checked += 1
print(f"=== shipped mods: {checked} file(s) match Herdr's hashes" + (f"; DIFFERENT: {', '.join(changed)}" if changed else "; none edited in place"))
PY
}

case "${1:-}" in
  upstream) upstream ;;
  shipped) shipped ;;
  meta) meta ;;
  pull) pull ;;
  *) echo "usage: $0 meta|pull|upstream|shipped" >&2; exit 2 ;;
esac
