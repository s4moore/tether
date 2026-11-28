"""Backup, restore, export, import and forgetting a backup. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import tarfile
import subprocess
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import link, SESSION, SESSION2, slurp, TetherTestCase
from tetherlib import backups, core, herdr, launch, workspace


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


if __name__ == "__main__":
    unittest.main()
