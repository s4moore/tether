"""Constants, errors and the small helpers every other module uses: the TETHER_* environment, messages, config.json, option parsing."""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from datetime import timezone


__version__ = "0.5.1"  # keep in step with the VERSION file (tests check it)

AGENTS = ("claude", "copilot")

# If you pass one of these yourself you are choosing the session: tether steps aside.
EXPLICIT_SESSION_FLAGS = {
    "claude": {"--resume", "-r", "--continue", "-c", "--session-id", "--fork-session"},
    "copilot": {"--resume", "-r", "--continue", "--session-id", "--connect"},
}

COMMANDS = {"claude", "copilot", "read", "context", "continue", "swap", "search", "stats", "resume", "link", "list", "open", "delete", "touch", "status", "reset",
            "backup", "restore", "export", "import", "help", "-h", "--help"}


class TetherError(Exception):
    pass


def env(name):
    """The TETHER_<name> environment variable, or None."""
    return os.environ.get("TETHER_" + name)


def say(msg):
    print(f"tether: {msg}", file=sys.stderr)


TOOL_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")


def find_tool(name):
    """A program on PATH, else in the usual install folders. The herdr app (and so the mods it
    runs) has a minimal PATH without ~/.local/bin, which is where herdr-app and hc live."""
    found = shutil.which(name)
    if found:
        return found
    for d in TOOL_DIRS:
        cand = os.path.join(os.path.expanduser(d), name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ago(iso):
    try:
        secs = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return "unknown"
    if secs < 90:
        return "just now"
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if secs >= n:
            return f"{int(secs // n)} {unit} ago"
    return "just now"


def state_dir():
    """Where links are stored: $TETHER_STATE_DIR, else ~/.local/state/tether."""
    return env("STATE_DIR") or os.path.expanduser("~/.local/state/tether")


def pick(prompt, items):
    """Arrow-key menu. items: [(label, value)] or [(label, value, pinned)]; pinned entries stay
    visible whatever you type and are drawn in green. Up/down (or ctrl-p/ctrl-n) move, enter
    selects, typing filters, esc / ctrl-c cancels. Returns the value, or None."""
    import select
    import termios
    import tty
    items = [(it + (False,))[:3] for it in items]
    if not items:
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise TetherError("need a terminal to pick from a list; pass a name")
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    query, sel, drawn = "", 0, 0
    out = sys.stdout

    def visible():
        q = query.lower()
        return [it for it in items if it[2] or q in it[0].lower()]

    def first_match():
        """While filtering, start on the first real match, not the pinned entries."""
        vis = visible()
        if query:
            for i, it in enumerate(vis):
                if not it[2]:
                    return i
        return 0

    def draw():
        nonlocal sel, drawn
        vis = visible()
        sel = max(0, min(sel, len(vis) - 1))
        room = max(5, shutil.get_terminal_size().lines - 4)
        top = max(0, min(sel - room // 2, len(vis) - room))
        window = vis[top:top + room]
        lines = [f"\x1b[1m{prompt}\x1b[0m {query}\x1b[2m"
                 f"{'' if query else ' (type to filter)'}   up/down, enter, esc\x1b[0m"]
        for i, (label, _, pinned) in enumerate(window, start=top):
            text = f"\x1b[1;32m{label}\x1b[0m" if pinned else label
            lines.append(f"\x1b[7m> {text}\x1b[0m" if i == sel else f"  {text}")
        hidden = len(vis) - len(window)
        if hidden > 0:
            lines.append(f"\x1b[2m  ... {hidden} more (keep going up/down, or type to filter)\x1b[0m")
        if drawn:
            out.write(f"\x1b[{drawn}A")
        out.write("\r\x1b[J" + "\n".join(lines) + "\n")
        out.flush()
        drawn = len(lines)

    def clear():
        if drawn:
            out.write(f"\x1b[{drawn}A\r\x1b[J")
            out.flush()

    try:
        tty.setcbreak(fd)
        out.write("\x1b[?25l")
        draw()
        while True:
            ch = os.read(fd, 1)
            if ch == b"\x1b":
                if select.select([fd], [], [], 0.05)[0]:
                    seq = os.read(fd, 2)
                    if seq in (b"[A", b"OA"):
                        sel -= 1
                    elif seq in (b"[B", b"OB"):
                        sel += 1
                    # other escape sequences (left/right...) are ignored
                else:
                    clear()
                    return None
            elif ch in (b"\r", b"\n"):
                vis = visible()
                clear()
                return vis[sel][1] if vis else None
            elif ch in (b"\x03", b"\x04"):
                clear()
                return None
            elif ch == b"\x10":
                sel -= 1
            elif ch == b"\x0e":
                sel += 1
            elif ch in (b"\x7f", b"\x08"):
                query = query[:-1]
                sel = first_match()
            elif ch and ch[0] >= 32:
                try:
                    query += (ch + (os.read(fd, 3) if ch[0] >= 0xC0 else b"")).decode()
                except UnicodeDecodeError:
                    pass
                sel = first_match()
            draw()
    finally:
        out.write("\x1b[?25h")
        out.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _json_file(path, default):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


def config_path():
    return os.path.join(state_dir(), "config.json")


def load_config():
    try:
        with open(config_path()) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(state_dir(), exist_ok=True)
    tmp = config_path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, config_path())


def parse_opts(args, flags=(), valued=()):
    """(set of flags given, {option: value}, plain words). Unknown options are an error."""
    found, vals, words, i = set(), {}, [], 0
    while i < len(args):
        a = args[i]
        if a in flags:
            found.add(a)
            i += 1
        elif a in valued:
            if i + 1 >= len(args):
                sys.exit(f"tether: {a} needs a value")
            vals[a] = args[i + 1]
            i += 2
        elif a.startswith("-") and len(a) > 1:
            sys.exit(f"tether: unknown option {a}")
        else:
            words.append(a)
            i += 1
    return found, vals, words


def _slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "space"


def unique_path(path):
    """path, or path with -2, -3 ... before the extension if that file already exists."""
    if not os.path.exists(path):
        return path
    stem, ext = (path[:-len(".tar.gz")], ".tar.gz") if path.endswith(".tar.gz") else os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem}-{n}{ext}"):
        n += 1
    return f"{stem}-{n}{ext}"
