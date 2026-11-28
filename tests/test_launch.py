"""Starting an agent here, and link / status / reset. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import link, SESSION, TetherTestCase
from tetherlib import core, herdr, launch, store


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


if __name__ == "__main__":
    unittest.main()
