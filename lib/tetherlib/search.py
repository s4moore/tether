"""Search over conversations: free keyword matching, then optional AI ranking."""

import collections
import concurrent.futures
import functools
import glob
import json
import math
import os
import re
import sys

from tetherlib.core import AGENTS, TetherError, UUID_RE, ago, parse_opts

from tetherlib import ai, backups, herdr, store


STOPWORDS = frozenset(
    "a an and are as at be been by do does did for from had has have how i if in is it its me my of on or "
    "so that the their there this to was we were what when where which who why will with would you your "
    "about find found look looking talked discussed conversation session chat remember remembered thing "
    "things stuff something somewhere".split())

SEARCH_SCOPES = ("open", "closed", "deleted")

SHORT_TERM = 3    # a search term of up to this many characters matches only as a whole word

DIGEST_TOP = 12   # candidates the ranking step sees

DEEP_TOP = 3      # conversations a deep read covers

DEEP_CAP = 60000  # characters of one conversation sent in a deep read


def transcript_file(agent, session, home=None):
    """The one file that holds a session's conversation (under the agent's home, or a backup laid out the same)."""
    if not UUID_RE.match(session or ""):
        return None
    if agent == "claude":
        hits = glob.glob(os.path.join(home or store._claude_home(), "projects", "*", session + ".jsonl"))
        return hits[0] if hits else None
    path = os.path.join(home or store._copilot_home(), "session-state", session, "events.jsonl")
    return path if os.path.isfile(path) else None


def readable_turns(agent, path):
    """[(role, text)] in order: what the user and the assistant said. No tool output, attachments or
    system reminders."""
    turns = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                t = e.get("type")
                if agent == "claude":
                    if t not in ("user", "assistant") or e.get("isMeta"):
                        continue
                    c = (e.get("message") or {}).get("content")
                    if isinstance(c, list):
                        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
                    role = t
                else:
                    if t not in ("user.message", "assistant.message"):
                        continue
                    c = (e.get("data") or {}).get("content")
                    role = "user" if t == "user.message" else "assistant"
                c = (c or "").strip() if isinstance(c, str) else ""
                if c and not c.startswith(("<", "Caveat:")):
                    turns.append((role, c))
    except OSError:
        pass
    return turns


def search_corpus(scopes):
    """The conversations to search: [{space_id, space, state, agent, session, tab, last_used, path,
    via_backup}]. open / closed: the sessions of linked spaces, from the agent's own copy or, if the agent
    has swept it, from the backup. deleted: the backup of spaces tether no longer links. A session is
    searched once (a backup of a session of a live space is just another copy of it)."""
    try:
        live = {sp["id"]: sp for sp in herdr.herdr_ls()}
    except TetherError:
        live = None
    with store.State() as st:
        data = {k: v for k, v in st.data.items() if v.get("terminals") or v.get("agents")}
    docs, seen = [], set()

    def add(sid, name, state, e):
        agent = e.get("agent")
        if agent not in AGENTS or not UUID_RE.match(e.get("session") or ""):
            return
        src = backups.source_session(e)
        if (agent, src) in seen:
            return
        path = transcript_file(agent, src)
        via_backup = False
        if not path:
            path = transcript_file(agent, src, os.path.join(backups.backup_dir(), agent))
            via_backup = bool(path)
        if not path:
            return
        seen.add((agent, src))
        docs.append({"space_id": sid, "space": name or sid, "state": state, "agent": agent, "session": e["session"],
                     "src": src, "tab": e.get("tab_name") or "", "last_used": e.get("last_used") or "",
                     "path": path, "via_backup": via_backup, "cwd": e.get("cwd") or ""})

    for sid, sp, _addr, e in store.all_links(data):
        state = "open" if live is not None and sid in live else "closed"
        if state in scopes:
            add(sid, (live[sid].get("name") if state == "open" else None) or sp.get("name"), state, e)
    if "deleted" in scopes:
        for r in backups.deleted_spaces():
            for l in r["links"]:
                add(r["id"], r["name"], "deleted", dict(l, last_used=r["backed_up"]))
    return docs


def query_terms(query):
    # "Acme's" is Acme, not Acme and "s": a possessive is dropped, and so is any one-letter leftover, because a
    # term of one letter matches inside nearly every word and drowns the words that were meant.
    text = re.sub(r"['’]s\b", "", query.lower())
    words = re.findall(r"[a-z0-9][a-z0-9_.\-/]*[a-z0-9]|[a-z0-9]", text)
    return list(dict.fromkeys([w for w in words if w not in STOPWORDS and len(w) > 1] or words))


@functools.lru_cache(maxsize=256)
def _term_re(term):
    """How a search term is found in lower-case text. A longer term matches inside words ("investig" finds
    "investigation"); one of up to three characters ("us", "api") only as a whole word, or "us" would match
    "status" and "business". The web mod highlights with the same rule (pieces() in tether-search)."""
    body = re.escape(term)
    return re.compile(body if len(term) > SHORT_TERM else rf"(?<![a-z0-9]){body}(?![a-z0-9])")


def _snippet(text, terms, width=260):
    low = text.lower()
    hits = [m.start() for m in (_term_re(t).search(low) for t in terms) if m]
    i = min(hits) if hits else 0
    start = max(0, i - 90)
    return ("…" if start else "") + " ".join(text[start:start + width].split()) + ("…" if start + width < len(text) else "")


def lexical_rank(terms, docs, limit=15):
    """Tier 1. Rows for the conversations that match, best first. A term counts as a substring (so
    "investig" finds "investigation"), rarer terms weigh more, words the user typed count extra, and
    the whole query or two terms in a row as a phrase count more. score is 0..100."""
    n = len(docs)
    scored = []
    df = collections.Counter()
    for d in docs:
        turns = readable_turns(d["agent"], d["path"])
        low = [(role, t.lower()) for role, t in turns]
        tf = {w: sum(len(_term_re(w).findall(x)) for _, x in low) for w in terms}
        tfu = {w: sum(len(_term_re(w).findall(x)) for role, x in low if role == "user") for w in terms}
        for w in terms:
            if tf[w]:
                df[w] += 1
        scored.append((d, turns, low, tf, tfu))
    rows = []
    phrase = " ".join(terms)
    pairs = [" ".join(terms[i:i + 2]) for i in range(len(terms) - 1)]
    for d, turns, low, tf, tfu in scored:
        idf = {w: math.log(1 + n / (1 + df[w])) for w in terms}
        score = sum(idf[w] * (1 + math.log(tf[w])) for w in terms if tf[w])
        score += sum(0.5 * idf[w] * (1 + math.log(tfu[w])) for w in terms if tfu[w])
        joined = "\n".join(x for _, x in low)
        if len(terms) > 1 and phrase in joined:
            score += 2 * sum(idf.values())
        score += sum(0.5 * sum(idf[w] for w in p.split()) for p in pairs if p in joined)
        if score <= 0:
            continue
        # A conversation with all the words ranks above one with a few of them, however often they come up there.
        score *= sum(1 for w in terms if tf[w]) / len(terms)
        ranked_turns = sorted(range(len(low)), key=lambda i: -sum(1 for w in terms if _term_re(w).search(low[i][1])))
        snippets = [{"role": turns[i][0], "text": _snippet(turns[i][1], terms)}
                    for i in ranked_turns[:3] if any(_term_re(w).search(low[i][1]) for w in terms)]
        rows.append(dict(d, score=score, snippets=snippets, chars=sum(len(t) for _, t in turns), _turns=turns))
    rows.sort(key=lambda r: -r["score"])
    top = rows[0]["score"] if rows else 1
    for r in rows:
        r["score"] = round(100 * r["score"] / top)
        r["title"] = backups.session_title(r["agent"], r["src"])
    return rows[:limit]


def recent_rows(docs, limit=DIGEST_TOP):
    """Candidates when no keyword matched: the most recently used conversations, for the AI step to judge."""
    rows = []
    for d in sorted(docs, key=lambda d: d["last_used"], reverse=True)[:limit]:
        turns = readable_turns(d["agent"], d["path"])
        rows.append(dict(d, score=0, snippets=[], chars=sum(len(t) for _, t in turns), _turns=turns,
                         title=backups.session_title(d["agent"], d["src"])))
    return rows


def _digest(i, r):
    turns = r.get("_turns") or readable_turns(r["agent"], r["path"])
    users = [t for role, t in turns if role == "user"]
    firsts, lasts = users[:2], [u for u in users[-2:] if u not in users[:2]]
    lines = [f"[{i}] space: {r['space']}  tab: {r['tab'] or '-'}  ({r['state']}, {r['agent']}, last used {r['last_used'][:10] or '?'})",
             f"    first asked: " + " | ".join(backups._clean(u, 200) for u in firsts)]
    if lasts:
        lines.append("    last asked: " + " | ".join(backups._clean(u, 200) for u in lasts))
    for sn in r["snippets"]:
        lines.append(f"    matched ({sn['role']}): {sn['text']}")
    return "\n".join(lines)


def search_estimate(rows, corpus, model):
    """What the AI steps would cost, in tokens (about 4 characters each), for these results."""
    digests = sum(len(_digest(i, r)) for i, r in enumerate(rows[:DIGEST_TOP], 1))
    deep = sum(min(r["chars"], DEEP_CAP) for r in rows[:DEEP_TOP])
    return {"corpus_sessions": corpus, "rank_candidates": min(len(rows), DIGEST_TOP), "rank_tokens": digests // 4 + 500,
            "deep_sessions": min(len(rows), DEEP_TOP), "deep_tokens": deep // 4 + 400 * min(len(rows), DEEP_TOP),
            "model": model}


def rank_with_ai(query, rows, model):
    """Tier 2: re-rank the top candidates from their digests. -> (rows, cost)"""
    cands = rows[:DIGEST_TOP]
    prompt = (f"You rank past conversations for a search. The user is looking for:\n  {query}\n\n"
              f"Below are {len(cands)} candidate conversations (digests only). Judge which one the user most likely means. "
              'Reply with ONLY a JSON array, best first: [{"id": <number>, "score": <0-100>, "why": "<one short sentence>"}]. '
              "Give a low score (0-10) to ones that clearly do not match. Use only the digests.\n\n"
              + "\n\n".join(_digest(i, r) for i, r in enumerate(cands, 1)))
    text, cost = ai.ask_claude(prompt, model)
    answer = ai.parse_json_answer(text)
    by_id = {}
    for a in answer if isinstance(answer, list) else []:
        try:
            by_id[int(a["id"])] = a
        except (KeyError, TypeError, ValueError):
            continue
    for i, r in enumerate(cands, 1):
        a = by_id.get(i)
        r["keyword_score"] = r["score"]
        r["score"] = max(0, min(100, int(a.get("score", 0)))) if a else 0
        r["why"] = str(a.get("why", ""))[:300] if a else ""
    cands.sort(key=lambda r: -r["score"])
    return cands + rows[DIGEST_TOP:], cost


def deep_read(query, rows, model):
    """Tier 3: the full text of each given row, one Claude call each, in parallel. -> (rows, cost)"""
    def one(r):
        turns = r.get("_turns") or readable_turns(r["agent"], r["path"])
        text = "\n".join(f"{role.upper()}: {t}" for role, t in turns)
        if len(text) > DEEP_CAP:
            text = text[:DEEP_CAP // 2] + "\n[... middle left out ...]\n" + text[-DEEP_CAP // 2:]
        prompt = (f"The user is looking for a past conversation:\n  {query}\n\nHere is one conversation (what was said; "
                  'tool output left out). Does it match? Reply with ONLY JSON: {"score": <0-100>, "why": "<one short sentence>", '
                  '"quote": "<a short verbatim passage from it that matches, or empty>"}\n\n' + text)
        out, cost = ai.ask_claude(prompt, model)
        return r, ai.parse_json_answer(out), cost
    total, done = 0.0, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=DEEP_TOP) as pool:
        for r, a, cost in pool.map(one, rows):
            r["keyword_score"] = r.get("keyword_score", r["score"])
            r["score"] = max(0, min(100, int(a.get("score", 0)))) if isinstance(a, dict) else 0
            r["why"] = str(a.get("why", ""))[:300] if isinstance(a, dict) else ""
            r["quote"] = str(a.get("quote", ""))[:300] if isinstance(a, dict) else ""
            total += cost or 0
            done.append(r)
    done.sort(key=lambda r: -r["score"])
    return done, total


def search_cmd(args):
    flags, vals, words = parse_opts(args, flags=("--rank", "--deep", "--json"), valued=("--scope", "--limit", "--sessions"))
    query = " ".join(words).strip()
    if not query:
        sys.exit("usage: tether search <words or a question> [--scope open,closed,deleted] [--rank] [--deep] [--json]")
    scopes = [x for x in (vals.get("--scope") or "open,closed").split(",") if x]
    bad = [x for x in scopes if x not in SEARCH_SCOPES]
    if bad or not scopes:
        sys.exit(f"tether: --scope takes {', '.join(SEARCH_SCOPES)} (comma separated), not {bad or 'nothing'}")
    try:
        limit = int(vals.get("--limit") or 15)
    except ValueError:
        sys.exit("tether: --limit takes a number")
    model, cost, tier = ai.search_model(), None, 1
    docs = search_corpus(scopes)
    terms = query_terms(query)
    rows = lexical_rank(terms, docs, limit)
    fallback = False
    estimate = search_estimate(rows if rows else recent_rows(docs), len(docs), model)
    estimate["no_keyword_match"] = not rows
    if not rows and ("--rank" in flags or "--deep" in flags):
        rows, fallback = recent_rows(docs), True  # nothing matched the words: let the AI judge the recent ones
    wanted = [x for x in (vals.get("--sessions") or "").split(",") if x]
    if "--rank" in flags and rows:
        rows, cost = rank_with_ai(query, rows, model)
        tier = 2
    if "--deep" in flags and rows:
        targets = [r for r in rows if r["session"] in wanted or r["src"] in wanted] if wanted else rows[:DEEP_TOP]
        if targets:
            deep, c = deep_read(query, targets, model)
            cost = (cost or 0) + c
            rest = [r for r in rows if r not in targets]
            rows, tier = sorted(deep + rest, key=lambda r: (r in rest, -r["score"])), 3
    out = [{k: v for k, v in r.items() if not k.startswith("_") and k not in ("path", "chars")} for r in rows]
    if "--json" in flags:
        print(json.dumps({"query": query, "terms": terms, "scopes": scopes, "tier": tier, "model": model, "cost_usd": cost,
                          "corpus": len(docs), "estimate": estimate, "fallback": fallback, "results": out}))
        return
    if not out:
        print(f"no keyword match in {len(docs)} conversation(s) of {', '.join(scopes)} spaces"
              + (f"; --rank would look at your {estimate['rank_candidates']} most recent ones (about {estimate['rank_tokens']:,} tokens)"
                 if docs else ""))
        return
    for r in out:
        print(f"{r['score']:>3}  {r['space']} / {r['tab'] or '-'}  [{r['state']}, {r['agent']}]  {ago(r['last_used']) if r['last_used'] else ''}")
        print(f"     {r['title'] or '(no first message found)'}")
        if r.get("why"):
            print(f"     why: {r['why']}")
        if r.get("quote"):
            print(f"     “{r['quote']}”")
        elif r["snippets"]:
            print(f"     {r['snippets'][0]['role']}: {r['snippets'][0]['text']}")
    print(f"{len(out)} of {len(docs)} conversation(s)"
          + (f"; AI cost ${cost:.3f}" if cost else f"; --rank would use about {estimate['rank_tokens']:,} tokens, "
             f"--deep about {estimate['deep_tokens']:,} ({model})"))
