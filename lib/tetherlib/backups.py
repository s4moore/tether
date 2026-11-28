"""Backup, restore, export and import of conversations."""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from uuid import uuid4

from tetherlib.core import AGENTS, TetherError, UUID_RE, ago, env, load_config, now, parse_opts, pick, state_dir

from tetherlib import core, herdr, store, workspace


def purge_session(agent, session):
    """Delete a session everywhere: the agent's own copy and the backup copy. Refuses a running one.
    -> {name, sessions, bytes}"""
    if agent not in AGENTS or not UUID_RE.match(session or ""):
        raise TetherError("not a valid session")
    if any(r["session"] == session and r["agent"] == agent for r in store.running_agents()):
        raise TetherError("that session is running; close it first")
    nbytes, found = 0, False
    for path in store.session_paths(agent, session):
        for root, _d, files in os.walk(path):
            nbytes += sum(os.path.getsize(os.path.join(root, f)) for f in files if not os.path.islink(os.path.join(root, f)))
        if os.path.isfile(path):
            nbytes += os.path.getsize(path)
        store._remove(path)
        found = True
    name = None
    try:
        b = delete_backup_of_session(agent, session)
        nbytes, name, found = nbytes + b["bytes"], b["name"], True
    except TetherError:
        pass
    if not found:
        raise TetherError("there is nothing of that session to delete")
    return {"name": name or session[:8], "sessions": 1, "bytes": nbytes}


BACKUP_EVERY = 6 * 3600  # seconds between background backups


def backup_dir():
    chosen = env("BACKUP_DIR") or load_config().get("backup_dir") or "~/.local/share/tether/backups"
    return os.path.abspath(os.path.expanduser(chosen))


def live_home(agent):
    return store._claude_home() if agent == "claude" else store._copilot_home()


def session_files(agent, session, home=None):
    """[(absolute path, path relative to home)] for every regular file of one session under home."""
    home = home or live_home(agent)
    out = []
    for path in store.session_paths(agent, session, home):
        if os.path.islink(path):
            continue
        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                for name in files:
                    full = os.path.join(root, name)
                    if not os.path.islink(full):
                        out.append((full, os.path.relpath(full, home)))
        else:
            out.append((path, os.path.relpath(path, home)))
    return out


def _same_file(src, dst):
    try:
        a, b = os.stat(src), os.stat(dst)
    except OSError:
        return False
    return a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns


def copy_files(files, dst_home, overwrite=True):
    """Copy (src, rel) files under dst_home. Unchanged files are skipped; with overwrite=False an
    existing file is never replaced. Returns (copied, skipped, bytes copied)."""
    copied = skipped = nbytes = 0
    for src, rel in files:
        dst = os.path.join(dst_home, rel)
        if os.path.exists(dst) and (not overwrite or _same_file(src, dst)):
            skipped += 1
            continue
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        tmp = dst + ".tether-tmp"
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        copied += 1
        nbytes += os.path.getsize(dst)
    return copied, skipped, nbytes


def ensure_not_committable(path):
    """Transcripts hold whatever you worked on. If the backup folder is inside a git repository, it
    must be git-ignored there, or one `git add .` would put conversations in a commit."""
    git = core.find_tool("git")
    if not git:
        return
    probe = path
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    top = subprocess.run([git, "-C", probe, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode:
        return  # not in a repository
    os.makedirs(path, mode=0o700, exist_ok=True)
    ignored = subprocess.run([git, "-C", top.stdout.strip(), "check-ignore", "-q", os.path.join(path, "x")],
                             capture_output=True)
    if ignored.returncode:
        raise TetherError(f"{path} is inside the git repository {top.stdout.strip()} and is not git-ignored, so "
                          f"conversations could be committed. Add it to that repo's .gitignore, or set "
                          f"TETHER_BACKUP_DIR to a folder outside any repository.")


def prepare_backup_dir():
    dst = backup_dir()
    ensure_not_committable(dst)
    os.makedirs(dst, mode=0o700, exist_ok=True)
    os.chmod(dst, 0o700)
    return dst


def _selected_spaces(words, data, sid=None):
    """Spaces with links: the one with this exact id, else those whose stored name contains the
    words (all of them when no words)."""
    if sid:
        if sid not in data or not (data[sid].get("terminals") or data[sid].get("agents")):
            sys.exit(f"tether: no linked space with id {sid}")
        return {sid: data[sid]}
    needle = " ".join(words).strip().lower()
    return {k: sp for k, sp in data.items()
            if (sp.get("terminals") or sp.get("agents")) and needle in (sp.get("name") or k).lower()}


def source_session(e):
    """The session whose files hold this link's conversation. Normally its own; a fork nobody has typed
    in yet has no transcript of its own and is, for now, a copy of the session it was forked from."""
    own = e["session"]
    src = e.get("fork_of")
    if src and not store.session_exists(e["agent"], own) and not has_backup(e["agent"], own) \
            and (store.session_exists(e["agent"], src) or has_backup(e["agent"], src)):
        return src
    return own


def has_backup(agent, session):
    return bool(session_files(agent, session, os.path.join(backup_dir(), agent)))


def available(agent, session):
    """Can this session be resumed: the agent has it, or a backup can put it back."""
    return store.session_exists(agent, session) or has_backup(agent, session)


def link_summary(e, addr):
    return {"agent": e["agent"], "session": e["session"], "cwd": e.get("cwd"), "tab_name": e.get("tab_name") or "",
            "terminal": addr, "last_used": e.get("last_used"), **({"fork_of": e["fork_of"]} if e.get("fork_of") else {})}


def backup_status(data):
    """What the backup folder holds, and how it compares with the linked sessions (for `--status`)."""
    dst = backup_dir()
    cfg = load_config()
    flag = env("AUTO_BACKUP")
    auto = flag.lower() in ("1", "yes", "true", "on") if flag else bool(cfg.get("auto"))
    links = [e for sid in data for _, _, _, e in store.all_links({sid: data[sid]})]
    missing = [e for e in links if not store.session_exists(e["agent"], source_session(e))]
    size = 0
    for root, _dirs, files in os.walk(dst):
        for name in files:
            try:
                size += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    last = None
    try:
        with open(os.path.join(dst, "manifest.json")) as f:
            stamps = [sp.get("backed_up") for sp in json.load(f).get("spaces", {}).values() if sp.get("backed_up")]
        last = max(stamps, default=None)
    except (OSError, ValueError):
        pass
    return {"dir": dst, "exists": os.path.isdir(dst), "auto": auto, "last_backup": last, "bytes": size,
            "linked": len(links), "backed_up": sum(1 for e in links if has_backup(e["agent"], source_session(e))),
            "missing": len(missing), "recoverable": sum(1 for e in missing if has_backup(e["agent"], source_session(e)))}


def _clean(text, limit=90):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def session_title(agent, session):
    """What you first asked in a session, to tell sessions apart in a list ('' if unknown).
    Read from the agent's own copy, else from the backup. Only the start of the file is read."""
    for home in (None, os.path.join(backup_dir(), agent)):
        try:
            if agent == "claude":
                for path in store.session_paths("claude", session, home):
                    if not path.endswith(".jsonl"):
                        continue
                    with open(path, errors="replace") as f:
                        for _, line in zip(range(400), f):
                            try:
                                e = json.loads(line)
                            except ValueError:
                                continue
                            if e.get("type") != "user":
                                continue
                            c = (e.get("message") or {}).get("content")
                            if isinstance(c, list):
                                c = next((p.get("text") for p in c if isinstance(p, dict) and p.get("type") == "text"), "")
                            c = (c or "").strip()
                            if c and not c.startswith(("<", "Caveat:")):
                                return _clean(c)
            else:
                for path in store.session_paths("copilot", session, home):
                    events = os.path.join(path, "events.jsonl")
                    if not os.path.isfile(events):
                        continue
                    with open(events, errors="replace") as f:
                        for _, line in zip(range(400), f):
                            try:
                                e = json.loads(line)
                            except ValueError:
                                continue
                            if e.get("type") == "user.message":
                                c = ((e.get("data") or {}).get("content") or "").strip()
                                if c:
                                    return _clean(c)
        except OSError:
            continue
    return ""


def backup_list():
    """Every session the backup knows, newest first, with names a person can read."""
    try:
        with open(os.path.join(backup_dir(), "manifest.json")) as f:
            spaces = json.load(f).get("spaces", {})
    except (OSError, ValueError):
        spaces = {}
    try:
        live = {sp["id"]: sp.get("name") for sp in herdr.herdr_ls()}
    except TetherError:
        live = None
    with store.State() as st:
        data = dict(st.data)
    # linked under ANY space: a reopened space gets a new id, the manifest may still hold the old one
    linked = {e["session"] for _, _, _, e in store.all_links(data)}
    rows = []
    for sid, sp in spaces.items():
        for link in sp.get("links", []):
            agent, session = link.get("agent"), link.get("session")
            if agent not in AGENTS or not session:
                continue
            src = source_session(dict(link, agent=agent, session=session))  # an unused fork shows its source's files
            files = session_files(agent, src, os.path.join(backup_dir(), agent))
            in_agent = store.session_exists(agent, src)
            if not files and not in_agent:
                continue
            size = 0
            for full, _rel in files:
                try:
                    size += os.path.getsize(full)
                except OSError:
                    pass
            rows.append({
                "space_id": sid,
                "space": (live or {}).get(sid) or sp.get("name") or sid,
                "space_open": live is not None and sid in live,
                "tab": link.get("tab_name") or "",
                "agent": agent, "session": session, "cwd": link.get("cwd"),
                "last_used": link.get("last_used"), "backed_up": sp.get("backed_up"),
                "bytes": size, "title": session_title(agent, src),
                "in_agent": in_agent, "in_backup": bool(files), "linked": session in linked,
            })
    rows.sort(key=lambda r: (r["linked"], r.get("backed_up") or "", r.get("last_used") or ""), reverse=True)
    seen, unique = set(), []
    for r in rows:  # a session that moved between spaces appears once, under the space that has it now
        if (r["agent"], r["session"]) not in seen:
            seen.add((r["agent"], r["session"]))
            unique.append(r)
    unique.sort(key=lambda r: r.get("last_used") or "", reverse=True)
    return unique


def backup_cmd(args):
    flags, vals, words = parse_opts(args, flags=("--quiet", "--json", "--status", "--list", "--forget", "--everything"), valued=("--id", "--session", "--agent"))
    quiet = "--quiet" in flags
    if not ({"--status", "--list", "--forget"} & flags):
        store.safe_adopt()  # so tabs started without tether are backed up too
    if "--forget" in flags:
        if vals.get("--session"):  # one session's backup, whoever links it
            agent = vals.get("--agent") or next((a for a in AGENTS if has_backup(a, vals["--session"])
                                                  or store.session_exists(a, vals["--session"])), None)
            removed = (purge_session if "--everything" in flags else delete_backup_of_session)(agent, vals["--session"])
        elif not vals.get("--id"):
            sys.exit("usage: tether backup --forget --session <id> [--agent claude|copilot]  |  --forget --id <space-id>")
        else:
            removed = forget_backup(vals["--id"])
        print(json.dumps(removed) if "--json" in flags else
              f"removed {removed['sessions']} session(s) ({removed['bytes'] / 1e6:.1f} MB) of {removed['name']!r} from the backup")
        return
    if "--list" in flags:
        rows = backup_list()
        if "--json" in flags:
            print(json.dumps(rows))
            return
        if not rows:
            print("the backup holds no sessions yet; run `tether backup`")
        for r in rows:
            where = "on disk" if r["in_agent"] else "backup only (restore it)"
            print(f"{r['space']} / {r['tab'] or '-'}   {r['agent']}   {ago(r['last_used'])}   {r['bytes'] / 1e6:.1f} MB   [{where}]")
            print(f"    {r['title'] or '(no first message found)'}")
            print(f"    {r['agent']} --resume {r['session']}")
        return
    with store.State() as st:
        data = {k: v for k, v in st.data.items() if v.get("terminals") or v.get("agents")}
        if "--status" in flags:
            status = backup_status(data)
            if "--json" in flags:
                print(json.dumps(status))
            else:
                print(f"folder       {status['dir']}" + ("" if status["exists"] else "  (not created yet)"))
                print(f"automatic    {'on' if status['auto'] else 'off'}")
                print(f"last backup  {ago(status['last_backup']) if status['last_backup'] else 'never'}")
                print(f"size         {status['bytes'] / 1e6:.1f} MB")
                print(f"sessions     {status['backed_up']} of {status['linked']} linked sessions are backed up")
                print(f"missing      {status['missing']} linked transcript(s) gone, {status['recoverable']} recoverable "
                      f"from the backup")
            return
        chosen = _selected_spaces(words, st.data, vals.get("--id"))
    if not chosen:
        if "--json" in flags:
            print(json.dumps({"sessions": 0, "copied": 0, "skipped": 0, "bytes": 0, "empty": 0}))
        elif not quiet:
            print("nothing to back up: no matching linked spaces")
        return
    dst = prepare_backup_dir()
    copied = skipped = nbytes = sessions = empty = 0
    manifest_path = os.path.join(dst, "manifest.json")
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        manifest = {}
    manifest.setdefault("version", 1)
    spaces = manifest.setdefault("spaces", {})
    for sid, sp in chosen.items():
        links = []
        for _, _, addr, e in store.all_links({sid: sp}):
            files = session_files(e["agent"], source_session(e))
            if not files:
                empty += 1
            else:
                c, k, b = copy_files(files, os.path.join(dst, e["agent"]))
                copied, skipped, nbytes, sessions = copied + c, skipped + k, nbytes + b, sessions + 1
            links.append(link_summary(e, addr))
        spaces[sid] = {"name": sp.get("name") or sid, "links": links, "backed_up": now()}
    tmp = manifest_path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    os.replace(tmp, manifest_path)
    if "--json" in flags:
        print(json.dumps({"sessions": sessions, "copied": copied, "skipped": skipped, "bytes": nbytes,
                          "empty": empty, "dir": dst}))
    elif not quiet:
        print(f"backed up {sessions} session(s) to {dst}: {copied} file(s) new or changed "
              f"({nbytes / 1e6:.1f} MB), {skipped} unchanged" + (f"; {empty} had no transcript yet" if empty else ""))


def deleted_spaces():
    """Spaces the backup's manifest knows that tether no longer links, newest first."""
    try:
        with open(os.path.join(backup_dir(), "manifest.json")) as f:
            spaces = json.load(f).get("spaces", {})
    except (OSError, ValueError):
        return []
    with store.State() as st:
        data = st.data
    linked = {(e["agent"], e["session"]) for _, _, _, e in store.all_links(data)}
    rows = []
    for sid, sp in spaces.items():
        links = [l for l in sp.get("links", []) if l.get("agent") in AGENTS and UUID_RE.match(l.get("session", ""))]
        if sid in data or not links or all((l["agent"], l["session"]) in linked for l in links):
            continue
        rows.append({"id": sid, "name": sp.get("name") or sid, "backed_up": sp.get("backed_up") or "",
                     "links": [{"agent": l["agent"], "tab_name": l.get("tab_name") or "", "session": l["session"],
                                "transcript": store.session_exists(l["agent"], l["session"]),
                                "recoverable": has_backup(l["agent"], l["session"])} for l in links]})
    rows.sort(key=lambda r: r["backed_up"], reverse=True)
    return rows


def undelete_cmd(args):
    flags, vals, words = parse_opts(args, flags=("--list", "--json"), valued=("--id",))
    rows = deleted_spaces()
    if "--list" in flags:
        if "--json" in flags:
            print(json.dumps(rows))
        elif not rows:
            print("no deleted spaces in the backup")
        for r in rows:
            print(f"{r['name']:<32} {ago(r['backed_up'])}   ({len(r['links'])} session(s))")
        return
    if vals.get("--id"):
        pick_row = next((r for r in rows if r["id"] == vals["--id"]), None)
    elif words:
        q = " ".join(words).lower()
        hits = [r for r in rows if q in r["name"].lower()]
        exact = [r for r in hits if r["name"].lower() == q]
        hits = exact or hits
        pick_row = hits[0] if len(hits) == 1 else None
        if len(hits) > 1:
            sid = pick(f"{len(hits)} deleted spaces match {q!r}:",
                       [(f"{r['name']:<32} {ago(r['backed_up'])}", r["id"]) for r in hits])
            pick_row = next((r for r in hits if r["id"] == sid), None)
            if pick_row is None:
                return
    else:
        if not rows:
            sys.exit("tether: no deleted spaces in the backup")
        sid = pick("bring back which space?", [(f"{r['name']:<32} {ago(r['backed_up'])}", r["id"]) for r in rows])
        if sid is None:
            return
        pick_row = next(r for r in rows if r["id"] == sid)
    if pick_row is None:
        sys.exit("tether: no deleted space matches; `tether undelete --list` shows them")
    name, terms, restored = undelete_space(pick_row)
    if "--json" in flags:
        print(json.dumps({"id": pick_row["id"], "name": name, "sessions": len(terms), "restored": restored}))
    else:
        print(f"brought back {name!r} ({len(terms)} session(s)"
              + (f", {restored} transcript(s) restored from the backup" if restored else "")
              + "); it is a closed space: `tether open` it, or use the history button")


def undelete_space(pick_row):
    """Recreate a deleted space's links from the backup's record. -> (name, links, transcripts restored)"""
    with store.State() as st:
        if pick_row["id"] in st.data:
            raise TetherError("that space is already linked")
        existing = {workspace.norm_name(sp.get("name") or "") for sp in st.data.values()}
        name = pick_row["name"]
        if workspace.norm_name(name) in existing:
            name = f"{name} (restored)"
        terms = {}
        with open(os.path.join(backup_dir(), "manifest.json")) as f:
            stored = json.load(f)["spaces"][pick_row["id"]]["links"]
        for i, l in enumerate(stored):
            if l.get("agent") not in AGENTS or not UUID_RE.match(l.get("session", "")):
                continue
            addr = l.get("terminal") or f"t-restored{i}"
            terms[addr] = {"agent": l["agent"], "session": l["session"], "cwd": l.get("cwd") or os.path.expanduser("~"),
                           "tab": "", "tab_name": l.get("tab_name") or "", "created": l.get("last_used") or now(),
                           "last_used": l.get("last_used") or now()}
        st.data[pick_row["id"]] = {"name": name, "terminals": terms, "agents": {}}
        st.save()
    restored = sum(1 for e in terms.values() if not store.session_exists(e["agent"], e["session"])
                   and restore_session(e["agent"], e["session"]))
    return name, terms, restored


def delete_backup_of_session(agent, session):
    """Delete ONE session's backup: its copies in the backup folder and its record in the manifest.
    Allowed for any session, linked or not (the next `tether backup` copies a linked one again); never
    touches the agent's own files. -> {name, sessions, bytes}"""
    if agent not in AGENTS or not UUID_RE.match(session or ""):
        raise TetherError("not a valid session")
    home = os.path.join(backup_dir(), agent)
    nbytes = removed = 0
    for path in store.session_paths(agent, session, home):
        for root, _d, files in os.walk(path):
            nbytes += sum(os.path.getsize(os.path.join(root, f)) for f in files if not os.path.islink(os.path.join(root, f)))
        if os.path.isfile(path):
            nbytes += os.path.getsize(path)
        store._remove(path)
        removed = 1
        parent = os.path.dirname(path)
        while os.path.realpath(parent).startswith(os.path.realpath(home) + os.sep):
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)
    manifest_path = os.path.join(backup_dir(), "manifest.json")
    name = None
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        manifest = None
    if manifest:
        for sid in list(manifest.get("spaces", {})):
            sp = manifest["spaces"][sid]
            keep = [l for l in sp.get("links", []) if not (l.get("agent") == agent and l.get("session") == session)]
            if len(keep) != len(sp.get("links", [])):
                name = name or sp.get("name")
                removed = removed or 1
                if keep:
                    sp["links"] = keep
                else:
                    del manifest["spaces"][sid]
        tmp = manifest_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        os.replace(tmp, manifest_path)
    if not removed:
        raise TetherError("the backup has no copy of that session")
    return {"name": name or session[:8], "sessions": 1, "bytes": nbytes}


def forget_backup(sid, only_session=None):
    """Remove a DELETED space from the backup: its manifest entry and the backed-up copies of its
    sessions (unless another space still uses them). only_session: remove just that one session. Never touches the agents' own transcripts, and
    refuses a space tether still links."""
    with store.State() as st:
        if sid in st.data:
            raise TetherError("that space is still linked; delete it first, then remove its backup")
        linked = {(e["agent"], e["session"]) for _, _, _, e in store.all_links(st.data)}
    manifest_path = os.path.join(backup_dir(), "manifest.json")
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
        space = manifest["spaces"][sid]
    except (OSError, ValueError, KeyError):
        raise TetherError("the backup has no record of that space")
    others = {(l["agent"], l["session"]) for k, sp in manifest["spaces"].items() if k != sid
              for l in sp.get("links", []) if l.get("agent") in AGENTS and l.get("session")}
    removed = nbytes = 0
    kept_links = []
    for l in space.get("links", []):
        key = (l.get("agent"), l.get("session"))
        if only_session and key[1] != only_session:
            kept_links.append(l)
            continue
        if key[0] not in AGENTS or key in linked or key in others:
            continue
        home = os.path.join(backup_dir(), key[0])
        for path in store.session_paths(key[0], key[1], home):
            for root, _d, files in os.walk(path):
                nbytes += sum(os.path.getsize(os.path.join(root, f)) for f in files if not os.path.islink(os.path.join(root, f)))
            if os.path.isfile(path):
                nbytes += os.path.getsize(path)
            store._remove(path)
            parent = os.path.dirname(path)
            while os.path.realpath(parent).startswith(os.path.realpath(home) + os.sep):
                try:
                    os.rmdir(parent)
                except OSError:
                    break
                parent = os.path.dirname(parent)
        removed += 1
    if only_session and kept_links:
        manifest["spaces"][sid]["links"] = kept_links
    else:
        del manifest["spaces"][sid]
    tmp = manifest_path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    os.replace(tmp, manifest_path)
    return {"id": sid, "name": space.get("name") or sid, "sessions": removed, "bytes": nbytes}


def restore_session(agent, session):
    """Put a session's transcript back from the backup if the agent no longer has it. Never
    overwrites a file the agent has. Returns how many files were restored."""
    files = session_files(agent, session, os.path.join(backup_dir(), agent))
    if not files:
        return 0
    return copy_files(files, live_home(agent), overwrite=False)[0]


def restore_cmd(args):
    _, vals, words = parse_opts(args, valued=("--id", "--session", "--agent"))
    if vals.get("--session"):  # one session by its id, whether or not any space still links it
        session = vals["--session"]
        agent = vals.get("--agent")
        if agent not in AGENTS:
            agent = next((a for a in AGENTS if has_backup(a, session)), None)
        if agent is None or not UUID_RE.match(session):
            sys.exit(f"tether: no backup of session {session!r}")
        if store.session_exists(agent, session):
            print(f"{agent} already has session {session}; nothing to restore")
            return
        n = restore_session(agent, session)
        if not n:
            sys.exit(f"tether: no copy of {agent} session {session} in the backup")
        print(f"restored {agent} session {session} ({n} file(s)); resume it with: {agent} --resume {session}")
        return
    with store.State() as st:
        chosen = _selected_spaces(words, st.data, vals.get("--id"))
    total = missing = 0
    for sid, sp in chosen.items():
        for _, _, _, e in store.all_links({sid: sp}):
            src = source_session(e)
            if store.session_exists(e["agent"], src):
                continue
            missing += 1
            n = restore_session(e["agent"], src)
            total += bool(n)
            print(f"  {sp.get('name') or sid}: {e['agent']} {e['session'][:8]}  "
                  + (f"restored ({n} file(s))" if n else "no copy in the backup"))
    print(f"{total} of {missing} missing transcript(s) restored" if missing else "no transcript is missing")


def auto_backup_if_due():
    """Start `tether backup` in the background, at most every BACKUP_EVERY seconds, if enabled."""
    flag = env("AUTO_BACKUP")
    on = flag.lower() in ("1", "yes", "true", "on") if flag else bool(load_config().get("auto"))
    if not on:
        return
    stamp = os.path.join(state_dir(), "last-backup")
    try:
        if time.time() - os.path.getmtime(stamp) < BACKUP_EVERY:
            return
    except OSError:
        pass
    try:
        open(stamp, "w").close()
        subprocess.Popen([sys.executable, os.path.realpath(sys.argv[0]), "backup", "--quiet"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


def export_space(args):
    flags, vals, words = parse_opts(args, flags=("--json",), valued=("-o", "--output", "--id"))
    out = vals.get("-o") or vals.get("--output")
    if not words and "--id" not in vals:
        sys.exit("usage: tether export <space> | --id <space-id> [-o file.tar.gz | folder] [--json]")
    try:
        live = {sp["id"]: sp for sp in herdr.herdr_ls()}
    except TetherError:
        live = {}
    with store.State() as st:
        data = {k: v for k, v in st.data.items() if v.get("terminals") or v.get("agents")}
    if "--id" in vals:
        sid = vals["--id"] if vals["--id"] in data else None
    else:
        sid = workspace.match_space(" ".join(words), workspace.space_rows(live, data))
    if sid is None:
        sys.exit(f"tether: no space matches {' '.join(words) or vals.get('--id')!r}")
    sp = data[sid]
    name = (live[sid].get("name") if sid in live else None) or sp.get("name") or sid
    default = f"tether-export-{core._slug(name)}-{datetime.now().strftime('%Y%m%d')}.tar.gz"
    if out and os.path.isdir(out):
        out = os.path.join(out, default)  # a folder: name the file for the space
    out = core.unique_path(out or default)
    links = [(addr, e) for _, _, addr, e in store.all_links({sid: sp})]
    manifest = {"version": 1, "exported": now(), "space": {"name": name, "links": [
        link_summary(e, addr) for addr, e in links]}}
    members, missing = [], 0
    for _, e in links:
        files = session_files(e["agent"], e["session"])
        if not files:  # the agent has swept it: fall back to the backup
            files = session_files(e["agent"], e["session"], os.path.join(backup_dir(), e["agent"]))
        if not files:
            missing += 1
        members += [(path, f"files/{e['agent']}/{rel}") for path, rel in files]
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
        blob = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size, info.mtime, info.mode = len(blob), time.time(), 0o600
        tar.addfile(info, io.BytesIO(blob))
        for path, arc in members:
            tar.add(path, arcname=arc, recursive=False)
    if "--json" in flags:
        print(json.dumps({"path": os.path.abspath(out), "name": name, "sessions": len(links),
                          "files": len(members), "missing": missing}))
        return
    print(f"exported {name!r}: {len(links)} session(s), {len(members)} file(s) -> {out}"
          + (f"  ({missing} had no transcript anywhere)" if missing else ""))
    print("  this file holds the full conversations (0600). Keep it private; import it with `tether import`.")


def _member_session(parts):
    """The session uuid a `files/<agent>/...` path belongs to, or None if it is not session data."""
    agent, rel = parts[1], parts[2:]
    if agent == "claude":
        if rel[0] == "projects" and len(rel) == 3 and rel[2].endswith(".jsonl"):
            return rel[2][:-len(".jsonl")]
        if rel[0] == "projects" and len(rel) > 3:
            return rel[2]
        if rel[0] in ("file-history", "session-env") and len(rel) >= 3:
            return rel[1]
    elif rel[0] == "session-state" and len(rel) >= 3:
        return rel[1]
    return None


def import_space(args):
    flags, vals, words = parse_opts(args, flags=("--json",), valued=("--name",))
    name_override = vals.get("--name")
    if len(words) != 1:
        sys.exit("usage: tether import <file.tar.gz> [--name NAME] [--json]")
    try:
        tar = tarfile.open(words[0], "r:gz")
    except (OSError, tarfile.TarError) as err:
        sys.exit(f"tether: cannot read {words[0]}: {err}")
    with tar:
        try:
            manifest = json.load(tar.extractfile("manifest.json"))
            links = manifest["space"]["links"]
            for lk in links:
                if lk["agent"] not in AGENTS or not UUID_RE.match(lk["session"]):
                    raise ValueError("bad link")
        except (KeyError, ValueError, TypeError, AttributeError):
            sys.exit("tether: not a tether export (missing or invalid manifest.json)")
        wanted = {lk["session"] for lk in links}
        todo = []
        for m in tar.getmembers():
            if m.name == "manifest.json" or m.isdir():
                continue
            parts = m.name.split("/")
            bad = (not m.isfile() or m.name.startswith("/") or ".." in parts or "" in parts or "\x00" in m.name
                   or len(parts) < 4 or parts[0] != "files" or parts[1] not in AGENTS
                   or _member_session(parts) not in wanted)
            if bad:
                sys.exit(f"tether: refusing to import: unexpected entry {m.name!r} in the bundle")
            todo.append(m)
        restored = skipped = 0
        for m in todo:
            parts = m.name.split("/")
            dst = os.path.join(live_home(parts[1]), *parts[2:])
            if os.path.exists(dst):  # never replace a conversation this machine already has
                skipped += 1
                continue
            os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
            tmp = dst + ".tether-tmp"
            with tar.extractfile(m) as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(tmp, 0o600)
            os.replace(tmp, dst)
            restored += 1
    try:
        taken = {workspace.norm_name(sp.get("name") or "") for sp in herdr.herdr_ls()}
    except TetherError:
        taken = set()
    with store.State() as st:
        taken |= {workspace.norm_name(sp.get("name") or "") for sp in st.data.values()}
        name = name_override or manifest["space"].get("name") or "imported"
        base, n = name, 1
        while workspace.norm_name(name) in taken:
            n += 1
            name = f"{base} (imported{'' if n == 2 else ' ' + str(n - 1)})"
        sid = "imported-" + uuid4().hex[:12]
        stamp = now()
        terms = {}
        for k, lk in enumerate(links):
            terms[lk.get("terminal") or f"x{k}"] = {
                "agent": lk["agent"], "session": lk["session"], "cwd": lk.get("cwd") or "", "tab": "",
                "tab_name": lk.get("tab_name") or "", "created": stamp, "last_used": stamp}
        st.data[sid] = {"name": name, "terminals": terms, "agents": {}, "accessed": stamp}
        st.save()
    if "--json" in flags:
        print(json.dumps({"id": sid, "name": name, "sessions": len(links), "files": restored, "skipped": skipped}))
        return
    print(f"imported {name!r}: {len(links)} session(s), {restored} file(s) written, {skipped} already here")
    print(f"  it is a closed space now; restore it with: tether open {name!r}")
