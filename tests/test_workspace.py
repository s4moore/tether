"""Spaces: open, reopen, fork and delete. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import subprocess
import time
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import link, SESSION, SESSION2, slurp, TetherTestCase
from tetherlib import backups, core, herdr, launch, store, workspace


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


if __name__ == "__main__":
    unittest.main()
