"""Tests for bin/tether. Run: python3 -m unittest discover -s tests -v

Nothing here touches the real herdr app, ~/.claude, or ~/.local/state: every test uses a temporary
state directory and mocks the herdr/hc calls and the agent session lookups.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("tether", os.path.join(HERE, "..", "bin", "tether"))
_spec = importlib.util.spec_from_loader("tether", _loader)
tether = importlib.util.module_from_spec(_spec)
_loader.exec_module(tether)

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


class SmallHelpers(TetherTestCase):
    def test_ago(self):
        self.assertEqual(tether.ago("not a date"), "unknown")
        self.assertEqual(tether.ago(tether.now()), "just now")

    def test_env_reads_tether_names(self):
        with mock.patch.dict(os.environ, {"TETHER_NO_CD": "1"}):
            self.assertEqual(tether.env("NO_CD"), "1")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(tether.env("NO_CD"))

    def test_agent_command_is_the_agent_not_a_shell_mentioning_it(self):
        self.assertTrue(tether._is_agent_command("claude --resume abc --name x", "claude"))
        self.assertTrue(tether._is_agent_command("/Users/me/.local/bin/claude", "claude"))
        self.assertTrue(tether._is_agent_command("node /opt/x/copilot.js --resume y", "copilot"))
        self.assertFalse(tether._is_agent_command("/bin/zsh -c tether link claude abc", "claude"))
        self.assertFalse(tether._is_agent_command("", "claude"))

    def test_session_paths_only_for_real_uuids(self):
        self.assertEqual(tether.session_paths("claude", "../../etc"), [])
        self.assertEqual(tether.session_paths("claude", "*"), [])
        self.assertEqual(tether.session_paths("copilot", ""), [])

    def test_match_space_exact_beats_substring(self):
        rows = [("a", "work", "", False), ("b", "work extra", "", False)]
        self.assertEqual(tether.match_space("work", rows), "a")
        self.assertEqual(tether.match_space("extra", rows), "b")
        self.assertIsNone(tether.match_space("nothing", rows))


class State(TetherTestCase):
    def test_corrupt_state_is_kept_aside_and_replaced(self):
        with open(os.path.join(self.dir, "spaces.json"), "w") as f:
            f.write("{not json")
        with contextlib.redirect_stderr(io.StringIO()), tether.State() as st:
            self.assertEqual(st.data, {})
        self.assertTrue(any(n.startswith("spaces.json.corrupt-") for n in os.listdir(self.dir)))

    def test_state_dir_default_and_override(self):
        with mock.patch.dict(os.environ, {"TETHER_STATE_DIR": "/somewhere/else"}):
            self.assertEqual(tether.state_dir(), "/somewhere/else")
        env = {k: v for k, v in os.environ.items() if k != "TETHER_STATE_DIR"}
        env["HOME"] = "/home/someone"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(tether.state_dir(), "/home/someone/.local/state/tether")

class FindingTerminals(TetherTestCase):
    def test_exact_then_machine_prefixed(self):
        spaces = [space("t1", "box/t5")]
        self.assertEqual(tether.find_terminal(spaces, "t1")[2]["terminal"], "t1")
        self.assertEqual(tether.find_terminal(spaces, "t5")[2]["terminal"], "box/t5")
        self.assertIsNone(tether.find_terminal(spaces, "t9"))

    def test_same_address_in_two_spaces_is_refused(self):
        a, b = space("m1/t1"), dict(space("m2/t1"), id="spOTHER")
        with self.assertRaises(tether.TetherError):
            tether.find_terminal([a, b], "t1")

    def test_plan_space_says_why_a_terminal_is_skipped(self):
        sp = {"id": "spLIVE", "name": "work", "tabs": [
            {"id": "tab1", "name": "a", "terminals": [{"terminal": "t1"}]},
            {"id": "tab2", "name": "b", "terminals": [{"terminal": "t2", "app": "claude"}]},
            {"id": "tab9", "name": "c", "terminals": [{"terminal": "t3"}]},
        ]}
        data = {"terminals": {"t1": link(tab="tab1"), "t2": link(SESSION2, tab="tab2"),
                              "t3": link(tab="tab3"), "t4": link(tab="tab4")}}
        _, _, _, plan, skipped = tether.plan_space(data, sp)
        self.assertEqual([p[0] for p in plan], ["t1"])
        reasons = {addr: why for addr, _, _, why in skipped}
        self.assertIn("already running", reasons["t2"])
        self.assertIn("different tab", reasons["t3"])
        self.assertIn("no longer exists", reasons["t4"])


class Launch(TetherTestCase):
    def run_dry(self, agent, args=(), where=None, exists=True):
        where = where or tether.Where("spLIVE", "work", "t1", "tab1", "claude")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"TETHER_DRY_RUN": "1"}), \
                mock.patch.object(tether, "resolve_where", return_value=(where, None)), \
                mock.patch.object(tether, "find_tool", return_value="/bin/" + agent), \
                mock.patch.object(tether, "session_exists", return_value=exists), \
                contextlib.redirect_stdout(out):
            tether.launch(agent, list(args))
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
                mock.patch.object(tether, "find_tool", return_value="/bin/claude"), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as out:
            tether.launch("claude", ["--resume", "abc"])
        self.assertIn("would run: claude --resume abc", out.getvalue())

    def test_running_session_is_not_started_twice(self):
        entry = link(pid=4242, pid_started="Mon 1 Jan")
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": entry}}})
        with mock.patch.object(tether, "_running", return_value=True), \
                mock.patch.object(tether, "resolve_where",
                                  return_value=(tether.Where("spLIVE", "work", "t1", "tab1", "claude"), None)), \
                mock.patch.object(tether, "find_tool", return_value="/bin/claude"), \
                mock.patch.object(tether, "session_exists", return_value=True):
            with self.assertRaises(SystemExit) as cm:
                tether.launch("claude", [])
        self.assertIn("already running", str(cm.exception))


class Linking(TetherTestCase):
    where = tether.Where("spLIVE", "work", "t1", "tab1", "claude")

    def do_link(self, detected, session_arg=None):
        patches = [mock.patch.object(tether, "resolve_where", return_value=(self.where, None)),
                   mock.patch.object(tether, "session_exists", return_value=True),
                   mock.patch.object(tether, "session_cwd", return_value="/tmp"),
                   mock.patch.object(tether, "current_session", return_value=SESSION if detected else None),
                   mock.patch.object(tether, "guess_session", return_value=SESSION),
                   mock.patch.object(tether, "agent_process", return_value=(4242, "Mon 1 Jan"))]
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()):
            for p in patches:
                stack.enter_context(p)
            tether.link("claude", session_arg)
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
                raise tether.TetherError("boom")
            return {"space": "spNEW", "terminal": f"t{len(calls)}"}

        live = [{"id": "spNEW", "name": "work", "tabs": [
            {"id": "tabN", "name": "x", "terminals": [{"terminal": "t1"}, {"terminal": "t2"}]}]}]
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(tether, "find_tool", return_value="/bin/hc"), \
                mock.patch.object(tether, "herdr_do", side_effect=herdr_do), \
                mock.patch.object(tether, "herdr_ls", return_value=live), \
                mock.patch.object(tether, "session_exists", return_value=True), \
                mock.patch.object(tether.subprocess, "run", return_value=ok), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            tether.reopen_closed("spOLD", self.read_state()["spOLD"], yes=True)
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
        with mock.patch.object(tether, "ensure_app"), mock.patch.object(tether, "herdr_ls", return_value=[]), \
                mock.patch.object(tether, "open_space") as opened:
            tether.open_by_name_or_fail("previous")
        opened.assert_called_once_with(None, False, sid="spOLD")

    def test_unknown_name_fails_clearly(self):
        with mock.patch.object(tether, "ensure_app"), mock.patch.object(tether, "herdr_ls", return_value=[]):
            with self.assertRaises(SystemExit) as cm:
                tether.open_by_name_or_fail("nothing")
        self.assertIn("unknown command or space", str(cm.exception))


class Deleting(TetherTestCase):
    def test_delete_with_sessions_keeps_a_running_session(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t9": link(pid=1, pid_started="x")}}})
        removed = []
        with mock.patch.object(tether, "herdr_ls", return_value=[]), \
                mock.patch.object(tether, "_running", return_value=True), \
                mock.patch.object(tether, "session_paths", return_value=["/fake/transcript"]), \
                mock.patch.object(tether, "_remove", side_effect=removed.append), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                tether.delete_space(["--id", "spOLD", "--sessions", "-y"])
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(removed, [], "a running session's transcript must not be deleted")

    def test_delete_unlink_only_keeps_transcripts(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t9": link()}}})
        removed = []
        with mock.patch.object(tether, "herdr_ls", return_value=[]), \
                mock.patch.object(tether, "session_paths", return_value=["/fake/transcript"]), \
                mock.patch.object(tether, "_remove", side_effect=removed.append), \
                contextlib.redirect_stdout(io.StringIO()):
            tether.delete_space(["--id", "spOLD", "-y"])
        self.assertEqual(removed, [])
        self.assertNotIn("spOLD", self.read_state())


class DeletingForGood(TetherTestCase):
    def test_close_waits_for_the_space_and_its_agents_then_unlinks(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t9": link(session=SESSION)}}})
        live = [{"id": "spOLD", "name": "work", "tabs": []}]
        closed = []
        running = [{"agent": "claude", "session": SESSION}]
        with mock.patch.object(tether, "herdr_ls", side_effect=[live, live, []]), \
                mock.patch.object(tether, "running_agents", side_effect=[running, []]), \
                mock.patch.object(tether, "herdr_do", side_effect=lambda a: closed.append(a) or {}), \
                mock.patch.object(tether, "backup_cmd"), \
                mock.patch.object(tether.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            tether.delete_space(["--id", "spOLD", "-y", "--close"])
        self.assertEqual(closed, [["space.close", "--id", "spOLD", "--confirmed", "true"]])
        self.assertNotIn("spOLD", self.read_state())

    def test_permanent_delete_removes_the_backup_copy_too(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t9": link(session=SESSION)}}})
        gone = []
        with mock.patch.object(tether, "herdr_ls", return_value=[]), \
                mock.patch.object(tether, "session_paths", return_value=["/fake/transcript"]), \
                mock.patch.object(tether, "_remove"), \
                mock.patch.object(tether, "delete_backup_of_session", side_effect=lambda a, ss: gone.append((a, ss))), \
                contextlib.redirect_stdout(io.StringIO()):
            tether.delete_space(["--id", "spOLD", "--sessions", "-y"])
        self.assertEqual(gone, [("claude", SESSION)])

    def test_purge_session_deletes_both_copies_and_refuses_a_running_one(self):
        claude, bk = os.path.join(self.dir, "claude"), os.path.join(self.dir, "bk")
        paths = [os.path.join(h, "projects", "p", SESSION + ".jsonl") for h in (claude, os.path.join(bk, "claude"))]
        for path in paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("x" * 5)
        with mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": bk}), \
                mock.patch.object(tether, "_claude_home", return_value=claude):
            with mock.patch.object(tether, "running_agents", return_value=[{"agent": "claude", "session": SESSION}]):
                with self.assertRaises(tether.TetherError):
                    tether.purge_session("claude", SESSION)
            self.assertTrue(all(os.path.exists(p) for p in paths), "a running session is not touched")
            with mock.patch.object(tether, "running_agents", return_value=[]):
                out = tether.purge_session("claude", SESSION)
            self.assertEqual(out["bytes"], 10)
            self.assertFalse(any(os.path.exists(p) for p in paths))
            with mock.patch.object(tether, "running_agents", return_value=[]):
                with self.assertRaises(tether.TetherError):
                    tether.purge_session("claude", SESSION)


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
            p = mock.patch.object(tether, name, side_effect=tether.TetherError("no herdr in tests"))
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
        out = self.say(tether.backup_cmd, [])
        self.assertIn("backed up 2 session(s)", out)
        for rel in ("claude/projects/-tmp-x/" + SESSION + ".jsonl", "claude/file-history/" + SESSION + "/snap",
                    "copilot/session-state/" + SESSION2 + "/events.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(self.backup, rel)), rel)
        self.assertEqual(os.stat(self.backup).st_mode & 0o777, 0o700)
        manifest = os.path.join(self.backup, "manifest.json")
        self.assertEqual(os.stat(manifest).st_mode & 0o777, 0o600)
        self.assertEqual(len(json.loads(slurp(manifest))["spaces"]["spLIVE"]["links"]), 2)
        self.assertIn("0 file(s) new or changed", self.say(tether.backup_cmd, []), "second run copies nothing")

    def test_backup_picks_up_a_changed_transcript(self):
        self.say(tether.backup_cmd, [])
        with open(self.transcript, "a") as f:
            f.write('{"more": true}\n')
        self.assertIn("1 file(s) new or changed", self.say(tether.backup_cmd, []))
        copy = os.path.join(self.backup, "claude", "projects", "-tmp-x", SESSION + ".jsonl")
        self.assertIn("more", slurp(copy))

    def test_backup_never_deletes_what_the_agent_swept(self):
        self.say(tether.backup_cmd, [])
        os.remove(self.transcript)
        self.say(tether.backup_cmd, [])
        self.assertTrue(os.path.exists(os.path.join(self.backup, "claude", "projects", "-tmp-x", SESSION + ".jsonl")))

    def test_backup_inside_an_unignored_git_repo_is_refused(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, True)
        subprocess = tether.subprocess
        subprocess.run(["git", "init", "-q", repo], check=True)
        target = os.path.join(repo, "backups")
        with mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": target}):
            with self.assertRaises(tether.TetherError) as cm:
                self.say(tether.backup_cmd, [])
            self.assertIn("not git-ignored", str(cm.exception))
            with open(os.path.join(repo, ".gitignore"), "w") as f:
                f.write("backups/\n")
            self.assertIn("backed up 2 session(s)", self.say(tether.backup_cmd, []))

    def test_restore_puts_back_a_swept_transcript_and_never_overwrites(self):
        self.say(tether.backup_cmd, [])
        os.remove(self.transcript)
        with open(self.extra, "w") as f:
            f.write("newer, written after the backup")
        self.assertIn("restored", self.say(tether.restore_cmd, []))
        self.assertTrue(os.path.exists(self.transcript))
        self.assertEqual(slurp(self.extra), "newer, written after the backup")

    def test_backup_list_names_sessions_for_people(self):
        with open(self.transcript, "w") as f:
            f.write('{"type": "system"}\n{"type": "user", "message": {"content": "<command-name>x</command-name>"}}\n'
                    '{"type": "user", "message": {"content": "Plan the   ledger\\nmigration"}, "cwd": "/tmp"}\n')
        with open(self.copilot_file, "w") as f:
            f.write('{"type": "session.start"}\n{"type": "user.message", "data": {"content": "/tether list"}}\n')
        self.say(tether.backup_cmd, [])
        rows = {r["agent"]: r for r in tether.backup_list()}
        self.assertEqual(rows["claude"]["title"], "Plan the ledger migration")  # wrappers skipped, spaces collapsed
        self.assertEqual(rows["copilot"]["title"], "/tether list")
        self.assertEqual((rows["claude"]["space"], rows["copilot"]["tab"]), ("work", "copilot"))
        self.assertTrue(rows["claude"]["in_agent"] and rows["claude"]["in_backup"] and rows["claude"]["linked"])
        self.assertGreater(rows["claude"]["bytes"], 0)

    def test_backup_list_marks_swept_sessions_and_lists_each_once(self):
        self.say(tether.backup_cmd, [])
        os.remove(self.transcript)
        manifest = os.path.join(self.backup, "manifest.json")
        data = json.loads(slurp(manifest))
        data["spaces"]["spOLD"] = {"name": "older entry", "backed_up": "2020-01-01T00:00:00+00:00",
                                   "links": [dict(data["spaces"]["spLIVE"]["links"][0], tab_name="old")]}
        with open(manifest, "w") as f:
            json.dump(data, f)
        rows = [r for r in tether.backup_list() if r["agent"] == "claude"]
        self.assertEqual(len(rows), 1, "a session that moved between spaces is listed once")
        self.assertEqual(rows[0]["space_id"], "spLIVE")
        self.assertFalse(rows[0]["in_agent"])
        self.assertTrue(rows[0]["in_backup"], "gone from the agent but restorable")

    def test_backup_list_skips_sessions_that_exist_nowhere(self):
        self.say(tether.backup_cmd, [])
        shutil.rmtree(os.path.join(self.backup, "copilot"))
        shutil.rmtree(os.path.join(self.copilot, "session-state"))
        self.assertEqual([r["agent"] for r in tether.backup_list()], ["claude"])

    def test_restore_one_session_by_id(self):
        self.say(tether.backup_cmd, [])
        os.remove(self.transcript)
        out = self.say(tether.restore_cmd, ["--session", SESSION])
        self.assertIn("claude --resume " + SESSION, out)
        self.assertTrue(os.path.exists(self.transcript))
        self.assertIn("already has", self.say(tether.restore_cmd, ["--session", SESSION]))
        with self.assertRaises(SystemExit):
            self.say(tether.restore_cmd, ["--session", "00000000-0000-4000-8000-000000000009"])
        with self.assertRaises(SystemExit):
            self.say(tether.restore_cmd, ["--session", "../../etc/passwd"])

    def test_launch_restores_a_gone_transcript_from_the_backup(self):
        self.say(tether.backup_cmd, [])
        os.remove(self.transcript)
        where = tether.Where("spLIVE", "work", "t1", "tab1", "claude")
        with mock.patch.dict(os.environ, {"TETHER_DRY_RUN": "1"}), \
                mock.patch.object(tether, "resolve_where", return_value=(where, None)), \
                mock.patch.object(tether, "find_tool", return_value="/bin/claude"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as out:
                tether.launch("claude", [])
        self.assertIn("(resume)", out.getvalue())
        self.assertIn("a backup has it", err.getvalue())
        self.assertFalse(os.path.exists(self.transcript), "a dry run restores nothing")

    def test_background_backup_runs_at_most_once_per_window(self):
        with mock.patch.dict(os.environ, {"TETHER_AUTO_BACKUP": "1"}), \
                mock.patch.object(tether.subprocess, "Popen") as popen:
            tether.auto_backup_if_due()
            tether.auto_backup_if_due()
        self.assertEqual(popen.call_count, 1)
        self.assertIn("backup", popen.call_args[0][0])

    def test_background_backup_is_off_unless_enabled(self):
        with mock.patch.object(tether.subprocess, "Popen") as popen:
            tether.auto_backup_if_due()
        popen.assert_not_called()

    def export(self, name="work"):
        out = os.path.join(tempfile.mkdtemp(), "bundle.tar.gz")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        self.say(tether.export_space, [name, "-o", out])
        return out

    def test_export_then_import_moves_a_space_to_another_machine(self):
        bundle = self.export()
        self.assertEqual(os.stat(bundle).st_mode & 0o777, 0o600)
        shutil.rmtree(os.path.join(self.claude, "projects"))
        shutil.rmtree(os.path.join(self.claude, "file-history"))
        shutil.rmtree(os.path.join(self.copilot, "session-state"))
        self.write_state({})
        out = self.say(tether.import_space, [bundle])
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
        out = self.say(tether.import_space, [bundle])
        self.assertIn("'work (imported)'", out)
        self.assertEqual(slurp(self.transcript), "this machine's own copy")

    def test_export_falls_back_to_the_backup_when_the_agent_swept_it(self):
        self.say(tether.backup_cmd, [])
        os.remove(self.transcript)
        shutil.rmtree(os.path.join(self.claude, "file-history"))
        bundle = self.export()
        os.remove(os.path.join(self.backup, "claude", "projects", "-tmp-x", SESSION + ".jsonl"))
        with tether.tarfile.open(bundle) as tar:
            names = tar.getnames()
        self.assertIn(f"files/claude/projects/-tmp-x/{SESSION}.jsonl", names)

    def test_options_are_parsed_strictly(self):
        with self.assertRaises(SystemExit) as cm:
            tether.parse_opts(["--nope"], flags=("--json",))
        self.assertIn("unknown option", str(cm.exception))
        with self.assertRaises(SystemExit):
            tether.parse_opts(["--id"], valued=("--id",))
        flags, vals, words = tether.parse_opts(["a", "--json", "--id", "x", "b"], flags=("--json",), valued=("--id",))
        self.assertEqual((flags, vals, words), ({"--json"}, {"--id": "x"}, ["a", "b"]))

    def test_backup_by_id_touches_only_that_space(self):
        state = self.read_state()
        state["spOTHER"] = {"name": "other", "terminals": {"t5": link("claude", SESSION2, tab_name="x")}}
        self.write_state(state)
        out = json.loads(self.say(tether.backup_cmd, ["--id", "spLIVE", "--json"]))
        self.assertEqual(out["sessions"], 2)
        manifest = json.loads(slurp(os.path.join(self.backup, "manifest.json")))
        self.assertEqual(list(manifest["spaces"]), ["spLIVE"])
        with self.assertRaises(SystemExit):
            self.say(tether.backup_cmd, ["--id", "spNOPE"])

    def test_status_reports_what_is_backed_up_missing_and_recoverable(self):
        status = json.loads(self.say(tether.backup_cmd, ["--status", "--json"]))
        self.assertEqual((status["linked"], status["backed_up"], status["missing"]), (2, 0, 0))
        self.assertIsNone(status["last_backup"])
        self.say(tether.backup_cmd, [])
        os.remove(self.transcript)
        status = json.loads(self.say(tether.backup_cmd, ["--status", "--json"]))
        self.assertEqual((status["backed_up"], status["missing"], status["recoverable"]), (2, 1, 1))
        self.assertTrue(status["last_backup"])
        self.assertGreater(status["bytes"], 0)
        self.assertIn("recoverable", self.say(tether.backup_cmd, ["--status"]))

    def test_list_json_says_which_links_a_backup_can_restore(self):
        self.say(tether.backup_cmd, [])
        os.remove(self.transcript)
        with mock.patch.object(tether, "herdr_ls", return_value=[]):
            rows = json.loads(self.say(tether.list_json))
        by_agent = {lk["agent"]: lk for lk in rows[0]["links"]}
        self.assertEqual((by_agent["claude"]["transcript"], by_agent["claude"]["recoverable"]), (False, True))
        self.assertEqual((by_agent["copilot"]["transcript"], by_agent["copilot"]["recoverable"]), (True, True))
        self.assertTrue(tether.available("claude", SESSION))

    def test_export_to_a_folder_names_the_file_and_never_overwrites(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        first = json.loads(self.say(tether.export_space, ["--id", "spLIVE", "-o", folder, "--json"]))
        second = json.loads(self.say(tether.export_space, ["--id", "spLIVE", "-o", folder, "--json"]))
        self.assertTrue(first["path"].startswith(os.path.abspath(folder)))
        self.assertRegex(os.path.basename(first["path"]), r"^tether-export-work-\d{8}\.tar\.gz$")
        self.assertRegex(os.path.basename(second["path"]), r"^tether-export-work-\d{8}-2\.tar\.gz$")
        self.assertEqual((first["sessions"], first["missing"]), (2, 0))
        self.assertEqual(os.stat(first["path"]).st_mode & 0o777, 0o600)

    def test_import_json_reports_the_new_closed_space(self):
        bundle = self.export()
        result = json.loads(self.say(tether.import_space, [bundle, "--name", "moved", "--json"]))
        self.assertEqual((result["name"], result["sessions"]), ("moved", 2))
        self.assertIn(result["id"], self.read_state())

    def bundle_with(self, *extra, session=SESSION):
        """A bundle whose manifest lists `session`, plus the given extra tar members."""
        path = os.path.join(tempfile.mkdtemp(), "evil.tar.gz")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        manifest = {"version": 1, "space": {"name": "evil", "links": [
            {"agent": "claude", "session": session, "terminal": "t1"}]}}
        with tether.tarfile.open(path, "w:gz") as tar:
            blob = json.dumps(manifest).encode()
            info = tether.tarfile.TarInfo("manifest.json")
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
            for member in extra:
                tar.addfile(*member)
        return path

    def member(self, name, data=b"x", kind=None):
        info = tether.tarfile.TarInfo(name)
        if kind == "symlink":
            info.type, info.linkname = tether.tarfile.SYMTYPE, "/etc/passwd"
            return (info,)
        info.size = len(data)
        return info, io.BytesIO(data)

    def assert_refused(self, bundle):
        before = self.read_state()
        with self.assertRaises(SystemExit) as cm:
            self.say(tether.import_space, [bundle])
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
        with tether.tarfile.open(path, "w:gz"):
            pass
        with self.assertRaises(SystemExit) as cm:
            self.say(tether.import_space, [path])
        self.assertIn("not a tether export", str(cm.exception))


class HomeDir(TetherTestCase):
    def dry(self, cwd, **env):
        where = tether.Where("spLIVE", "work", "t1", "tab1", "claude")
        out = io.StringIO()
        with mock.patch.dict(os.environ, dict({"TETHER_DRY_RUN": "1"}, **env)), \
                mock.patch.object(tether, "resolve_where", return_value=(where, None)), \
                mock.patch.object(tether, "find_tool", return_value="/bin/claude"), \
                mock.patch.object(tether.os, "getcwd", return_value=cwd), \
                contextlib.redirect_stdout(out):
            tether.launch("claude", [])
        return out.getvalue()

    def test_config_round_trip_and_refuses_home(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        with contextlib.redirect_stdout(io.StringIO()):
            tether.config_cmd(["home_dir", other])
        self.assertEqual(tether.home_dir_setting(), other)
        with self.assertRaises(SystemExit):
            tether.config_cmd(["home_dir", "~"])
        with contextlib.redirect_stdout(io.StringIO()):
            tether.config_cmd(["home_dir", "--unset"])
        self.assertIsNone(tether.home_dir_setting())
        with contextlib.redirect_stdout(io.StringIO()):
            tether.config_cmd(["home_dir", "--default"])
        fake = os.path.join(self.dir, "fakehome")
        with mock.patch.object(tether, "DEFAULT_HOME_DIR", fake):
            self.assertEqual(tether.home_dir_setting(), fake)
            self.assertTrue(os.path.isdir(fake), "created on first use")
        self.assertEqual(oct(os.stat(tether.config_path()).st_mode & 0o777), "0o600")

    def test_new_session_started_in_home_goes_to_home_dir(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        out = self.dry(os.path.expanduser("~"), TETHER_HOME_DIR=other)
        self.assertIn(f"would run: cd {other} &&", out)

    def test_other_folders_and_unset_setting_are_left_alone(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        self.assertIn("would run: cd /tmp &&", self.dry("/tmp", TETHER_HOME_DIR=other))
        with open(tether.config_path(), "w") as f:
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
        with mock.patch.object(tether, "ensure_app"), \
                mock.patch.object(tether, "find_tool", return_value="/bin/hc"), \
                mock.patch.object(tether, "herdr_do", side_effect=herdr_do), \
                mock.patch.object(tether, "herdr_ls", return_value=live), \
                mock.patch.object(tether, "session_exists", return_value=False), \
                mock.patch.object(tether, "restore_session", return_value=restored), \
                mock.patch.object(tether.subprocess, "run", side_effect=lambda cmd, **k: sent.append(cmd) or ok), \
                contextlib.redirect_stdout(out):
            tether.open_session([SESSION])
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
                mock.patch.object(tether, "reopen_closed") as reopen:
            self.run_open(live, {})
        self.assertEqual(self.read_state()["spGONE"]["name"], "work")
        reopen.assert_called_once()

    def test_unlinked_session_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_open([], {"spLIVE": {"name": "work", "terminals": {"t1": link(session=SESSION2)}}})


class Doctor(TetherTestCase):
    def test_reports_problems_and_never_crashes_without_herdr(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t1": link()}}})
        with mock.patch.object(tether, "find_tool", return_value=None), \
                mock.patch.object(tether, "session_exists", return_value=False), \
                mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": os.path.join(self.dir, "none")}):
            results = tether.doctor([])
        levels = {t: l for l, t, _ in results}
        self.assertEqual(levels["herdr-app not found"], "bad")
        self.assertEqual(levels["Herdr is not running"], "warn")
        self.assertTrue(any("no transcript anywhere" in t for _, t, _ in results))
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(tether, "doctor", return_value=results):
                tether.doctor_cmd([])
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
        out = tether.forget_backup("spGONE")
        self.assertEqual((out["sessions"], out["bytes"]), (1, 10))
        self.assertFalse(os.path.exists(self.f1))
        self.assertTrue(os.path.exists(self.f2))
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            self.assertEqual(list(json.load(fh)["spaces"]), ["spKEEP"])

    def test_session_linked_under_a_new_space_id_counts_as_linked(self):
        self.write_state({"spNEW": {"name": "old", "terminals": {"t1": link()}}})
        with mock.patch.object(tether, "herdr_ls", return_value=[]), \
                mock.patch.object(tether, "session_title", return_value="t"):
            rows = {r["session"]: r["linked"] for r in tether.backup_list()}
        self.assertTrue(rows[SESSION])
        self.assertFalse(rows[SESSION2])

    def test_one_session_can_be_removed_and_the_space_keeps_the_rest(self):
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            m = json.load(fh)
        m["spaces"]["spGONE"]["links"].append({"agent": "claude", "session": SESSION2, "tab_name": "d"})
        m["spaces"].pop("spKEEP")
        with open(os.path.join(self.bk, "manifest.json"), "w") as fh:
            json.dump(m, fh)
        out = tether.forget_backup("spGONE", only_session=SESSION)
        self.assertEqual(out["sessions"], 1)
        self.assertFalse(os.path.exists(self.f1))
        self.assertTrue(os.path.exists(self.f2), "the other session is untouched")
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            left = json.load(fh)["spaces"]["spGONE"]["links"]
        self.assertEqual([l["session"] for l in left], [SESSION2])
        tether.forget_backup("spGONE", only_session=SESSION2)
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            self.assertNotIn("spGONE", json.load(fh)["spaces"], "no sessions left: the space entry goes too")

    def test_any_session_backup_can_be_deleted_even_a_linked_one(self):
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": link(session=SESSION)}}})
        out = tether.delete_backup_of_session("claude", SESSION)
        self.assertEqual((out["sessions"], out["bytes"]), (1, 10))
        self.assertFalse(os.path.exists(self.f1))
        self.assertTrue(os.path.exists(self.f2), "other sessions untouched")
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            self.assertEqual(list(json.load(fh)["spaces"]), ["spKEEP"], "the emptied space entry is gone")
        self.assertEqual(self.read_state()["spLIVE"]["terminals"]["t1"]["session"], SESSION, "the link is untouched")
        with self.assertRaises(tether.TetherError):
            tether.delete_backup_of_session("claude", SESSION)
        with self.assertRaises(tether.TetherError):
            tether.delete_backup_of_session("claude", "../../etc")

    def test_a_linked_space_is_refused_and_a_shared_session_kept(self):
        self.write_state({"spGONE": {"name": "old", "terminals": {"t1": link()}}})
        with self.assertRaises(tether.TetherError):
            tether.forget_backup("spGONE")
        self.write_state({})
        with open(os.path.join(self.bk, "manifest.json")) as fh:
            m = json.load(fh)
        m["spaces"]["spKEEP"]["links"].append({"agent": "claude", "session": SESSION})
        with open(os.path.join(self.bk, "manifest.json"), "w") as fh:
            json.dump(m, fh)
        self.assertEqual(tether.forget_backup("spGONE")["sessions"], 0)
        self.assertTrue(os.path.exists(self.f1), "another space still uses this session")


class Forking(TetherTestCase):
    def test_claude_fork_starts_with_fork_session_and_the_new_id(self):
        self.write_state({"spLIVE": {"name": "work", "terminals": {"t1": link(session=SESSION2, fork_of=SESSION)}}})
        where = tether.Where("spLIVE", "work", "t1", "tab1", "claude")
        out = io.StringIO()
        have = lambda agent, sess: sess == SESSION  # only the original has a transcript
        with mock.patch.dict(os.environ, {"TETHER_DRY_RUN": "1"}), \
                mock.patch.object(tether, "resolve_where", return_value=(where, None)), \
                mock.patch.object(tether, "find_tool", return_value="/bin/claude"), \
                mock.patch.object(tether, "session_exists", side_effect=have), \
                contextlib.redirect_stdout(out):
            tether.launch("claude", [])
        self.assertIn(f"claude --resume {SESSION} --fork-session --session-id {SESSION2}", out.getvalue())

    def test_copilot_copy_gets_a_new_id_everywhere_and_no_lock(self):
        home = os.path.join(self.dir, "cop")
        src = os.path.join(home, "session-state", SESSION)
        os.makedirs(src)
        for name, text in (("workspace.yaml", f"id: {SESSION}\n"), ("events.jsonl", f'{{"sessionId":"{SESSION}"}}\n'),
                           (f"inuse.1.lock", "")):
            with open(os.path.join(src, name), "w") as f:
                f.write(text)
        with mock.patch.object(tether, "_copilot_home", return_value=home):
            tether.copy_copilot_session(SESSION, SESSION2)
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
        with mock.patch.object(tether, "ensure_app"), \
                mock.patch.object(tether, "find_tool", return_value="/bin/hc"), \
                mock.patch.object(tether, "herdr_do", side_effect=herdr_do), \
                mock.patch.object(tether, "herdr_ls", side_effect=[live[:1], live[:1], live]), \
                mock.patch.object(tether, "running_agents", return_value=[]), \
                mock.patch.object(tether, "session_exists", return_value=True), \
                mock.patch.object(tether, "backup_cmd"), \
                mock.patch.object(tether, "copy_copilot_session") as copy, \
                mock.patch.object(tether.subprocess, "run", return_value=ok), \
                contextlib.redirect_stdout(io.StringIO()):
            tether.fork_space(["--id", "spOLD", "-y"])
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
        with mock.patch.object(tether, "herdr_ls", return_value=live), \
                mock.patch.object(tether, "running_agents", return_value=running):
            got = tether.adopt()
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
        with mock.patch.object(tether, "herdr_ls", return_value=live), mock.patch.object(tether, "running_agents", return_value=[]):
            tether.adopt("spLIVE")
        data = self.read_state()
        self.assertEqual((data["spLIVE"]["name"], data["spLIVE"]["terminals"]["t1"]["tab_name"]), ("renamed", "work"))
        self.assertEqual(data["spOTHER"]["name"], "other", "adopt for one space leaves the others alone")
        with mock.patch.object(tether, "herdr_ls", return_value=live), mock.patch.object(tether, "running_agents", return_value=[]):
            tether.adopt()
        self.assertEqual(self.read_state()["spOTHER"]["name"], "other renamed")

    def test_adopt_runs_on_space_switch_and_backup_but_never_raises(self):
        os.environ.pop("TETHER_NO_ADOPT")
        with mock.patch.object(tether, "adopt", return_value=[]) as ad:
            tether.touch("spLIVE")
        ad.assert_called_once_with("spLIVE")
        with mock.patch.object(tether, "adopt", side_effect=tether.TetherError("herdr is not running")):
            self.assertEqual(tether.safe_adopt(), [], "no Herdr: nothing happens, nothing raised")
        with mock.patch.object(tether, "adopt") as ad:
            with contextlib.redirect_stdout(io.StringIO()):
                tether.backup_cmd(["--quiet"])
            ad.assert_called_once()

    def test_an_unused_fork_is_forked_from_the_session_it_was_forked_from(self):
        self.write_state({"spOLD": {"name": "work (fork)", "terminals": {
            "t1": link(session=SESSION2, tab_name="c", fork_of=SESSION)}}})
        live = [{"id": "spOLD", "name": "work (fork)", "tabs": [{"id": "tab1", "name": "c", "terminals": [{"terminal": "t1"}]}]},
                {"id": "spNEW", "name": "work (fork 2)", "tabs": [{"id": "tabN", "name": "c", "terminals": [{"terminal": "n1"}]}]}]
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        have = lambda agent, sess: sess == SESSION  # only the original session has a transcript
        with mock.patch.object(tether, "ensure_app"), \
                mock.patch.object(tether, "find_tool", return_value="/bin/hc"), \
                mock.patch.object(tether, "herdr_do", return_value={"space": "spNEW", "terminal": "n1"}), \
                mock.patch.object(tether, "herdr_ls", side_effect=[live[:1], live[:1], live]), \
                mock.patch.object(tether, "running_agents", return_value=[]), \
                mock.patch.object(tether, "session_exists", side_effect=have), \
                mock.patch.object(tether, "restore_session", return_value=0), \
                mock.patch.object(tether, "backup_cmd"), \
                mock.patch.object(tether.subprocess, "run", return_value=ok), \
                contextlib.redirect_stdout(io.StringIO()):
            tether.fork_space(["--id", "spOLD", "-y"])
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
                mock.patch.object(tether, "_claude_home", return_value=os.path.join(self.dir, "claude")), \
                mock.patch.object(tether, "live_home", return_value=os.path.join(self.dir, "claude")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            tether.backup_cmd(["--id", "spF"])
            copied = os.path.join(bk, "claude", "projects", "p", SESSION + ".jsonl")
            self.assertTrue(os.path.exists(copied), "the source session is in the backup")
            with open(os.path.join(bk, "manifest.json")) as f:
                (lk,) = json.load(f)["spaces"]["spF"]["links"]
            self.assertEqual(lk["fork_of"], SESSION)
            status = tether.backup_status({"spF": self.read_state()["spF"]})
        self.assertEqual((status["backed_up"], status["missing"]), (1, 0))

    def test_name_gets_a_number_when_taken(self):
        self.assertEqual(tether.fork_name("work", {"work (fork)"}), "work (fork 2)")
        self.assertEqual(tether.fork_name("work (fork 2)", {"work (fork)", "work (fork 2)"}), "work (fork 3)")


class Searching(TetherTestCase):
    def setUp(self):
        super().setUp()
        self.claude = os.path.join(self.dir, "claude")
        self.bk = os.path.join(self.dir, "bk")
        for patcher in (mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": self.bk}),
                        mock.patch.object(tether, "_claude_home", return_value=self.claude),
                        mock.patch.object(tether, "live_home", return_value=self.claude)):
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
        self.assertEqual(tether.readable_turns("claude", path), [("user", "hello kafka"), ("assistant", "lag is the consumer")])
        cop = os.path.join(self.dir, "events.jsonl")
        with open(cop, "w") as f:
            f.write(json.dumps({"type": "user.message", "data": {"content": "copilot question", "transformedContent": "<x>"}}) + "\n")
            f.write(json.dumps({"type": "tool.execution_start", "data": {"content": "NOPE"}}) + "\n")
            f.write(json.dumps({"type": "assistant.message", "data": {"content": ""}}) + "\n")
            f.write(json.dumps({"type": "assistant.message", "data": {"content": "copilot answer"}}) + "\n")
        self.assertEqual(tether.readable_turns("copilot", cop), [("user", "copilot question"), ("assistant", "copilot answer")])

    def test_keyword_ranking_prefers_rare_terms_phrases_and_partial_words(self):
        a = self.transcript(SESSION, [("user", "why is the grafana dashboard empty"), ("assistant", "the datasource is wrong")])
        b = self.transcript(SESSION2, [("user", "the dashboard for billing"), ("assistant", "ok")])
        docs = [{"agent": "claude", "path": a, "src": SESSION, "session": SESSION, "space": "A", "state": "open", "tab": "", "last_used": ""},
                {"agent": "claude", "path": b, "src": SESSION2, "session": SESSION2, "space": "B", "state": "open", "tab": "", "last_used": ""}]
        terms = tether.query_terms("which grafana dashboard was empty?")
        self.assertEqual(terms, ["grafana", "dashboard", "empty"])
        rows = tether.lexical_rank(terms, docs)
        self.assertEqual([r["space"] for r in rows], ["A", "B"])
        self.assertEqual(rows[0]["score"], 100)
        self.assertNotIn("SECRETWORD", json.dumps([r["snippets"] for r in rows]))
        self.assertEqual([r["space"] for r in tether.lexical_rank(["grafan"], docs)], ["A"], "partial words match")
        self.assertEqual(tether.lexical_rank(["zzz"], docs), [])

    def test_a_possessive_or_a_short_word_does_not_flood_the_results(self):
        # "Acme's" used to give "acme" and "s"; "s" is inside nearly every word, so it decided the ranking.
        self.assertEqual(tether.query_terms("Acme's US partner"), ["acme", "us", "partner"])
        self.assertEqual(tether.query_terms("Acme’s US partner"), ["acme", "us", "partner"], "a typographic apostrophe too")
        self.assertEqual(tether.query_terms("a"), ["a"], "a query of nothing but one letter still searches")
        a = self.transcript(SESSION, [("user", "the US partner for acme"), ("assistant", "ok")])
        b = self.transcript(SESSION2, [("user", "status of the business, it is used a lot"), ("assistant", "yes")])
        c = self.transcript("33333333-3333-4333-8333-333333333333", [("user", "partner partner partner partner"), ("assistant", "ok")])
        docs = [{"agent": "claude", "path": p, "src": s, "session": s, "space": n, "state": "open", "tab": "", "last_used": ""}
                for p, s, n in ((a, SESSION, "A"), (b, SESSION2, "B"), (c, "33333333-3333-4333-8333-333333333333", "C"))]
        terms = ["acme", "us", "partner"]
        rows = tether.lexical_rank(terms, docs)
        self.assertEqual([r["space"] for r in rows], ["A", "C"], "'us' is not found inside 'status', 'business' or 'used'; "
                         "all the words rank above one word said often")
        self.assertIn("US partner", rows[0]["snippets"][0]["text"])
        self.assertEqual([r["space"] for r in tether.lexical_rank(["us"], docs)], ["A"], "a short word matches as a whole word")
        self.assertEqual([r["space"] for r in tether.lexical_rank(["stat"], docs)], ["B"], "a longer term still matches inside a word")

    def corpus(self, scopes, live_ids=("spOPEN",)):
        self.write_state({"spOPEN": {"name": "o", "terminals": {"t1": link(session=SESSION)}},
                          "spCLOSED": {"name": "c", "terminals": {"t2": link(session=SESSION2)}}})
        live = [{"id": i, "name": "o", "tabs": []} for i in live_ids]
        with mock.patch.object(tether, "herdr_ls", return_value=live):
            return {(d["state"], d["session"]): d for d in tether.search_corpus(scopes)}

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
        rows = tether.lexical_rank(["grafana"], docs)
        prompts = []

        def fake(prompt, model):
            prompts.append(prompt)
            if "ranks" in prompt or "rank past" in prompt:
                return 'Sure: [{"id": 2, "score": 90, "why": "alerts"}, {"id": 1, "score": 10, "why": "no"}]', 0.01
            return '{"score": 77, "why": "it matches", "quote": "grafana alerts"}', 0.02
        with mock.patch.object(tether, "ask_claude", side_effect=fake):
            ranked, cost = tether.rank_with_ai("alerts question", rows, "sonnet")
            self.assertEqual([r["space"] for r in ranked], ["B", "A"])
            self.assertEqual((ranked[0]["score"], ranked[0]["why"]), (90, "alerts"))
            deep, dcost = tether.deep_read("alerts question", ranked[:1], "sonnet")
        self.assertEqual((deep[0]["score"], deep[0]["quote"]), (77, "grafana alerts"))
        self.assertEqual(prompts[0].count("[1]") + prompts[0].count("[2]"), 2)
        self.assertNotIn("SECRETWORD", "".join(prompts))
        with mock.patch.object(tether, "ask_claude", return_value=("not json at all", None)):
            with self.assertRaises(tether.TetherError):
                tether.rank_with_ai("q", rows, "sonnet")

    def run_search(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tether.search_cmd(list(args))
        return out.getvalue()

    def test_command_estimates_falls_back_to_recent_and_validates(self):
        self.transcript(SESSION, [("user", "grafana dashboard"), ("assistant", "done")])
        self.write_state({"spOPEN": {"name": "o", "terminals": {"t1": link(session=SESSION)}}})
        live = [{"id": "spOPEN", "name": "o", "tabs": []}]
        with mock.patch.object(tether, "herdr_ls", return_value=live):
            out = json.loads(self.run_search("grafana", "--json"))
            self.assertEqual((out["tier"], out["corpus"], len(out["results"])), (1, 1, 1))
            self.assertGreater(out["estimate"]["rank_tokens"], 0)
            self.assertNotIn("path", out["results"][0])
            nothing = json.loads(self.run_search("zzzqqq", "--json"))
            self.assertEqual(nothing["results"], [])
            self.assertTrue(nothing["estimate"]["no_keyword_match"])
            with mock.patch.object(tether, "ask_claude", return_value=('[{"id": 1, "score": 55, "why": "recent"}]', 0.01)):
                ranked = json.loads(self.run_search("zzzqqq", "--rank", "--json"))
            self.assertTrue(ranked["fallback"])
            self.assertEqual(ranked["results"][0]["score"], 55)
            for bad in (["x", "--scope", "nope"], []):
                with self.assertRaises(SystemExit):
                    self.run_search(*bad)


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
        with mock.patch.object(tether, "search_corpus", return_value=docs), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            tether.read_cmd(["grafana", *args])
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
