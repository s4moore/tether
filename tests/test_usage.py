"""The local usage log. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import TetherTestCase
from tetherlib import ai, cli, context, core, settings, usage


class UsageLog(TetherTestCase):
    """The local usage log: counts, timings and outcomes only, never content; it can be switched off and never breaks a command."""

    def lines(self):
        try:
            with open(usage.path()) as f:
                return [json.loads(l) for l in f]
        except OSError:
            return []

    def test_record_keeps_numbers_flags_and_short_tokens_and_drops_everything_else(self):
        usage.record("demo.run", agent="claude", n=3, ok=True, seconds=1.23456, note="a free sentence", path="/Users/me/x", empty=None, **{"Bad Key": 1})
        (event,) = self.lines()
        self.assertEqual({k: event[k] for k in event if k not in ("t", "v")}, {"feature": "demo.run", "outcome": "ok", "agent": "claude", "n": 3, "ok": True, "seconds": 1.2346})
        self.assertEqual(event["v"], core.__version__)
        self.assertEqual(oct(os.stat(usage.path()).st_mode & 0o777), "0o600")

    def test_it_can_be_turned_off_by_the_setting_or_the_environment(self):
        with mock.patch.dict(os.environ, {"TETHER_USAGE_LOG": "0"}):
            usage.record("demo.run")
        self.assertEqual(self.lines(), [])
        settings.config_set("usage_log", "off")
        usage.record("demo.run")
        self.assertEqual(self.lines(), [])
        with mock.patch.dict(os.environ, {"TETHER_USAGE_LOG": "1"}):
            usage.record("demo.run")
        self.assertEqual(len(self.lines()), 1, "the environment wins over the setting")

    def test_a_log_that_cannot_be_written_never_breaks_the_command(self):
        with mock.patch.dict(os.environ, {"TETHER_STATE_DIR": "/proc/none/denied"}):
            usage.record("demo.run", n=1)  # must not raise

    def test_the_file_rotates_instead_of_growing_forever(self):
        with open(usage.path(), "w") as f:
            f.write("x" * (usage.EVENTS_MAX + 1))
        usage.record("demo.run")
        self.assertTrue(os.path.exists(usage.path() + ".1"))
        self.assertEqual(len(self.lines()), 1)

    def test_nothing_said_reaches_the_log_when_features_run(self):
        ctx = os.path.join(self.dir, "ctx")
        with mock.patch.dict(os.environ, {"TETHER_CONTEXT_DIR": ctx}):
            context.add_fact(ctx, "repos", "the zebrafish service is in ~/zf", about="where repos live")
            with contextlib.redirect_stdout(io.StringIO()):
                context.context_find(["zebrafish", "service"])
                context.context_find(["unicornword"])
            with mock.patch.object(ai, "ask_claude", return_value=('[{"topic": "repos", "fact": "the quokka api is in ~/q"}]', 0.02)):
                ai.ask_ai("PRIVATE-PROMPT-TEXT", "haiku")
        with open(usage.path()) as f:
            raw = f.read()
        for secret in ("zebrafish", "unicornword", "quokka", "PRIVATE-PROMPT-TEXT", "~/zf"):
            self.assertNotIn(secret, raw)
        find, miss, call = self.lines()
        self.assertEqual((find["feature"], find["hits"], find["exact"]), ("context.find", 1, True))
        self.assertEqual((miss["hits"], miss["exact"]), (0, False))
        self.assertEqual((call["feature"], call["agent"], call["model"], call["cost"], call["prompt_chars"]), ("ai.call", "claude", "haiku", 0.02, 19))

    def test_the_export_is_a_summary_of_totals_and_versions_that_passes_its_own_safety_check(self):
        usage.record("ai.call", agent="claude", model="haiku", seconds=2.5, cost=0.03, prompt_chars=500)
        usage.record("context.learn", proposed=2, agent="copilot", model="sonnet", seconds=7)
        with mock.patch.object(usage, "_tool_version", side_effect=lambda n: {"claude": "2.1.5"}.get(n)):
            summary = usage.export_summary(30)
        self.assertTrue(usage._only_safe(summary))
        self.assertEqual((summary["tether"], summary["agents"]["claude"], summary["agents"]["copilot"]), (core.__version__, "2.1.5", None))
        self.assertEqual(summary["features"]["ai.call"]["by_model"], {"claude:haiku": 1})
        self.assertNotIn("t", summary)
        self.assertFalse(any(k in json.dumps(summary) for k in ('"seconds":', "prompt_chars")), "no per-event fields")
        self.assertFalse(usage._only_safe({"note": "free text with spaces"}))
        self.assertFalse(usage._only_safe({"path": "/Users/me/x"}))

    def test_export_shows_everything_asks_first_and_writes_only_when_told(self):
        usage.record("context.find", terms=1, hits=0)
        target = os.path.join(self.dir, "out.json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(usage, "_tool_version", return_value=None):
            with self.assertRaises(core.TetherError):  # no terminal in the tests: it must not write unasked
                usage.stats_cmd(["--export", "-o", target])
            self.assertFalse(os.path.exists(target))
            usage.stats_cmd(["--export", "-o", target, "-y"])
        self.assertIn("everything the file contains", out.getvalue())
        with open(target) as f:
            written = json.load(f)
        self.assertEqual((written["tether_usage_summary"], written["features"]["context.find"]["no_hit"]), (1, 1))

    def test_acceptance_is_logged_per_agent_and_model_that_proposed_the_fact(self):
        ctx = os.path.join(self.dir, "ctx")
        with mock.patch.dict(os.environ, {"TETHER_CONTEXT_DIR": ctx}), contextlib.redirect_stdout(io.StringIO()):
            context.add_fact(ctx, "repos", "the api is in ~/api", about="where repos live")
            ops = [{"topic": "repos", "fact": f"fact number {n}", "about": "", "source": "s", "agent": a, "model": m}
                   for n, (a, m) in enumerate([("claude", "haiku"), ("claude", "haiku"), ("copilot", "sonnet"), ("copilot", "sonnet")])]
            context.context_queue_save(ctx, ops)
            context.context_decide(["--only", "1,3"], True)
            context.context_decide(["--only", "1"], False)  # what is left is now numbered from 1: the second claude fact
        origins = usage.summarize(self.lines())["learning"]["by_origin"]
        self.assertEqual((origins["claude:haiku"]["accepted"], origins["claude:haiku"]["rejected"]), (1, 1))
        self.assertEqual((origins["copilot:sonnet"]["accepted"], origins["copilot:sonnet"].get("rejected", 0)), (1, 0))
        self.assertEqual(origins["claude:haiku"]["accept_rate"], 0.5)

    def test_failures_are_logged_as_a_kind_never_as_the_message(self):
        self.assertEqual([ai.failure_kind(m) for m in ("claude not found on PATH: x", "the AI step failed (TimeoutExpired)", "Invalid API key, please login",
                                                       "rate limit reached", 'Model "x" is not available.', "weird")],
                         ["missing", "timeout", "auth", "limit", "model", "other"])
        with mock.patch.object(ai, "ask_claude", side_effect=core.TetherError("Invalid API key for jane@example.com")):
            with self.assertRaises(core.TetherError):
                ai.ask_ai("p", "haiku")
        (event,) = self.lines()
        self.assertEqual((event["outcome"], event["kind"]), ("error", "auth"))
        with open(usage.path()) as f:
            self.assertNotIn("jane", f.read())

    def test_commands_are_logged_by_name_only_and_a_space_name_never_is(self):
        def run(*argv, code=None, error=None):
            with mock.patch.object(sys, "argv", ["tether", *argv]), mock.patch.object(cli, "main", side_effect=error or SystemExit(code) if (error or code is not None) else None):
                try:
                    cli.run()
                except SystemExit:
                    pass
        run("fork", "My Secret Space")
        run("my-secret-space")
        run("delete", "x", "--sessions", code=2)
        run("status")  # not a feature worth logging
        with open(usage.path()) as f:
            raw = f.read()
        self.assertNotIn("secret", raw.lower())
        names = [(e["name"], e["outcome"], e.get("permanent")) for e in self.lines()]
        self.assertEqual(names, [("fork", "ok", None), ("open", "ok", None), ("delete", "error", True)])

    def test_hot_commands_are_logged_only_when_slow_or_failing(self):
        def run(argv, elapsed=0.0):
            clock = iter([100.0, 100.0 + elapsed, 100.0 + elapsed])
            with mock.patch.object(sys, "argv", ["tether", *argv]), mock.patch.object(cli, "main"), mock.patch.object(cli.time, "time", side_effect=lambda: next(clock)), \
                    mock.patch.object(usage.time, "time", side_effect=lambda: next(clock)):
                cli.run()
        run(["touch", "sp1"], 0.1)
        self.assertEqual(self.lines(), [])
        run(["touch", "sp1"], 3.0)
        (event,) = self.lines()
        self.assertEqual((event["name"], event["slow"]), ("touch", True))

    def test_a_find_followed_by_a_topic_list_is_counted(self):
        usage.record("context.find", terms=1, hits=0)
        usage.record("context.list", topic=True)
        usage.record("context.find", terms=1, hits=2)
        self.assertEqual(usage.summarize(self.lines())["context.find"]["then_list"], 1)

    def test_the_store_snapshot_and_job_rows_are_numbers_and_days(self):
        ctx = os.path.join(self.dir, "ctx")
        context.add_fact(ctx, "repos", "a fact", about="where")
        context.add_fact(ctx, "repos", "another fact")
        snap = context.store_snapshot(ctx)
        self.assertEqual((snap["topics"], snap["facts"], snap["largest_topic"], snap["waiting"]), (1, 2, 2, 0))
        usage.record("job", job="harvest", reason="cap", read=1)
        usage.record("job", "skipped", job="harvest", reason="locked")
        row = usage.summarize(self.lines())["job"]
        self.assertEqual((row["by_job"]["harvest"]["runs"], row["by_job"]["harvest"]["skipped"]), (2, 1))
        self.assertEqual(row["reasons"], {"cap": 1, "locked": 1})
        self.assertTrue(usage._only_safe(row))

    def test_stats_summarises_learning_and_lookups(self):
        for e in ({"feature": "context.learn", "proposed": 4, "similar": 1, "agent": "claude", "seconds": 6, "cost": 0.1},
                  {"feature": "context.accept", "accepted": 3, "into": True}, {"feature": "context.reject", "rejected": 1},
                  {"feature": "context.find", "hits": 0}, {"feature": "context.find", "hits": 2, "exact": True}):
            usage.record(e.pop("feature"), **e)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            usage.stats_cmd(["--json"])
        summary = json.loads(out.getvalue())["features"]
        self.assertEqual({k: summary["learning"]["all"][k] for k in ("proposed", "accepted", "rejected", "accept_rate")},
                         {"proposed": 4, "accepted": 3, "rejected": 1, "accept_rate": 0.75})
        self.assertEqual((summary["context.find"]["runs"], summary["context.find"]["no_hit"]), (2, 1))
        self.assertEqual(summary["context.accept"]["filed_elsewhere"], 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            usage.stats_cmd([])
        self.assertIn("context.learn", out.getvalue())
        self.assertIn("never what was said", out.getvalue())


if __name__ == "__main__":
    unittest.main()
