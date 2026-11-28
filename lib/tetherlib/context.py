"""Context: facts every agent can look up, in a small tree of plain files."""

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

from tetherlib.core import AGENTS, TetherError, _json_file, env, load_config, now, parse_opts, say

from tetherlib import ai, reading, search


CONTEXT_INDEX_MAX = 40       # index lines before the index is too long to read cheaply

CONTEXT_TOPIC_MAX = 100      # facts in one topic before it should be split

CONTEXT_STALE_DAYS = 365

CONTEXT_CHECK_EVERY = 86400  # seconds between background checks

CONTEXT_FACT_MAX = 300

TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")

FACT_RE = re.compile(r"^- (?P<fact>.+?)(?:\s+_\((?P<date>\d{4}-\d\d-\d\d)(?:, from: (?P<source>[^)]*))?\)_)?\s*$")

INDEX_RE = re.compile(r"^- \[(?P<topic>[a-z0-9-]+)\]\((?P<file>[^)]+)\)(?: — (?P<about>.*))?\s*$")

CONTEXT_LINK_RE = re.compile(r"\[\[([a-z0-9][a-z0-9-]*)\]\]")

SECRETISH_RE = re.compile(r"(?i)(password|passwd|secret|api[ _-]?key|token|bearer|private key|authorization)\s*[:=]|\b[A-Za-z0-9+/_-]{32,}\b|-----BEGIN")


def context_dir():
    return os.path.expanduser(env("CONTEXT_DIR") or load_config().get("context_dir") or "~/.local/share/tether/context")


def _norm_fact(text):
    return re.sub(r"\s+", " ", text.lower()).strip(" .;:")


def context_topics(d):
    try:
        return sorted(f[:-3] for f in os.listdir(d) if f.endswith(".md") and f != "index.md" and TOPIC_RE.match(f[:-3]))
    except OSError:
        return []


def context_facts(d, topic):
    """[(line number, fact, date or None, source or None)] of a topic file."""
    out = []
    try:
        with open(os.path.join(d, topic + ".md"), errors="replace") as f:
            for n, line in enumerate(f, 1):
                m = FACT_RE.match(line.rstrip("\n"))
                if m:
                    out.append((n, m["fact"], m["date"], m["source"]))
    except OSError:
        pass
    return out


def context_index(d):
    """{topic: (file, about)} in index order, plus the number of lines in the file."""
    out, lines = {}, 0
    try:
        with open(os.path.join(d, "index.md"), errors="replace") as f:
            for line in f:
                lines += 1
                m = INDEX_RE.match(line.rstrip("\n"))
                if m:
                    out[m["topic"]] = (m["file"], m["about"] or "")
    except OSError:
        pass
    return out, lines


def add_fact(d, topic, fact, source="", about=""):
    """Append a fact to a topic (creating the topic and its index line if new). -> "added" or "already there"."""
    if not TOPIC_RE.match(topic):
        raise TetherError(f"topic names are lowercase words with dashes (a-z, 0-9, -), not {topic!r}")
    fact = " ".join(fact.split())
    if not fact or len(fact) > CONTEXT_FACT_MAX:
        raise TetherError(f"a fact is one line of at most {CONTEXT_FACT_MAX} characters")
    if SECRETISH_RE.search(fact):
        raise TetherError("that looks like it holds a secret (a password, key or token); facts are stored in plain text, so it was not saved")
    os.makedirs(d, mode=0o700, exist_ok=True)
    index, _ = context_index(d)
    path = os.path.join(d, topic + ".md")
    if topic not in index and not about:
        raise TetherError(f"'{topic}' is a new topic: say what it covers, so the index can say when to read it (--about \"...\")")
    if any(_norm_fact(fact) == _norm_fact(x[1]) for x in context_facts(d, topic)):
        return "already there"
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(f"# {topic}\n\n")
    meta = f"_({datetime.now().date().isoformat()}" + (f", from: {' '.join(source.split())[:80].replace(')', '')}" if source else "") + ")_"
    with open(path, "a") as f:
        f.write(f"- {fact}  {meta}\n")
    if topic not in index:
        with open(os.path.join(d, "index.md"), "a") as f:
            if not os.path.getsize(os.path.join(d, "index.md")):
                f.write("# Context index\n\nOne line per topic: read the topic file that matches the question.\n\n")
            f.write(f"- [{topic}]({topic}.md) — {' '.join(about.split())}\n")
    return "added"


def context_check_issues(d):
    """What is wrong with the store: [{"level": "warn"|"info", "kind", "where", "text"}]."""
    issues = []

    def add(level, kind, where, text):
        issues.append({"level": level, "kind": kind, "where": where, "text": text})

    index, lines = context_index(d)
    topics = context_topics(d)
    if lines > CONTEXT_INDEX_MAX:
        add("warn", "index-long", "index.md", f"the index has {lines} lines (more than {CONTEXT_INDEX_MAX}): group topics so it stays cheap to read")
    for topic, (file, _) in index.items():
        if not os.path.exists(os.path.join(d, file)):
            add("warn", "index-dead", "index.md", f"the index lists {topic} but {file} does not exist")
    for topic in topics:
        if topic not in index:
            add("warn", "orphan", f"{topic}.md", f"{topic}.md is not in the index, so no agent will find it")
    seen = {}
    today = datetime.now().date()
    for topic in topics:
        facts = context_facts(d, topic)
        if not facts:
            add("info", "empty", f"{topic}.md", f"{topic}.md has no facts")
        if len(facts) > CONTEXT_TOPIC_MAX:
            add("warn", "topic-long", f"{topic}.md", f"{topic}.md has {len(facts)} facts (more than {CONTEXT_TOPIC_MAX}): split it into subtopics")
        try:
            with open(os.path.join(d, topic + ".md"), errors="replace") as f:
                text = f.read()
        except OSError:
            text = ""
        for link in sorted(set(CONTEXT_LINK_RE.findall(text))):
            if link not in topics:
                add("warn", "link-dead", f"{topic}.md", f"{topic}.md links to [[{link}]], which does not exist")
        for n, fact, date, _ in facts:
            key = _norm_fact(fact)
            if key in seen:
                add("info", "duplicate", f"{topic}.md:{n}", f"duplicate of {seen[key]}: {fact[:60]}")
            else:
                seen[key] = f"{topic}.md:{n}"
            if date:
                try:
                    if (today - datetime.strptime(date, "%Y-%m-%d").date()).days > CONTEXT_STALE_DAYS:
                        add("info", "stale", f"{topic}.md:{n}", f"older than {CONTEXT_STALE_DAYS} days ({date}): still true? {fact[:60]}")
                except ValueError:
                    pass
    return issues


def context_fix(d, issues):
    """The safe repairs, after a copy of the whole store in .backup/: index lines for orphan topics, dropping index
    lines whose file is gone, removing exact duplicates inside one file. -> a list of what was done."""
    done = []
    if not issues:
        return done
    backup = os.path.join(d, ".backup")
    stamp = os.path.join(backup, datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copytree(d, stamp, ignore=shutil.ignore_patterns(".backup", ".proposals.json", ".report.json", ".check.lock"))
    for old in sorted(os.listdir(backup))[:-5]:
        shutil.rmtree(os.path.join(backup, old), ignore_errors=True)
    index_path = os.path.join(d, "index.md")
    for i in issues:
        if i["kind"] == "orphan":
            topic = i["where"][:-3]
            with open(index_path, "a") as f:
                f.write(f"- [{topic}]({topic}.md) — (no description yet: say when to read it)\n")
            done.append(f"added {topic} to the index")
        elif i["kind"] == "index-dead":
            topic = re.match(r"the index lists (\S+)", i["text"]).group(1)
            with open(index_path) as f:
                kept = [x for x in f if not (INDEX_RE.match(x.rstrip("\n")) and INDEX_RE.match(x.rstrip("\n"))["topic"] == topic)]
            with open(index_path, "w") as f:
                f.writelines(kept)
            done.append(f"removed {topic} from the index (its file is gone)")
    for topic in context_topics(d):  # exact duplicates inside one file
        path, seen, kept, dropped = os.path.join(d, topic + ".md"), set(), [], 0
        with open(path, errors="replace") as f:
            for line in f:
                m = FACT_RE.match(line.rstrip("\n"))
                if m and _norm_fact(m["fact"]) in seen:
                    dropped += 1
                    continue
                if m:
                    seen.add(_norm_fact(m["fact"]))
                kept.append(line)
        if dropped:
            with open(path, "w") as f:
                f.writelines(kept)
            done.append(f"removed {dropped} duplicate line(s) from {topic}.md")
    return done


def context_check(args):
    flags, _, _ = parse_opts(args, flags=("--fix", "--quiet", "--json", "--background"))
    d = context_dir()
    if not os.path.isdir(d):
        if "--background" not in flags:
            print(f"no context store yet ({d}): add a fact with `tether context add <topic> \"fact\" --about \"what it covers\"`")
        return
    if "--background" in flags:  # one background check at a time; the caller has already stamped the time
        with open(os.path.join(d, ".check.lock"), "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return
            return _context_check_run(d, flags)
    return _context_check_run(d, flags)


def _context_check_run(d, flags):
    issues = context_check_issues(d)
    fixed = context_fix(d, issues) if "--fix" in flags else []
    if fixed:
        issues = context_check_issues(d)
    try:
        with open(os.path.join(d, ".report.json"), "w") as f:
            json.dump({"at": now(), "issues": issues}, f)
        open(os.path.join(d, ".last-check"), "w").close()
    except OSError:
        pass
    if "--json" in flags:
        print(json.dumps({"issues": issues, "fixed": fixed}))
    elif "--quiet" not in flags and "--background" not in flags:
        index, _ = context_index(d)
        topics = context_topics(d)
        print(f"{len(topics)} topic(s), {sum(len(context_facts(d, t)) for t in topics)} fact(s) in {d}")
        for line in fixed:
            print(f"  fixed: {line}")
        for i in issues:
            print(f"  {i['level']:<4} {i['where']}: {i['text']}")
        print("no problems found" if not issues else f"{len(issues)} thing(s) to look at" + ("" if "--fix" in flags else "; `tether context check --fix` repairs the safe ones"))


def context_check_if_due():
    """Start `tether context check` in the background, at most once a day, if a store exists. Costs nothing but a process:
    the check reads a few small files and calls no model."""
    d = context_dir()
    if not os.path.isfile(os.path.join(d, "index.md")):
        return
    stamp = os.path.join(d, ".last-check")
    try:
        if time.time() - os.path.getmtime(stamp) < CONTEXT_CHECK_EVERY:
            return
    except OSError:
        pass
    try:
        open(stamp, "w").close()
        subprocess.Popen([sys.executable, os.path.realpath(sys.argv[0]), "context", "check", "--background", "--fix"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def context_hint():
    """One line when there are proposed facts to review or the last background check left things to look at, at most once a day."""
    d = context_dir()
    shown = os.path.join(d, ".hint-shown")
    try:
        if time.time() - os.path.getmtime(shown) < CONTEXT_CHECK_EVERY:
            return
    except OSError:
        pass
    waiting = len(context_queue(d))
    try:
        with open(os.path.join(d, ".report.json")) as f:
            issues = [i for i in json.load(f).get("issues", []) if i.get("level") == "warn"]
    except (OSError, ValueError):
        issues = []
    parts = ([f"{waiting} proposed fact(s) to review (tether context proposals)"] if waiting else []) \
        + ([f"{len(issues)} thing(s) to look at (tether context check)"] if issues else [])
    if parts:
        say("the context store has " + " and ".join(parts))
        try:
            open(shown, "w").close()
        except OSError:
            pass


FIND_STOP = frozenset("a an the and or of to in on at for from with by is are was be do does did we you i our my your us it this that "
                      "what which where how when who why can could should about use used using".split())


def _stem(word):
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def _stems(text):
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower())}


def _stem_hit(q, have):
    return any(h == q or (len(q) >= 4 and (h.startswith(q) or q.startswith(h)) and len(h) >= 4) for h in have)


def context_find(args):
    """Search the store with no model call. Forgiving on purpose, because the asker is an agent guessing words: a hyphen is a space,
    "tracking" finds "tracks", and when no fact has every word, the ones with the most words come first (never nothing if one word hits)."""
    flags, _, words = parse_opts(args, flags=("--json",))
    if not words:
        sys.exit("usage: tether context find <words> [--json]")
    d = context_dir()
    terms = [_stem(w) for w in re.findall(r"[a-z0-9]+", " ".join(words).lower()) if w not in FIND_STOP] or \
            [_stem(w) for w in re.findall(r"[a-z0-9]+", " ".join(words).lower())]
    index, _ = context_index(d)
    scored = []
    for topic in context_topics(d):
        about = index.get(topic, ("", ""))[1]
        for n, fact, date, source in context_facts(d, topic):
            have = _stems(f"{topic} {about} {fact}")
            score = sum(_stem_hit(q, have) for q in terms)
            if score:
                scored.append((score, {"topic": topic, "line": n, "fact": fact, "date": date, "source": source}))
    for topic, (file, about) in index.items():  # a topic whose description matches points the way too
        if not any(h["topic"] == topic for _, h in scored):
            score = sum(_stem_hit(q, _stems(f"{topic} {about}")) for q in terms)
            if score:
                scored.append((score, {"topic": topic, "line": 0, "fact": f"(topic) {about}", "date": None, "source": None}))
    best = max((sc for sc, _ in scored), default=0)
    full = [h for sc, h in scored if sc == len(terms)]
    hits = full or [h for sc, h in sorted(scored, key=lambda x: -x[0]) if sc >= max(1, best)] if best else []
    if "--json" in flags:
        print(json.dumps(hits[:40]))
        return
    for h in hits[:20]:
        print(f"{h['topic']}: {h['fact']}" + (f"  ({h['date']})" if h["date"] else ""))
    if len(hits) > 20:
        print(f"... {len(hits) - 20} more: narrow the words, or `tether context list <topic>`")
    if not hits:
        print("nothing found" + (" for those words; the topics are:" if index else ""))
        for topic, (_, about) in index.items():
            print(f"  {topic}: {about}")
        if index:
            print("read the likely one with `tether context list <topic>`")


def context_remove(args):
    """Delete one saved fact: `remove <topic> --line N` (line numbers come from `list --json`). The topic file stays, even if it empties."""
    _, vals, words = parse_opts(args, valued=("--line",))
    d = context_dir()
    try:
        line = int(vals.get("--line") or "")
    except ValueError:
        sys.exit("usage: tether context remove <topic> --line N  (line numbers: tether context list --json)")
    if len(words) != 1 or not TOPIC_RE.match(words[0]):
        sys.exit("usage: tether context remove <topic> --line N")
    path = os.path.join(d, words[0] + ".md")
    try:
        with open(path, errors="replace") as f:
            lines = f.read().split("\n")
    except OSError:
        sys.exit(f"tether: no topic called '{words[0]}' (tether context list)")
    m = FACT_RE.match(lines[line - 1]) if 0 < line <= len(lines) else None
    if not m:
        sys.exit(f"tether: line {line} of {words[0]}.md is not a fact (tether context list {words[0]})")
    del lines[line - 1]
    with open(path, "w") as f:
        f.write("\n".join(lines))
    log = context_auto_added(d)
    kept = [e for e in log if not (e["topic"] == words[0] and _norm_fact(e["fact"]) == _norm_fact(m["fact"]))]
    if len(kept) != len(log):
        context_auto_log(d, kept)
    print(f"removed: {m['fact']}")


def context_list(args):
    d = context_dir()
    if args and args[0] == "--json":
        index, _ = context_index(d)
        topics = list(index) + [t for t in context_topics(d) if t not in index]
        print(json.dumps([{"topic": t, "about": index.get(t, ("", ""))[1], "facts": [
            {"line": n, "fact": fact, "date": date, "source": source, "auto": bool(source and source.startswith("auto: "))}
            for n, fact, date, source in context_facts(d, t)]} for t in topics if t in index or os.path.exists(os.path.join(d, t + ".md"))]))
        return
    if not args:
        index, _ = context_index(d)
        for topic, (file, about) in index.items():
            print(f"{topic:<20} {len(context_facts(d, topic)):>3} fact(s)  {about}")
        if not index:
            print("the context store is empty: tether context add <topic> \"fact\" --about \"what it covers\"")
        return
    topic = args[0]
    if not os.path.exists(os.path.join(d, topic + ".md")):
        sys.exit(f"tether: no topic called '{topic}' (tether context list)")
    for _, fact, date, source in context_facts(d, topic):
        print(f"- {fact}" + (f"  ({date}" + (f", {source}" if source else "") + ")" if date else ""))


def context_add(args):
    _, vals, words = parse_opts(args, valued=("--source", "--about"))
    if len(words) < 2:
        sys.exit('usage: tether context add <topic> "fact" [--about "what a new topic covers"] [--source "where it came from"]')
    print(add_fact(context_dir(), words[0], " ".join(words[1:]), vals.get("--source") or "", vals.get("--about") or ""))


def context_proposals_path(d):
    return os.path.join(d, ".proposals.json")


def context_queue(d):
    try:
        with open(context_proposals_path(d)) as f:
            ops = json.load(f).get("ops", [])
        return [op for op in ops if isinstance(op, dict)]
    except (OSError, ValueError):
        return []


def context_queue_save(d, ops):
    if not ops:
        try:
            os.remove(context_proposals_path(d))
        except OSError:
            pass
        return
    os.makedirs(d, mode=0o700, exist_ok=True)
    with open(context_proposals_path(d), "w") as f:
        json.dump({"at": now(), "ops": ops}, f, indent=1)


def context_propose(text, source, model):
    """One model call: facts worth keeping from some conversation text. -> (ops, cost). Nothing is saved: every op is a
    proposal that passes the same rules as `add` (a one-line fact, no secrets, a description for a new topic)."""
    d = context_dir()
    index, _ = context_index(d)
    known = "\n".join(f"- {t}: {a}" for t, (_, a) in index.items()) or "(none yet)"
    prompt = (
        "You keep a small knowledge base of durable facts about one person's working environment, so that coding agents need not ask "
        "again. Read the conversation below and propose facts worth keeping: where things live (repository paths, folders), the stack and "
        "tools in use, service names and how they relate, commands that worked and when to use them, conventions and preferences.\n"
        "Rules: one line per fact, at most 200 characters, self-contained. Never include secrets, passwords, tokens, keys, customer or personal "
        "data. Skip anything temporary or only true of this one task. Prefer an existing topic; a new topic needs an 'about' saying when to read it. "
        "Topic names are lowercase words with dashes.\n"
        f"Existing topics:\n{known}\n\n"
        'Reply with ONLY a JSON array: [{"topic": "...", "fact": "...", "about": "only for a new topic", "source": "short, optional"}]. '
        "Reply [] if there is nothing worth keeping.\n\n=== CONVERSATION ===\n" + text)
    answer, cost = ai.ask_ai(prompt, model, budget=0.5, agent=context_agent())
    try:
        raw = ai.parse_json_answer(answer)
    except (TetherError, ValueError):
        raw = None
    if not isinstance(raw, list):
        raise TetherError("the model did not return a list of facts; try again")
    ops, seen = [], {_norm_fact(op["fact"]) for op in context_queue(d)}
    for item in raw:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("topic", "fact")):
            continue
        topic, fact = item["topic"].strip().lower(), " ".join(item["fact"].split())
        about = " ".join(str(item.get("about") or "").split())
        if not TOPIC_RE.match(topic) or not fact or len(fact) > CONTEXT_FACT_MAX or SECRETISH_RE.search(fact) or _norm_fact(fact) in seen:
            continue
        if topic not in index and not about:
            continue
        if any(_norm_fact(fact) == _norm_fact(x[1]) for x in context_facts(d, topic)):
            continue
        seen.add(_norm_fact(fact))
        ops.append({"topic": topic, "fact": fact, "about": about, "source": " ".join(str(item.get("source") or source).split())[:80]})
    return ops, cost


def context_learn(args):
    """Propose facts from a space's conversation. One model call; nothing is saved until `tether context accept`."""
    flags, vals, words = parse_opts(args, flags=("--json",), valued=("--id", "--session", "--model"))
    convs = reading.find_conversations(words, vals.get("--id"), vals.get("--session"))
    parts = []
    for c in convs:
        text, _, _ = reading.conversation_text(c["agent"], c["path"], reading.EXTRACT_CAP // len(convs))
        if text:
            parts.append(text)
    if not parts:
        raise TetherError("those conversations have nothing readable in them yet")
    d = context_dir()
    index, _ = context_index(d)
    ops, cost = context_propose("\n\n".join(parts), convs[0]["space"], vals.get("--model") or ai.search_model())
    queue = context_queue(d) + ops
    context_queue_save(d, queue)
    if "--json" in flags:
        print(json.dumps({"proposed": len(ops), "waiting": len(queue), "cost": cost}))
        return
    for op in ops:
        print(f"[{op['topic']}] {op['fact']}" + (f"   (new topic: {op['about']})" if op["about"] and op["topic"] not in index else ""))
    print(f"{len(ops)} fact(s) proposed" + (f" (model call ${cost:.2f})" if cost else "") + f", {len(queue)} waiting; nothing is saved yet: "
          + ("`tether context proposals` lists them, `tether context accept [--only 1,3]` or `reject` decides" if queue else "nothing worth keeping"))


def context_proposals(args):
    flags, _, _ = parse_opts(args, flags=("--json",))
    d = context_dir()
    index, _ = context_index(d)
    rows = [dict(op, n=n, new_topic=op["topic"] not in index) for n, op in enumerate(context_queue(d), 1)]
    if "--json" in flags:
        print(json.dumps(rows))
        return
    for r in rows:
        print(f"{r['n']}. [{r['topic']}] {r['fact']}" + (f"   (new topic: {r['about']})" if r["new_topic"] else ""))
    print(f"{len(rows)} proposed fact(s)" if rows else "no proposed facts")


def context_decide(args, accept):
    _, vals, _ = parse_opts(args, valued=("--only",))
    d = context_dir()
    ops = context_queue(d)
    if not ops:
        sys.exit("tether: no proposed facts (tether context learn <space>)")
    chosen = set(range(1, len(ops) + 1))
    if vals.get("--only"):
        try:
            chosen = {int(x) for x in vals["--only"].split(",")}
        except ValueError:
            sys.exit("tether: --only takes numbers like 1,3")
    rest = [op for n, op in enumerate(ops, 1) if n not in chosen]
    if accept:
        done = []
        for n, op in enumerate(ops, 1):
            if n in chosen:
                try:
                    done.append(add_fact(d, op["topic"], op["fact"], op["source"], op["about"]))
                except TetherError as err:
                    say(f"{op['fact'][:50]}: {err}")
        print(f"{done.count('added')} fact(s) added" + (f", {done.count('already there')} already there" if "already there" in done else ""))
    else:
        print(f"discarded {len(ops) - len(rest)} proposed fact(s)")
    context_queue_save(d, rest)
    if rest:
        print(f"{len(rest)} still waiting")


RISKY_FACT_RE = re.compile(r"`|https?://|www\.|\$\(|&&|\||;|\b(?:sudo|curl|wget|chmod|eval|rm\s+-)", re.I)

AUTO_LOG_KEEP = 50


def context_auto_accept_on():
    flag = env("CONTEXT_AUTO_ACCEPT")
    return flag.lower() in ("1", "yes", "true", "on") if flag else bool(load_config().get("context_auto_accept"))


def auto_acceptable(op, index):
    """Only a plain statement about an existing topic may skip review: never a new topic, a command, a URL or anything shell-like."""
    return op["topic"] in index and not op["about"] and not RISKY_FACT_RE.search(op["fact"])


def _auto_log_path(d):
    return os.path.join(d, ".auto-added.json")


def context_auto_added(d):
    return _json_file(_auto_log_path(d), [])


def context_auto_log(d, entries):
    with open(_auto_log_path(d), "w") as f:
        json.dump(entries[-AUTO_LOG_KEEP:], f, indent=1)


def context_recent(args):
    """Facts saved without review (background learning with context_auto_accept on), newest last."""
    flags, _, _ = parse_opts(args, flags=("--json",))
    rows = [dict(e, n=n) for n, e in enumerate(context_auto_added(context_dir()), 1)]
    if "--json" in flags:
        print(json.dumps(rows))
        return
    for r in rows:
        print(f"{r['n']}. [{r['topic']}] {r['fact']}   ({r['at'][:10]})")
    print(f"{len(rows)} fact(s) added without review; `tether context undo --only N` removes one" if rows else "nothing was added without review")


def context_undo(args):
    """Remove facts that were saved without review: --only 1,3 or --all."""
    flags, vals, _ = parse_opts(args, flags=("--all",), valued=("--only",))
    d = context_dir()
    log = context_auto_added(d)
    if not log:
        sys.exit("tether: nothing was added without review")
    if "--all" in flags:
        chosen = set(range(1, len(log) + 1))
    else:
        try:
            chosen = {int(x) for x in (vals.get("--only") or "").split(",")}
        except ValueError:
            sys.exit("tether: undo takes --only 1,3 (numbers from `tether context recent`) or --all")
    removed = 0
    for n, e in enumerate(log, 1):
        if n not in chosen:
            continue
        path = os.path.join(d, e["topic"] + ".md")
        try:
            with open(path, errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            continue
        for i, line in enumerate(lines):
            m = FACT_RE.match(line)
            if m and _norm_fact(m["fact"]) == _norm_fact(e["fact"]):
                del lines[i]
                with open(path, "w") as f:
                    f.write("\n".join(lines))
                removed += 1
                break
    context_auto_log(d, [e for n, e in enumerate(log, 1) if n not in chosen])
    print(f"removed {removed} fact(s)")


# ---- background learning (opt-in): propose facts from conversations as they progress ----
CONTEXT_HARVEST_EVERY = 6 * 3600   # seconds between background harvests

CONTEXT_HARVEST_MIN_NEW = 6        # new messages in a conversation before it is worth reading again

CONTEXT_HARVEST_IDLE = 1800        # a conversation untouched this long (seconds) counts as settled

CONTEXT_HARVEST_MAX = 3            # conversations per run

CONTEXT_HARVEST_CAP_USD = 0.50     # model spend per day

CONTEXT_HARVEST_CHARS = 40_000


def context_auto_on():
    flag = env("CONTEXT_AUTO")
    return flag.lower() in ("1", "yes", "true", "on") if flag else bool(load_config().get("context_auto"))


def context_agent():
    chosen = env("CONTEXT_AGENT") or load_config().get("context_agent")
    return chosen if chosen in AGENTS else "claude"


def context_model():
    return env("CONTEXT_MODEL") or load_config().get("context_model") or "haiku"


def context_harvest(args):
    """Read conversations that have grown since last time and QUEUE proposed facts (never saves one). Opt-in
    (`context_auto`). Enabling it starts from now: existing conversations are marked as read, not mined."""
    flags, _, _ = parse_opts(args, flags=("--background", "--quiet"))
    if not context_auto_on():
        if "--background" not in flags:
            print('background learning is off: set "context_auto": true in the config (or TETHER_CONTEXT_AUTO=1)')
        return
    d = context_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    with open(os.path.join(d, ".harvest.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        _context_harvest_run(d, flags)


def _context_harvest_run(d, flags):
    marks_path, spend_path = os.path.join(d, ".harvested.json"), os.path.join(d, ".harvest-spend.json")
    first = not os.path.exists(marks_path)
    marks = _json_file(marks_path, {})
    spend = _json_file(spend_path, {})
    today = datetime.now().date().isoformat()
    if spend.get("date") != today:
        spend = {"date": today, "usd": 0.0}
    docs = [x for x in search.search_corpus(["open", "closed"]) if x.get("path") and os.path.isfile(x["path"])]
    docs.sort(key=lambda x: x.get("last_used") or "", reverse=True)
    done = 0
    for doc in docs:
        lines = reading.conversation_text(doc["agent"], doc["path"], as_lines=True)
        lines = [m for m in lines if not m.startswith("COMMAND: ")]
        seen = (marks.get(doc["session"]) or {}).get("lines", 0)
        if first:
            marks[doc["session"]] = {"lines": len(lines), "at": now()}
            continue
        if done >= CONTEXT_HARVEST_MAX or spend["usd"] >= CONTEXT_HARVEST_CAP_USD:
            break
        if len(lines) - seen < CONTEXT_HARVEST_MIN_NEW or time.time() - os.path.getmtime(doc["path"]) < CONTEXT_HARVEST_IDLE:
            continue
        text = "\n".join(lines[seen:])[-CONTEXT_HARVEST_CHARS:]
        try:
            ops, cost = context_propose(text, doc["space"], context_model())
        except TetherError as err:
            say(f"background learning: {err}")
            break
        spend["usd"] += cost or 0.0
        marks[doc["session"]] = {"lines": len(lines), "at": now()}
        if ops and context_auto_accept_on():
            index, _ = context_index(d)
            keep, log = [], context_auto_added(d)
            for op in ops:
                if auto_acceptable(op, index):
                    try:
                        if add_fact(d, op["topic"], op["fact"], "auto: " + op["source"]) == "added":
                            log.append({"topic": op["topic"], "fact": op["fact"], "source": op["source"], "at": now()})
                        continue
                    except TetherError:
                        pass
                keep.append(op)
            context_auto_log(d, log)
            ops = keep
        if ops:
            context_queue_save(d, context_queue(d) + ops)
        done += 1
    with open(marks_path, "w") as f:
        json.dump(marks, f)
    with open(spend_path, "w") as f:
        json.dump(spend, f)
    if "--quiet" not in flags and "--background" not in flags:
        print("started from now: existing conversations are marked as read" if first else
              f"read {done} conversation(s); {len(context_queue(d))} proposed fact(s) waiting (tether context proposals)")


def context_harvest_if_due():
    """Start `tether context harvest` in the background, at most every CONTEXT_HARVEST_EVERY seconds, if background learning is on."""
    if not context_auto_on():
        return
    d = context_dir()
    stamp = os.path.join(d, ".last-harvest")
    try:
        if time.time() - os.path.getmtime(stamp) < CONTEXT_HARVEST_EVERY:
            return
    except OSError:
        pass
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        open(stamp, "w").close()
        subprocess.Popen([sys.executable, os.path.realpath(sys.argv[0]), "context", "harvest", "--background"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def context_cmd(args):
    table = {"add": context_add, "list": context_list, "find": context_find, "check": context_check, "learn": context_learn,
             "proposals": context_proposals, "harvest": context_harvest, "recent": context_recent, "remove": context_remove, "undo": context_undo, "accept": lambda a: context_decide(a, True), "reject": lambda a: context_decide(a, False)}
    if not args or args[0] not in table:
        sys.exit("usage: tether context add|list|find|check|learn|proposals|accept|reject|recent|undo|remove|harvest  (tether --help)")
    table[args[0]](args[1:])
