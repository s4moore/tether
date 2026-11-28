"""Talking to the Herdr app: its spaces, terminals and commands."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import namedtuple

from tetherlib.core import TetherError, say

from tetherlib import core, store


Where = namedtuple("Where", "space_id name terminal tab tab_name")


def herdr_ls():
    """The `herdr-app ls` spaces list, or TetherError saying why not."""
    app = core.find_tool("herdr-app")
    if not app:
        raise TetherError("herdr-app not found on PATH")
    try:
        out = subprocess.run([app, "ls"], capture_output=True, text=True, timeout=5)
        return json.loads(out.stdout)["spaces"]
    except Exception as e:  # timeout, app not running, bad JSON
        raise TetherError(f"could not read `herdr-app ls` ({type(e).__name__})")


def ensure_app():
    """Make sure the herdr app is running, starting it if not (from a plain terminal).
    Inside a herdr terminal it obviously is. Everything tether does to spaces needs the app."""
    if os.environ.get("HC_TERMINAL_ID"):
        return
    try:
        herdr_ls()
        return
    except TetherError as first:
        err = first
    app = core.find_tool("herdr-app")
    if not app:
        raise err
    say("herdr-app is not running; starting it...")
    real = os.path.realpath(app)
    bundle = real.split(".app/")[0] + ".app" if ".app/" in real else None
    if sys.platform == "darwin" and bundle and os.path.isdir(bundle):
        subprocess.run(["open", bundle])
    else:
        subprocess.Popen([app], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        time.sleep(1)
        try:
            herdr_ls()
            time.sleep(1)  # let it finish restoring its spaces
            return
        except TetherError:
            continue
    raise TetherError("started herdr-app, but it did not become ready within 30 s; open it "
                      "yourself and run tether again")


def terms_of(node):
    """Every terminal dict under a tab (sub-agents included)."""
    if isinstance(node, dict):
        if isinstance(node.get("terminal"), str):
            yield node
        for v in node.values():
            yield from terms_of(v)
    elif isinstance(node, list):
        for v in node:
            yield from terms_of(v)


def find_terminal(spaces, tid):
    """(space, tab, terminal dict) for an address; exact first, then machine-prefixed."""
    for exact in (True, False):
        hits = []
        for s in spaces:
            for tab in s.get("tabs", []):
                for t in terms_of(tab):
                    a = t["terminal"]
                    if a == tid if exact else a.endswith("/" + tid):
                        hits.append((s, tab, t))
        if len({h[0]["id"] for h in hits}) > 1:
            raise TetherError(f"terminal {tid} matches more than one space; not guessing")
        if hits:
            return hits[0]
    return None


def resolve_where():
    """(Where, None), or (None, reason) if this is not a herdr-app terminal."""
    tid = os.environ.get("HC_TERMINAL_ID")
    if not tid:
        return None, "not running inside a herdr terminal (HC_TERMINAL_ID is not set)"
    try:
        hit = find_terminal(herdr_ls(), tid)
    except TetherError as e:
        return None, str(e)
    if not hit:
        return None, f"terminal {tid} is not in any herdr-app space"
    s, tab, t = hit
    return Where(s["id"], s.get("name") or "", t["terminal"], tab.get("id") or "",
                 tab.get("name") or ""), None


def herdr_do(args):
    """Run `herdr-app do <args>`; returns what it made ({space, tab, terminal}) or raises."""
    app = core.find_tool("herdr-app")
    if not app:
        raise TetherError("herdr-app not found on PATH")
    r = subprocess.run([app, "do"] + args, capture_output=True, text=True, timeout=15)
    try:
        made = json.loads(r.stdout)
    except ValueError:
        made = {"error": (r.stderr or r.stdout).strip()[:200] or "no output"}
    if r.returncode or "error" in made:
        raise TetherError(f"herdr-app do {args[0]} failed: {made.get('error')}")
    return made


def terminal_in_tab(tab_id):
    """The one tether-linked terminal in a Herdr tab, as a Where (for `continue --tab`, used by the tab menu)."""
    ensure_app()
    for space in herdr_ls():
        for tab in space.get("tabs", []):
            if tab.get("id") != tab_id:
                continue
            addrs = [t["terminal"] for t in terms_of(tab)]
            with store.State() as st:
                linked = [a for a in addrs if a in ((st.data.get(space["id"]) or {}).get("terminals") or {})]
            if not linked:
                sys.exit("tether: no agent is linked to a terminal in this tab (start one with tether claude or tether copilot)")
            if len(linked) > 1:
                sys.exit("tether: this tab has several linked terminals; run `tether continue` in the one you want")
            return Where(space["id"], space.get("name") or "", linked[0], tab_id, tab.get("name") or "")
    sys.exit("tether: that tab was not found")


def _terminal_of_pid(pid):
    """The herdr terminal a process runs in (its HC_TERMINAL_ID), or None."""
    r = subprocess.run(["ps", "eww", "-p", str(pid)], capture_output=True, text=True)
    m = re.search(r"HC_TERMINAL_ID=(\S+)", r.stdout)
    return m.group(1) if m else None


def tether_cmd():
    """What to type into a shell to run this script."""
    return "tether" if shutil.which("tether") else f"'{os.path.realpath(sys.argv[0])}'"
