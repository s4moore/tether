"""Continue in the other agent. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import subprocess
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import link, SESSION, TetherTestCase
from tetherlib import cli, continue_, core, herdr, search, store, workspace


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


if __name__ == "__main__":
    unittest.main()
