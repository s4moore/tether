"""Continue: carry a conversation over to the other agent, in a new tab."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import uuid4

from tetherlib.core import AGENTS, TetherError, env, now, parse_opts, say

from tetherlib import backups, core, herdr, search, store, usage


CONTINUE_CAP = 150_000      # characters of conversation carried over: the newest part, whole turns

CONTINUE_NOTE = 300         # characters kept of one tool call or result

CONTINUE_CLAUDE_VERSION = "2.1.0"

CONTINUE_COPILOT_VERSION = "1.0.116"

CONTINUE_COPILOT_MODEL = "claude-sonnet-5.5"


def _shorten(value, n):
    text = " ".join(str(value).split())
    return text if len(text) <= n else text[:n] + "…"


def _tool_what(args):
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return args
    if isinstance(args, dict):
        for key in ("command", "file_path", "path", "pattern", "query", "url"):
            if args.get(key):
                return args[key]
        return json.dumps(args)
    return args


def continue_turns(agent, path):
    """The conversation in a transcript as [(role, text)]: what was said, tool calls and results as short
    bracketed notes inside the assistant turn they belong to, same-role lines merged, starting with the user."""
    out = []

    def add(role, text):
        if out and out[-1][0] == role:
            out[-1] = (role, out[-1][1] + "\n\n" + text)
        else:
            out.append((role, text))

    try:
        with open(path, errors="replace") as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                if agent == "claude":
                    if e.get("isSidechain") or e.get("type") not in ("user", "assistant") or e.get("isMeta"):
                        continue
                    content = (e.get("message") or {}).get("content")
                    for x in [{"type": "text", "text": content}] if isinstance(content, str) else (content or []):
                        if not isinstance(x, dict):
                            continue
                        text = (x.get("text") or "").strip()
                        if x.get("type") == "text" and text and not text.startswith(("<", "Caveat:")):
                            add(e["type"], text)
                        elif x.get("type") == "tool_use":
                            add("assistant", f"[used {x.get('name')}: {_shorten(_tool_what(x.get('input')), CONTINUE_NOTE)}]")
                        elif x.get("type") == "tool_result":
                            r = x.get("content")
                            r = " ".join(y.get("text", "") for y in r if isinstance(y, dict)) if isinstance(r, list) else r
                            add("assistant", f"[result: {_shorten(r, CONTINUE_NOTE)}]")
                else:
                    d = e.get("data") or {}
                    kind = e.get("type")
                    if kind in ("user.message", "assistant.message") and isinstance(d.get("content"), str) and d["content"].strip():
                        add("user" if kind == "user.message" else "assistant", d["content"].strip())
                    elif kind == "tool.execution_start":
                        add("assistant", f"[used {d.get('toolName')}: {_shorten(_tool_what(d.get('arguments')), CONTINUE_NOTE)}]")
                    elif kind == "tool.execution_complete":
                        r = d.get("result")
                        r = r.get("content") if isinstance(r, dict) else r
                        add("assistant", f"[result: {_shorten(r, CONTINUE_NOTE)}]")
    except OSError:
        pass
    while out and out[0][0] != "user":
        out.pop(0)
    return out


def continue_trim(turns, source, target):
    """Keep the newest whole turns that fit CONTINUE_CAP. If older ones are left out, say so in a first exchange."""
    kept, size = [], 0
    for turn in reversed(turns):
        if kept and size + len(turn[1]) > CONTINUE_CAP:
            break
        kept.append(turn)
        size += len(turn[1])
    kept.reverse()
    while kept and kept[0][0] != "user":
        kept.pop(0)
    dropped = len(turns) - len(kept)
    if dropped:
        kept = [("user", f"[The first {dropped} messages of this conversation were left out when it was moved from {source} to {target}.]"),
                ("assistant", "Understood.")] + kept
    return kept, dropped


def _stamps(count):
    """`count` increasing ISO times ending now, a second apart."""
    end = datetime.now(timezone.utc)
    return [(end - timedelta(seconds=count - i)).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (i % 1000)
            for i in range(count)]


def write_claude_session(turns, cwd):
    """A new Claude Code session holding `turns`; -> (session id, file)."""
    sid = str(uuid4())
    folder = os.path.join(store._claude_home(), "projects", "".join(c if c.isalnum() else "-" for c in os.path.realpath(cwd)))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, sid + ".jsonl")
    parent, stamps = None, _stamps(len(turns))
    with open(path, "w") as f:
        for (role, text), when in zip(turns, stamps):
            uid = str(uuid4())
            line = {"parentUuid": parent, "isSidechain": False, "type": role, "uuid": uid, "timestamp": when, "userType": "external",
                    "entrypoint": "cli", "cwd": cwd, "sessionId": sid, "version": CONTINUE_CLAUDE_VERSION}
            if role == "user":
                line["message"] = {"role": "user", "content": text}
            else:
                line["message"] = {"id": "msg_" + uuid4().hex[:24], "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
                                   "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                                   "usage": {"input_tokens": 0, "output_tokens": 0}}
            f.write(json.dumps(line) + "\n")
            parent = uid
    os.chmod(path, 0o600)
    return sid, path


def write_copilot_session(turns, cwd, name=""):
    """A new Copilot CLI session holding `turns`; -> (session id, folder)."""
    sid = str(uuid4())
    folder = os.path.join(store._copilot_home(), "session-state", sid)
    for sub in ("checkpoints", "files", "research"):
        os.makedirs(os.path.join(folder, sub), exist_ok=True)
    stamps = _stamps(len(turns) * 4 + 3)
    events, parent = [], None

    def add(kind, data):
        nonlocal parent
        eid = str(uuid4())
        events.append({"type": kind, "data": data, "id": eid, "timestamp": stamps[min(len(events), len(stamps) - 1)], "parentId": parent})
        parent = eid

    add("session.start", {"sessionId": sid, "version": 1, "producer": "copilot-agent", "copilotVersion": CONTINUE_COPILOT_VERSION,
                          "startTime": stamps[0], "context": {"cwd": cwd}, "alreadyInUse": False})
    add("session.context_changed", {"cwd": cwd})
    index, turn_no = 0, 0
    while index < len(turns):
        role, text = turns[index]
        index += 1
        if role != "user":
            continue
        inter, mid = str(uuid4()), str(uuid4())
        add("user.message", {"content": text, "transformedContent": text, "messageId": mid, "interactionId": inter, "turnId": str(turn_no),
                             "delivery": "idle", "hasSubmittedTask": True, "supportedNativeDocumentMimeTypes": []})
        add("assistant.turn_start", {"turnId": str(turn_no), "interactionId": inter, "agentMode": "interactive"})
        if index < len(turns) and turns[index][0] == "assistant":
            add("assistant.message", {"messageId": str(uuid4()), "content": turns[index][1], "toolRequests": [], "interactionId": inter,
                                      "turnId": str(turn_no), "model": CONTINUE_COPILOT_MODEL, "providerId": "copilot",
                                      "originatingMessageId": mid, "rte": True})
            index += 1
        add("assistant.turn_end", {"turnId": str(turn_no)})
        turn_no += 1
    with open(os.path.join(folder, "events.jsonl"), "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    title = next((t for r, t in turns if r == "user"), "converted")
    with open(os.path.join(folder, "workspace.yaml"), "w") as f:
        f.write(f"id: {sid}\ncwd: {json.dumps(cwd)}\nclient_name: github/cli\nlast_client_name: github/cli\n"
                f"name: {json.dumps((name or _shorten(title, 80)))}\nuser_named: false\nsummary_count: 0\nfork_count: 0\n"
                f"created_at: {stamps[0]}\nupdated_at: {stamps[-1]}\n")
    for name_ in ("events.jsonl", "workspace.yaml"):
        os.chmod(os.path.join(folder, name_), 0o600)
    return sid, folder


def continue_session(where, target=None):
    """Convert the conversation of the terminal `where` into the other agent's store: writes a NEW session and leaves the
    terminal's own link and session alone. -> a dict: source, target, old, new (session ids), count, dropped, cwd.
    Exits with the reason if it cannot."""
    with store.State() as st:
        entry = ((st.data.get(where.space_id) or {}).get("terminals") or {}).get(where.terminal)
    if not entry:
        sys.exit("tether: nothing is linked to this terminal yet: start an agent with `tether claude` or `tether copilot` first")
    source = entry["agent"]
    target = target or ("copilot" if source == "claude" else "claude")
    if target == source:
        sys.exit(f"tether: this terminal already uses {source}")
    if not core.find_tool(target):
        sys.exit(f"tether: {target} not found on PATH")
    session = entry["session"]
    if not search.transcript_file(source, session) and backups.restore_session(source, session):
        say(f"restored the {source} session from the backup")
    path = search.transcript_file(source, session)
    if not path:
        sys.exit(f"tether: this terminal's {source} session has no conversation yet, so there is nothing to carry over")
    turns = continue_turns(source, path)
    if not turns:
        sys.exit(f"tether: nothing readable in the {source} conversation")
    turns, dropped = continue_trim(turns, source, target)
    cwd = entry.get("cwd") if entry.get("cwd") and os.path.isdir(entry["cwd"]) else os.getcwd()
    if env("DRY_RUN"):
        print(f"would write a {target} session of {len(turns)} messages from the {source} session {session[:8]} and open it in a new tab")
        return None
    new_id, _ = write_claude_session(turns, cwd) if target == "claude" else write_copilot_session(turns, cwd, where.name)
    return {"source": source, "target": target, "old": session, "new": new_id, "count": len(turns), "dropped": dropped, "cwd": cwd}


def continue_cmd(args):
    """tether continue [claude|copilot] [--tab <id>]: continue this terminal's conversation with the other agent, in a new tab."""
    _, vals, words = parse_opts(args, valued=("--tab",))
    if len(words) > 1 or (words and words[0] not in AGENTS):
        sys.exit("usage: tether continue [claude|copilot] [--tab <tab-id>]")
    if vals.get("--tab"):
        where = herdr.terminal_in_tab(vals["--tab"])
    else:
        where, why = herdr.resolve_where()
        if where is None:
            sys.exit(f"tether: {why}")
    t0 = time.time()
    done = continue_session(where, words[0] if words else None)
    if done is None:
        return
    herdr.ensure_app()
    target, label = done["target"], f"{where.tab_name} · {done['target']}" if where.tab_name else done["target"]
    try:
        made = herdr.herdr_do(["tab.new", "--space", where.space_id, "--folder", done["cwd"], "--name", label])
        term = made.get("terminal")
        if not term:
            raise TetherError("herdr-app did not return a terminal for the new tab")
    except TetherError as err:
        sys.exit(f"tether: converted, but could not open a tab ({err}); the {target} session is {done['new']} ({target} --resume {done['new']})")
    hit = herdr.find_terminal(herdr.herdr_ls(), term)
    with store.State() as st:
        space = st.data.setdefault(where.space_id, {})
        space.setdefault("agents", {})
        space.setdefault("terminals", {})[term] = {
            "agent": target, "session": done["new"], "cwd": done["cwd"], "tab": (hit[1].get("id") if hit else "") or "", "tab_name": label,
            "created": now(), "last_used": now(), "continued_from": {"agent": done["source"], "session": done["old"], "at": now()}}
        st.save()
    usage.record("continue", source=done["source"], target=target, messages=done["count"], dropped=done["dropped"], seconds=usage.since(t0))
    say(f"{done['count']} messages carried over from {done['source']} to {target}"
        + (f" ({done['dropped']} older ones left out to fit)" if done["dropped"] else "")
        + f"; the original tab and its {done['source']} session {done['old'][:8]} are untouched")
    say("tool calls and their results came across as short notes, not as tool history; images and attachments did not")
    hc = core.find_tool("hc")
    r = subprocess.run([hc, "send", term, f"{herdr.tether_cmd()} resume"], capture_output=True, text=True) if hc else None
    if r is None or r.returncode:
        say(f"run `tether` in the new tab ({term}) to continue with {target}")
    else:
        print(f"continuing with {target} in a new tab ({label!r})")
