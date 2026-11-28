"""Search over conversations. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import link, SESSION, SESSION2, TetherTestCase
from tetherlib import ai, backups, core, herdr, search, store


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


if __name__ == "__main__":
    unittest.main()
