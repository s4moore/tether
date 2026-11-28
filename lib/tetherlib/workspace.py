"""Spaces: list, open, fork, new, delete, and the menu."""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from tetherlib.core import TetherError, UUID_RE, ago, env, now, parse_opts, pick, say

from tetherlib import backups, core, herdr, launch, store


def list_json():
    """Machine-readable `list`, newest first: what the herdr mod reads."""
    try:
        spaces = herdr.herdr_ls()
        names = {s["id"]: s.get("name") for s in spaces}
        live = set(names)
        open_tabs = {t["id"] for s in spaces for t in s.get("tabs") or [] if t.get("id")}
    except TetherError as e:
        # exit non-zero: a caller must not read "no closed spaces" from an unreadable herdr
        sys.exit(f"tether: {e}")
    with store.State() as st:
        data = st.data
    out = []
    for sid, sp in data.items():
        if not (sp.get("terminals") or sp.get("agents")):
            continue
        links = [{"agent": e["agent"], "tab": e.get("tab") or "", "tab_name": e.get("tab_name") or "", "session": e["session"],
                  "tab_open": bool(e.get("tab")) and e["tab"] in open_tabs,
                  "transcript": store.session_exists(e["agent"], e["session"]),
                  "recoverable": backups.has_backup(e["agent"], e["session"])}
                 for _, _, _, e in store.all_links({sid: sp})]
        out.append({"id": sid, "name": names.get(sid) or sp.get("name") or sid,
                    "last_used": store.last_used(sp), "closed": live is not None and sid not in live,
                    "forked_from": (sp.get("forked_from") or {}).get("name"),
                    "links": links})
    out.sort(key=lambda r: r["last_used"], reverse=True)
    print(json.dumps(out))


def list_all(args):
    if "--json" in args:
        return list_json()
    show_all = "--all" in args
    needle = " ".join(a for a in args if a != "--all").strip().lower()
    try:
        names = {s["id"]: s.get("name") for s in herdr.herdr_ls()}
        live = set(names)
    except TetherError as e:
        names, live = {}, None
        say(f"{e}; can't tell which spaces still exist")
    with store.State() as st:
        data = st.data
    rows = []
    for sid, sp in data.items():
        if not (sp.get("terminals") or sp.get("agents")):
            continue
        gone = live is not None and sid not in live
        label = names.get(sid) or sp.get("name") or sid
        hay = (label + " " + " ".join(e.get("session", "") for _, _, _, e in
               store.all_links({sid: sp}))).lower()
        if needle and needle not in hay:
            continue
        rows.append((store.last_used(sp), sid, dict(sp, name=label), gone))
    for last, sid, sp, gone in sorted(rows, key=lambda r: r[0], reverse=True):
        print(f"{sp.get('name') or sid:<32} {ago(last):<12}" + ("  (closed)" if gone else ""))
        if gone and show_all:
            for _, _, addr, e in store.all_links({sid: sp}):
                print(f"    {e['agent']:<8} {e.get('tab_name') or '':<18} "
                      f"reopen: {e['agent']} --resume {e['session']}")
    if not rows:
        print("no matching spaces" if needle else "nothing linked yet")


def space_rows(live, data, openable_only=False):
    """[(space_id, name, last_used, closed)], newest first. openable_only: just the spaces
    `open` can do something with: closed ones, and open ones with an idle linked terminal."""
    rows = []
    for k, sp in data.items():
        if not (sp.get("terminals") or sp.get("agents")):
            continue
        closed = k not in live
        if openable_only and not closed and not plan_space(sp, live[k])[3]:
            continue
        name = sp.get("name") if closed else live[k].get("name")
        rows.append((k, name or k, store.last_used(sp), closed))
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows


def row_label(name, last, closed=False):
    return f"{name:<32} {ago(last)}"


def reopen_closed(sid, sp, yes):
    """Recreate a closed space with a tab per linked terminal and resume each session in it."""
    name = sp.get("name") or sid
    items = [(e.get("tab_name") or "", e) for _, e in sorted(sp.get("terminals", {}).items())]
    items += [(agent, dict(e, agent=agent)) for agent, e in sp.get("agents", {}).items()]
    old_addr = {id(e): a for a, e in sp.get("terminals", {}).items()}
    ok = list(items)
    print(f"space {name!r} is closed")
    for t, e in items:
        note = ("-> new tab, resume" if backups.available(e["agent"], e["session"])
                else "-> new tab, fresh session (nothing to resume yet)")
        print(f"  [{t or e['agent']}]  {e['agent']} {e['session'][:8]}  {note}")
    if not ok:
        print("nothing to restore")
        return
    if not yes:
        if not sys.stdin.isatty():
            sys.exit("tether: not a terminal; pass -y to recreate the space")
        if input(f"recreate {name!r} with {len(ok)} tab(s) and resume them? [y/N] ").strip().lower() != "y":
            print("cancelled")
            return
    if not core.find_tool("hc"):
        raise TetherError("hc not found on PATH")
    new_space, created = None, []
    done = set()  # ids of the (tab name, link) items that now have a terminal
    try:
        for tab_name, e in ok:
            folder = e.get("cwd") if e.get("cwd") and os.path.isdir(e["cwd"]) else os.getcwd()
            if new_space is None:
                args = ["space.new", "--name", name, "--folder", folder]
                args += ["--tab", tab_name] if tab_name else []
            else:
                args = ["tab.new", "--space", new_space, "--folder", folder]
                args += ["--name", tab_name] if tab_name else []
            made = herdr.herdr_do(args)
            if not made.get("terminal"):
                raise TetherError(f"herdr-app did not return a terminal for {tab_name or e['agent']!r}")
            new_space = new_space or made.get("space")
            created.append((made["terminal"], tab_name, e))
            done.add(id(e))
    except TetherError as err:
        say(str(err))
    if not created:
        raise TetherError("could not recreate the space")
    now_live = herdr.herdr_ls()
    with store.State() as st:
        if not new_space:  # find it from the first terminal we made
            hit = herdr.find_terminal(now_live, created[0][0])
            new_space = hit[0]["id"] if hit else None
        if not new_space:
            raise TetherError("created terminals but could not tell which space they are in")
        sp_new = st.data.setdefault(new_space, {})
        sp_new.setdefault("agents", {})
        sp_new["name"] = name
        terms = sp_new.setdefault("terminals", {})
        for term, tab_name, e in created:
            hit = herdr.find_terminal(now_live, term)
            link = {k: e[k] for k in ("agent", "session", "cwd", "created") if e.get(k)}
            link.update(tab=(hit[1].get("id") if hit else "") or "", tab_name=tab_name,
                        last_used=e.get("last_used") or now())
            terms[term] = link
        left = [(t, e) for t, e in ok if id(e) not in done]
        if new_space != sid:
            if left:
                # some tabs could not be recreated: keep their links under the closed space so
                # they can be reopened later, instead of forgetting them
                kept = {"name": name, "terminals": {}, "agents": {}}
                for _, e in left:
                    if id(e) in old_addr:
                        kept["terminals"][old_addr[id(e)]] = e
                    else:
                        kept["agents"][e["agent"]] = {k: v for k, v in e.items() if k != "agent"}
                st.data[sid] = kept
                say(f"{len(left)} of {len(ok)} tab(s) could not be recreated; their links are kept "
                    f"in the closed space {name!r}")
            else:
                st.data.pop(sid, None)  # the closed space is now this one
        st.save()
    typed = f"{herdr.tether_cmd()} resume"
    for term, tab_name, e in created:
        r = subprocess.run([core.find_tool("hc"), "send", term, typed], capture_output=True, text=True)
        if r.returncode:
            say(f"could not type into {term}: {(r.stderr or r.stdout).strip()[:200]}")
        else:
            print(f"  {term} [{tab_name or e['agent']}]: resuming {e['agent']}")
    print(f"recreated space {name!r}")


def match_space(query, rows):
    """rows: [(id, name, last, closed)] -> chosen id, or None if nothing matches."""
    q = query.strip().lower()
    exact = [r for r in rows if r[1].lower() == q]
    cands = exact or [r for r in rows if q in r[1].lower()]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0][0]
    return pick(f"{len(cands)} spaces match {query!r}:",
                [(row_label(n, l, c), i) for i, n, l, c in cands])


def plan_space(sp_data, space):
    """What `open` would do for an open space: (terms, legacy, adopted, plan, skipped).
    plan = [(address, tab name, link)] to type `tether resume` into."""
    here = {t["terminal"]: (tab, t) for tab in space.get("tabs", []) for t in herdr.terms_of(tab)}
    terms = dict(sp_data.get("terminals", {}))
    legacy = dict(sp_data.get("agents", {}))
    tops = [(t["terminal"], tab) for tab in space.get("tabs", []) for t in tab.get("terminals", [])]
    adopted = None  # an earlier per-space link goes to the space's only terminal, if unambiguous
    if legacy and len(tops) == 1 and tops[0][0] not in terms:
        agent = max(legacy, key=lambda a: legacy[a].get("last_used", ""))
        addr, tab = tops[0]
        terms[addr] = dict(legacy.pop(agent), agent=agent, tab=tab.get("id") or "",
                           tab_name=tab.get("name") or "")
        adopted = (addr, agent)
    plan, skipped = [], []
    for addr, e in sorted(terms.items()):
        tab_label = e.get("tab_name") or "?"
        if addr not in here:
            skipped.append((addr, tab_label, e, "terminal no longer exists"))
            continue
        tab, t = here[addr]
        if e.get("tab") and tab.get("id") != e["tab"]:
            skipped.append((addr, tab_label, e, "address now belongs to a different tab"))
        elif t.get("app") or t.get("status"):
            skipped.append((addr, tab_label, e, f"already running {t.get('app') or 'an agent'}"))
        elif store._running(e, e["agent"]):
            skipped.append((addr, tab_label, e, "already running"))
        else:
            plan.append((addr, tab.get("name") or tab_label, e))
    return terms, legacy, adopted, plan, skipped


def open_space(query, yes, sid=None):
    herdr.ensure_app()
    spaces = herdr.herdr_ls()
    with store.State() as st:
        all_data = st.data
        data = {k: sp for k, sp in st.data.items() if sp.get("terminals") or sp.get("agents")}
    live = {s["id"]: s for s in spaces}
    linked = space_rows(live, data)
    if not linked:
        sys.exit("tether: nothing is linked yet; run `tether claude` or `tether copilot` in a "
                 "herdr terminal first")
    if sid:
        pass
    elif query:
        sid = match_space(query, linked)
        if sid is None:
            names = ", ".join(repr(n) for _, n, _, _ in linked)
            sys.exit(f"tether: no space matches {query!r}. Spaces with links: {names}")
    else:
        choices = space_rows(live, data, openable_only=True)
        if not choices:
            sys.exit("tether: nothing to open: no closed spaces, and every open space's linked "
                     "terminals are already running")
        sid = pick("open space:", [(row_label(n, l), i) for i, n, l, c in choices])
        if sid is None:
            return
    if sid not in live:
        return reopen_closed(sid, data[sid], yes)
    space, name = live[sid], live[sid].get("name") or sid
    terms, legacy, adopted, plan, skipped = plan_space(data[sid], space)
    print(f"space {name!r}")
    for addr, tab_label, e in plan:
        fresh = "" if backups.available(e["agent"], e["session"]) else "  (fresh session: nothing to resume yet)"
        print(f"  {addr:<5} [{tab_label}]  {e['agent']} {e['session'][:8]}  -> resume{fresh}")
    for addr, tab_label, e, why in skipped:
        print(f"  {addr:<5} [{tab_label}]  {e['agent']} {e['session'][:8]}  skipped: {why}")
    for agent in legacy:
        print(f"  (no terminal)  {agent}: earlier link, not tied to a terminal (the space has "
              f"several); run `tether {agent}` in the terminal you want it in")
    if not plan:
        print("nothing to resume")
        return
    if not yes:
        if not sys.stdin.isatty():
            sys.exit("tether: not a terminal; pass -y to type into these terminals")
        if input(f"type `tether resume` into {len(plan)} terminal(s)? [y/N] ").strip().lower() != "y":
            print("cancelled")
            return
    hc = core.find_tool("hc")
    if not hc:
        sys.exit("tether: hc not found on PATH")
    if adopted and any(a == adopted[0] for a, _, _ in plan):
        with store.State() as st:  # keep the assignment so the terminal's own `tether` finds it
            sp = st.data.setdefault(sid, {})
            sp.setdefault("terminals", {})[adopted[0]] = terms[adopted[0]]
            sp.setdefault("agents", {}).pop(adopted[1], None)
            st.save()
    me = shutil.which("tether")
    typed = "tether resume" if me else f"'{os.path.realpath(sys.argv[0])}' resume"
    for addr, _, _ in plan:
        r = subprocess.run([hc, "send", addr, typed], capture_output=True, text=True)
        if r.returncode:
            say(f"could not type into {addr}: {(r.stderr or r.stdout).strip()[:200]}")
        else:
            print(f"  sent to {addr}")


def copy_copilot_session(src_id, dst_id):
    """Copy a Copilot session folder under a new id: the folder name is what Copilot goes by, and the
    ids written inside are rewritten so the copy is consistent. Locks of a running copy are dropped."""
    root = os.path.join(store._copilot_home(), "session-state")
    src, dst = os.path.join(root, src_id), os.path.join(root, dst_id)
    if not os.path.isdir(src):
        raise TetherError(f"copilot session {src_id} not found")
    shutil.copytree(src, dst, symlinks=True)
    for name in os.listdir(dst):
        if name.startswith("inuse.") and name.endswith(".lock"):
            os.remove(os.path.join(dst, name))
    for name in ("workspace.yaml", "events.jsonl"):
        path = os.path.join(dst, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace(src_id, dst_id))


def fork_name(base, taken):
    base = re.sub(r"\s*\(fork(?: \d+)?\)$", "", base) or base  # a fork of a fork is numbered like its siblings
    for n in range(1, 100):
        cand = f"{base} (fork)" if n == 1 else f"{base} (fork {n})"
        if norm_name(cand) not in taken:
            return cand
    raise TetherError("too many forks of this space; rename or delete some")


def fork_space(args):
    """Copy a space: a new space whose tabs continue copies of the original's sessions."""
    _, vals, words = parse_opts(args, flags=("-y", "--yes"), valued=("--id",))
    yes = "-y" in args or "--yes" in args
    herdr.ensure_app()
    live_list = herdr.herdr_ls()
    live = {sp["id"]: sp for sp in live_list}
    with store.State() as st:
        data = {k: v for k, v in st.data.items() if v.get("terminals") or v.get("agents")}
    rows = space_rows(live, data)
    sid = vals.get("--id")
    if not sid:
        if words:
            sid = match_space(" ".join(words), rows)
            if sid is None:
                sys.exit(f"tether: no space matches {' '.join(words)!r}")
        else:
            sid = pick("fork which space?", [(row_label(n, l), i) for i, n, l, c in rows])
            if sid is None:
                return
    if sid in live:  # agents started without tether are linked first, or they would be left out
        try:
            for space, tab, agent, session in store.adopt(sid):
                print(f"  linked {agent} {session[:8]} in [{tab or '?'}] (it was not linked to tether yet)")
        except (TetherError, OSError) as err:
            say(f"could not look for unlinked agents ({err})")
        with store.State() as st:
            data = {k: v for k, v in st.data.items() if v.get("terminals") or v.get("agents")}
    if sid not in data:
        sys.exit(f"tether: tether has nothing linked for space {sid!r}")
    sp = data[sid]
    name = (live[sid].get("name") if sid in live else None) or sp.get("name") or sid
    links = [(addr, e) for _, _, addr, e in store.all_links({sid: sp})]
    print(f"forking {name!r}")
    # make sure the original is safe first, and that every transcript we copy exists
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            backups.backup_cmd(["--id", sid, "--quiet"])
    except (SystemExit, TetherError, OSError) as err:
        say(f"could not back up first ({err}); continuing, the original is not modified")
    plan = []
    for addr, e in sorted(links, key=lambda x: x[0] or ""):
        if not store.session_exists(e["agent"], e["session"]):
            backups.restore_session(e["agent"], e["session"])
        tab = e.get("tab_name") or e["agent"]
        src = e.get("fork_of")
        if not store.session_exists(e["agent"], e["session"]) and src and store.session_exists(e["agent"], src):
            # a fork nobody has typed in yet has no transcript of its own, but it shows (and is) a copy
            # of the session it was forked from: fork that one
            print(f"  [{tab}] {e['agent']} {e['session'][:8]}  -> copied from the session it was forked from "
                  f"({src[:8]}); nothing has been said in this tab yet")
            plan.append((addr, dict(e, session=src)))
        elif not store.session_exists(e["agent"], e["session"]):
            print(f"  [{tab}] {e['agent']} {e['session'][:8]}  skipped: no conversation to copy "
                  f"(nothing has been said in this tab yet)")
        else:
            running = " (running now: copied as it is at this moment)" if store._running(e, e["agent"]) else ""
            print(f"  [{tab}] {e['agent']} {e['session'][:8]}  -> copied{running}")
            plan.append((addr, e))
    if not plan:
        sys.exit("tether: nothing to fork: no linked session has a conversation yet")
    taken = {norm_name(x.get("name") or "") for x in live_list}
    taken |= {norm_name(x.get("name") or "") for x in data.values()}
    new_name = fork_name(name, taken)
    if not yes:
        if not sys.stdin.isatty():
            sys.exit("tether: not a terminal; pass -y to fork")
        if input(f"create {new_name!r} with {len(plan)} tab(s)? [y/N] ").strip().lower() != "y":
            print("cancelled")
            return
    if not core.find_tool("hc"):
        raise TetherError("hc not found on PATH")
    new_space, created = None, []
    try:
        for addr, e in plan:
            folder = e.get("cwd") if e.get("cwd") and os.path.isdir(e["cwd"]) else os.getcwd()
            tab = e.get("tab_name") or ""
            if new_space is None:
                args2 = ["space.new", "--name", new_name, "--folder", folder] + (["--tab", tab] if tab else [])
            else:
                args2 = ["tab.new", "--space", new_space, "--folder", folder] + (["--name", tab] if tab else [])
            made = herdr.herdr_do(args2)
            if not made.get("terminal"):
                raise TetherError(f"herdr-app did not return a terminal for {tab or e['agent']!r}")
            new_space = new_space or made.get("space")
            new_id = str(uuid4())
            link_new = {"agent": e["agent"], "session": new_id, "cwd": e.get("cwd") or folder, "tab_name": tab,
                        "created": now(), "last_used": now()}
            if e["agent"] == "claude":
                link_new["fork_of"] = e["session"]  # Claude forks lazily on first start (--fork-session)
            else:
                copy_copilot_session(e["session"], new_id)
            created.append((made["terminal"], link_new))
    except (TetherError, OSError) as err:
        say(str(err))
    if not created:
        raise TetherError("could not create the forked space")
    now_live = herdr.herdr_ls()
    with store.State() as st:
        if not new_space:
            hit = herdr.find_terminal(now_live, created[0][0])
            new_space = hit[0]["id"] if hit else None
        if not new_space:
            raise TetherError("created terminals but could not tell which space they are in")
        rec = st.data.setdefault(new_space, {})
        rec["name"] = new_name
        rec["forked_from"] = {"name": name, "id": sid, "at": now()}
        rec.setdefault("agents", {})
        terms = rec.setdefault("terminals", {})
        for term, link_new in created:
            hit = herdr.find_terminal(now_live, term)
            terms[term] = dict(link_new, tab=(hit[1].get("id") if hit else "") or "")
        st.save()
    for term, link_new in created:
        r = subprocess.run([core.find_tool("hc"), "send", term, f"{herdr.tether_cmd()} resume"], capture_output=True, text=True)
        if r.returncode:
            say(f"could not type into {term}: {(r.stderr or r.stdout).strip()[:200]}")
        else:
            print(f"  {term} [{link_new['tab_name'] or link_new['agent']}]: starting {link_new['agent']} (fork)")
    print(f"created {new_name!r}; {name!r} is unchanged")


def open_session(args):
    """Put one session back in a new tab of the space that linked it."""
    _, vals, words = parse_opts(args, valued=("--agent",))
    session = words[0] if words else None
    if not session or not UUID_RE.match(session):
        sys.exit("usage: tether open-session <session-id> [--agent claude|copilot]")
    with store.State() as st:
        hits = [(sid, sp, addr, e) for sid, sp, addr, e in store.all_links(st.data)
                if e["session"] == session and vals.get("--agent") in (None, e["agent"])]
    if not hits:  # maybe its space was deleted: the backup still knows it
        gone = next((r for r in backups.deleted_spaces() if any(l["session"] == session for l in r["links"])), None)
        if gone:
            print(f"bringing back the deleted space {gone['name']!r}")
            backups.undelete_space(gone)
            with store.State() as st:
                hits = [(sid, sp, addr, e) for sid, sp, addr, e in store.all_links(st.data)
                        if e["session"] == session and vals.get("--agent") in (None, e["agent"])]
    if not hits:
        sys.exit(f"tether: no space links session {session}; restore it with "
                 f"`tether restore --session {session}` and resume it by hand")
    sid, sp, addr, e = hits[0]
    agent = e["agent"]
    herdr.ensure_app()
    live = {s["id"]: s for s in herdr.herdr_ls()}
    src = backups.source_session(e)
    if not store.session_exists(agent, src):
        if not backups.restore_session(agent, src):
            print(f"no transcript for {src[:8]} anywhere; it will start fresh")
        else:
            print(f"restored {agent} session {src[:8]} from the backup")
    if sid not in live:
        return reopen_closed(sid, sp, True)
    if store.running_link({sid: sp}, session, agent):
        print(f"{agent} session {session[:8]} is already running in this space")
        return
    folder = e.get("cwd") if e.get("cwd") and os.path.isdir(e["cwd"]) else os.getcwd()
    tab_name = e.get("tab_name") or ""
    made = herdr.herdr_do(["tab.new", "--space", sid, "--folder", folder] + (["--name", tab_name] if tab_name else []))
    term = made.get("terminal")
    if not term:
        raise TetherError("herdr-app did not return a terminal for the new tab")
    hit = herdr.find_terminal(herdr.herdr_ls(), term)
    with store.State() as st:
        space = st.data.setdefault(sid, {})
        terms = space.setdefault("terminals", {})
        if addr and addr in terms and terms[addr].get("session") == session:
            terms.pop(addr)  # the old terminal's link moves to the new tab
        space.setdefault("agents", {})
        terms[term] = dict(e, agent=agent, tab=(hit[1].get("id") if hit else "") or "",
                           tab_name=tab_name, last_used=now())
        st.save()
    hc = core.find_tool("hc")
    if not hc:
        raise TetherError("hc not found on PATH")
    r = subprocess.run([hc, "send", term, f"{herdr.tether_cmd()} resume"], capture_output=True, text=True)
    if r.returncode:
        raise TetherError(f"could not type into {term}: {(r.stderr or r.stdout).strip()[:200]}")
    print(f"resuming {agent} session {session[:8]} in a new tab of {sp.get('name') or sid!r}")


def close_space_and_wait(sid, links, timeout=8.0):
    """Close a space in Herdr, then wait until it is gone and none of its sessions is still running
    (instead of a fixed pause), so their files can be touched safely."""
    herdr.herdr_do(["space.close", "--id", sid, "--confirmed", "true"])
    wanted = {(e["agent"], e["session"]) for e in links}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            gone = all(sp["id"] != sid for sp in herdr.herdr_ls())
            busy = any((r["agent"], r["session"]) in wanted for r in store.running_agents())
        except TetherError:
            return
        if gone and not busy:
            return
        time.sleep(0.25)
    say("the space is closed but an agent may still be shutting down")


def delete_space(args):
    """Forget a space: unlink it from tether, and with --sessions also delete its sessions'
    transcripts. Never touches herdr (the herdr mod closes a live space itself)."""
    sessions = "--sessions" in args
    close = "--close" in args
    yes = "-y" in args or "--yes" in args
    sid, words, i = None, [], 0
    while i < len(args):
        if args[i] == "--id":
            if i + 1 >= len(args):
                sys.exit("usage: tether delete [space | --id <space-id>] [--sessions] [-y]")
            sid, i = args[i + 1], i + 2
            continue
        if args[i] not in ("--sessions", "--close", "-y", "--yes"):
            words.append(args[i])
        i += 1
    try:
        live = {sp["id"]: sp for sp in herdr.herdr_ls()}
    except TetherError:
        live = {}  # can't tell; stored names are used and nothing is closed here anyway
    with store.State() as st:
        data = {k: v for k, v in st.data.items() if v.get("terminals") or v.get("agents")}
    rows = space_rows(live, data)
    if sid is None:
        if words:
            sid = match_space(" ".join(words), rows)
            if sid is None:
                sys.exit(f"tether: no space matches {' '.join(words)!r}")
        else:
            sid = pick("delete which space?", [(row_label(n, l), i) for i, n, l, c in rows])
            if sid is None:
                return
    if sid not in data:
        sys.exit(f"tether: tether has nothing linked for space {sid!r}")
    sp = data[sid]
    name = (live[sid].get("name") if sid in live else None) or sp.get("name") or sid
    links = [e for _, _, _, e in store.all_links({sid: sp})]
    force = bool(env("FORCE"))
    print(f"space {name!r}" + ("  (still open in herdr)" if sid in live else "  (closed)"))
    for e in links:
        paths = store.session_paths(e["agent"], e["session"])
        running = store._running(e, e["agent"])
        note = "RUNNING" if running else ("transcript found" if paths else "transcript already gone")
        print(f"  {e['agent']:<8} {e['session']}  [{note}]")
    if not yes:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            sys.exit("tether: not a terminal; pass -y (and --sessions to delete transcripts)")
        if sessions:
            ask = input("unlink AND permanently delete these sessions' transcripts and their backup copies? type 'delete': ")
            if ask.strip().lower() != "delete":
                print("cancelled")
                return
        else:
            ask = input("[u]nlink (recoverable from the backup) / [d]elete everything permanently (transcripts and backups) / [N]o: ").strip().lower()
            if ask.startswith("d"):
                sessions = True
            elif not ask.startswith("u"):
                print("cancelled")
                return
    if close and sid in live:
        close_space_and_wait(sid, links)
    if not sessions:  # unlink only: keep a record, so `tether undelete` can bring it back
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                backups.backup_cmd(["--id", sid, "--quiet"])
        except (SystemExit, TetherError, OSError) as err:
            say(f"could not back up before unlinking ({err}); `tether undelete` may not find it")
    with store.State() as st:
        st.data.pop(sid, None)
        st.save()
    print(f"unlinked {name!r} from tether" + ("" if sessions else "  (undo: tether undelete)"))
    failed = 0
    if sessions:
        for e in links:
            if store._running(e, e["agent"]) and not force:
                print(f"  kept {e['agent']} {e['session']}: still running (close it first)")
                failed += 1
                continue
            try:
                for path in store.session_paths(e["agent"], e["session"]):
                    store._remove(path)
                try:
                    backups.delete_backup_of_session(e["agent"], e["session"])  # permanent means the backup copy too
                except TetherError:
                    pass
                print(f"  deleted {e['agent']} session {e['session']}")
            except OSError as err:
                print(f"  could not delete {e['agent']} {e['session']}: {err}")
                failed += 1
    else:
        for e in links:
            if store.session_paths(e["agent"], e["session"]):
                print(f"  kept {e['agent']} session: {e['agent']} --resume {e['session']}")
    if failed:
        sys.exit(3)


def open_by_name_or_fail(word):
    try:
        herdr.ensure_app()
        live = {s["id"]: s for s in herdr.herdr_ls()}
        with store.State() as st:
            data = {k: v for k, v in st.data.items() if v.get("terminals") or v.get("agents")}
        rows = space_rows(live, data)
        sid = match_space(word, rows) if rows else None
        if sid is not None:
            return open_space(None, False, sid=sid)
    except TetherError:
        pass
    sys.exit(f"tether: unknown command or space {word!r} (try `tether --help`)")


def norm_name(s):
    return " ".join(s.split()).lower()


def name_problem(name):
    """Why this can't be a new space's name (same rule as the herdr "+" dialog), else None.
    Open and closed spaces both count: reopening a closed one by its name would be ambiguous."""
    n = norm_name(name)
    live = herdr.herdr_ls()
    if any(norm_name(sp.get("name") or "") == n for sp in live):
        return f"A space named {name.strip()!r} is already open."
    ids = {sp["id"] for sp in live}
    with store.State() as st:
        for sid, sp in st.data.items():
            if sid not in ids and (sp.get("terminals") or sp.get("agents")) \
                    and norm_name(sp.get("name") or "") == n:
                return (f"A closed space named {name.strip()!r} exists. Reopen it from the "
                        f"menu (`tether`), or pick another name.")
    return None


def new_space():
    """Make a herdr space (asks for a name and an agent) and start the agent in it."""
    app = core.find_tool("herdr-app")
    hc = core.find_tool("hc")
    if not (app and hc):
        raise TetherError("herdr-app and hc must be on PATH")
    herdr.ensure_app()
    while True:
        try:
            name = input("name for the new space (empty to cancel): ").strip()
        except EOFError:
            return
        if not name:
            print("cancelled")
            return
        problem = name_problem(name)
        if not problem:
            break
        print(f"  {problem}")
    agent = pick("start which agent?", [("claude", "claude"), ("copilot", "copilot")])
    if not agent:
        print("cancelled")
        return
    r = subprocess.run([app, "do", "space.new", "--name", name, "--folder", os.getcwd()],
                       capture_output=True, text=True, timeout=15)
    try:
        made = json.loads(r.stdout)
    except ValueError:
        made = {"error": (r.stderr or r.stdout).strip()[:200]}
    if r.returncode or "error" in made or not made.get("terminal"):
        raise TetherError(f"could not create the space: {made.get('error', 'no terminal returned')}")
    send = subprocess.run([hc, "send", made["terminal"], f"{herdr.tether_cmd()} {agent}"],
                          capture_output=True, text=True)
    if send.returncode:
        raise TetherError(f"space {name!r} created, but could not start {agent} in "
                          f"{made['terminal']}: {(send.stderr or send.stdout).strip()[:200]}")
    print(f"created space {name!r}; started {agent} in {made['terminal']}")


def menu():
    """Bare `tether`: resume this terminal's link if it has one, else an arrow-key menu."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(__doc__)
        return
    where = None
    if os.environ.get("HC_TERMINAL_ID"):
        where, _ = herdr.resolve_where()
    if where is not None:
        with store.State() as st:
            has = where.terminal in st.data.get(where.space_id, {}).get("terminals", {})
        if has:  # outside the `with`: resume_here takes the same lock
            return launch.resume_here()
    items = []
    if where is not None:
        items += [("claude   start in this terminal", ("here", "claude"), True),
                  ("copilot  start in this terminal", ("here", "copilot"), True)]
    items.append(("+ new space...", ("new", None), True))
    if where is None:
        herdr.ensure_app()
    live = {s["id"]: s for s in herdr.herdr_ls()}
    with store.State() as st:
        data = dict(st.data)
    for sid, name, last, closed in space_rows(live, data, openable_only=True):
        items.append((row_label(name, last), ("space", sid)))
    choice = pick("tether:", items)
    if not choice:
        return
    kind, val = choice
    if kind == "here":
        launch.launch(val, [])
    elif kind == "new":
        new_space()
    else:
        open_space(None, False, sid=val)
