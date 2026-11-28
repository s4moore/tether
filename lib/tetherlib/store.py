"""Stored links (which terminal runs which session) and finding agent sessions and processes on this machine."""

import fcntl
import glob
import json
import os
import re
import shutil
import subprocess
from datetime import datetime

from tetherlib.core import TetherError, UUID_RE, env, now, parse_opts, say, state_dir

from tetherlib import herdr


class State:
    """spaces.json under an exclusive lock; read-modify-write is atomic.

    spaces[space_id] = {name, terminals: {address: link}, agents: {agent: link}}
    `agents` is the older per-space format; such a link is adopted by the next terminal in that
    space that runs that agent."""

    def __enter__(self):
        os.makedirs(state_dir(), exist_ok=True)
        self.path = os.path.join(state_dir(), "spaces.json")
        self.lock = open(os.path.join(state_dir(), "lock"), "w")
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        try:
            with open(self.path) as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = {}
        except json.JSONDecodeError:
            backup = f"{self.path}.corrupt-{int(datetime.now().timestamp())}"
            os.replace(self.path, backup)
            say(f"spaces.json was invalid; kept as {backup}")
            self.data = {}
        self.old_format = bool(self.data) and not any("terminals" in s for s in self.data.values())
        return self

    def save(self):
        if self.old_format and os.path.exists(self.path):
            keep = self.path + ".v1-backup"
            if not os.path.exists(keep):
                shutil.copy2(self.path, keep)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def __exit__(self, *exc):
        self.lock.close()


def all_links(data):
    """(space_id, space, address-or-None, link) for every stored link."""
    for sid, sp in data.items():
        for addr, e in sp.get("terminals", {}).items():
            yield sid, sp, addr, e
        for agent, e in sp.get("agents", {}).items():
            yield sid, sp, None, dict(e, agent=agent)


def last_used(sp):
    """When the space was last launched in or switched to (`touch`), newest of both."""
    stamps = [e.get("last_used", "") for e in sp.get("terminals", {}).values()]
    stamps += [e.get("last_used", "") for e in sp.get("agents", {}).values()]
    stamps.append(sp.get("accessed", ""))
    return max(stamps, default="")


def _started(pid):
    r = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True)
    return r.stdout.strip()


def _running(entry, agent):
    """Is the process that last launched this link still alive and still the same one?
    Compares the process start time, so a recycled pid is not mistaken for it."""
    pid, started = entry.get("pid"), entry.get("pid_started")
    if not pid or not started or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return _started(pid) == started and agent in r.stdout


def running_link(data, session, agent):
    """Any stored link to this session whose launcher is still alive."""
    for _, _, _, e in all_links(data):
        if e.get("session") == session and _running(e, agent):
            return e
    return None


def _claude_home():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def _copilot_home():
    return os.environ.get("COPILOT_HOME") or os.path.expanduser("~/.copilot")


def session_exists(agent, session):
    if agent == "claude":
        return bool(glob.glob(os.path.join(_claude_home(), "projects", "*", f"{session}.jsonl")))
    return os.path.isdir(os.path.join(_copilot_home(), "session-state", session))


def _newest(paths):
    paths = [p for p in paths if os.path.exists(p)]
    return max(paths, key=os.path.getmtime) if paths else None


def ancestors():
    """Parent pids of this process, nearest first."""
    pids, pid = [], os.getppid()
    while pid and pid != 1 and pid not in pids:
        pids.append(pid)
        r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
        try:
            pid = int(r.stdout.strip())
        except ValueError:
            break
    return pids


def ancestor_pids():
    return set(ancestors())


def _is_agent_command(command, agent):
    """Is this process command line the agent itself (`claude ...`, `node /x/copilot ...`), as
    opposed to a shell that merely has the word in its arguments?"""
    parts = command.split()
    if not parts:
        return False
    names = [os.path.basename(parts[0])]
    if names[0] in ("node", "bun", "deno") and len(parts) > 1:
        names.append(os.path.basename(parts[1]).removesuffix(".js"))
    return agent in names


def agent_process(agent):
    """(pid, start time) of the agent process this command is running under, else (None, None)."""
    for pid in ancestors():
        r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
        if _is_agent_command(r.stdout, agent):
            return pid, _started(pid)
    return None, None


def current_session(agent):
    """The session this command is running inside of (exact, not a guess), or None.
    claude: Claude Code puts its session id in $CLAUDE_CODE_SESSION_ID.
    copilot: the live session's folder holds inuse.<pid>.lock for the copilot process, which
    is one of our ancestors."""
    if agent == "claude":
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
        return sid if sid and session_exists("claude", sid) else None
    mine = ancestor_pids()
    for lock in glob.glob(os.path.join(_copilot_home(), "session-state", "*", "inuse.*.lock")):
        try:
            if int(os.path.basename(lock).split(".")[1]) in mine:
                return os.path.basename(os.path.dirname(lock))
        except (ValueError, IndexError):
            pass
    return None


def guess_session(agent):
    """Most recently written session: for claude, in this folder; for copilot, anywhere."""
    if agent == "claude":
        enc = "".join(c if c.isalnum() else "-" for c in os.path.realpath(os.getcwd()))
        hit = _newest(glob.glob(os.path.join(_claude_home(), "projects", enc, "*.jsonl")))
        return os.path.basename(hit)[:-len(".jsonl")] if hit else None
    hit = _newest(glob.glob(os.path.join(_copilot_home(), "session-state", "*")))
    return os.path.basename(hit) if hit else None


def session_cwd(agent, session):
    """Folder a claude session was started in (read from its transcript), else the current one."""
    if agent == "claude":
        for f in glob.glob(os.path.join(_claude_home(), "projects", "*", f"{session}.jsonl")):
            try:
                with open(f) as fh:
                    for _, line in zip(range(50), fh):
                        c = json.loads(line).get("cwd")
                        if c and os.path.isdir(c):
                            return c
            except (OSError, ValueError):
                pass
    return os.getcwd()


def running_agents():
    """[{agent, pid, terminal, session, cwd}] for every Claude / Copilot process that runs inside a
    herdr terminal and whose session can be read: Claude from ~/.claude/sessions/<pid>.json,
    Copilot from its session folder's inuse.<pid>.lock."""
    found = []
    out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        pid, base = int(parts[0]), os.path.basename(parts[1])
        agent = "claude" if base == "claude" else "copilot" if base == "copilot" else None
        if not agent:
            continue
        term = herdr._terminal_of_pid(pid)
        if not term:
            continue
        session = cwd = None
        if agent == "claude":
            try:
                with open(os.path.join(_claude_home(), "sessions", f"{pid}.json")) as f:
                    info = json.load(f)
                session, cwd = info.get("sessionId"), info.get("cwd")
            except (OSError, ValueError):
                pass
        else:
            locks = glob.glob(os.path.join(_copilot_home(), "session-state", "*", f"inuse.{pid}.lock"))
            if locks:
                lock = max(locks, key=lambda p: os.path.getmtime(p))
                session = os.path.basename(os.path.dirname(lock))
                try:
                    with open(os.path.join(os.path.dirname(lock), "workspace.yaml")) as f:
                        m = re.search(r"^cwd:\s*(.+)$", f.read(), re.M)
                    cwd = m.group(1).strip().strip("'\"") if m else None
                except OSError:
                    pass
        if session and UUID_RE.match(session):
            found.append({"agent": agent, "pid": pid, "terminal": term, "session": session, "cwd": cwd})
    return found


def sync_names(data, live, only=None):
    """Copy the current names of live spaces and tabs into the links tether keeps for them, so a rename
    made in Herdr is not lost: a closed space is shown, and reopened, under the name it last had. Only
    spaces that already have links; returns True if anything changed."""
    changed = False
    for s in live:
        rec = data.get(s.get("id"))
        if (only and s.get("id") != only) or not isinstance(rec, dict) or not (rec.get("terminals") or rec.get("agents")):
            continue
        if s.get("name") and rec.get("name") != s["name"]:
            rec["name"], changed = s["name"], True
        tabs = {t.get("id"): t.get("name") for t in s.get("tabs", [])}
        for term in (rec.get("terminals") or {}).values():
            if tabs.get(term.get("tab")) and term.get("tab_name") != tabs[term["tab"]]:
                term["tab_name"], changed = tabs[term["tab"]], True
    return changed


def adopt(space_id=None):
    """Link agents running in herdr terminals that tether does not know yet (never changes a link
    that exists), and keep the stored space and tab names current. Returns [(space name, tab name,
    agent, session)] for what it linked."""
    live = herdr.herdr_ls()
    adopted = []
    with State() as st:
        renamed = sync_names(st.data, live, space_id)
        for r in running_agents():
            hit = herdr.find_terminal(live, r["terminal"])
            if not hit:
                continue
            sp, tab, t = hit
            if space_id and sp["id"] != space_id:
                continue
            rec = st.data.setdefault(sp["id"], {})
            terms = rec.setdefault("terminals", {})
            rec.setdefault("agents", {})
            if t["terminal"] in terms:
                continue
            if any(e.get("session") == r["session"] and e.get("agent") == r["agent"]
                   for _, _, _, e in all_links(st.data)):
                continue  # this session is linked somewhere already
            rec["name"] = sp.get("name") or rec.get("name") or sp["id"]
            terms[t["terminal"]] = {
                "agent": r["agent"], "session": r["session"],
                "cwd": r["cwd"] if r["cwd"] and os.path.isdir(r["cwd"]) else os.getcwd(),
                "tab": tab.get("id") or "", "tab_name": tab.get("name") or "",
                "created": now(), "last_used": now(), "pid": r["pid"], "pid_started": _started(r["pid"])}
            adopted.append((rec["name"], tab.get("name") or "", r["agent"], r["session"]))
        if adopted or renamed:
            st.save()
    return adopted


def adopt_cmd(args):
    _, vals, _ = parse_opts(args, flags=("--json",), valued=("--id",))
    herdr.ensure_app()
    got = adopt(vals.get("--id"))
    if "--json" in args:
        print(json.dumps([{"space": a, "tab": b, "agent": c, "session": d} for a, b, c, d in got]))
        return
    for space, tab, agent, session in got:
        print(f"linked {agent} {session[:8]} in {space!r} [{tab or '?'}]")
    print(f"{len(got)} agent(s) linked" if got else "nothing to link: every running agent is linked already")


def safe_adopt(space_id=None):
    """adopt() for background use: never raises, does nothing when Herdr is not running or
    TETHER_NO_ADOPT=1. Returns what it linked."""
    if env("NO_ADOPT"):
        return []
    try:
        return adopt(space_id)
    except (TetherError, OSError, subprocess.SubprocessError, ValueError):
        return []


def session_paths(agent, session, home=None):
    """Everything on disk that belongs to one session (only ever keyed by its uuid), under the
    agent's own folder or, with `home`, under a folder laid out the same way (a backup)."""
    if not UUID_RE.match(session or ""):
        return []
    if agent == "claude":
        home = home or _claude_home()
        found = glob.glob(os.path.join(home, "projects", "*", session + ".jsonl"))
        found += glob.glob(os.path.join(home, "projects", "*", session))
        found += [os.path.join(home, "file-history", session), os.path.join(home, "session-env", session)]
    else:
        found = [os.path.join(home or _copilot_home(), "session-state", session)]
    return [f for f in found if os.path.lexists(f)]


def _remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
