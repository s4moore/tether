"""Starting an agent in this terminal, and link / status / reset."""

import os
import sys
from uuid import uuid4

from tetherlib.core import EXPLICIT_SESSION_FLAGS, TetherError, ago, env, now, say

from tetherlib import backups, context, core, herdr, settings, store


def plain(agent, args, why):
    say(f"{why}; starting plain {agent}")
    binary = core.find_tool(agent)
    if not binary:
        sys.exit(f"tether: {agent} not found on PATH")
    if env("DRY_RUN"):
        print("would run:", " ".join([agent] + args))
        return
    os.execvp(binary, [agent] + args)


def launch(agent, args):
    flags = {a.split("=", 1)[0] for a in args}
    if flags & EXPLICIT_SESSION_FLAGS[agent]:
        return plain(agent, args, "you chose a session explicitly")

    where, why = herdr.resolve_where()
    if where is None:
        return plain(agent, args, why)

    binary = core.find_tool(agent)
    if not binary:
        sys.exit(f"tether: {agent} not found on PATH")
    dry = bool(env("DRY_RUN"))

    with store.State() as st:
        space = st.data.setdefault(where.space_id, {})
        terms = space.setdefault("terminals", {})
        legacy = space.setdefault("agents", {})
        entry = terms.get(where.terminal)
        replaced = None

        if entry and entry["agent"] != agent:
            replaced, entry = entry, None
        elif entry and entry.get("tab") and entry["tab"] != where.tab:
            say(f"{where.terminal} is in a different tab than when it was linked "
                f"(address reused?); not resuming the old session")
            replaced, entry = entry, None
        if not entry and agent in legacy and not store._running(legacy[agent], agent):
            entry = legacy.pop(agent)
            say(f"using this space's earlier {agent} session for this terminal")
        if replaced:
            say(f"replacing this terminal's {replaced['agent']} link (transcript kept; reopen "
                f"with: {replaced['agent']} --resume {replaced['session']})")

        if entry:
            other = store.running_link(st.data, entry["session"], agent)
            if other and not env("FORCE"):
                sys.exit(
                    f"tether: this {agent} session is already running (pid {other['pid']}).\n"
                    f"        Two copies on one session would write over each other.\n"
                    f"        Use that one, run plain `{agent}` for a separate session, "
                    f"or TETHER_FORCE=1 to override."
                )

        cwd = os.getcwd()
        resume = bool(entry) and store.session_exists(agent, entry["session"])
        # a forked Claude tab: the first start forks the original into this link's session id
        fork_src = entry.get("fork_of") if entry and not resume and agent == "claude" else None
        if fork_src and not store.session_exists(agent, fork_src):
            if backups.restore_session(agent, fork_src):
                say(f"restored the original session {fork_src[:8]} from the backup")
            if not store.session_exists(agent, fork_src):
                say(f"the session this was forked from ({fork_src[:8]}) is gone; starting a new one")
                fork_src = None
        if entry and not resume and not fork_src:
            backed_up = bool(backups.session_files(agent, entry["session"], os.path.join(backups.backup_dir(), agent)))
            if backed_up and dry:
                say(f"linked session {entry['session'][:8]} is gone, but a backup has it: it would be restored")
                resume = True
            elif backed_up and backups.restore_session(agent, entry["session"]):
                say(f"linked session {entry['session'][:8]} was gone; restored it from the backup")
                resume = store.session_exists(agent, entry["session"])
        if entry and not resume and not fork_src:
            say(f"linked session {entry['session'][:8]} has no transcript (never used, or cleaned "
                f"up); starting a new one")
        if resume or fork_src:
            session = entry["session"]
            target = entry.get("cwd")
            if target and os.path.isdir(target) and not env("NO_CD"):
                if os.path.realpath(target) != os.path.realpath(cwd):
                    say(f"resuming in {target} (where this session started)")
                cwd = target
            elif target and not os.path.isdir(target):
                say(f"{target} no longer exists; resuming from {cwd}")
        else:
            session = str(uuid4())
            alt = settings.home_dir_setting()
            if alt and os.path.realpath(cwd) == os.path.realpath(os.path.expanduser("~")) \
                    and not env("NO_CD"):
                say(f"starting in {alt} (home_dir), so {agent} does not ask to trust your home folder")
                cwd = alt

        label = where.name.strip()
        if label and any(a != where.terminal for a in terms):
            label = f"{label} · {where.tab_name}" if where.tab_name else label
        if agent == "claude":
            if fork_src:
                cmd = [agent, "--resume", fork_src, "--fork-session", "--session-id", session]
            else:
                cmd = [agent, "--resume" if resume else "--session-id", session]
            if label:
                cmd += ["--name", label]
        else:
            cmd = [agent, f"--resume={session}" if resume else f"--session-id={session}"]
            if label and not resume:  # copilot only accepts a name for a new session
                cmd += [f"--name={label}"]
        cmd += args

        if dry:
            print(f"space   : {where.space_id} ({where.name!r})  terminal {where.terminal} "
                  f"tab {where.tab_name!r}")
            print(f"session : {session} ({'resume' if resume else 'new'})")
            print(f"would run: cd {cwd} && {' '.join(cmd)}")
            return

        space["name"] = where.name
        terms[where.terminal] = {
            "agent": agent,
            "session": session,
            "cwd": (entry.get("cwd") or cwd) if (resume or fork_src) else cwd,
            "tab": where.tab,
            "tab_name": where.tab_name,
            "created": (entry.get("created") if entry else None) or now(),
            **({"fork_of": fork_src} if fork_src else {}),
            "last_used": now(),
            "pid": os.getpid(),  # becomes the agent's pid after exec
            "pid_started": store._started(os.getpid()),
        }
        st.save()

    backups.auto_backup_if_due()
    context.context_check_if_due()
    context.context_harvest_if_due()
    context.context_hint()
    os.chdir(cwd)
    os.execvp(binary, cmd)


def resume_here():
    """Bare `tether` / `tether resume` inside a terminal: whatever this terminal had."""
    where, why = herdr.resolve_where()
    if where is None:
        sys.exit(f"tether: {why}")
    with store.State() as st:
        entry = st.data.get(where.space_id, {}).get("terminals", {}).get(where.terminal)
    if not entry:
        sys.exit("tether: nothing is linked to this terminal yet; run `tether claude` or "
                 "`tether copilot` (or `tether link claude [id]` for an existing session)")
    launch(entry["agent"], [])


def link(agent, session):
    where, why = herdr.resolve_where()
    if where is None:
        sys.exit(f"tether: {why}")
    how = "given"
    if session is None:
        session = store.current_session(agent)
        how = "detected: the session this command is running in"
        if not session:
            session = store.guess_session(agent)
            how = ("GUESSED: the most recent one in this folder" if agent == "claude"
                   else "GUESSED: the most recent one anywhere")
            if not session:
                sys.exit(f"tether: no {agent} session found to link; pass an id: "
                         f"tether link {agent} <session-id>")
    if not store.session_exists(agent, session):
        sys.exit(f"tether: no {agent} session {session!r} found on disk")
    with store.State() as st:
        running = store.running_link(st.data, session, agent)
        if running and not env("FORCE"):
            sys.exit(f"tether: that session is running under another link (pid {running['pid']}); "
                     f"TETHER_FORCE=1 to move it anyway")
        # a session belongs to exactly one terminal: take it from wherever it was
        moved = []
        for sid, sp, addr, e in list(store.all_links(st.data)):
            if e.get("session") == session and not (sid == where.space_id and addr == where.terminal):
                (sp["terminals"].pop(addr) if addr else sp["agents"].pop(e["agent"]))
                moved.append(sp.get("name") or sid)
        space = st.data.setdefault(where.space_id, {})
        space.setdefault("agents", {})
        old = space.setdefault("terminals", {}).get(where.terminal)
        space["name"] = where.name
        space["terminals"][where.terminal] = {
            "agent": agent,
            "session": session,
            "cwd": store.session_cwd(agent, session),
            "tab": where.tab,
            "tab_name": where.tab_name,
            "created": (old.get("created") if old else None) or now(),
            "last_used": now(),
        }
        if how.startswith("detected"):  # we are inside that session, so we know its process
            pid, started = store.agent_process(agent)
            if pid:
                space["terminals"][where.terminal].update(pid=pid, pid_started=started)
        st.save()
    print(f"linked {agent} session {session} ({how}) to terminal {where.terminal} "
          f"[{where.tab_name or 'tab'}] in space {where.name!r}")
    if old and old["session"] != session:
        print(f"  replaced {old['agent']} {old['session']} (transcript kept)")
    if moved:
        print(f"  moved it from: {', '.join(moved)}")
    if how.startswith("GUESSED"):
        print(f"  could not tell which {agent} session this is, so this is a guess; if it is the "
              f"wrong one, run: tether link {agent} <session-id>")
    print("  `tether` in this terminal resumes it; after a restart, `tether` here (or "
          "`tether open <space>` for the whole space)")


def status():
    where, why = herdr.resolve_where()
    if where is None:
        sys.exit(f"tether: {why}")
    try:
        live = {t["terminal"]: tab.get("id") for s in herdr.herdr_ls() if s["id"] == where.space_id
                for tab in s.get("tabs", []) for t in herdr.terms_of(tab)}
    except TetherError:
        live = None
    print(f"space: {where.name!r}  ({where.space_id})   this terminal: {where.terminal}")
    with store.State() as st:
        space = st.data.get(where.space_id, {})
    terms = space.get("terminals", {})
    for addr, e in sorted(terms.items()):
        flags = ["running" if store._running(e, e["agent"]) else "idle"]
        flags.append("transcript found" if store.session_exists(e["agent"], e["session"])
                     else "transcript MISSING")
        if live is not None and addr not in live:
            flags.append("terminal gone")
        elif live is not None and e.get("tab") and live[addr] != e["tab"]:
            flags.append("terminal moved to another tab")
        here = " <- here" if addr == where.terminal else ""
        print(f"  {addr:<5} [{e.get('tab_name') or '?'}]  {e['agent']}: {e['session']}  "
              f"[{', '.join(flags)}]{here}")
        print(f"        cwd {e.get('cwd')}   last used {ago(e.get('last_used'))}")
    for agent, e in space.get("agents", {}).items():
        print(f"  (no terminal)  {agent}: {e['session']}  earlier link; the next terminal here "
              f"that runs `tether {agent}` takes it over")
    if not terms and not space.get("agents"):
        print("  nothing linked yet; run `tether claude` or `tether copilot`")


def reset(which):
    where, why = herdr.resolve_where()
    if where is None:
        sys.exit(f"tether: {why}")
    with store.State() as st:
        space = st.data.get(where.space_id, {})
        terms, legacy = space.get("terminals", {}), space.get("agents", {})
        removed = []
        e = terms.get(where.terminal)
        if e and (not which or e["agent"] == which):
            removed.append(terms.pop(where.terminal))
        elif not e:
            for agent in ([which] if which else list(legacy)):
                if agent in legacy:
                    removed.append(dict(legacy.pop(agent), agent=agent))
        for r in removed:
            print(f"unlinked {r['agent']} from {where.name!r}; transcript kept, "
                  f"reopen it with: {r['agent']} --resume {r['session']}")
        if not removed:
            print("nothing to reset")
        st.save()


def touch(space_id):
    """Record that a space was just switched to. Only spaces that already have links. Agents running
    in this space without a tether link are linked first."""
    store.safe_adopt(space_id)
    with store.State() as st:
        sp = st.data.get(space_id)
        if sp and (sp.get("terminals") or sp.get("agents")):
            sp["accessed"] = now()
            st.save()
