"""Reading another conversation (`tether read`)."""

import json
import sys

from tetherlib.core import TetherError, parse_opts, say

from tetherlib import search


READ_CAP = 30000   # characters printed at most

READ_MESSAGE = 1500  # one message is cut here unless --full


def read_cmd(args):
    """tether read <space> [--last N] [--commands] [--full] [--id X | --session S] [--json]: what a space's conversation said."""
    flags, vals, words = parse_opts(args, flags=("--commands", "--full", "--json"), valued=("--last", "--id", "--session"))
    try:
        last = max(1, int(vals.get("--last") or 12))
    except ValueError:
        sys.exit("tether: --last takes a number")
    if not words and not vals.get("--id") and not vals.get("--session"):
        sys.exit("usage: tether read <space name> [--last N] [--commands] [--full] [--id <space-id> | --session <id>] [--json]")
    convs = find_conversations(words, vals.get("--id"), vals.get("--session"))
    out = []
    for c in convs:
        msgs = conversation_text(c["agent"], c["path"], as_lines=True)
        if "--commands" not in flags:
            msgs = [m for m in msgs if not m.startswith("COMMAND: ")]
        total = len(msgs)
        shown = msgs[-last:]
        if "--full" not in flags:
            shown = [m if len(m) <= READ_MESSAGE else m[:READ_MESSAGE] + " [cut]" for m in shown]
        out.append({"space": c["space"], "state": c.get("state"), "agent": c["agent"], "session": c["session"], "tab": c.get("tab"),
                    "last_used": c.get("last_used"), "messages_total": total, "messages": shown})
    if "--json" in flags:
        print(json.dumps(out, indent=2))
        return
    budget = READ_CAP
    for c in out:
        head = f"=== {c['space']} ({c['agent']} session {c['session'][:8]}, last {len(c['messages'])} of {c['messages_total']} messages) ==="
        print(head)
        body = "\n".join(c["messages"])
        if len(body) > budget:
            body = "[older part left out]\n" + body[-budget:]
        budget = max(0, budget - len(body))
        print(body + "\n")
    say("what was said only, never tool output; summarise it for the user rather than pasting it, it can hold personal data or secrets.")


EXTRACT_CAP = 60_000  # characters of one conversation returned at most (start and end kept)


def conversation_text(agent, path, cap=EXTRACT_CAP, as_lines=False):
    """-> (text, turns, commands). What a conversation said (USER / ASSISTANT lines) and the shell commands the agent ran
    (COMMAND lines, each cut short, repeats left out). Command OUTPUT is never read: it can hold customer data."""
    lines, seen, turns, cmds = [], set(), 0, 0
    try:
        with open(path, errors="replace") as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                t, said, cmd = e.get("type"), None, None
                if agent == "claude":
                    if t in ("user", "assistant") and not e.get("isMeta"):
                        c = (e.get("message") or {}).get("content")
                        if isinstance(c, list):
                            for part in c:
                                if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("name") == "Bash":
                                    cmd = (part.get("input") or {}).get("command")
                            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                        said = (t.upper(), c)
                else:
                    d = e.get("data") or {}
                    if t in ("user.message", "assistant.message"):
                        said = ("USER" if t == "user.message" else "ASSISTANT", d.get("content"))
                    elif t == "tool.execution_start" and d.get("toolName") in (None, "bash"):
                        a = d.get("arguments") or d.get("input") or {}
                        cmd = a.get("command") if isinstance(a, dict) else None
                if said and isinstance(said[1], str) and said[1].strip() and not said[1].strip().startswith(("<", "Caveat:")):
                    lines.append(f"{said[0]}: {said[1].strip()}")
                    turns += 1
                if isinstance(cmd, str) and cmd.strip() and cmd.strip() not in seen:
                    seen.add(cmd.strip())
                    lines.append("COMMAND: " + cmd.strip()[:700])
                    cmds += 1
    except OSError:
        pass
    if as_lines:
        return lines
    text = "\n".join(lines)
    if len(text) > cap:
        text = text[:cap // 2] + "\n[... middle left out ...]\n" + text[-cap // 2:]
    return text, turns, cmds


def find_conversations(words, space_id, session):
    """The conversations of a space (by id or name) or one session, from the agent's own files or the backup."""
    docs = [d for d in search.search_corpus(["open", "closed", "deleted"]) if d.get("path")]
    if session:
        hit = [d for d in docs if d["session"].startswith(session)]
    elif space_id:
        hit = [d for d in docs if d["space_id"] == space_id]
    else:
        needle = " ".join(words).strip().lower()
        if not needle:
            sys.exit("usage: tether read <space name> [--id <space-id> | --session <id>] [--last N]")
        exact = {d["space_id"] for d in docs if d["space"].lower() == needle}
        ids = exact or {d["space_id"] for d in docs if needle in d["space"].lower()}
        if len(ids) > 1:
            raise TetherError("more than one space matches: " + ", ".join(sorted({d["space"] for d in docs if d["space_id"] in ids})))
        hit = [d for d in docs if d["space_id"] in ids]
    if not hit:
        raise TetherError("no conversation found there (a space nobody has typed in has none)")
    return hit
