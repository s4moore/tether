"""The local usage log: one JSON line per feature use in <state dir>/events.jsonl (mode 0600), and `tether stats` to read it.

It holds counts, timings, outcomes and the agent and model used. It never holds what was said, a fact, a search word, a name or a
path: `record` drops any field that is not a number, a boolean or a short token made of letters, digits and `_.:+-`, so free text cannot
get in by accident. It never leaves the machine, and logging can never make a command fail."""

import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from tetherlib import context, core
from tetherlib.core import env, load_config, state_dir

EVENTS_MAX = 1_000_000  # bytes; the file is rotated to events.jsonl.1 (replacing the older copy) when it grows past this
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,40}$")


def enabled():
    flag = env("USAGE_LOG")
    if flag:
        return flag.lower() in ("1", "yes", "true", "on")
    return bool(load_config().get("usage_log", True))


def path():
    return os.path.join(state_dir(), "events.jsonl")


def clean(fields):
    """Only numbers, booleans and short tokens survive."""
    out = {}
    for key, value in fields.items():
        if not KEY_RE.match(key) or value is None:
            continue
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            if math.isfinite(value):
                out[key] = round(value, 4) if isinstance(value, float) else value
        elif isinstance(value, str) and TOKEN_RE.match(value):
            out[key] = value
    return out


def record(feature, outcome="ok", **fields):
    """Append one event. Never raises."""
    try:
        if not enabled() or not KEY_RE.match(feature.replace(".", "_")):
            return
        event = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "v": core.__version__, "feature": feature,
                 "outcome": outcome if TOKEN_RE.match(outcome) else "other"}
        event.update(clean(fields))
        os.makedirs(state_dir(), exist_ok=True)
        p = path()
        try:
            if os.path.getsize(p) > EVENTS_MAX:
                os.replace(p, p + ".1")
        except OSError:
            pass
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")
    except Exception:  # noqa: BLE001 - a log must never break the command it describes
        pass


def since(t0):
    return round(time.time() - t0, 1)


def load(days=30):
    """Events from the last `days` days, oldest first (the rotated file included)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    out = []
    for p in (path() + ".1", path()):
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(e, dict) and str(e.get("t", "")) >= cutoff:
                        out.append(e)
        except OSError:
            continue
    return out


def _sum(events, key):
    return sum(e.get(key, 0) for e in events if isinstance(e.get(key), (int, float)) and not isinstance(e.get(key), bool))


def _avg(events, key):
    vals = [e[key] for e in events if isinstance(e.get(key), (int, float)) and not isinstance(e.get(key), bool)]
    return round(sum(vals) / len(vals), 1) if vals else None


def _day(e):
    return str(e.get("t", ""))[:10]


def _followed(finds, lists, within=120):
    """How many events in `lists` have one in `finds` within `within` seconds before them (a lookup that needed a second step)."""
    def when(e):
        try:
            return datetime.fromisoformat(e["t"]).timestamp()
        except (KeyError, ValueError, TypeError):
            return None
    before = [w for w in map(when, finds) if w is not None]
    return sum(1 for w in map(when, lists) if w is not None and any(0 <= w - x <= within for x in before))


def summarize(events):
    """{feature: {...}} the figures `tether stats` shows, built only from the events."""
    by = {}
    for e in events:
        by.setdefault(e.get("feature", "?"), []).append(e)
    out = {}
    for feature, evs in sorted(by.items()):
        row = {"runs": len(evs), "errors": sum(e.get("outcome") != "ok" for e in evs)}
        if feature in ("context.learn", "context.harvest"):
            row.update(proposed=_sum(evs, "proposed"), auto_saved=_sum(evs, "auto_saved"), similar=_sum(evs, "similar"),
                       avg_seconds=_avg(evs, "seconds"), cost=round(_sum(evs, "cost"), 3))
            row["by_agent"] = {a: sum(1 for e in evs if e.get("agent") == a) for a in sorted({e.get("agent") for e in evs if e.get("agent")})}
        elif feature == "context.accept":
            row.update(accepted=_sum(evs, "accepted"), filed_elsewhere=sum(bool(e.get("into")) for e in evs))
        elif feature == "context.reject":
            row.update(rejected=_sum(evs, "rejected"))
        elif feature == "context.undo":
            row.update(undone=_sum(evs, "removed"))
        elif feature == "context.find":
            row.update(no_hit=sum(e.get("hits") == 0 for e in evs), exact=sum(bool(e.get("exact")) for e in evs),
                       then_list=_followed(evs, by.get("context.list", [])))
        elif feature == "context.list":
            row.update(of_a_topic=sum(bool(e.get("topic")) for e in evs))
        elif feature == "ai.call":
            row.update(avg_seconds=_avg(evs, "seconds"), cost=round(_sum(evs, "cost"), 3))
            row["by_model"] = {f"{a}:{m}": sum(1 for e in evs if (e.get("agent"), e.get("model")) == (a, m))
                               for a, m in sorted({(e.get("agent"), e.get("model")) for e in evs if e.get("agent")})}
            kinds = sorted({e.get("kind") for e in evs if e.get("kind")})
            if kinds:
                row["failures"] = {k: sum(1 for e in evs if e.get("kind") == k) for k in kinds}
        elif feature == "search":
            row.update(no_result=sum(e.get("results") == 0 for e in evs), with_ai=sum(e.get("mode") in ("rank", "deep") for e in evs),
                       avg_seconds=_avg(evs, "seconds"), cost=round(_sum(evs, "cost"), 3))
        elif feature == "backup":
            row.update(avg_seconds=_avg(evs, "seconds"), sessions=_sum(evs, "sessions"), megabytes=round(_sum(evs, "bytes") / 1e6, 1),
                       last_day=_day(evs[-1]))
        elif feature == "continue":
            row.update(avg_seconds=_avg(evs, "seconds"))
        elif feature == "command":
            names = sorted({e.get("name") for e in evs if e.get("name")})
            row.update(slow=sum(bool(e.get("slow")) for e in evs), avg_seconds=_avg(evs, "seconds"))
            row["by_name"] = {n: sum(1 for e in evs if e.get("name") == n) for n in names}
            failed = {n: sum(1 for e in evs if e.get("name") == n and e.get("outcome") != "ok") for n in names}
            row["failed_by_name"] = {n: c for n, c in failed.items() if c}
        elif feature == "job":
            jobs = sorted({e.get("job") for e in evs if e.get("job")})
            row["by_job"] = {j: {"runs": sum(1 for e in evs if e.get("job") == j), "skipped": sum(1 for e in evs if e.get("job") == j and e.get("outcome") == "skipped"),
                                 "last_day": _day([e for e in evs if e.get("job") == j][-1])} for j in jobs}
            reasons = sorted({e.get("reason") for e in evs if e.get("reason")})
            row["reasons"] = {r: sum(1 for e in evs if e.get("reason") == r) for r in reasons}
        out[feature] = row
    # how good is each agent and model at proposing facts: what was proposed, then what you accepted, rejected and undid
    origins = {}
    def origin(e):
        return f"{e['agent']}:{e['model']}" if e.get("agent") and e.get("model") else "unknown"
    for e in events:
        f = e.get("feature")
        if f in ("context.learn", "context.harvest"):
            o = origins.setdefault(origin(e), {})
            o["proposed"] = o.get("proposed", 0) + e.get("proposed", 0)
            o["saved_without_review"] = o.get("saved_without_review", 0) + e.get("auto_saved", 0)
        elif f in ("context.accept", "context.reject", "context.undo"):
            o = origins.setdefault(origin(e), {})
            key = {"context.accept": "accepted", "context.reject": "rejected", "context.undo": "undone"}[f]
            o[key] = o.get(key, 0) + e.get({"context.accept": "accepted", "context.reject": "rejected", "context.undo": "removed"}[f], 0)
    for o in origins.values():
        if o.get("accepted", 0) + o.get("rejected", 0):
            o["accept_rate"] = round(o.get("accepted", 0) / (o.get("accepted", 0) + o.get("rejected", 0)), 2)
    if origins:
        total = {k: sum(o.get(k, 0) for o in origins.values()) for k in ("proposed", "accepted", "rejected", "undone", "saved_without_review")}
        if total["accepted"] + total["rejected"]:
            total["accept_rate"] = round(total["accepted"] / (total["accepted"] + total["rejected"]), 2)
        out["learning"] = {"all": total, "by_origin": origins}
    return out


ISSUES_URL = "https://github.com/s4moore/tether/issues/new?template=usage-summary.md"
VERSION_RE = re.compile(r"\d+(?:\.\d+)+")


def _tool_version(name):
    """The version number a program reports (digits and dots only), or None. Never the rest of its output."""
    binary = core.find_tool(name)
    if not binary:
        return None
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    m = VERSION_RE.search(r.stdout + r.stderr)
    return m.group(0) if m else None


def export_summary(days=30):
    """What `tether stats --export` writes: totals per feature and the versions in use. No event lines, no times finer than a day, nothing
    that identifies a person, a space, a conversation or a path."""
    events = load(days)
    features = summarize(events)
    return {
        "tether_usage_summary": 1,
        "tether": core.__version__,
        "python": platform.python_version(),
        "macos": ".".join(platform.mac_ver()[0].split(".")[:2]) or None,
        "agents": {name: _tool_version(name) for name in ("claude", "copilot", "herdr-app")},
        "day": datetime.now(timezone.utc).date().isoformat(),
        "days_covered": days,
        "events": len(events),
        "store": context.store_snapshot(context.context_dir()),
        "features": features,
    }


def _only_safe(value, depth=0):
    """True when everything in an export is a number, a boolean, None, a short token, or a dict/list of those (the guarantee the export makes)."""
    if depth > 7:
        return False
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return bool(TOKEN_RE.match(value))
    if isinstance(value, dict):
        return all(isinstance(k, str) and TOKEN_RE.match(k) and _only_safe(v, depth + 1) for k, v in value.items())
    if isinstance(value, list):
        return all(_only_safe(v, depth + 1) for v in value)
    return False


def export_cmd(days, out_path, yes, print_only=False):
    summary = export_summary(days)
    if not _only_safe(summary):  # a bug, not a user problem: refuse rather than write something unexpected
        raise core.TetherError("the summary held something that is not a count or a version, so nothing was written (please report this)")
    text = json.dumps(summary, indent=2)
    if print_only:  # what the Settings dialog shows before anything is saved
        print(text)
        return
    name = f"tether-usage-summary-{summary['day'].replace('-', '')}.json"
    path = os.path.abspath(os.path.expanduser(out_path or name))
    if os.path.isdir(path):
        path = os.path.join(path, name)
    print(text)
    print(f"\nThat is everything the file contains: totals and versions, no events, no text from your conversations, no names, no paths.")
    if not yes:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise core.TetherError("not a terminal: pass -y to write it without asking")
        if input(f"Write it to {path}? [y/N] ").strip().lower() not in ("y", "yes"):
            print("cancelled; nothing was written")
            return
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"wrote {path}\nNothing is sent: attach the file to an issue if you want to share it: {ISSUES_URL}")


def stats_cmd(args):
    """`tether stats [--days N] [--json]`: what the usage log says about how the features are doing. `--export [-o file] [-y]` writes a
    summary that is safe to share (see export_summary)."""
    flags, vals, _ = core.parse_opts(args, flags=("--json", "--export", "-y"), valued=("--days", "-o"))
    try:
        days = int(vals.get("--days") or 30)
    except ValueError:
        raise core.TetherError("--days takes a number")
    if "--export" in flags:
        return export_cmd(days, vals.get("-o"), "-y" in flags, print_only="--json" in flags)
    events = load(days)
    summary = summarize(events)
    if "--json" in flags:
        print(json.dumps({"days": days, "events": len(events), "enabled": enabled(), "store": context.store_snapshot(context.context_dir()),
                          "features": summary}))
        return
    if not events:
        print(f"nothing logged in the last {days} days" + ("" if enabled() else " (the usage log is off: `tether config set usage_log on`)"))
        return
    print(f"last {days} days: {len(events)} events in {path()}" + ("" if enabled() else "  (logging is off now)"))
    store_now = context.store_snapshot(context.context_dir())
    print("  store            " + "; ".join(f"{k.replace('_', ' ')} {v}" for k, v in store_now.items()))
    for feature, row in summary.items():
        bits = [f"{k.replace('_', ' ')} {v}" for k, v in row.items() if not isinstance(v, dict) and v not in (None, 0, 0.0) or k == "runs"]
        bits += [f"{k.replace('_', ' ')}: {_fmt(v)}" for k, v in row.items() if isinstance(v, dict) and v]
        print(f"  {feature:<16} " + "; ".join(b for b in bits if not (feature == "learning" and b.startswith("runs"))))
    print("Only counts, timings and outcomes are logged, never what was said. Turn it off: `tether config set usage_log off`. Share a summary: `tether stats --export`.")


def _fmt(value):
    """A dict of counts (or of dicts of counts) as one line: `a 3, b 1` or `a (x 2, y 1), b (x 1)`."""
    if isinstance(value, dict):
        return ", ".join(f"{k} ({_fmt(v)})" if isinstance(v, dict) else f"{k} {v}" for k, v in value.items())
    return str(value)
