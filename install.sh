#!/usr/bin/env bash
# Installs (or removes) tether: the tool, its Herdr mods and layout, and its Claude skill.
#
#   ./install.sh               install or upgrade (safe to re-run)
#   ./install.sh --uninstall   remove what this script installed (your links and backups are kept)
#   ./install.sh --help
#
# Normally run through get.sh (the curl one-liner in the README), which downloads a release, checks its
# checksum and runs this script from the unpacked folder (TETHER_SRC). It also works from a git clone.
# Overrides (mostly for testing): HERDR_DIR, BIN_DIR, LIB_DIR, SKILL_ROOT, AGENTS_SKILLS, BACKUP_DIR (where THIS script
# keeps copies of what it replaces), TETHER_STATE_DIR, HERDR_ALLOW_RUNNING=1, INSTALL_HERDR=yes,
# TETHER_AUTO_BACKUP=yes|no, CLAUDE_RETENTION_DAYS=<days>.
set -euo pipefail

HERE="${TETHER_SRC:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}"
HERDR_DIR="${HERDR_DIR:-$HOME/.config/herdr-app}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
LIB_DIR="${LIB_DIR:-$HOME/.local/lib}"   # the tetherlib package, next to bin/: the tool finds it as ../lib
SKILL_ROOT="${SKILL_ROOT:-$HOME/.claude/skills}"
AGENTS_SKILLS="${AGENTS_SKILLS:-$HOME/.agents/skills}"   # where Copilot CLI looks for personal skills
STATE_DIR="${TETHER_STATE_DIR:-$HOME/.local/state/tether}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_ROOT="$HOME/.tether-backups"
BACKUP_DIR="${BACKUP_DIR:-$BACKUP_ROOT/$STAMP}"
LAYOUT_NAME="tether"
HERDR_TESTED="0.1.16"   # the Herdr version the mods were last checked against
HERDR_MIN="0.1.15"      # the oldest version their cloned originals come from
HERDR_INSTALL_URL="https://releases.herdr.dev/app/install.sh"
KEEP_BACKUPS=5

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }
warn() { echo "warning: $*" >&2; }

MODE=install
case "${1:-}" in
  "") ;;
  --uninstall) MODE=uninstall ;;
  -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "unknown option $1 (try --help)" ;;
esac

# Questions go to the terminal, never to stdin (which is the script itself under `curl | sh`).
have_tty() { [ -r /dev/tty ] && ( : </dev/tty ) 2>/dev/null; }
# ask "question" ENV_VALUE default(y|n): the environment value wins; else ask on the terminal; with no
# terminal the answer is no (nobody to ask: never opt in on someone's behalf).
ask() {
  local answer="$2"
  if [ -z "$answer" ]; then
    have_tty || return 1
    local hint="[y/N]"; [ "$3" = y ] && hint="[Y/n]"
    printf '%s %s ' "$1" "$hint" >/dev/tty
    read -r answer </dev/tty || answer=""
    [ -z "$answer" ] && answer="$3"
  fi
  case "$answer" in y|Y|yes|YES|true|1) return 0 ;; *) return 1 ;; esac
}

python3 -c 'import sys' >/dev/null 2>&1 || die "python3 is not usable. On a new Mac run 'xcode-select --install', accept the prompt, then run this again. (tether needs Python 3.9+, standard library only.)"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "Python 3.9+ required, found $(python3 --version 2>&1)"

if pgrep -x Herdr >/dev/null 2>&1 && [ -z "${HERDR_ALLOW_RUNNING:-}" ]; then
  die "Herdr is running and rewrites shared.json when it quits. Quit it first, then run this again (or set HERDR_ALLOW_RUNNING=1)."
fi

# Back up anything we are about to replace, keeping its path relative to $HOME. If a second argument (the
# new content) is given and is identical to what is there, nothing is copied.
backup() {
  local target="$1"
  [ -e "$target" ] || return 0
  [ -n "${2:-}" ] && diff -rq -x __pycache__ "$2" "$target" >/dev/null 2>&1 && return 0
  local rel="${target#"$HOME"/}"
  mkdir -p "$BACKUP_DIR/$(dirname "$rel")"
  [ -e "$BACKUP_DIR/$rel" ] || cp -R "$target" "$BACKUP_DIR/$rel"
}

# Put $1 back as it was before this run: the copy in the backup folder, or nothing if there was none.
restore() {
  local target="$1" rel="${1#"$HOME"/}"
  rm -rf "$target"
  [ -e "$BACKUP_DIR/$rel" ] && { mkdir -p "$(dirname "$target")"; cp -R "$BACKUP_DIR/$rel" "$target"; }
  return 0
}

# Reads shared.json, sets or removes the "layout" key. Refuses (exit 3) when the file is not valid JSON.
set_layout() {  # set_layout <path> <layout name | "-" to remove>
  python3 - "$1" "$2" <<'PY'
import json, os, sys
path, layout = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    try:
        with open(path) as f:
            data = json.load(f)
    except ValueError:
        sys.exit(3)
    if not isinstance(data, dict):
        sys.exit(3)
if layout == "-":
    if data.get("layout") != "tether":
        sys.exit(0)
    data.pop("layout")
else:
    data["layout"] = layout
with open(path, "w") as f:
    json.dump(data, f, separators=(",", ":"))
PY
}

# ---------------------------------------------------------------------------------------- uninstall
if [ "$MODE" = uninstall ]; then
  say "removing tether (your links and conversation backups are kept)"
  mods=()
  for d in "$HERDR_DIR"/mods/tether-*/; do [ -d "$d" ] && mods+=("$(basename "$d")"); done
  if [ -f "$HERDR_DIR/shared.json" ]; then
    backup "$HERDR_DIR/shared.json"
    set_layout "$HERDR_DIR/shared.json" - || warn "shared.json is not valid JSON; left alone (remove the \"layout\" line yourself if it says tether)"
  fi
  backup "$HERDR_DIR/layouts/$LAYOUT_NAME.json"; rm -f "$HERDR_DIR/layouts/$LAYOUT_NAME.json"
  for m in ${mods[@]+"${mods[@]}"}; do backup "$HERDR_DIR/mods/$m"; rm -rf "$HERDR_DIR/mods/$m"; done
  backup "$BIN_DIR/tether"; rm -f "$BIN_DIR/tether"
  backup "$LIB_DIR/tetherlib"; rm -rf "${LIB_DIR:?}/tetherlib"
  for skill in tether tether-context; do
    backup "$SKILL_ROOT/$skill"; rm -rf "${SKILL_ROOT:?}/$skill"
    if [ -L "$AGENTS_SKILLS/$skill" ]; then rm -f "$AGENTS_SKILLS/$skill"; fi
  done
  echo "Removed: $BIN_DIR/tether, $LIB_DIR/tetherlib, the skills, ${#mods[@]} mod(s) and the layout (copies are in $BACKUP_DIR)."
  echo "Kept: your space links ($STATE_DIR) and conversation backups (~/.local/share/tether/backups)."
  echo "Delete them too only if you will not use tether again: rm -rf \"$STATE_DIR\" ~/.local/share/tether"
  echo "If you let the installer set cleanupPeriodDays in ~/.claude/settings.json, that setting stays."
  exit 0
fi

# ---------------------------------------------------------------------------------------- install
for f in bin/tether lib/tetherlib/cli.py skill/SKILL.md herdr/layouts/$LAYOUT_NAME.json; do
  [ -f "$HERE/$f" ] || die "$HERE/$f not found. Run this from the tether download (or a clone), or use get.sh."
done
VERSION="$(cat "$HERE/VERSION" 2>/dev/null || echo unknown)"

if ! command -v herdr-app >/dev/null && [ ! -x "$BIN_DIR/herdr-app" ] && [ ! -d "$HERDR_DIR/mods" ]; then
  echo "Herdr is not installed. tether needs it (spaces, 'herdr-app', 'hc')."
  echo "The official installer is:  curl -fsSL $HERDR_INSTALL_URL | sh   (see https://herdr.dev/download)"
  if ask "Run it now?" "${INSTALL_HERDR:-}" n; then   # INSTALL_HERDR=yes opts in without a prompt
    command -v curl >/dev/null || die "curl not found; install Herdr yourself, then run this again"
    curl -fsSL "$HERDR_INSTALL_URL" | sh
    die "Herdr is installed. Launch it once (so it creates its config folder), quit it, then run this again."
  fi
  die "Install Herdr, launch it once, quit it, then run this again."
fi
[ -d "$HERDR_DIR/mods" ] || die "$HERDR_DIR/mods does not exist. Launch Herdr once, quit it, then run this again."

# Look before touching anything: a broken shared.json would otherwise leave a half-install.
if [ -s "$HERDR_DIR/shared.json" ]; then
  python3 - "$HERDR_DIR/shared.json" <<'PY' || die "$HERDR_DIR/shared.json is not valid JSON. Fix or move it, then run this again (nothing was changed)."
import json, sys
if not isinstance(json.load(open(sys.argv[1])), dict):
    raise ValueError
PY
fi

# Herdr version: the mods are copies of Herdr's own mods from the versions below.
HERDR_VERSION=""
if command -v herdr-app >/dev/null; then
  HERDR_VERSION="$(herdr-app --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
fi
if [ -z "$HERDR_VERSION" ]; then
  warn "could not read the Herdr version; tether was tested with Herdr $HERDR_TESTED"
elif python3 -c 'import sys; v=lambda s: tuple(map(int, s.split("."))); sys.exit(0 if v(sys.argv[1]) >= v(sys.argv[2]) else 1)' "$HERDR_VERSION" "$HERDR_MIN"; then
  [ "$HERDR_VERSION" = "$HERDR_TESTED" ] || echo "note: tether was tested with Herdr $HERDR_TESTED; you have $HERDR_VERSION. If the sidebar looks wrong afterwards, ./install.sh --uninstall puts Herdr's own back."
else
  warn "Herdr $HERDR_VERSION is older than $HERDR_MIN, which the tether mods are based on; they may not load. Update Herdr first."
fi

say "tether $VERSION -> $BIN_DIR (the code is the tetherlib package in $LIB_DIR/tetherlib)"
mkdir -p "$BIN_DIR" "$LIB_DIR"
# The package first, so the new entry point never runs against the old modules. It is replaced whole: a module that was renamed or
# removed must not stay behind.
backup "$LIB_DIR/tetherlib" "$HERE/lib/tetherlib"
rm -rf "${LIB_DIR:?}/tetherlib"
cp -R "$HERE/lib/tetherlib" "$LIB_DIR/tetherlib"
find "$LIB_DIR/tetherlib" -name __pycache__ -type d -prune -exec rm -rf {} +
backup "$BIN_DIR/tether" "$HERE/bin/tether"
cp "$HERE/bin/tether" "$BIN_DIR/tether"
chmod +x "$BIN_DIR/tether"

# Two skills: tether (the commands) and tether-context (looking up and remembering facts about your environment).
for pair in "skill:tether" "skill-context:tether-context"; do
  src_dir="${pair%%:*}"; skill="${pair##*:}"
  [ -f "$HERE/$src_dir/SKILL.md" ] || continue
  say "Claude skill -> $SKILL_ROOT/$skill"
  mkdir -p "$SKILL_ROOT/$skill"
  backup "$SKILL_ROOT/$skill/SKILL.md" "$HERE/$src_dir/SKILL.md"
  cp "$HERE/$src_dir/SKILL.md" "$SKILL_ROOT/$skill/SKILL.md"
  # Copilot CLI does not read ~/.claude/skills; it reads ~/.agents/skills. Link the skill there so both
  # agents share one copy.
  mkdir -p "$AGENTS_SKILLS"
  if [ ! -e "$AGENTS_SKILLS/$skill" ] || [ -L "$AGENTS_SKILLS/$skill" ]; then
    say "Copilot skill -> $AGENTS_SKILLS/$skill (link to $SKILL_ROOT/$skill)"
    ln -sfn "$SKILL_ROOT/$skill" "$AGENTS_SKILLS/$skill"
  else
    warn "$AGENTS_SKILLS/$skill exists and is not a link; left alone, so Copilot may have a different copy"
  fi
done

say "Herdr mods -> $HERDR_DIR/mods"
MODS=()
for src in "$HERE"/herdr/mods/*/; do
  name="$(basename "$src")"
  MODS+=("$name")
  backup "$HERDR_DIR/mods/$name"
  mkdir -p "$HERDR_DIR/mods/$name"
  cp -R "$src". "$HERDR_DIR/mods/$name/"
done

say "layout -> $HERDR_DIR/layouts/$LAYOUT_NAME.json"
mkdir -p "$HERDR_DIR/layouts"
backup "$HERDR_DIR/layouts/$LAYOUT_NAME.json" "$HERE/herdr/layouts/$LAYOUT_NAME.json"
cp "$HERE/herdr/layouts/$LAYOUT_NAME.json" "$HERDR_DIR/layouts/$LAYOUT_NAME.json"

say "selecting layout '$LAYOUT_NAME' in shared.json (only the \"layout\" key is touched)"
backup "$HERDR_DIR/shared.json"
set_layout "$HERDR_DIR/shared.json" "$LAYOUT_NAME"

# Let Herdr load every mod; if one does not load, put the previous mods, layout and setting back.
if command -v herdr-app >/dev/null; then
  say "herdr-app check (also regenerates the git-ignored gpui-kit.d.ts / jsconfig.json in each mod)"
  failed=()
  for name in "${MODS[@]}"; do
    out="$(herdr-app check "$HERDR_DIR/mods/$name" 2>&1 || true)"
    printf '%s\n' "$out" | grep -q '^check passed' || failed+=("$name: $(printf '%s\n' "$out" | tail -1)")
  done
  if [ "${#failed[@]}" -gt 0 ]; then
    printf 'mod check failed: %s\n' "${failed[@]}" >&2
    warn "putting your previous Herdr mods, layout and settings back"
    for name in "${MODS[@]}"; do restore "$HERDR_DIR/mods/$name"; done
    restore "$HERDR_DIR/layouts/$LAYOUT_NAME.json"
    restore "$HERDR_DIR/shared.json"
    die "tether's Herdr mods do not load with Herdr ${HERDR_VERSION:-(unknown version)}; the tether command itself is installed. Please report this (docs/troubleshooting.md)."
  fi
else
  warn "herdr-app is not on PATH; skipped the mod checks"
fi

# ---- conversation backups and optional Claude retention ----
mkdir -p "$STATE_DIR"
if [ -e "$STATE_DIR/config.json" ]; then
  say "keeping your existing $STATE_DIR/config.json"
else
  auto=false
  ask "Back up your linked conversations in the background (at most every 6 h) into ~/.local/share/tether/backups? They are plain copies of your transcripts." "${TETHER_AUTO_BACKUP:-}" y && auto=true
  python3 - "$STATE_DIR/config.json" "$auto" <<'PY'
import json, sys
with open(sys.argv[1], "w") as f:
    json.dump({"auto": sys.argv[2] == "true"}, f, indent=2)
PY
  say "automatic backups: $auto (run 'tether backup' any time)"
fi

SETTINGS="$HOME/.claude/settings.json"
if python3 - "$SETTINGS" <<'PY' 2>/dev/null
import json, os, sys
sys.exit(0 if not os.path.exists(sys.argv[1]) or "cleanupPeriodDays" not in json.load(open(sys.argv[1])) else 1)
PY
then
  # Claude deletes transcripts after 30 days by default, so a space left alone for a month loses its history.
  if ask "Keep Claude Code transcripts for ${CLAUDE_RETENTION_DAYS:-365} days instead of the default 30? (this edits $SETTINGS; an admin-managed setting would override it)" "${CLAUDE_RETENTION_DAYS:+y}" n; then
    mkdir -p "$HOME/.claude"
    backup "$SETTINGS"
    if python3 - "$SETTINGS" "${CLAUDE_RETENTION_DAYS:-365}" <<'PY'
import json, os, sys
path, days = sys.argv[1], int(sys.argv[2])
data = json.load(open(path)) if os.path.exists(path) else {}
data["cleanupPeriodDays"] = days
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
    then say "set cleanupPeriodDays=${CLAUDE_RETENTION_DAYS:-365} in $SETTINGS"
    else warn "$SETTINGS is not valid JSON; left alone"; fi
  fi
fi

# Keep only the newest few copies of replaced files.
if [ "$(dirname "$BACKUP_DIR")" = "$BACKUP_ROOT" ] && [ -d "$BACKUP_ROOT" ]; then
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -r | tail -n +$((KEEP_BACKUPS + 1)) | while read -r old; do rm -rf "$old"; done
fi

# What tether needs at run time (it also looks in ~/.local/bin, /opt/homebrew/bin and /usr/local/bin).
for tool in herdr-app hc claude; do
  command -v "$tool" >/dev/null || [ -x "$BIN_DIR/$tool" ] || warn "'$tool' not found; tether needs it (docs/install.md, Requirements)"
done
command -v copilot >/dev/null || echo "note: 'copilot' not found; only 'tether copilot' needs it"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH, so 'tether' will not be found (and restored tabs cannot run it). Add it:"
     echo "    echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc   # then open a new terminal" ;;
esac

[ -d "$BACKUP_DIR" ] && echo "copies of what was replaced: $BACKUP_DIR"
cat <<MSG

Done: tether $VERSION. Start Herdr, then in a space run 'tether status'.
Your space links live in $STATE_DIR. Conversation backups: 'tether backup'
(automatic if you said yes). Uninstall: ./install.sh --uninstall. See README.md.
MSG
