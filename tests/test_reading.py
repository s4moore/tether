"""`tether read`. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import TetherTestCase
from tetherlib import reading, search


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
