"""Small shared helpers. Run: python3 -m unittest discover -s tests"""
import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import TetherTestCase
from tetherlib import core, store, workspace


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


if __name__ == "__main__":
    unittest.main()
