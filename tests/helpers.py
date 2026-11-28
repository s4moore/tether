"""Shared by the test modules: the temporary-state base class and a few builders."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))

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
