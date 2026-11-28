"""Tests for the tetherlib package behind bin/tether. Run: python3 -m unittest discover -s tests -v

Nothing here touches the real herdr app, ~/.claude, or ~/.local/state: every test uses a temporary
state directory and mocks the herdr/hc calls and the agent session lookups.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import tarfile
import subprocess
import time
import unittest
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))

from tetherlib import ai, backups, cli, context, continue_, core, doctor, herdr, launch, reading, search, settings, store, usage, workspace  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"
SESSION2 = "66666666-7777-8888-9999-000000000000"


def slurp(path):
    with open(path) as f:
        return f.read()


def link(agent="claude", session=SESSION, **extra):
    return dict({"agent": agent, "session": session, "cwd": "/tmp", "tab": "tab1", "tab_name": "claude",
                 "created": "2026-01-01T00:00:00+00:00", "last_used": "2026-01-01T00:00:00+00:00"}, **extra)


def space(*terminals, tab="tab1", name="work"):
    """A `herdr-app ls` space with the given terminal addresses in one tab."""
    return {"id": "spLIVE", "name": name,
            "tabs": [{"id": tab, "name": "claude", "terminals": [{"terminal": t} for t in terminals]}]}


class TetherTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        patcher = mock.patch.dict(os.environ, {"TETHER_STATE_DIR": self.dir}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ["TETHER_NO_ADOPT"] = "1"
        self.addCleanup(os.environ.pop, "TETHER_NO_ADOPT", None)
        for k in ("TETHER_DRY_RUN", "TETHER_FORCE"):
            os.environ.pop(k, None)

    def write_state(self, data):
        with open(os.path.join(self.dir, "spaces.json"), "w") as f:
            json.dump(data, f)

    def read_state(self):
        with open(os.path.join(self.dir, "spaces.json")) as f:
            return json.load(f)


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


class SmallHelpers(TetherTestCase):
    def test_ago(self):
        self.assertEqual(core.ago("not a date"), "unknown")
        self.assertEqual(core.ago(core.now()), "just now")

    def test_env_reads_tether_names(self):
        with mock.patch.dict(os.environ, {"TETHER_NO_CD": "1"}):
            self.assertEqual(core.env("NO_CD"), "1")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(core.env("NO_CD"))

    def test_agent_command_is_the_agent_not_a_shell_mentioning_it(self):
        self.assertTrue(store._is_agent_command("claude --resume abc --name x", "claude"))
        self.assertTrue(store._is_agent_command("/Users/me/.local/bin/claude", "claude"))
        self.assertTrue(store._is_agent_command("node /opt/x/copilot.js --resume y", "copilot"))
        self.assertFalse(store._is_agent_command("/bin/zsh -c tether link claude abc", "claude"))
        self.assertFalse(store._is_agent_command("", "claude"))

    def test_session_paths_only_for_real_uuids(self):
        self.assertEqual(store.session_paths("claude", "../../etc"), [])
        self.assertEqual(store.session_paths("claude", "*"), [])
        self.assertEqual(store.session_paths("copilot", ""), [])

    def test_match_space_exact_beats_substring(self):
        rows = [("a", "work", "", False), ("b", "work extra", "", False)]
        self.assertEqual(workspace.match_space("work", rows), "a")
        self.assertEqual(workspace.match_space("extra", rows), "b")
        self.assertIsNone(workspace.match_space("nothing", rows))


class State(TetherTestCase):
    def test_corrupt_state_is_kept_aside_and_replaced(self):
        with open(os.path.join(self.dir, "spaces.json"), "w") as f:
            f.write("{not json")
        with contextlib.redirect_stderr(io.StringIO()), store.State() as st:
            self.assertEqual(st.data, {})
        self.assertTrue(any(n.startswith("spaces.json.corrupt-") for n in os.listdir(self.dir)))

    def test_state_dir_default_and_override(self):
        with mock.patch.dict(os.environ, {"TETHER_STATE_DIR": "/somewhere/else"}):
            self.assertEqual(core.state_dir(), "/somewhere/else")
        env = {k: v for k, v in os.environ.items() if k != "TETHER_STATE_DIR"}
        env["HOME"] = "/home/someone"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(core.state_dir(), "/home/someone/.local/state/tether")

class FindingTerminals(TetherTestCase):
    def test_exact_then_machine_prefixed(self):
        spaces = [space("t1", "box/t5")]
        self.assertEqual(herdr.find_terminal(spaces, "t1")[2]["terminal"], "t1")
        self.assertEqual(herdr.find_terminal(spaces, "t5")[2]["terminal"], "box/t5")
        self.assertIsNone(herdr.find_terminal(spaces, "t9"))

    def test_same_address_in_two_spaces_is_refused(self):
        a, b = space("m1/t1"), dict(space("m2/t1"), id="spOTHER")
        with self.assertRaises(core.TetherError):
            herdr.find_terminal([a, b], "t1")

    def test_plan_space_says_why_a_terminal_is_skipped(self):
        sp = {"id": "spLIVE", "name": "work", "tabs": [
            {"id": "tab1", "name": "a", "terminals": [{"terminal": "t1"}]},
            {"id": "tab2", "name": "b", "terminals": [{"terminal": "t2", "app": "claude"}]},
            {"id": "tab9", "name": "c", "terminals": [{"terminal": "t3"}]},
        ]}
        data = {"terminals": {"t1": link(tab="tab1"), "t2": link(SESSION2, tab="tab2"),
                              "t3": link(tab="tab3"), "t4": link(tab="tab4")}}
        _, _, _, plan, skipped = workspace.plan_space(data, sp)
        self.assertEqual([p[0] for p in plan], ["t1"])
        reasons = {addr: why for addr, _, _, why in skipped}
        self.assertIn("already running", reasons["t2"])
        self.assertIn("different tab", reasons["t3"])
        self.assertIn("no longer exists", reasons["t4"])


class Launch(TetherTestCase):
    def run_dry(self, agent, args=(), where=None, exists=True):
        where = where or herdr.Where("spLIVE", "work", "t1", "tab1", "claude")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"TETHER_DRY_RUN": "1"}), \
                mock.patch.object(herdr, "resolve_where", return_value=(where, None)), \
                mock.patch.object(core, "find_tool", return_value="/bin/" + agent), \
                mock.patch.object(store, "session_exists", return_value=exists), \
                contextlib.redirect_stdout(out):
            launch.launch(agent, list(args))
        return out.getvalue()

    def test_first_run_starts_a_new_named_session(self):
        out = self.run_dry("claude")
        self.assertIn("(new)", out)
        self.assertIn("--session-id", out)
        self.assertIn("--name work", out)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "spaces.json")), "dry run writes nothing")

    def test_second_run_resumes_the_linked_session(self):
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": link()}}})
        out = self.run_dry("claude")
        self.assertIn("(resume)", out)
        self.assertIn(f"--resume {SESSION}", out)

    def test_linked_session_without_a_transcript_starts_fresh(self):
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": link()}}})
        out = self.run_dry("claude", exists=False)
        self.assertIn("(new)", out)
        self.assertNotIn(SESSION, out)

    def test_other_agent_in_same_terminal_replaces_the_link_but_keeps_the_transcript(self):
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": link("claude")}}})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = self.run_dry("copilot")
        self.assertIn("(new)", out)
        self.assertIn("transcript kept", err.getvalue())

    def test_explicit_session_flag_steps_aside(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"TETHER_DRY_RUN": "1"}), \
                mock.patch.object(core, "find_tool", return_value="/bin/claude"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as out:
            launch.launch("claude", ["--resume", "abc"])
        self.assertIn("would run: claude --resume abc", out.getvalue())

    def test_running_session_is_not_started_twice(self):
        entry = link(pid=4242, pid_started="Mon 1 Jan")
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": entry}}})
        with mock.patch.object(store, "_running", return_value=True), \
                mock.patch.object(herdr, "resolve_where",
                                  return_value=(herdr.Where("spLIVE", "work", "t1", "tab1", "claude"), None)), \
                mock.patch.object(core, "find_tool", return_value="/bin/claude"), \
                mock.patch.object(store, "session_exists", return_value=True):
            with self.assertRaises(SystemExit) as cm:
                launch.launch("claude", [])
        self.assertIn("already running", str(cm.exception))


class Linking(TetherTestCase):
    where = herdr.Where("spLIVE", "work", "t1", "tab1", "claude")

    def do_link(self, detected, session_arg=None):
        patches = [mock.patch.object(herdr, "resolve_where", return_value=(self.where, None)),
                   mock.patch.object(store, "session_exists", return_value=True),
                   mock.patch.object(store, "session_cwd", return_value="/tmp"),
                   mock.patch.object(store, "current_session", return_value=SESSION if detected else None),
                   mock.patch.object(store, "guess_session", return_value=SESSION),
                   mock.patch.object(store, "agent_process", return_value=(4242, "Mon 1 Jan"))]
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()):
            for p in patches:
                stack.enter_context(p)
            launch.link("claude", session_arg)
        return self.read_state()["spLIVE"]["terminals"]["t1"]

    def test_linking_the_session_you_are_in_records_its_process(self):
        entry = self.do_link(detected=True)
        self.assertEqual((entry["pid"], entry["pid_started"]), (4242, "Mon 1 Jan"))

    def test_linking_a_given_or_guessed_session_records_no_process(self):
        self.assertNotIn("pid", self.do_link(detected=False))
        self.assertNotIn("pid", self.do_link(detected=False, session_arg=SESSION))

    def test_a_session_belongs_to_one_terminal(self):
        self.write_state({"spOTHER": {"name": "old", "terminals": {"t7": link()}}})
        self.do_link(detected=False, session_arg=SESSION)
        self.assertEqual(self.read_state()["spOTHER"]["terminals"], {})


class Reopening(TetherTestCase):
    def reopen(self, fail_on=None):
        """Reopen a closed space with two linked terminals; creating tab number `fail_on` fails."""
        self.write_state({"spOLD": {"name": "work", "terminals": {
            "t9": link(session=SESSION, tab_name="one"), "t10": link(session=SESSION2, tab_name="two")}}})
        calls = []

        def herdr_do(args):
            calls.append(args)
            if fail_on is not None and len(calls) == fail_on:
                raise core.TetherError("boom")
            return {"space": "spNEW", "terminal": f"t{len(calls)}"}

        live = [{"id": "spNEW", "name": "work", "tabs": [
            {"id": "tabN", "name": "x", "terminals": [{"terminal": "t1"}, {"terminal": "t2"}]}]}]
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(core, "find_tool", return_value="/bin/hc"), \
                mock.patch.object(herdr, "herdr_do", side_effect=herdr_do), \
                mock.patch.object(herdr, "herdr_ls", return_value=live), \
                mock.patch.object(store, "session_exists", return_value=True), \
                mock.patch.object(subprocess, "run", return_value=ok), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            workspace.reopen_closed("spOLD", self.read_state()["spOLD"], yes=True)
        return self.read_state()

    def test_everything_recreated_moves_the_links_to_the_new_space(self):
        state = self.reopen()
        self.assertNotIn("spOLD", state)
        self.assertEqual(len(state["spNEW"]["terminals"]), 2)

    def test_tabs_that_could_not_be_recreated_keep_their_links(self):
        state = self.reopen(fail_on=2)
        self.assertEqual(len(state["spNEW"]["terminals"]), 1)
        kept = state["spOLD"]["terminals"]
        self.assertEqual(len(kept), 1, "the link that failed must not be forgotten")
        restored = {e["session"] for e in state["spNEW"]["terminals"].values()}
        self.assertEqual(restored | {list(kept.values())[0]["session"]}, {SESSION, SESSION2})


class OpeningByName(TetherTestCase):
    def test_bare_name_finds_a_closed_space(self):
        self.write_state({"spOLD": {"name": "Previous work", "terminals": {"t9": link()}}})
        with mock.patch.object(herdr, "ensure_app"), mock.patch.object(herdr, "herdr_ls", return_value=[]), \
                mock.patch.object(workspace, "open_space") as opened:
            workspace.open_by_name_or_fail("previous")
        opened.assert_called_once_with(None, False, sid="spOLD")

    def test_unknown_name_fails_clearly(self):
        with mock.patch.object(herdr, "ensure_app"), mock.patch.object(herdr, "herdr_ls", return_value=[]):
            with self.assertRaises(SystemExit) as cm:
                workspace.open_by_name_or_fail("nothing")
        self.assertIn("unknown command or space", str(cm.exception))


class Deleting(TetherTestCase):
    def test_delete_with_sessions_keeps_a_running_session(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t9": link(pid=1, pid_started="x")}}})
        removed = []
        with mock.patch.object(herdr, "herdr_ls", return_value=[]), \
                mock.patch.object(store, "_running", return_value=True), \
                mock.patch.object(store, "session_paths", return_value=["/fake/transcript"]), \
                mock.patch.object(store, "_remove", side_effect=removed.append), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                workspace.delete_space(["--id", "spOLD", "--sessions", "-y"])
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(removed, [], "a running session's transcript must not be deleted")

    def test_delete_unlink_only_keeps_transcripts(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t9": link()}}})
        removed = []
        with mock.patch.object(herdr, "herdr_ls", return_value=[]), \
                mock.patch.object(store, "session_paths", return_value=["/fake/transcript"]), \
                mock.patch.object(store, "_remove", side_effect=removed.append), \
                contextlib.redirect_stdout(io.StringIO()):
            workspace.delete_space(["--id", "spOLD", "-y"])
        self.assertEqual(removed, [])
        self.assertNotIn("spOLD", self.read_state())


class DeletingForGood(TetherTestCase):
    def test_close_waits_for_the_space_and_its_agents_then_unlinks(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t9": link(session=SESSION)}}})
        live = [{"id": "spOLD", "name": "work", "tabs": []}]
        closed = []
        running = [{"agent": "claude", "session": SESSION}]
        with mock.patch.object(herdr, "herdr_ls", side_effect=[live, live, []]), \
                mock.patch.object(store, "running_agents", side_effect=[running, []]), \
                mock.patch.object(herdr, "herdr_do", side_effect=lambda a: closed.append(a) or {}), \
                mock.patch.object(backups, "backup_cmd"), \
                mock.patch.object(time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            workspace.delete_space(["--id", "spOLD", "-y", "--close"])
        self.assertEqual(closed, [["space.close", "--id", "spOLD", "--confirmed", "true"]])
        self.assertNotIn("spOLD", self.read_state())

    def test_permanent_delete_removes_the_backup_copy_too(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t9": link(session=SESSION)}}})
        gone = []
        with mock.patch.object(herdr, "herdr_ls", return_value=[]), \
                mock.patch.object(store, "session_paths", return_value=["/fake/transcript"]), \
                mock.patch.object(store, "_remove"), \
                mock.patch.object(backups, "delete_backup_of_session", side_effect=lambda a, ss: gone.append((a, ss))), \
                contextlib.redirect_stdout(io.StringIO()):
            workspace.delete_space(["--id", "spOLD", "--sessions", "-y"])
        self.assertEqual(gone, [("claude", SESSION)])

    def test_purge_session_deletes_both_copies_and_refuses_a_running_one(self):
        claude, bk = os.path.join(self.dir, "claude"), os.path.join(self.dir, "bk")
        paths = [os.path.join(h, "projects", "p", SESSION + ".jsonl") for h in (claude, os.path.join(bk, "claude"))]
        for path in paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("x" * 5)
        with mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": bk}), \
                mock.patch.object(store, "_claude_home", return_value=claude):
            with mock.patch.object(store, "running_agents", return_value=[{"agent": "claude", "session": SESSION}]):
                with self.assertRaises(core.TetherError):
                    backups.purge_session("claude", SESSION)
            self.assertTrue(all(os.path.exists(p) for p in paths), "a running session is not touched")
            with mock.patch.object(store, "running_agents", return_value=[]):
                out = backups.purge_session("claude", SESSION)
            self.assertEqual(out["bytes"], 10)
            self.assertFalse(any(os.path.exists(p) for p in paths))
            with mock.patch.object(store, "running_agents", return_value=[]):
                with self.assertRaises(core.TetherError):
                    backups.purge_session("claude", SESSION)


class Sessions(TetherTestCase):
    """Backup, restore, export, import: with a fake Claude home, a fake Copilot home and a backup folder."""

    def setUp(self):
        super().setUp()
        self.claude, self.copilot, self.backup = (tempfile.mkdtemp() for _ in range(3))
        for d in (self.claude, self.copilot, self.backup):
            self.addCleanup(shutil.rmtree, d, True)
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.claude, "COPILOT_HOME": self.copilot,
                                           "TETHER_BACKUP_DIR": self.backup})
        env.start()
        self.addCleanup(env.stop)
        for name in ("herdr_ls",):
            p = mock.patch.object(herdr, name, side_effect=core.TetherError("no herdr in tests"))
            p.start()
            self.addCleanup(p.stop)
        self.transcript = os.path.join(self.claude, "projects", "-tmp-x", SESSION + ".jsonl")
        self.extra = os.path.join(self.claude, "file-history", SESSION, "snap")
        self.copilot_file = os.path.join(self.copilot, "session-state", SESSION2, "events.jsonl")
        for path, text in ((self.transcript, '{"cwd": "/tmp"}\n'), (self.extra, "snap"), (self.copilot_file, "evt")):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        self.write_state({"spLIVE": {"name": "work", "terminals": {
            "t1": link("claude", SESSION), "t2": link("copilot", SESSION2, tab_name="copilot")}}})

    def say(self, fn, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            fn(*args)
        return out.getvalue()

    def test_backup_copies_everything_once_and_is_private(self):
        out = self.say(backups.backup_cmd, [])
        self.assertIn("backed up 2 session(s)", out)
        for rel in ("claude/projects/-tmp-x/" + SESSION + ".jsonl", "claude/file-history/" + SESSION + "/snap",
                    "copilot/session-state/" + SESSION2 + "/events.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(self.backup, rel)), rel)
        self.assertEqual(os.stat(self.backup).st_mode & 0o777, 0o700)
        manifest = os.path.join(self.backup, "manifest.json")
        self.assertEqual(os.stat(manifest).st_mode & 0o777, 0o600)
        self.assertEqual(len(json.loads(slurp(manifest))["spaces"]["spLIVE"]["links"]), 2)
        self.assertIn("0 file(s) new or changed", self.say(backups.backup_cmd, []), "second run copies nothing")

    def test_backup_picks_up_a_changed_transcript(self):
        self.say(backups.backup_cmd, [])
        with open(self.transcript, "a") as f:
            f.write('{"more": true}\n')
        self.assertIn("1 file(s) new or changed", self.say(backups.backup_cmd, []))
        copy = os.path.join(self.backup, "claude", "projects", "-tmp-x", SESSION + ".jsonl")
        self.assertIn("more", slurp(copy))

    def test_backup_never_deletes_what_the_agent_swept(self):
        self.say(backups.backup_cmd, [])
        os.remove(self.transcript)
        self.say(backups.backup_cmd, [])
        self.assertTrue(os.path.exists(os.path.join(self.backup, "claude", "projects", "-tmp-x", SESSION + ".jsonl")))

    def test_backup_inside_an_unignored_git_repo_is_refused(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, True)
        subprocess.run(["git", "init", "-q", repo], check=True)
        target = os.path.join(repo, "backups")
        with mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": target}):
            with self.assertRaises(core.TetherError) as cm:
                self.say(backups.backup_cmd, [])
            self.assertIn("not git-ignored", str(cm.exception))
            with open(os.path.join(repo, ".gitignore"), "w") as f:
                f.write("backups/\n")
            self.assertIn("backed up 2 session(s)", self.say(backups.backup_cmd, []))

    def test_restore_puts_back_a_swept_transcript_and_never_overwrites(self):
        self.say(backups.backup_cmd, [])
        os.remove(self.transcript)
        with open(self.extra, "w") as f:
            f.write("newer, written after the backup")
        self.assertIn("restored", self.say(backups.restore_cmd, []))
        self.assertTrue(os.path.exists(self.transcript))
        self.assertEqual(slurp(self.extra), "newer, written after the backup")

    def test_backup_list_names_sessions_for_people(self):
        with open(self.transcript, "w") as f:
            f.write('{"type": "system"}\n{"type": "user", "message": {"content": "<command-name>x</command-name>"}}\n'
                    '{"type": "user", "message": {"content": "Plan the   ledger\\nmigration"}, "cwd": "/tmp"}\n')
        with open(self.copilot_file, "w") as f:
            f.write('{"type": "session.start"}\n{"type": "user.message", "data": {"content": "/tether list"}}\n')
        self.say(backups.backup_cmd, [])
        rows = {r["agent"]: r for r in backups.backup_list()}
        self.assertEqual(rows["claude"]["title"], "Plan the ledger migration")  # wrappers skipped, spaces collapsed
        self.assertEqual(rows["copilot"]["title"], "/tether list")
        self.assertEqual((rows["claude"]["space"], rows["copilot"]["tab"]), ("work", "copilot"))
        self.assertTrue(rows["claude"]["in_agent"] and rows["claude"]["in_backup"] and rows["claude"]["linked"])
        self.assertGreater(rows["claude"]["bytes"], 0)

    def test_backup_list_marks_swept_sessions_and_lists_each_once(self):
        self.say(backups.backup_cmd, [])
        os.remove(self.transcript)
        manifest = os.path.join(self.backup, "manifest.json")
        data = json.loads(slurp(manifest))
        data["spaces"]["spOLD"] = {"name": "older entry", "backed_up": "2020-01-01T00:00:00+00:00",
                                   "links": [dict(data["spaces"]["spLIVE"]["links"][0], tab_name="old")]}
        with open(manifest, "w") as f:
            json.dump(data, f)
        rows = [r for r in backups.backup_list() if r["agent"] == "claude"]
        self.assertEqual(len(rows), 1, "a session that moved between spaces is listed once")
        self.assertEqual(rows[0]["space_id"], "spLIVE")
        self.assertFalse(rows[0]["in_agent"])
        self.assertTrue(rows[0]["in_backup"], "gone from the agent but restorable")

    def test_backup_list_skips_sessions_that_exist_nowhere(self):
        self.say(backups.backup_cmd, [])
        shutil.rmtree(os.path.join(self.backup, "copilot"))
        shutil.rmtree(os.path.join(self.copilot, "session-state"))
        self.assertEqual([r["agent"] for r in backups.backup_list()], ["claude"])

    def test_restore_one_session_by_id(self):
        self.say(backups.backup_cmd, [])
        os.remove(self.transcript)
        out = self.say(backups.restore_cmd, ["--session", SESSION])
        self.assertIn("claude --resume " + SESSION, out)
        self.assertTrue(os.path.exists(self.transcript))
        self.assertIn("already has", self.say(backups.restore_cmd, ["--session", SESSION]))
        with self.assertRaises(SystemExit):
            self.say(backups.restore_cmd, ["--session", "00000000-0000-4000-8000-000000000009"])
        with self.assertRaises(SystemExit):
            self.say(backups.restore_cmd, ["--session", "../../etc/passwd"])

    def test_launch_restores_a_gone_transcript_from_the_backup(self):
        self.say(backups.backup_cmd, [])
        os.remove(self.transcript)
        where = herdr.Where("spLIVE", "work", "t1", "tab1", "claude")
        with mock.patch.dict(os.environ, {"TETHER_DRY_RUN": "1"}), \
                mock.patch.object(herdr, "resolve_where", return_value=(where, None)), \
                mock.patch.object(core, "find_tool", return_value="/bin/claude"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as out:
                launch.launch("claude", [])
        self.assertIn("(resume)", out.getvalue())
        self.assertIn("a backup has it", err.getvalue())
        self.assertFalse(os.path.exists(self.transcript), "a dry run restores nothing")

    def test_background_backup_runs_at_most_once_per_window(self):
        with mock.patch.dict(os.environ, {"TETHER_AUTO_BACKUP": "1"}), \
                mock.patch.object(subprocess, "Popen") as popen:
            backups.auto_backup_if_due()
            backups.auto_backup_if_due()
        self.assertEqual(popen.call_count, 1)
        self.assertIn("backup", popen.call_args[0][0])

    def test_background_backup_is_off_unless_enabled(self):
        with mock.patch.object(subprocess, "Popen") as popen:
            backups.auto_backup_if_due()
        popen.assert_not_called()

    def export(self, name="work"):
        out = os.path.join(tempfile.mkdtemp(), "bundle.tar.gz")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        self.say(backups.export_space, [name, "-o", out])
        return out

    def test_export_then_import_moves_a_space_to_another_machine(self):
        bundle = self.export()
        self.assertEqual(os.stat(bundle).st_mode & 0o777, 0o600)
        shutil.rmtree(os.path.join(self.claude, "projects"))
        shutil.rmtree(os.path.join(self.claude, "file-history"))
        shutil.rmtree(os.path.join(self.copilot, "session-state"))
        self.write_state({})
        out = self.say(backups.import_space, [bundle])
        self.assertIn("tether open 'work'", out)
        self.assertEqual(slurp(self.transcript), '{"cwd": "/tmp"}\n')
        self.assertTrue(os.path.exists(self.copilot_file))
        state = self.read_state()
        (sid, sp), = state.items()
        self.assertTrue(sid.startswith("imported-"))
        self.assertEqual({e["session"] for e in sp["terminals"].values()}, {SESSION, SESSION2})

    def test_import_never_overwrites_and_renames_a_clashing_space(self):
        bundle = self.export()
        with open(self.transcript, "w") as f:
            f.write("this machine's own copy")
        out = self.say(backups.import_space, [bundle])
        self.assertIn("'work (imported)'", out)
        self.assertEqual(slurp(self.transcript), "this machine's own copy")

    def test_export_falls_back_to_the_backup_when_the_agent_swept_it(self):
        self.say(backups.backup_cmd, [])
        os.remove(self.transcript)
        shutil.rmtree(os.path.join(self.claude, "file-history"))
        bundle = self.export()
        os.remove(os.path.join(self.backup, "claude", "projects", "-tmp-x", SESSION + ".jsonl"))
        with tarfile.open(bundle) as tar:
            names = tar.getnames()
        self.assertIn(f"files/claude/projects/-tmp-x/{SESSION}.jsonl", names)

    def test_options_are_parsed_strictly(self):
        with self.assertRaises(SystemExit) as cm:
            core.parse_opts(["--nope"], flags=("--json",))
        self.assertIn("unknown option", str(cm.exception))
        with self.assertRaises(SystemExit):
            core.parse_opts(["--id"], valued=("--id",))
        flags, vals, words = core.parse_opts(["a", "--json", "--id", "x", "b"], flags=("--json",), valued=("--id",))
        self.assertEqual((flags, vals, words), ({"--json"}, {"--id": "x"}, ["a", "b"]))

    def test_backup_by_id_touches_only_that_space(self):
        state = self.read_state()
        state["spOTHER"] = {"name": "other", "terminals": {"t5": link("claude", SESSION2, tab_name="x")}}
        self.write_state(state)
        out = json.loads(self.say(backups.backup_cmd, ["--id", "spLIVE", "--json"]))
        self.assertEqual(out["sessions"], 2)
        manifest = json.loads(slurp(os.path.join(self.backup, "manifest.json")))
        self.assertEqual(list(manifest["spaces"]), ["spLIVE"])
        with self.assertRaises(SystemExit):
            self.say(backups.backup_cmd, ["--id", "spNOPE"])

    def test_status_reports_what_is_backed_up_missing_and_recoverable(self):
        status = json.loads(self.say(backups.backup_cmd, ["--status", "--json"]))
        self.assertEqual((status["linked"], status["backed_up"], status["missing"]), (2, 0, 0))
        self.assertIsNone(status["last_backup"])
        self.say(backups.backup_cmd, [])
        os.remove(self.transcript)
        status = json.loads(self.say(backups.backup_cmd, ["--status", "--json"]))
        self.assertEqual((status["backed_up"], status["missing"], status["recoverable"]), (2, 1, 1))
        self.assertTrue(status["last_backup"])
        self.assertGreater(status["bytes"], 0)
        self.assertIn("recoverable", self.say(backups.backup_cmd, ["--status"]))

    def test_list_json_says_which_links_a_backup_can_restore(self):
        self.say(backups.backup_cmd, [])
        os.remove(self.transcript)
        with mock.patch.object(herdr, "herdr_ls", return_value=[]):
            rows = json.loads(self.say(workspace.list_json))
        by_agent = {lk["agent"]: lk for lk in rows[0]["links"]}
        self.assertEqual((by_agent["claude"]["transcript"], by_agent["claude"]["recoverable"]), (False, True))
        self.assertEqual((by_agent["copilot"]["transcript"], by_agent["copilot"]["recoverable"]), (True, True))
        self.assertTrue(backups.available("claude", SESSION))

    def test_export_to_a_folder_names_the_file_and_never_overwrites(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        first = json.loads(self.say(backups.export_space, ["--id", "spLIVE", "-o", folder, "--json"]))
        second = json.loads(self.say(backups.export_space, ["--id", "spLIVE", "-o", folder, "--json"]))
        self.assertTrue(first["path"].startswith(os.path.abspath(folder)))
        self.assertRegex(os.path.basename(first["path"]), r"^tether-export-work-\d{8}\.tar\.gz$")
        self.assertRegex(os.path.basename(second["path"]), r"^tether-export-work-\d{8}-2\.tar\.gz$")
        self.assertEqual((first["sessions"], first["missing"]), (2, 0))
        self.assertEqual(os.stat(first["path"]).st_mode & 0o777, 0o600)

    def test_import_json_reports_the_new_closed_space(self):
        bundle = self.export()
        result = json.loads(self.say(backups.import_space, [bundle, "--name", "moved", "--json"]))
        self.assertEqual((result["name"], result["sessions"]), ("moved", 2))
        self.assertIn(result["id"], self.read_state())

    def bundle_with(self, *extra, session=SESSION):
        """A bundle whose manifest lists `session`, plus the given extra tar members."""
        path = os.path.join(tempfile.mkdtemp(), "evil.tar.gz")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        manifest = {"version": 1, "space": {"name": "evil", "links": [
            {"agent": "claude", "session": session, "terminal": "t1"}]}}
        with tarfile.open(path, "w:gz") as tar:
            blob = json.dumps(manifest).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
            for member in extra:
                tar.addfile(*member)
        return path

    def member(self, name, data=b"x", kind=None):
        info = tarfile.TarInfo(name)
        if kind == "symlink":
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            return (info,)
        info.size = len(data)
        return info, io.BytesIO(data)

    def assert_refused(self, bundle):
        before = self.read_state()
        with self.assertRaises(SystemExit) as cm:
            self.say(backups.import_space, [bundle])
        self.assertIn("refusing", str(cm.exception))
        self.assertEqual(self.read_state(), before)
        self.assertFalse(os.path.exists(os.path.join(self.claude, "evil")))

    def test_import_refuses_path_traversal(self):
        self.assert_refused(self.bundle_with(self.member("files/claude/projects/p/../../../evil")))

    def test_import_refuses_absolute_paths_and_symlinks(self):
        self.assert_refused(self.bundle_with(self.member("/etc/evil")))
        self.assert_refused(self.bundle_with(self.member(f"files/claude/projects/p/{SESSION}.jsonl", kind="symlink")))

    def test_import_refuses_files_that_are_not_the_listed_sessions(self):
        self.assert_refused(self.bundle_with(self.member(f"files/claude/projects/p/{SESSION2}.jsonl")))
        self.assert_refused(self.bundle_with(self.member("files/claude/settings.json")))
        self.assert_refused(self.bundle_with(self.member("files/claude/projects/p/notes.txt")))

    def test_import_rejects_something_that_is_not_an_export(self):
        path = os.path.join(self.backup, "junk.tar.gz")
        with tarfile.open(path, "w:gz"):
            pass
        with self.assertRaises(SystemExit) as cm:
            self.say(backups.import_space, [path])
        self.assertIn("not a tether export", str(cm.exception))


class HomeDir(TetherTestCase):
    def dry(self, cwd, **env):
        where = herdr.Where("spLIVE", "work", "t1", "tab1", "claude")
        out = io.StringIO()
        with mock.patch.dict(os.environ, dict({"TETHER_DRY_RUN": "1"}, **env)), \
                mock.patch.object(herdr, "resolve_where", return_value=(where, None)), \
                mock.patch.object(core, "find_tool", return_value="/bin/claude"), \
                mock.patch.object(os, "getcwd", return_value=cwd), \
                contextlib.redirect_stdout(out):
            launch.launch("claude", [])
        return out.getvalue()

    def test_config_round_trip_and_refuses_home(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        with contextlib.redirect_stdout(io.StringIO()):
            settings.config_cmd(["home_dir", other])
        self.assertEqual(settings.home_dir_setting(), other)
        with self.assertRaises(SystemExit):
            settings.config_cmd(["home_dir", "~"])
        with contextlib.redirect_stdout(io.StringIO()):
            settings.config_cmd(["home_dir", "--unset"])
        self.assertIsNone(settings.home_dir_setting())
        with contextlib.redirect_stdout(io.StringIO()):
            settings.config_cmd(["home_dir", "--default"])
        fake = os.path.join(self.dir, "fakehome")
        with mock.patch.object(settings, "DEFAULT_HOME_DIR", fake):
            self.assertEqual(settings.home_dir_setting(), fake)
            self.assertTrue(os.path.isdir(fake), "created on first use")
        self.assertEqual(oct(os.stat(core.config_path()).st_mode & 0o777), "0o600")

    def test_new_session_started_in_home_goes_to_home_dir(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        out = self.dry(os.path.expanduser("~"), TETHER_HOME_DIR=other)
        self.assertIn(f"would run: cd {other} &&", out)

    def test_other_folders_and_unset_setting_are_left_alone(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        self.assertIn("would run: cd /tmp &&", self.dry("/tmp", TETHER_HOME_DIR=other))
        with open(core.config_path(), "w") as f:
            json.dump({"home_dir": None}, f)
        self.assertIn(f"would run: cd {os.path.expanduser('~')} &&", self.dry(os.path.expanduser("~")))


class OpeningOneSession(TetherTestCase):
    def run_open(self, live, state, restored=1):
        self.write_state(state)
        calls, sent = [], []

        def herdr_do(args):
            calls.append(args)
            return {"space": "spLIVE", "terminal": "t7"}

        ok = mock.Mock(returncode=0, stdout="", stderr="")
        out = io.StringIO()
        with mock.patch.object(herdr, "ensure_app"), \
                mock.patch.object(core, "find_tool", return_value="/bin/hc"), \
                mock.patch.object(herdr, "herdr_do", side_effect=herdr_do), \
                mock.patch.object(herdr, "herdr_ls", return_value=live), \
                mock.patch.object(store, "session_exists", return_value=False), \
                mock.patch.object(backups, "restore_session", return_value=restored), \
                mock.patch.object(subprocess, "run", side_effect=lambda cmd, **k: sent.append(cmd) or ok), \
                contextlib.redirect_stdout(out):
            workspace.open_session([SESSION])
        return calls, sent, out.getvalue()

    def test_live_space_gets_a_new_tab_and_the_link_moves(self):
        live = [{"id": "spLIVE", "name": "work", "tabs": [
            {"id": "tabN", "name": "claude", "terminals": [{"terminal": "t7"}]}]}]
        calls, sent, out = self.run_open(live, {"spLIVE": {"name": "work", "terminals": {"t1": link()}}})
        self.assertEqual(calls[0][:3], ["tab.new", "--space", "spLIVE"])
        self.assertEqual(list(self.read_state()["spLIVE"]["terminals"]), ["t7"])
        self.assertEqual(self.read_state()["spLIVE"]["terminals"]["t7"]["tab"], "tabN")
        self.assertTrue(any(c[:3] == ["/bin/hc", "send", "t7"] for c in sent))
        self.assertIn("restored", out)

    def test_deleted_space_is_brought_back_first(self):
        gone = {"id": "spGONE", "name": "work", "backed_up": "2026-01-01T00:00:00+00:00",
                "links": [{"agent": "claude", "tab_name": "claude", "session": SESSION}]}
        manifest = {"spaces": {"spGONE": {"name": "work", "backed_up": gone["backed_up"], "links": [
            {"agent": "claude", "session": SESSION, "cwd": "/tmp", "tab_name": "claude", "terminal": "t1"}]}}}
        os.makedirs(os.path.join(self.dir, "bk"))
        with open(os.path.join(self.dir, "bk", "manifest.json"), "w") as f:
            json.dump(manifest, f)
        live = []  # herdr has no space: it is closed, so the whole space is recreated
        with mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": os.path.join(self.dir, "bk")}), \
                mock.patch.object(workspace, "reopen_closed") as reopen:
            self.run_open(live, {})
        self.assertEqual(self.read_state()["spGONE"]["name"], "work")
        reopen.assert_called_once()

    def test_unlinked_session_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_open([], {"spLIVE": {"name": "work", "terminals": {"t1": link(session=SESSION2)}}})


class Doctor(TetherTestCase):
    def test_reports_problems_and_never_crashes_without_herdr(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t1": link()}}})
        with mock.patch.object(core, "find_tool", return_value=None), \
                mock.patch.object(store, "session_exists", return_value=False), \
                mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": os.path.join(self.dir, "none")}):
            results = doctor.doctor([])
        levels = {t: l for l, t, _ in results}
        self.assertEqual(levels["herdr-app not found"], "bad")
        self.assertEqual(levels["Herdr is not running"], "warn")
        self.assertTrue(any("no transcript anywhere" in t for _, t, _ in results))
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(doctor, "doctor", return_value=results):
                doctor.doctor_cmd([])
        self.assertEqual(cm.exception.code, 1)


class ForgettingABackup(TetherTestCase):
    def setUp(self):
        super().setUp()
        self.bk = os.path.join(self.dir, "bk")
        patcher = mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": self.bk})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.f1 = os.path.join(self.bk, "claude", "projects", "p", SESSION + ".jsonl")
        self.f2 = os.path.join(self.bk, "claude", "projects", "p", SESSION2 + ".jsonl")
        for f in (self.f1, self.f2):
            os.makedirs(os.path.dirname(f), exist_ok=True)
            with open(f, "w") as fh:
                fh.write("x" * 10)
        l = lambda sess: {"agent": "claude", "session": sess, "tab_name": "c", "terminal": "t1"}
        with open(os.path.join(self.bk, "manifest.json"), "w") as fh:
            json.dump({"spaces": {"spGONE": {"name": "old", "links": [l(SESSION)]},
                                  "spKEEP": {"name": "kept", "links": [l(SESSION2)]}}}, fh)

    def test_removes_only_the_deleted_spaces_copies(self):
        out = backups.forget_backup("spGONE")
        self.assertEqual((out["sessions"], out["bytes"]), (1, 10))
        self.assertFalse(os.path.exists(self.f1))
        self.assertTrue(os.path.exists(self.f2))
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            self.assertEqual(list(json.load(fh)["spaces"]), ["spKEEP"])

    def test_session_linked_under_a_new_space_id_counts_as_linked(self):
        self.write_state({"spNEW": {"name": "old", "terminals": {"t1": link()}}})
        with mock.patch.object(herdr, "herdr_ls", return_value=[]), \
                mock.patch.object(backups, "session_title", return_value="t"):
            rows = {r["session"]: r["linked"] for r in backups.backup_list()}
        self.assertTrue(rows[SESSION])
        self.assertFalse(rows[SESSION2])

    def test_one_session_can_be_removed_and_the_space_keeps_the_rest(self):
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            m = json.load(fh)
        m["spaces"]["spGONE"]["links"].append({"agent": "claude", "session": SESSION2, "tab_name": "d"})
        m["spaces"].pop("spKEEP")
        with open(os.path.join(self.bk, "manifest.json"), "w") as fh:
            json.dump(m, fh)
        out = backups.forget_backup("spGONE", only_session=SESSION)
        self.assertEqual(out["sessions"], 1)
        self.assertFalse(os.path.exists(self.f1))
        self.assertTrue(os.path.exists(self.f2), "the other session is untouched")
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            left = json.load(fh)["spaces"]["spGONE"]["links"]
        self.assertEqual([l["session"] for l in left], [SESSION2])
        backups.forget_backup("spGONE", only_session=SESSION2)
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            self.assertNotIn("spGONE", json.load(fh)["spaces"], "no sessions left: the space entry goes too")

    def test_any_session_backup_can_be_deleted_even_a_linked_one(self):
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": link(session=SESSION)}}})
        out = backups.delete_backup_of_session("claude", SESSION)
        self.assertEqual((out["sessions"], out["bytes"]), (1, 10))
        self.assertFalse(os.path.exists(self.f1))
        self.assertTrue(os.path.exists(self.f2), "other sessions untouched")
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            self.assertEqual(list(json.load(fh)["spaces"]), ["spKEEP"], "the emptied space entry is gone")
        self.assertEqual(self.read_state()["spLIVE"]["terminals"]["t1"]["session"], SESSION, "the link is untouched")
        with self.assertRaises(core.TetherError):
            backups.delete_backup_of_session("claude", SESSION)
        with self.assertRaises(core.TetherError):
            backups.delete_backup_of_session("claude", "../../etc")

    def test_a_linked_space_is_refused_and_a_shared_session_kept(self):
        self.write_state({"spGONE": {"name": "old", "terminals": {"t1": link()}}})
        with self.assertRaises(core.TetherError):
            backups.forget_backup("spGONE")
        self.write_state({})
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            m = json.load(fh)
        m["spaces"]["spKEEP"]["links"].append({"agent": "claude", "session": SESSION})
        with open(os.path.join(self.bk, "manifest.json"), "w") as fh:
            json.dump(m, fh)
        self.assertEqual(backups.forget_backup("spGONE")["sessions"], 0)
        self.assertTrue(os.path.exists(self.f1), "another space still uses this session")


class Forking(TetherTestCase):
    def test_claude_fork_starts_with_fork_session_and_the_new_id(self):
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": link(session=SESSION2, fork_of=SESSION)}}})
        where = herdr.Where("spLIVE", "work", "t1", "tab1", "claude")
        out = io.StringIO()
        have = lambda agent, sess: sess == SESSION  # only the original has a transcript
        with mock.patch.dict(os.environ, {"TETHER_DRY_RUN": "1"}), \
                mock.patch.object(herdr, "resolve_where", return_value=(where, None)), \
                mock.patch.object(core, "find_tool", return_value="/bin/claude"), \
                mock.patch.object(store, "session_exists", side_effect=have), \
                contextlib.redirect_stdout(out):
            launch.launch("claude", [])
        self.assertIn(f"claude --resume {SESSION} --fork-session --session-id {SESSION2}", out.getvalue())

    def test_copilot_copy_gets_a_new_id_everywhere_and_no_lock(self):
        home = os.path.join(self.dir, "cop")
        src = os.path.join(home, "session-state", SESSION)
        os.makedirs(src)
        for name, text in (("workspace.yaml", f"id: {SESSION}\n"), ("events.jsonl", f'{{"sessionId":"{SESSION}"}}\n'),
                           (f"inuse.1.lock", "")):
            with open(os.path.join(src, name), "w") as f:
                f.write(text)
        with mock.patch.object(store, "_copilot_home", return_value=home):
            workspace.copy_copilot_session(SESSION, SESSION2)
        dst = os.path.join(home, "session-state", SESSION2)
        self.assertEqual(slurp(os.path.join(dst, "workspace.yaml")), f"id: {SESSION2}\n")
        self.assertIn(SESSION2, slurp(os.path.join(dst, "events.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(dst, "inuse.1.lock")))
        self.assertIn(SESSION, slurp(os.path.join(src, "workspace.yaml")), "the original is untouched")

    def test_fork_space_makes_a_second_space_and_leaves_the_first(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {
            "t1": link(session=SESSION, tab_name="c"), "t2": link("copilot", SESSION2, tab_name="p")}}})
        calls = []

        def herdr_do(args):
            calls.append(args)
            return {"space": "spNEW", "terminal": f"n{len(calls)}"}

        live = [{"id": "spOLD", "name": "work", "tabs": [{"id": "tab1", "name": "c", "terminals": [{"terminal": "t1"}, {"terminal": "t2"}]}]},
                {"id": "spNEW", "name": "work (fork)", "tabs": [{"id": "tabN", "name": "c", "terminals": [{"terminal": "n1"}, {"terminal": "n2"}]}]}]
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(herdr, "ensure_app"), \
                mock.patch.object(core, "find_tool", return_value="/bin/hc"), \
                mock.patch.object(herdr, "herdr_do", side_effect=herdr_do), \
                mock.patch.object(herdr, "herdr_ls", side_effect=[live[:1], live[:1], live]), \
                mock.patch.object(store, "running_agents", return_value=[]), \
                mock.patch.object(store, "session_exists", return_value=True), \
                mock.patch.object(backups, "backup_cmd"), \
                mock.patch.object(workspace, "copy_copilot_session") as copy, \
                mock.patch.object(subprocess, "run", return_value=ok), \
                contextlib.redirect_stdout(io.StringIO()):
            workspace.fork_space(["--id", "spOLD", "-y"])
        state = self.read_state()
        self.assertEqual(len(state["spOLD"]["terminals"]), 2, "the original keeps its links")
        new = state["spNEW"]
        self.assertEqual(new["name"], "work (fork)")
        self.assertEqual(new["forked_from"]["name"], "work")
        agents = {e["agent"]: e for e in new["terminals"].values()}
        self.assertEqual(agents["claude"]["fork_of"], SESSION)
        self.assertNotEqual(agents["claude"]["session"], SESSION)
        self.assertNotIn("fork_of", agents["copilot"])
        copy.assert_called_once()
        self.assertEqual(copy.call_args[0][0], SESSION2)

    def test_adopt_links_unlinked_agents_and_never_changes_existing_links(self):
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": link(session=SESSION)}}})
        live = [{"id": "spLIVE", "name": "work", "tabs": [
            {"id": "tab1", "name": "one", "terminals": [{"terminal": "t1"}]},
            {"id": "tab2", "name": "two", "terminals": [{"terminal": "t2"}, ]},
            {"id": "tab3", "name": "three", "terminals": [{"terminal": "t3"}]}]}]
        running = [
            {"agent": "claude", "pid": 1, "terminal": "t1", "session": SESSION2, "cwd": "/tmp"},   # t1 already linked
            {"agent": "copilot", "pid": 2, "terminal": "t2", "session": SESSION2, "cwd": "/tmp"},  # new
            {"agent": "claude", "pid": 3, "terminal": "t3", "session": SESSION, "cwd": "/tmp"}]    # session linked already
        with mock.patch.object(herdr, "herdr_ls", return_value=live), \
                mock.patch.object(store, "running_agents", return_value=running):
            got = store.adopt()
        self.assertEqual([(g[1], g[2]) for g in got], [("two", "copilot")])
        terms = self.read_state()["spLIVE"]["terminals"]
        self.assertEqual(terms["t1"]["session"], SESSION, "an existing link is never changed")
        self.assertEqual(terms["t2"]["session"], SESSION2)
        self.assertNotIn("t3", terms)

    def test_a_rename_in_herdr_reaches_the_stored_space_and_tab_names(self):
        self.write_state({"spLIVE": {"name": "old", "terminals": {"t1": link(session=SESSION, tab="tab1", tab_name="1")}},
                          "spOTHER": {"name": "other", "terminals": {"t9": link(session=SESSION2, tab="tabX", tab_name="x")}}})
        live = [{"id": "spLIVE", "name": "renamed", "tabs": [{"id": "tab1", "name": "work", "terminals": [{"terminal": "t1"}]}]},
                {"id": "spOTHER", "name": "other renamed", "tabs": [{"id": "tabX", "name": "x2", "terminals": [{"terminal": "t9"}]}]}]
        with mock.patch.object(herdr, "herdr_ls", return_value=live), mock.patch.object(store, "running_agents", return_value=[]):
            store.adopt("spLIVE")
        data = self.read_state()
        self.assertEqual((data["spLIVE"]["name"], data["spLIVE"]["terminals"]["t1"]["tab_name"]), ("renamed", "work"))
        self.assertEqual(data["spOTHER"]["name"], "other", "adopt for one space leaves the others alone")
        with mock.patch.object(herdr, "herdr_ls", return_value=live), mock.patch.object(store, "running_agents", return_value=[]):
            store.adopt()
        self.assertEqual(self.read_state()["spOTHER"]["name"], "other renamed")

    def test_adopt_runs_on_space_switch_and_backup_but_never_raises(self):
        os.environ.pop("TETHER_NO_ADOPT")
        with mock.patch.object(store, "adopt", return_value=[]) as ad:
            launch.touch("spLIVE")
        ad.assert_called_once_with("spLIVE")
        with mock.patch.object(store, "adopt", side_effect=core.TetherError("herdr is not running")):
            self.assertEqual(store.safe_adopt(), [], "no Herdr: nothing happens, nothing raised")
        with mock.patch.object(store, "adopt") as ad:
            with contextlib.redirect_stdout(io.StringIO()):
                backups.backup_cmd(["--quiet"])
            ad.assert_called_once()

    def test_an_unused_fork_is_forked_from_the_session_it_was_forked_from(self):
        self.write_state({"spOLD": {"name": "work (fork)", "terminals": {
            "t1": link(session=SESSION2, tab_name="c", fork_of=SESSION)}}})
        live = [{"id": "spOLD", "name": "work (fork)", "tabs": [{"id": "tab1", "name": "c", "terminals": [{"terminal": "t1"}]}]},
                {"id": "spNEW", "name": "work (fork 2)", "tabs": [{"id": "tabN", "name": "c", "terminals": [{"terminal": "n1"}]}]}]
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        have = lambda agent, sess: sess == SESSION  # only the original session has a transcript
        with mock.patch.object(herdr, "ensure_app"), \
                mock.patch.object(core, "find_tool", return_value="/bin/hc"), \
                mock.patch.object(herdr, "herdr_do", return_value={"space": "spNEW", "terminal": "n1"}), \
                mock.patch.object(herdr, "herdr_ls", side_effect=[live[:1], live[:1], live]), \
                mock.patch.object(store, "running_agents", return_value=[]), \
                mock.patch.object(store, "session_exists", side_effect=have), \
                mock.patch.object(backups, "restore_session", return_value=0), \
                mock.patch.object(backups, "backup_cmd"), \
                mock.patch.object(subprocess, "run", return_value=ok), \
                contextlib.redirect_stdout(io.StringIO()):
            workspace.fork_space(["--id", "spOLD", "-y"])
        (entry,) = self.read_state()["spNEW"]["terminals"].values()
        self.assertEqual(entry["fork_of"], SESSION, "forks the original, not the unused fork")

    def test_backup_of_an_unused_fork_saves_the_session_it_was_forked_from(self):
        bk = os.path.join(self.dir, "bk2")
        src = os.path.join(self.dir, "claude", "projects", "p", SESSION + ".jsonl")
        os.makedirs(os.path.dirname(src))
        with open(src, "w") as f:
            f.write("hello")
        self.write_state({"spF": {"name": "work (fork)", "terminals": {
            "t1": link(session=SESSION2, fork_of=SESSION)}}})
        with mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": bk}), \
                mock.patch.object(store, "_claude_home", return_value=os.path.join(self.dir, "claude")), \
                mock.patch.object(backups, "live_home", return_value=os.path.join(self.dir, "claude")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            backups.backup_cmd(["--id", "spF"])
            copied = os.path.join(bk, "claude", "projects", "p", SESSION + ".jsonl")
            self.assertTrue(os.path.exists(copied), "the source session is in the backup")
            with open(os.path.join(bk, "manifest.json")) as f:
                (lk,) = json.load(f)["spaces"]["spF"]["links"]
            self.assertEqual(lk["fork_of"], SESSION)
            status = backups.backup_status({"spF": self.read_state()["spF"]})
        self.assertEqual((status["backed_up"], status["missing"]), (1, 0))

    def test_name_gets_a_number_when_taken(self):
        self.assertEqual(workspace.fork_name("work", {"work (fork)"}), "work (fork 2)")
        self.assertEqual(workspace.fork_name("work (fork 2)", {"work (fork)", "work (fork 2)"}), "work (fork 3)")


class Searching(TetherTestCase):
    def setUp(self):
        super().setUp()
        self.claude = os.path.join(self.dir, "claude")
        self.bk = os.path.join(self.dir, "bk")
        for patcher in (mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": self.bk}),
                        mock.patch.object(store, "_claude_home", return_value=self.claude),
                        mock.patch.object(backups, "live_home", return_value=self.claude)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def transcript(self, session, turns, home=None):
        path = os.path.join(home or self.claude, "projects", "p", session + ".jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for role, text in turns:
                f.write(json.dumps({"type": role, "message": {"content": text}}) + "\n")
            # things that must never be searched or sent anywhere
            f.write(json.dumps({"type": "user", "isMeta": True, "message": {"content": "META SECRETWORD"}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "TOOLOUTPUT SECRETWORD"}]}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"content": "<system-reminder>SECRETWORD</system-reminder>"}}) + "\n")
        return path

    def test_only_what_was_said_is_read(self):
        path = self.transcript(SESSION, [("user", "hello kafka"), ("assistant", "lag is the consumer")])
        self.assertEqual(search.readable_turns("claude", path), [("user", "hello kafka"), ("assistant", "lag is the consumer")])
        cop = os.path.join(self.dir, "events.jsonl")
        with open(cop, "w") as f:
            f.write(json.dumps({"type": "user.message", "data": {"content": "copilot question", "transformedContent": "<x>"}}) + "\n")
            f.write(json.dumps({"type": "tool.execution_start", "data": {"content": "NOPE"}}) + "\n")
            f.write(json.dumps({"type": "assistant.message", "data": {"content": ""}}) + "\n")
            f.write(json.dumps({"type": "assistant.message", "data": {"content": "copilot answer"}}) + "\n")
        self.assertEqual(search.readable_turns("copilot", cop), [("user", "copilot question"), ("assistant", "copilot answer")])

    def test_keyword_ranking_prefers_rare_terms_phrases_and_partial_words(self):
        a = self.transcript(SESSION, [("user", "why is the grafana dashboard empty"), ("assistant", "the datasource is wrong")])
        b = self.transcript(SESSION2, [("user", "the dashboard for billing"), ("assistant", "ok")])
        docs = [{"agent": "claude", "path": a, "src": SESSION, "session": SESSION, "space": "A", "state": "open", "tab": "", "last_used": ""},
                {"agent": "claude", "path": b, "src": SESSION2, "session": SESSION2, "space": "B", "state": "open", "tab": "", "last_used": ""}]
        terms = search.query_terms("which grafana dashboard was empty?")
        self.assertEqual(terms, ["grafana", "dashboard", "empty"])
        rows = search.lexical_rank(terms, docs)
        self.assertEqual([r["space"] for r in rows], ["A", "B"])
        self.assertEqual(rows[0]["score"], 100)
        self.assertNotIn("SECRETWORD", json.dumps([r["snippets"] for r in rows]))
        self.assertEqual([r["space"] for r in search.lexical_rank(["grafan"], docs)], ["A"], "partial words match")
        self.assertEqual(search.lexical_rank(["zzz"], docs), [])

    def test_a_possessive_or_a_short_word_does_not_flood_the_results(self):
        # "Acme's" used to give "acme" and "s"; "s" is inside nearly every word, so it decided the ranking.
        self.assertEqual(search.query_terms("Acme's US partner"), ["acme", "us", "partner"])
        self.assertEqual(search.query_terms("Acme’s US partner"), ["acme", "us", "partner"], "a typographic apostrophe too")
        self.assertEqual(search.query_terms("a"), ["a"], "a query of nothing but one letter still searches")
        a = self.transcript(SESSION, [("user", "the US partner for acme"), ("assistant", "ok")])
        b = self.transcript(SESSION2, [("user", "status of the business, it is used a lot"), ("assistant", "yes")])
        c = self.transcript("33333333-3333-4333-8333-333333333333", [("user", "partner partner partner partner"), ("assistant", "ok")])
        docs = [{"agent": "claude", "path": p, "src": s, "session": s, "space": n, "state": "open", "tab": "", "last_used": ""}
                for p, s, n in ((a, SESSION, "A"), (b, SESSION2, "B"), (c, "33333333-3333-4333-8333-333333333333", "C"))]
        terms = ["acme", "us", "partner"]
        rows = search.lexical_rank(terms, docs)
        self.assertEqual([r["space"] for r in rows], ["A", "C"], "'us' is not found inside 'status', 'business' or 'used'; "
                         "all the words rank above one word said often")
        self.assertIn("US partner", rows[0]["snippets"][0]["text"])
        self.assertEqual([r["space"] for r in search.lexical_rank(["us"], docs)], ["A"], "a short word matches as a whole word")
        self.assertEqual([r["space"] for r in search.lexical_rank(["stat"], docs)], ["B"], "a longer term still matches inside a word")

    def corpus(self, scopes, live_ids=("spOPEN",)):
        self.write_state({"spOPEN": {"name": "o", "terminals": {"t1": link(session=SESSION)}},
                          "spCLOSED": {"name": "c", "terminals": {"t2": link(session=SESSION2)}}})
        live = [{"id": i, "name": "o", "tabs": []} for i in live_ids]
        with mock.patch.object(herdr, "herdr_ls", return_value=live):
            return {(d["state"], d["session"]): d for d in search.search_corpus(scopes)}

    def test_scopes_pick_open_and_closed_spaces(self):
        self.transcript(SESSION, [("user", "x")])
        self.transcript(SESSION2, [("user", "y")])
        self.assertEqual(set(self.corpus(["open", "closed"])), {("open", SESSION), ("closed", SESSION2)})
        self.assertEqual(set(self.corpus(["open"])), {("open", SESSION)})
        self.assertEqual(set(self.corpus(["closed"])), {("closed", SESSION2)})

    def test_backup_is_searched_only_when_the_agent_lost_it_or_the_space_was_deleted(self):
        bkhome = os.path.join(self.bk, "claude")
        self.transcript(SESSION, [("user", "live copy")])
        self.transcript(SESSION, [("user", "backup copy of a live one")], home=bkhome)       # same session: not a second doc
        self.transcript(SESSION2, [("user", "only in the backup")], home=bkhome)           # swept from the agent: searched, from the backup
        docs = self.corpus(["open", "closed"])
        self.assertFalse(docs[("open", SESSION)]["via_backup"])
        self.assertTrue(docs[("closed", SESSION2)]["via_backup"])
        self.assertEqual(len(docs), 2, "a live session's backup is not searched again")
        # a deleted space: in the manifest, not linked
        gone = "99999999-aaaa-bbbb-cccc-000000000000"
        self.transcript(gone, [("user", "deleted space talk")], home=bkhome)
        with open(os.path.join(self.bk, "manifest.json"), "w") as f:
            json.dump({"spaces": {"spGONE": {"name": "gone", "backed_up": "2026-01-01T00:00:00+00:00",
                                              "links": [{"agent": "claude", "session": gone, "tab_name": "t"}]}}}, f)
        self.assertNotIn(("deleted", gone), self.corpus(["open", "closed"]))
        self.assertEqual(self.corpus(["deleted"])[("deleted", gone)]["space"], "gone")

    def test_ai_steps_rank_and_read_with_a_mocked_claude(self):
        a = self.transcript(SESSION, [("user", "grafana dashboard"), ("assistant", "done")])
        b = self.transcript(SESSION2, [("user", "grafana alerts"), ("assistant", "done")])
        docs = [{"agent": "claude", "path": a, "src": SESSION, "session": SESSION, "space": "A", "state": "open", "tab": "", "last_used": "2026-01-01"},
                {"agent": "claude", "path": b, "src": SESSION2, "session": SESSION2, "space": "B", "state": "closed", "tab": "", "last_used": "2026-01-02"}]
        rows = search.lexical_rank(["grafana"], docs)
        prompts = []

        def fake(prompt, model):
            prompts.append(prompt)
            if "ranks" in prompt or "rank past" in prompt:
                return 'Sure: [{"id": 2, "score": 90, "why": "alerts"}, {"id": 1, "score": 10, "why": "no"}]', 0.01
            return '{"score": 77, "why": "it matches", "quote": "grafana alerts"}', 0.02
        with mock.patch.object(ai, "ask_claude", side_effect=fake):
            ranked, cost = search.rank_with_ai("alerts question", rows, "sonnet")
            self.assertEqual([r["space"] for r in ranked], ["B", "A"])
            self.assertEqual((ranked[0]["score"], ranked[0]["why"]), (90, "alerts"))
            deep, dcost = search.deep_read("alerts question", ranked[:1], "sonnet")
        self.assertEqual((deep[0]["score"], deep[0]["quote"]), (77, "grafana alerts"))
        self.assertEqual(prompts[0].count("[1]") + prompts[0].count("[2]"), 2)
        self.assertNotIn("SECRETWORD", "".join(prompts))
        with mock.patch.object(ai, "ask_claude", return_value=("not json at all", None)):
            with self.assertRaises(core.TetherError):
                search.rank_with_ai("q", rows, "sonnet")

    def run_search(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            search.search_cmd(list(args))
        return out.getvalue()

    def test_command_estimates_falls_back_to_recent_and_validates(self):
        self.transcript(SESSION, [("user", "grafana dashboard"), ("assistant", "done")])
        self.write_state({"spOPEN": {"name": "o", "terminals": {"t1": link(session=SESSION)}}})
        live = [{"id": "spOPEN", "name": "o", "tabs": []}]
        with mock.patch.object(herdr, "herdr_ls", return_value=live):
            out = json.loads(self.run_search("grafana", "--json"))
            self.assertEqual((out["tier"], out["corpus"], len(out["results"])), (1, 1, 1))
            self.assertGreater(out["estimate"]["rank_tokens"], 0)
            self.assertNotIn("path", out["results"][0])
            nothing = json.loads(self.run_search("zzzqqq", "--json"))
            self.assertEqual(nothing["results"], [])
            self.assertTrue(nothing["estimate"]["no_keyword_match"])
            with mock.patch.object(ai, "ask_claude", return_value=('[{"id": 1, "score": 55, "why": "recent"}]', 0.01)):
                ranked = json.loads(self.run_search("zzzqqq", "--rank", "--json"))
            self.assertTrue(ranked["fallback"])
            self.assertEqual(ranked["results"][0]["score"], 55)
            for bad in (["x", "--scope", "nope"], []):
                with self.assertRaises(SystemExit):
                    self.run_search(*bad)


class ContinueInOtherAgent(TetherTestCase):
    """`tether continue`: a conversation carried between Claude Code and Copilot CLI by writing a new session in the other agent's store."""

    def setUp(self):
        super().setUp()
        self.claude, self.copilot, self.work = (os.path.join(self.dir, n) for n in ("claude", "copilot", "work"))
        os.makedirs(self.work)
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.claude, "COPILOT_HOME": self.copilot})
        patcher.start()
        self.addCleanup(patcher.stop)

    def claude_transcript(self, session=SESSION):
        folder = os.path.join(self.claude, "projects", "-work")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, session + ".jsonl")
        lines = [
            {"type": "queue-operation", "operation": "enqueue"},
            {"type": "user", "message": {"role": "user", "content": "run it"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "starting"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo hi"}}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "hi"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "it printed hi"}]}},
            {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": [{"type": "text", "text": "sub-agent chatter"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta"}},
            {"type": "user", "message": {"role": "user", "content": "thanks"}},
        ]
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(x) for x in lines) + "\n")
        return path

    EXPECTED = [("user", "run it"), ("assistant", "starting\n\n[used Bash: echo hi]\n\n[result: hi]\n\nit printed hi"), ("user", "thanks")]

    def test_claude_turns_fold_tool_calls_into_the_assistant_turn_and_skip_noise(self):
        self.assertEqual(continue_.continue_turns("claude", self.claude_transcript()), self.EXPECTED)

    def test_copilot_turns_do_the_same(self):
        folder = os.path.join(self.copilot, "session-state", SESSION)
        os.makedirs(folder)
        events = [
            {"type": "session.start", "data": {}},
            {"type": "user.message", "data": {"content": "run it"}},
            {"type": "assistant.message", "data": {"content": "starting", "toolRequests": []}},
            {"type": "tool.execution_start", "data": {"toolName": "Bash", "arguments": {"command": "echo hi"}}},
            {"type": "tool.execution_complete", "data": {"result": {"content": "hi"}}},
            {"type": "assistant.message", "data": {"content": "it printed hi"}},
            {"type": "user.message", "data": {"content": "thanks"}},
        ]
        path = os.path.join(folder, "events.jsonl")
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(x) for x in events) + "\n")
        self.assertEqual(continue_.continue_turns("copilot", path), self.EXPECTED)

    def test_a_conversation_survives_a_round_trip_through_either_store(self):
        turns = [("user", "one"), ("assistant", "two"), ("user", "three"), ("assistant", "four")]
        sid, folder = continue_.write_copilot_session(turns, self.work, "name")
        self.assertEqual(continue_.continue_turns("copilot", os.path.join(folder, "events.jsonl")), turns)
        sid, path = continue_.write_claude_session(turns, self.work)
        self.assertEqual(continue_.continue_turns("claude", path), turns)
        with open(path) as f:
            lines = [json.loads(x) for x in f]
        self.assertEqual([x["parentUuid"] for x in lines[1:]], [x["uuid"] for x in lines[:-1]], "each line points at the one before")
        self.assertTrue(search.transcript_file("claude", sid) and store.session_exists("claude", sid))

    def test_a_long_conversation_keeps_the_newest_turns_and_says_what_was_left_out(self):
        turns = [(("user", "assistant")[i % 2], f"message {i} " + "x" * 40_000) for i in range(10)]
        kept, dropped = continue_.continue_trim(turns, "claude", "copilot")
        self.assertGreater(dropped, 0)
        self.assertEqual(kept[0][0], "user")
        self.assertIn("left out", kept[0][1])
        self.assertEqual(kept[1], ("assistant", "Understood."))
        self.assertTrue(kept[-1][1].startswith("message 9 "), "the newest turn is kept")
        self.assertEqual(continue_.continue_trim(turns[:2], "claude", "copilot"), (turns[:2], 0))

    def linked(self, *terminals):
        self.write_state({"spLIVE": {"name": "work", "terminals": {t: link(session=SESSION, cwd=self.work, pid=4242, pid_started="x", tab_name="one")
                                                                    for t in (terminals or ("t1",))}}})

    def cont(self, *args, tab_terminals=None, hc=True):
        """Run `tether continue` with Herdr faked: a new tab t9 appears when asked for; returns what was typed into it."""
        live = [{"id": "spLIVE", "name": "work", "tabs": [
            {"id": "tab1", "name": "one", "terminals": [{"terminal": t} for t in (tab_terminals or ["t1"])]},
            {"id": "tab9", "name": "one · copilot", "terminals": [{"terminal": "t9"}]}]}]
        where = herdr.Where("spLIVE", "work", "t1", "tab1", "one")
        typed, opened = [], []
        with mock.patch.object(herdr, "resolve_where", return_value=(where, None)), mock.patch.object(herdr, "ensure_app"), \
                mock.patch.object(herdr, "herdr_ls", return_value=live), \
                mock.patch.object(herdr, "herdr_do", side_effect=lambda a: opened.append(a) or {"terminal": "t9"}), \
                mock.patch.object(core, "find_tool", side_effect=lambda n: "/bin/true" if n != "hc" or hc else None), \
                mock.patch.object(herdr, "tether_cmd", return_value="tether"), \
                mock.patch.object(subprocess, "run", side_effect=lambda cmd, **k: typed.append(cmd) or mock.Mock(returncode=0)), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            continue_.continue_cmd(list(args))
        return opened, typed

    def test_continue_opens_a_new_tab_with_the_new_agent_and_leaves_the_original_alone(self):
        old = self.claude_transcript()
        with open(old) as f:
            before = f.read()
        self.linked()
        before_state = self.read_state()["spLIVE"]["terminals"]["t1"]
        opened, typed = self.cont("copilot")
        self.assertEqual(opened[0][:3], ["tab.new", "--space", "spLIVE"])
        self.assertIn("one · copilot", opened[0])
        terms = self.read_state()["spLIVE"]["terminals"]
        self.assertEqual(terms["t1"], before_state, "the original terminal keeps its agent, session and pid")
        new = terms["t9"]
        self.assertEqual((new["agent"], new["tab"], new["tab_name"]), ("copilot", "tab9", "one · copilot"))
        self.assertNotEqual(new["session"], SESSION)
        self.assertEqual(new["continued_from"]["session"], SESSION)
        self.assertEqual(continue_.continue_turns("copilot", search.transcript_file("copilot", new["session"])), self.EXPECTED)
        self.assertEqual(typed, [["/bin/true", "send", "t9", "tether resume"]])
        with open(old) as f:
            self.assertEqual(f.read(), before, "the old session is not touched")

    def test_continue_without_an_agent_goes_to_the_other_one(self):
        self.claude_transcript()
        self.linked()
        self.cont()
        self.assertEqual(self.read_state()["spLIVE"]["terminals"]["t9"]["agent"], "copilot")

    def test_continue_works_while_the_original_agent_keeps_running(self):
        self.claude_transcript()
        self.linked()
        with mock.patch.object(store, "_running", return_value=True):
            self.cont("copilot")
        self.assertEqual(self.read_state()["spLIVE"]["terminals"]["t1"]["agent"], "claude")

    def test_continue_says_how_to_continue_when_there_is_no_hc(self):
        self.claude_transcript()
        self.linked()
        _, typed = self.cont("copilot", hc=False)
        self.assertEqual(typed, [])
        self.assertEqual(self.read_state()["spLIVE"]["terminals"]["t9"]["agent"], "copilot")

    def test_continue_needs_a_conversation_and_a_different_agent(self):
        self.linked()
        with self.assertRaises(SystemExit) as ctx:
            self.cont("copilot")
        self.assertIn("no conversation yet", str(ctx.exception))
        self.claude_transcript()
        with self.assertRaises(SystemExit) as ctx:
            self.cont("claude")
        self.assertIn("already uses claude", str(ctx.exception))

    def test_swap_is_still_accepted_as_the_old_name_and_list_json_names_each_links_tab(self):
        with mock.patch.object(continue_, "continue_cmd") as cmd:
            cli.main(["swap", "copilot"])
            cli.main(["continue"])
        self.assertEqual([c.args[0] for c in cmd.call_args_list], [["copilot"], []])
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": link(session=SESSION, tab="tab7")}}})
        out = io.StringIO()
        with mock.patch.object(herdr, "herdr_ls", return_value=[{"id": "spLIVE", "name": "work"}]), contextlib.redirect_stdout(out):
            workspace.list_json()
        self.assertEqual(json.loads(out.getvalue())[0]["links"][0]["tab"], "tab7")

    def test_continue_from_the_tab_menu_picks_the_tabs_one_linked_terminal(self):
        self.claude_transcript()
        self.linked("t1")
        live = [{"id": "spLIVE", "name": "work", "tabs": [{"id": "tab1", "name": "one", "terminals": [{"terminal": "t1"}, {"terminal": "t2"}]}]}]
        with mock.patch.object(herdr, "ensure_app"), mock.patch.object(herdr, "herdr_ls", return_value=live):
            where = herdr.terminal_in_tab("tab1")
        self.assertEqual((where.space_id, where.terminal, where.tab, where.tab_name), ("spLIVE", "t1", "tab1", "one"))
        self.linked("t1", "t2")
        with mock.patch.object(herdr, "ensure_app"), mock.patch.object(herdr, "herdr_ls", return_value=live), self.assertRaises(SystemExit) as ctx:
            herdr.terminal_in_tab("tab1")
        self.assertIn("several linked terminals", str(ctx.exception))
        self.linked("t7")
        with mock.patch.object(herdr, "ensure_app"), mock.patch.object(herdr, "herdr_ls", return_value=live), self.assertRaises(SystemExit) as ctx:
            herdr.terminal_in_tab("tab1")
        self.assertIn("no agent is linked", str(ctx.exception))
        with mock.patch.object(herdr, "ensure_app"), mock.patch.object(herdr, "herdr_ls", return_value=live), self.assertRaises(SystemExit) as ctx:
            herdr.terminal_in_tab("nope")
        self.assertIn("not found", str(ctx.exception))


class ContextStore(TetherTestCase):
    """`tether context`: a small tree of files (index, topics, one-line facts) every agent can look up, checked in the background."""

    def setUp(self):
        super().setUp()
        self.ctx = os.path.join(self.dir, "context")
        patcher = mock.patch.dict(os.environ, {"TETHER_CONTEXT_DIR": self.ctx})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cmd(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            context.context_cmd(list(args))
        return out.getvalue()

    def add(self, topic="repos", fact="the orders consumer is in ~/orders", about="where repositories live"):
        return self.run_cmd("add", topic, fact, "--about", about).strip()

    def test_add_creates_the_topic_and_its_index_line_and_refuses_duplicates(self):
        self.assertEqual(self.add(), "added")
        self.assertEqual(self.add(fact="The orders consumer is in ~/orders."), "already there")
        index, _ = context.context_index(self.ctx)
        self.assertEqual(index["repos"], ("repos.md", "where repositories live"))
        facts = context.context_facts(self.ctx, "repos")
        self.assertEqual([f[1] for f in facts], ["the orders consumer is in ~/orders"])
        self.assertRegex(facts[0][2], r"^\d{4}-\d\d-\d\d$")

    def test_a_new_topic_needs_a_description_and_secrets_are_refused(self):
        with self.assertRaises(core.TetherError) as ctx:
            self.run_cmd("add", "stack", "services use Kafka")
        self.assertIn("--about", str(ctx.exception))
        for bad in ("the api key: abc123", "password = hunter2", "deploy with " + "A1b2" * 10):
            with self.assertRaises(core.TetherError, msg=bad):
                self.add(fact=bad)
        with self.assertRaises(core.TetherError):
            self.add(topic="Bad Topic")
        self.assertEqual(context.context_topics(self.ctx), [])

    def test_find_matches_all_words_in_facts_and_topic_descriptions_without_a_model(self):
        self.add()
        self.add("stack", "services talk over Kafka, see [[repos]]", "stack and how services connect")
        self.assertIn("repos: the orders consumer", self.run_cmd("find", "orders", "consumer"))
        self.assertNotIn("stack:", self.run_cmd("find", "orders"))
        self.assertIn("stack: services talk over Kafka", self.run_cmd("find", "services", "connect"), "a topic description counts toward its facts")
        self.assertIn("nothing found", self.run_cmd("find", "zzz"))
        self.assertEqual(json.loads(self.run_cmd("find", "kafka", "--json"))[0]["topic"], "stack")

    def test_find_forgives_the_words_an_agent_guesses(self):
        self.add("work-tools", "Acme tracks work in Linear", "where tickets and projects live")
        for words in (("tracking", "work"), ("work", "tools"), ("which", "tool", "we", "use", "to", "track", "our", "work"), ("issue", "tracker")):
            self.assertIn("work-tools: Acme tracks work in Linear", self.run_cmd("find", *words), words)
        out = self.run_cmd("find", "kubernetes", "cluster")
        self.assertIn("nothing found", out)
        self.assertIn("work-tools: where tickets and projects live", out, "with no hit, the topics are listed so the agent can pick one")

    def test_a_new_topic_that_resembles_an_existing_one_is_flagged_and_can_be_filed_there(self):
        self.add("deployments", "acme-web deploys with make ship", "how and where things are deployed")
        self.assertEqual(context.similar_topic("deploy", "Read when shipping", {"deployments": "how and where things are deployed"}), "deployments")
        self.assertEqual(context.similar_topic("release-deploys", "when to ship to staging or production", {"deployments": "how and where things are deployed"}), "deployments")
        self.assertIsNone(context.similar_topic("databases", "how to reach data stores", {"deployments": "how and where things are deployed"}))
        context.context_queue_save(self.ctx, [{"topic": "deploys", "fact": "staging deploys use make stage", "about": "how to deploy", "source": "s"}])
        out = json.loads(self.run_cmd("proposals", "--json"))
        self.assertEqual((out[0]["new_topic"], out[0]["similar"]), (True, "deployments"))
        self.assertIn("similar to deployments", self.run_cmd("proposals"))
        self.run_cmd("accept", "--only", "1", "--into", "deployments")
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "deployments")], ["acme-web deploys with make ship", "staging deploys use make stage"])
        self.assertNotIn("deploys", context.context_topics(self.ctx), "no new topic was created")
        self.assertEqual(context.context_queue(self.ctx), [])

    def test_into_needs_one_fact_and_an_existing_topic(self):
        self.add()
        context.context_queue_save(self.ctx, [{"topic": "x", "fact": "a", "about": "b", "source": "s"}, {"topic": "y", "fact": "c", "about": "d", "source": "s"}])
        for args in (["accept", "--into", "repos"], ["accept", "--only", "1", "--into", "nope"], ["reject", "--only", "1", "--into", "repos"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.run_cmd(*args)
        self.assertEqual(len(context.context_queue(self.ctx)), 2)

    def test_the_prompt_lists_waiting_topics_and_a_fact_for_one_of_them_reuses_its_description(self):
        self.add()
        context.context_queue_save(self.ctx, [{"topic": "tools", "fact": "we use ripgrep", "about": "tools in use", "source": "s"}])
        with mock.patch.object(ai, "ask_ai", return_value=('[{"topic": "tools", "fact": "we use fd for file search"}]', None)) as ask:
            ops, _ = context.context_propose("USER: hi", "Demo", "haiku")
        prompt = ask.call_args.args[0]
        self.assertIn("- tools (proposed, waiting for review): tools in use", prompt)
        self.assertEqual([(o["topic"], o["about"]) for o in ops], [("tools", "tools in use")])

    def kinds(self):
        return sorted({i["kind"] for i in context.context_check_issues(self.ctx)})

    def test_check_reports_what_is_wrong_and_nothing_when_all_is_well(self):
        self.add()
        self.assertEqual(self.kinds(), [])
        with open(os.path.join(self.ctx, "orphan.md"), "w") as f:
            f.write("# orphan\n\n- a fact nobody can find\n")
        with open(os.path.join(self.ctx, "repos.md"), "a") as f:
            f.write("- see [[missing]]\n- the orders consumer is in ~/orders\n- old thing  _(2020-01-01, from: x)_\n")
        with open(os.path.join(self.ctx, "index.md"), "a") as f:
            f.write("- [gone](gone.md) — a topic whose file was deleted\n")
        self.assertEqual(self.kinds(), ["duplicate", "index-dead", "link-dead", "orphan", "stale"])

    def test_check_flags_long_files(self):
        self.add()
        with open(os.path.join(self.ctx, "repos.md"), "a") as f:
            f.writelines(f"- fact number {n}\n" for n in range(context.CONTEXT_TOPIC_MAX + 1))
        with open(os.path.join(self.ctx, "index.md"), "a") as f:
            f.writelines(f"- [t{n}](t{n}.md) — x\n" for n in range(context.CONTEXT_INDEX_MAX))
        self.assertIn("topic-long", self.kinds())
        self.assertIn("index-long", self.kinds())

    def test_fix_makes_only_the_safe_repairs_after_taking_a_backup(self):
        self.add()
        with open(os.path.join(self.ctx, "orphan.md"), "w") as f:
            f.write("# orphan\n\n- keep me\n")
        with open(os.path.join(self.ctx, "repos.md"), "a") as f:
            f.write("- the orders consumer is in ~/orders\n- [[nowhere]] link stays\n")
        with open(os.path.join(self.ctx, "index.md"), "a") as f:
            f.write("- [gone](gone.md) — deleted\n")
        out = self.run_cmd("check", "--fix")
        self.assertIn("fixed:", out)
        index, _ = context.context_index(self.ctx)
        self.assertIn("orphan", index)
        self.assertNotIn("gone", index)
        self.assertEqual(len(context.context_facts(self.ctx, "repos")), 2, "the duplicate is gone, the other facts stay")
        self.assertEqual(self.kinds(), ["link-dead"], "a dead link is reported, never edited away")
        backups = os.listdir(os.path.join(self.ctx, ".backup"))
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.ctx, ".backup", backups[0], "repos.md")) as f:
            self.assertEqual(f.read().count("the orders consumer"), 2, "the backup is the store as it was")

    def test_the_background_check_runs_at_most_once_a_day_and_only_with_a_store(self):
        with mock.patch.object(subprocess, "Popen") as popen:
            context.context_check_if_due()
            popen.assert_not_called()
            self.add()
            context.context_check_if_due()
            context.context_check_if_due()
            self.assertEqual(popen.call_count, 1, "the second launch within a day does nothing")
            self.assertEqual(popen.call_args.args[0][-3:], ["check", "--background", "--fix"])
            old = os.path.join(self.ctx, ".last-check")
            os.utime(old, (0, 0))
            context.context_check_if_due()
            self.assertEqual(popen.call_count, 2)

    def test_a_background_check_leaves_a_report_and_a_hint_is_shown_once(self):
        self.add()
        with open(os.path.join(self.ctx, "repos.md"), "a") as f:
            f.write("- see [[missing]]\n")
        context.context_check(["--background"])
        with open(os.path.join(self.ctx, ".report.json")) as f:
            self.assertEqual(json.load(f)["issues"][0]["kind"], "link-dead")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            context.context_hint()
            context.context_hint()
        self.assertEqual(err.getvalue().count("thing(s) to look at"), 1)

    def test_learn_proposes_facts_and_accept_saves_only_the_chosen_ones(self):
        self.add()
        proposal = json.dumps([
            {"topic": "repos", "fact": "the billing service is in ~/billing", "source": "chat"},
            {"topic": "stack", "fact": "builds use Gradle", "about": "build tools"},
            {"topic": "stack", "fact": "the token: abc123 is x"},
            {"topic": "newtopic", "fact": "has no description"},
            {"topic": "repos", "fact": "the orders consumer is in ~/orders"}])
        docs = [{"space_id": "s1", "space": "Work", "state": "open", "agent": "claude", "session": "x", "path": "/none"}]
        with mock.patch.object(reading, "find_conversations", return_value=docs), \
                mock.patch.object(reading, "conversation_text", return_value=("USER: hi", 1, 0)), \
                mock.patch.object(ai, "ask_claude", return_value=(proposal, 0.04)) as ask:
            out = self.run_cmd("learn", "Work")
        self.assertIn("2 fact(s) proposed", out)
        self.assertIn("nothing is saved yet", out)
        self.assertIn("- repos (1 fact(s)): where repositories live", ask.call_args.args[0])
        self.assertEqual(len(context.context_facts(self.ctx, "repos")), 1, "nothing saved by learn")
        self.run_cmd("accept", "--only", "2")
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "stack")], ["builds use Gradle"])
        self.assertEqual(len(context.context_facts(self.ctx, "repos")), 1)
        self.assertIn("discarded 1", self.run_cmd("reject"))
        self.assertFalse(os.path.exists(os.path.join(self.ctx, ".proposals.json")))


class ContextLearning(TetherTestCase):
    """The proposal queue and the opt-in background learning: it only ever proposes, it starts from now, and it is capped."""

    def setUp(self):
        super().setUp()
        self.ctx = os.path.join(self.dir, "context")
        patcher = mock.patch.dict(os.environ, {"TETHER_CONTEXT_DIR": self.ctx})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("TETHER_CONTEXT_AUTO", None)
        self.transcripts = []
        for n in range(4):
            path = os.path.join(self.dir, f"t{n}.jsonl")
            with open(path, "w") as f:
                f.write("x")
            os.utime(path, (1, 1))  # untouched for a long time: settled
            self.transcripts.append({"space_id": f"s{n}", "space": f"Space {n}", "state": "open", "agent": "claude", "session": f"sess{n}",
                                     "path": path, "last_used": f"2026-11-2{n}T00:00:00+00:00"})
        self.lines = {t["path"]: [f"USER: message {i}" for i in range(2)] for t in self.transcripts}

    def harvest(self, reply='[{"topic": "repos", "fact": "the billing service is in ~/billing", "about": "where repos live"}]', auto=True):
        calls = []

        def fake_ask(prompt, model, budget=2):
            calls.append((prompt, model))
            return reply, 0.02

        with mock.patch.dict(os.environ, {"TETHER_CONTEXT_AUTO": "1" if auto else "0"}), \
                mock.patch.object(search, "search_corpus", return_value=self.transcripts), \
                mock.patch.object(reading, "conversation_text", side_effect=lambda agent, path, cap=0, as_lines=False: self.lines[path]), \
                mock.patch.object(ai, "ask_claude", side_effect=fake_ask), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            context.context_harvest([])
        return calls

    def grow(self, n=8, which=None):
        for t in which or self.transcripts:
            self.lines[t["path"]] = self.lines[t["path"]] + [f"USER: more {i}" for i in range(n)]

    def queue(self):
        return context.context_queue(self.ctx)

    def test_it_is_off_by_default(self):
        self.assertEqual(self.harvest(auto=False), [])
        self.assertFalse(os.path.exists(os.path.join(self.ctx, ".harvested.json")))
        with mock.patch.object(subprocess, "Popen") as popen:
            context.context_harvest_if_due()
        popen.assert_not_called()

    def test_turning_it_on_starts_from_now_without_reading_history(self):
        self.assertEqual(self.harvest(), [], "no model call on the first run")
        with open(os.path.join(self.ctx, ".harvested.json")) as f:
            self.assertEqual(sorted(json.load(f)), ["sess0", "sess1", "sess2", "sess3"])
        self.assertEqual(self.queue(), [])

    def test_conversations_that_grew_are_read_from_the_new_part_and_only_proposed(self):
        self.harvest()
        self.grow()
        calls = self.harvest()
        self.assertEqual(len(calls), context.CONTEXT_HARVEST_MAX, "at most three conversations per run")
        self.assertEqual(calls[0][1], "haiku")
        self.assertIn("more 0", calls[0][0])
        self.assertNotIn("message 0", calls[0][0], "only what is new since the last read is sent")
        self.assertEqual(len(self.queue()), 1, "the same fact proposed again is not queued twice")
        self.assertEqual(context.context_topics(self.ctx), [], "nothing is saved by background learning")
        self.assertEqual(len(self.harvest()), 1, "the fourth conversation is read on the next run")
        self.assertEqual(self.harvest(), [], "and then nothing is left to read")

    def test_a_conversation_is_not_read_again_until_it_has_grown_enough_or_while_it_is_active(self):
        self.harvest()
        self.grow(n=context.CONTEXT_HARVEST_MIN_NEW - 1, which=self.transcripts[:1])
        self.assertEqual(self.harvest(), [])
        self.grow(n=2, which=self.transcripts[:1])
        os.utime(self.transcripts[0]["path"], None)  # written just now: still in use
        self.assertEqual(self.harvest(), [])
        os.utime(self.transcripts[0]["path"], (1, 1))
        self.assertEqual(len(self.harvest()), 1)

    def test_the_daily_spend_cap_stops_it(self):
        self.harvest()
        os.makedirs(self.ctx, exist_ok=True)
        with open(os.path.join(self.ctx, ".harvest-spend.json"), "w") as f:
            json.dump({"date": datetime.now().date().isoformat(), "usd": context.CONTEXT_HARVEST_CAP_USD}, f)
        self.grow()
        self.assertEqual(self.harvest(), [])

    def test_a_failed_model_call_stops_the_run_without_marking_conversations_as_read(self):
        self.harvest()
        self.grow()
        with mock.patch.dict(os.environ, {"TETHER_CONTEXT_AUTO": "1"}), mock.patch.object(search, "search_corpus", return_value=self.transcripts), \
                mock.patch.object(reading, "conversation_text", side_effect=lambda a, p, cap=0, as_lines=False: self.lines[p]), \
                mock.patch.object(ai, "ask_claude", side_effect=core.TetherError("no claude")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            context.context_harvest([])
        self.assertEqual(len(self.harvest()), context.CONTEXT_HARVEST_MAX, "still unread, so the next run tries again")

    def test_auto_accept_saves_only_plain_facts_about_existing_topics_and_can_be_undone(self):
        context.add_fact(self.ctx, "repos", "the api is in ~/api", about="where repos live")
        reply = json.dumps([
            {"topic": "repos", "fact": "the billing service is in ~/billing"},
            {"topic": "repos", "fact": "deploy with `make ship`"},
            {"topic": "repos", "fact": "docs live at https://example.com/docs"},
            {"topic": "tools", "fact": "we use ripgrep", "about": "tools in use"},
        ])
        self.harvest()
        self.grow()
        with mock.patch.dict(os.environ, {"TETHER_CONTEXT_AUTO_ACCEPT": "1"}):
            self.harvest(reply=reply)
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "repos")], ["the api is in ~/api", "the billing service is in ~/billing"])
        self.assertEqual(sorted(op["fact"] for op in self.queue()), ["deploy with `make ship`", "docs live at https://example.com/docs", "we use ripgrep"],
                         "commands, links and new topics still wait for review")
        self.assertIn("auto: ", context.context_facts(self.ctx, "repos")[1][3])
        log = context.context_auto_added(self.ctx)
        self.assertEqual([e["fact"] for e in log], ["the billing service is in ~/billing"])
        with contextlib.redirect_stdout(io.StringIO()):
            context.context_undo(["--only", "1"])
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "repos")], ["the api is in ~/api"])
        self.assertEqual(context.context_auto_added(self.ctx), [])

    def test_list_json_and_remove_give_the_window_what_it_needs(self):
        context.add_fact(self.ctx, "repos", "the api is in ~/api", source="Space 1", about="where repos live")
        context.add_fact(self.ctx, "repos", "the web app is in ~/web", source="auto: Space 2")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            context.context_list(["--json"])
        topics = json.loads(out.getvalue())
        self.assertEqual([(t["topic"], t["about"]) for t in topics], [("repos", "where repos live")])
        self.assertEqual([(f["fact"], f["auto"]) for f in topics[0]["facts"]], [("the api is in ~/api", False), ("the web app is in ~/web", True)])
        with contextlib.redirect_stdout(io.StringIO()):
            context.context_remove(["repos", "--line", str(topics[0]["facts"][1]["line"])])
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "repos")], ["the api is in ~/api"])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            context.context_remove(["repos", "--line", "1"])  # the title line is not a fact

    def test_without_auto_accept_everything_is_queued(self):
        context.add_fact(self.ctx, "repos", "the api is in ~/api", about="where repos live")
        self.harvest()
        self.grow()
        self.harvest(reply='[{"topic": "repos", "fact": "the billing service is in ~/billing"}]')
        self.assertEqual(len(context.context_facts(self.ctx, "repos")), 1)
        self.assertEqual(len(self.queue()), 1)

    def test_the_background_job_is_started_at_most_every_six_hours_when_on(self):
        with mock.patch.dict(os.environ, {"TETHER_CONTEXT_AUTO": "1"}), mock.patch.object(subprocess, "Popen") as popen:
            context.context_harvest_if_due()
            context.context_harvest_if_due()
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(popen.call_args.args[0][-2:], ["harvest", "--background"])
            os.utime(os.path.join(self.ctx, ".last-harvest"), (0, 0))
            context.context_harvest_if_due()
            self.assertEqual(popen.call_count, 2)

    def test_proposals_are_listed_for_the_ui_and_decided_by_number(self):
        os.makedirs(self.ctx)
        context.add_fact(self.ctx, "repos", "the orders consumer is in ~/orders", about="where repos live")
        context.context_queue_save(self.ctx, [
            {"topic": "repos", "fact": "the billing service is in ~/billing", "about": "", "source": "chat"},
            {"topic": "tools", "fact": "builds use Gradle", "about": "build tools", "source": "chat"}])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            context.context_proposals(["--json"])
        rows = json.loads(out.getvalue())
        self.assertEqual([(r["n"], r["topic"], r["new_topic"]) for r in rows], [(1, "repos", False), (2, "tools", True)])
        with contextlib.redirect_stdout(io.StringIO()):
            context.context_decide(["--only", "2"], True)
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "tools")], ["builds use Gradle"])
        self.assertEqual([op["fact"] for op in self.queue()], ["the billing service is in ~/billing"])
        with contextlib.redirect_stdout(io.StringIO()):
            context.context_decide([], False)
        self.assertEqual(self.queue(), [])

    def test_the_hint_mentions_waiting_proposals(self):
        os.makedirs(self.ctx)
        context.context_queue_save(self.ctx, [{"topic": "repos", "fact": "x is in ~/x", "about": "a", "source": ""}])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            context.context_hint()
        self.assertIn("1 proposed fact(s) to review", err.getvalue())


class Settings(TetherTestCase):
    """`tether config`: every default in config.json as a validated setting, with where its value comes from."""

    def setUp(self):
        super().setUp()
        for k in [k for k in os.environ if k.startswith("TETHER_") and k not in ("TETHER_STATE_DIR", "TETHER_NO_ADOPT")]:
            patcher = mock.patch.dict(os.environ)
            patcher.start()
            self.addCleanup(patcher.stop)
            os.environ.pop(k)
        patcher = mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": "", "TETHER_CONTEXT_DIR": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("TETHER_BACKUP_DIR", "TETHER_CONTEXT_DIR"):
            os.environ.pop(k)

    def cfg(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            settings.config_cmd(list(args))
        return out.getvalue()

    def rows(self):
        return {r["key"]: r for r in json.loads(self.cfg("--json"))}

    def test_every_setting_is_listed_with_its_default_and_where_the_value_comes_from(self):
        rows = self.rows()
        self.assertEqual([r["key"] for r in settings.SETTINGS], list(rows))
        self.assertEqual((rows["auto"]["value"], rows["auto"]["source"]), (False, "default"))
        self.assertEqual((rows["default_agent"]["value"], rows["context_model"]["value"], rows["search_model"]["value"]), ("claude", "haiku", "sonnet"))
        self.assertEqual(rows["context_agent"]["value"], "claude")
        self.assertFalse(rows["home_dir"]["editable"])
        self.assertTrue(rows["context_auto"]["privacy"])

    def test_set_validates_saves_and_resets(self):
        self.cfg("set", "auto", "on")
        self.cfg("set", "default_agent", "copilot")
        self.cfg("set", "context_model", "sonnet")
        rows = self.rows()
        self.assertEqual([(rows[k]["value"], rows[k]["source"]) for k in ("auto", "default_agent", "context_model")],
                         [(True, "config"), ("copilot", "config"), ("sonnet", "config")])
        self.assertEqual(settings.default_agent(), "copilot")
        self.assertEqual(self.cfg("get", "default_agent").strip(), "copilot")
        self.cfg("set", "default_agent", "default")
        self.assertEqual(self.rows()["default_agent"]["source"], "default")
        for bad in (("auto", "maybe"), ("default_agent", "pi"), ("nothing", "x"), ("home_dir", "/no/such/folder"), ("home_dir", os.path.expanduser("~"))):
            with self.assertRaises((core.TetherError, SystemExit), msg=str(bad)):
                self.cfg("set", *bad)

    def test_an_environment_variable_wins_and_is_reported_as_the_source(self):
        self.cfg("set", "default_agent", "copilot")
        with mock.patch.dict(os.environ, {"TETHER_DEFAULT_AGENT": "claude", "TETHER_AUTO_BACKUP": "1"}):
            rows = self.rows()
            self.assertEqual((rows["default_agent"]["value"], rows["default_agent"]["source"], rows["default_agent"]["env"]),
                             ("claude", "env", "TETHER_DEFAULT_AGENT"))
            self.assertEqual((rows["auto"]["value"], rows["auto"]["source"]), (True, "env"))
            self.assertIn("overrides", self.cfg("set", "default_agent", "copilot"))

    def test_the_older_home_dir_syntax_still_works_and_a_bad_default_agent_falls_back_to_claude(self):
        folder = os.path.join(self.dir, "start")
        os.makedirs(folder)
        self.cfg("home_dir", folder)
        self.assertEqual(self.rows()["home_dir"]["value"], folder)
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump({"default_agent": "bogus"}, f)
        self.assertEqual(settings.default_agent(), "claude")


class ReadAnotherConversation(TetherTestCase):
    """`tether read`: what another space's conversation said, user and assistant text only."""

    def write(self, lines):
        path = os.path.join(self.dir, "c.jsonl")
        with open(path, "w") as f:
            for e in lines:
                f.write(json.dumps(e) + "\n")
        return path

    def run_read(self, path, *args):
        docs = [{"space_id": "s1", "space": "Grafana", "state": "open", "agent": "claude", "session": "abcdef123456", "path": path}]
        out = io.StringIO()
        with mock.patch.object(search, "search_corpus", return_value=docs), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            reading.read_cmd(["grafana", *args])
        return out.getvalue()

    def test_prints_the_last_messages_and_never_tool_output(self):
        ev = []
        for i in range(5):
            ev.append({"type": "user", "message": {"content": f"question {i}"}})
            ev.append({"type": "assistant", "message": {"content": [
                {"type": "text", "text": f"answer {i}"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}}]}})
            ev.append({"type": "user", "message": {"content": [{"type": "tool_result", "content": "SECRET OUTPUT"}]}})
        out = self.run_read(self.write(ev), "--last", "2")
        self.assertIn("answer 4", out)
        self.assertNotIn("question 0", out)
        self.assertNotIn("SECRET OUTPUT", out)
        self.assertNotIn("echo hi", out)
        self.assertIn("last 2 of 10", out)
        self.assertIn("echo hi", self.run_read(self.write(ev), "--commands"))

    def test_long_messages_are_cut_unless_full(self):
        path = self.write([{"type": "user", "message": {"content": "x" * 5000}}])
        self.assertIn("[cut]", self.run_read(path))
        self.assertNotIn("[cut]", self.run_read(path, "--full"))


if __name__ == "__main__":
    unittest.main()
