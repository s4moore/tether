"""`tether doctor`: check the install, Herdr, links and backups."""

import json
import os
import shutil
import sys

from tetherlib.core import AGENTS, TetherError, config_path

from tetherlib import backups, context, core, herdr, store, workspace


def doctor(args):
    """Checks the install, Herdr, links and backups. Returns the list of problems."""
    results = []  # (level, text, fix)   level: ok | warn | bad

    def add(level, text, fix=""):
        results.append((level, text, fix))

    for tool in ("herdr-app", "hc"):
        add("ok" if core.find_tool(tool) else "bad", f"{tool} found" if core.find_tool(tool) else f"{tool} not found",
            "" if core.find_tool(tool) else "install Herdr: curl -fsSL https://releases.herdr.dev/app/install.sh | sh")
    for agent in AGENTS:
        add("ok" if core.find_tool(agent) else "warn", f"{agent} found" if core.find_tool(agent) else f"{agent} not found",
            "" if core.find_tool(agent) else f"install {agent} if you use it")
    here = shutil.which("tether")
    add("ok" if here else "warn", "tether is on PATH" if here else "tether is not on PATH",
        "" if here else "add ~/.local/bin to PATH (Herdr's `tether resume` typing needs it)")

    live = None
    try:
        if core.find_tool("herdr-app"):
            live = {s["id"]: s for s in herdr.herdr_ls()}
    except Exception:  # noqa: BLE001 - any failure just means "not running"
        live = None
    add("ok" if live is not None else "warn", "Herdr is running" if live is not None else "Herdr is not running",
        "" if live is not None else "start it: herdr-app (some checks below are skipped)")

    if core.find_tool("herdr-app"):
        # compatibility is checked by asking Herdr for what tether uses, not by comparing version numbers
        try:
            problems = herdr.contract_problems(spaces=list(live.values()) if live is not None else [])
        except Exception as err:  # noqa: BLE001 - an unreadable answer is itself the finding
            problems = [f"could not ask Herdr ({type(err).__name__})"]
        add("ok" if not problems else "bad", "Herdr offers what tether uses" if not problems else "Herdr does not match what tether expects: " + "; ".join(problems),
            "" if not problems else "this Herdr may be newer or older than tether supports: `herdr-app --version`, then please open an issue with the output of `tether doctor`")

    mods = os.path.expanduser("~/.config/herdr-app/mods")
    missing = [m for m in ("tether-spaces", "tether-new-space", "tether-history", "tether-menu",
                           "tether-backups", "tether-search", "tether-tabs", "tether-new-menu")
               if not os.path.isfile(os.path.join(mods, m, "main.js"))]
    add("ok" if not missing else "warn", "Herdr mods installed" if not missing else f"missing mods: {', '.join(missing)}",
        "" if not missing else "re-run ./install.sh from the tether folder (with Herdr quit)")
    skill = os.path.join(store._claude_home(), "skills", "tether", "SKILL.md")
    add("ok" if os.path.isfile(skill) else "warn", "agent skill installed" if os.path.isfile(skill) else "agent skill missing",
        "" if os.path.isfile(skill) else "re-run ./install.sh")

    with store.State() as st:
        data = {k: v for k, v in st.data.items() if v.get("terminals") or v.get("agents")}
    links = list(store.all_links(data))
    add("ok", f"{len(data)} linked space(s), {len(links)} link(s)")
    legacy = [sp.get("name") or sid for sid, sp, addr, _ in links if addr is None]
    if legacy:
        add("warn", f"{len(set(legacy))} space(s) still use the old per-space link: {', '.join(sorted(set(legacy)))}",
            "run `tether claude` (or copilot) once in each space's terminal to convert it")
    gone = recoverable = 0
    for sid, sp, addr, e in links:
        if not store.session_exists(e["agent"], backups.source_session(e)):
            if backups.has_backup(e["agent"], backups.source_session(e)):
                recoverable += 1
            else:
                gone += 1
    if recoverable:
        add("warn", f"{recoverable} linked transcript(s) gone from the agent but in the backup", "tether restore")
    if gone:
        add("warn", f"{gone} linked session(s) have no transcript anywhere (never used, or cleaned up before a backup)",
            "harmless: they start fresh. `tether reset` unlinks them.")
    if live is not None:
        stale = [(sp.get("name") or sid, addr) for sid, sp, addr, _ in links
                 if addr and sid in live and not herdr.find_terminal([live[sid]], addr)]
        if stale:
            add("warn", f"{len(stale)} link(s) point at terminals that no longer exist: "
                + ", ".join(f"{n}/{a}" for n, a in stale[:5]), "`tether open <space>` skips them; `tether reset` in the space clears it")
    if live is not None:
        try:
            unlinked = [r for r in store.running_agents() if (lambda h: h and h[2]["terminal"] not in
                        data.get(h[0]["id"], {}).get("terminals", {}))(herdr.find_terminal(list(live.values()), r["terminal"]))]
        except (TetherError, OSError):
            unlinked = []
        if unlinked:
            add("warn", f"{len(unlinked)} agent(s) run in Herdr tabs without a tether link (started with plain "
                f"`claude`/`copilot`): they will not survive a restart and `fork` would skip them", "tether adopt")
    names = {}
    for sid, sp in data.items():
        names.setdefault(workspace.norm_name(sp.get("name") or sid), []).append(sid)
    dup = [n for n, ids in names.items() if len(ids) > 1]
    if dup:
        add("warn", f"duplicate space names: {', '.join(dup)}", "`tether delete <space>` the extra one")

    bs = backups.backup_status(data)
    add("ok" if bs["exists"] else "warn", f"backup folder {bs['dir']}" if bs["exists"] else "no backup yet",
        "" if bs["exists"] else "run `tether backup`")
    if bs["exists"]:
        stale = bs["linked"] - bs["backed_up"] - bs["missing"]
        if stale > 0:
            add("warn", f"{stale} linked session(s) not backed up yet", "tether backup")
    add("ok" if bs["auto"] else "warn", "automatic backup on" if bs["auto"] else "automatic backup off",
        "" if bs["auto"] else 'enable: set "auto": true in ' + config_path())
    try:
        with open(os.path.join(store._claude_home(), "settings.json")) as f:
            keep = json.load(f).get("cleanupPeriodDays")
    except (OSError, ValueError):
        keep = None
    if keep is None:
        add("warn", "Claude deletes transcripts after its default 30 days",
            'raise it: "cleanupPeriodDays": 365 in ~/.claude/settings.json')
    else:
        add("ok", f"Claude keeps transcripts for {keep} days")
    cdir = context.context_dir()
    if os.path.isfile(os.path.join(cdir, "index.md")):
        topics = context.context_topics(cdir)
        bad = [i for i in context.context_check_issues(cdir) if i["level"] == "warn"]
        add("warn" if bad else "ok", f"context store: {len(topics)} topic(s), {sum(len(context.context_facts(cdir, t)) for t in topics)} fact(s)"
            + (f", {len(bad)} thing(s) to look at" if bad else ""), "tether context check" if bad else "")
    return results


def doctor_cmd(args):
    results = doctor(args)
    bad = [r for r in results if r[0] != "ok"]
    if "--json" in args:
        print(json.dumps([{"level": l, "text": t, "fix": f} for l, t, f in results], indent=2))
    else:
        mark = {"ok": "ok  ", "warn": "warn", "bad": "FAIL"}
        for level, text, fix in results:
            print(f"  {mark[level]}  {text}")
            if fix and level != "ok":
                print(f"        -> {fix}")
        print("all good" if not bad else f"{len(bad)} thing(s) need attention")
    if any(r[0] == "bad" for r in results):
        sys.exit(1)
