"""The stored links (spaces.json). Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import TetherTestCase
from tetherlib import core, store


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


if __name__ == "__main__":
    unittest.main()
