#!/usr/bin/env bash
# After upgrading herdr-app: show how each cloned mod and the layout differ from the Herdr originals they were cloned from (what
# upstream changed under us), and check that no mod Herdr ships was edited in place.
#   scripts/herdr-drift.sh            both
#   scripts/herdr-drift.sh --shipped  only the check for shipped mods edited in place
set -euo pipefail
HERDR_DIR="${HERDR_DIR:-$HOME/.config/herdr-app}"

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

if [ "${1:-}" = "--shipped" ]; then
  shipped
elif [ -z "${1:-}" ]; then
  upstream
else
  echo "usage: $0 [--shipped]" >&2; exit 2
fi
