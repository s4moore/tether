"""The context store and learning. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import subprocess
import unittest
from datetime import datetime
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import TetherTestCase
from tetherlib import ai, backups, context, core, reading, search


class ContextStore(TetherTestCase):
    """`tether context`: a small tree of files (index, topics, one-line facts) every agent can look up, checked in the background."""

    def setUp(self):
        super().setUp()
        self.ctx = os.path.join(self.dir, "context")
        patcher = mock.patch.dict(os.environ, {"TETHER_CONTEXT_DIR": self.ctx})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cmd(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            context.context_cmd(list(args))
        return out.getvalue()

    def add(self, topic="repos", fact="the orders consumer is in ~/orders", about="where repositories live"):
        return self.run_cmd("add", topic, fact, "--about", about).strip()

    def test_add_creates_the_topic_and_its_index_line_and_refuses_duplicates(self):
        self.assertEqual(self.add(), "added")
        self.assertEqual(self.add(fact="The orders consumer is in ~/orders."), "already there")
        index, _ = context.context_index(self.ctx)
        self.assertEqual(index["repos"], ("repos.md", "where repositories live"))
        facts = context.context_facts(self.ctx, "repos")
        self.assertEqual([f[1] for f in facts], ["the orders consumer is in ~/orders"])
        self.assertRegex(facts[0][2], r"^\d{4}-\d\d-\d\d$")

    def test_a_new_topic_needs_a_description_and_secrets_are_refused(self):
        with self.assertRaises(core.TetherError) as ctx:
            self.run_cmd("add", "stack", "services use Kafka")
        self.assertIn("--about", str(ctx.exception))
        for bad in ("the api key: abc123", "password = hunter2", "deploy with " + "A1b2" * 10):
            with self.assertRaises(core.TetherError, msg=bad):
                self.add(fact=bad)
        with self.assertRaises(core.TetherError):
            self.add(topic="Bad Topic")
        self.assertEqual(context.context_topics(self.ctx), [])

    def test_find_matches_all_words_in_facts_and_topic_descriptions_without_a_model(self):
        self.add()
        self.add("stack", "services talk over Kafka, see [[repos]]", "stack and how services connect")
        self.assertIn("repos: the orders consumer", self.run_cmd("find", "orders", "consumer"))
        self.assertNotIn("stack:", self.run_cmd("find", "orders"))
        self.assertIn("stack: services talk over Kafka", self.run_cmd("find", "services", "connect"), "a topic description counts toward its facts")
        self.assertIn("nothing found", self.run_cmd("find", "zzz"))
        self.assertEqual(json.loads(self.run_cmd("find", "kafka", "--json"))[0]["topic"], "stack")

    def test_find_forgives_the_words_an_agent_guesses(self):
        self.add("work-tools", "Acme tracks work in Linear", "where tickets and projects live")
        for words in (("tracking", "work"), ("work", "tools"), ("which", "tool", "we", "use", "to", "track", "our", "work"), ("issue", "tracker")):
            self.assertIn("work-tools: Acme tracks work in Linear", self.run_cmd("find", *words), words)
        out = self.run_cmd("find", "kubernetes", "cluster")
        self.assertIn("nothing found", out)
        self.assertIn("work-tools: where tickets and projects live", out, "with no hit, the topics are listed so the agent can pick one")

    def test_a_new_topic_that_resembles_an_existing_one_is_flagged_and_can_be_filed_there(self):
        self.add("deployments", "acme-web deploys with make ship", "how and where things are deployed")
        self.assertEqual(context.similar_topic("deploy", "Read when shipping", {"deployments": "how and where things are deployed"}), "deployments")
        self.assertEqual(context.similar_topic("release-deploys", "when to ship to staging or production", {"deployments": "how and where things are deployed"}), "deployments")
        self.assertIsNone(context.similar_topic("databases", "how to reach data stores", {"deployments": "how and where things are deployed"}))
        context.context_queue_save(self.ctx, [{"topic": "deploys", "fact": "staging deploys use make stage", "about": "how to deploy", "source": "s"}])
        out = json.loads(self.run_cmd("proposals", "--json"))
        self.assertEqual((out[0]["new_topic"], out[0]["similar"]), (True, "deployments"))
        self.assertIn("similar to deployments", self.run_cmd("proposals"))
        self.run_cmd("accept", "--only", "1", "--into", "deployments")
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "deployments")], ["acme-web deploys with make ship", "staging deploys use make stage"])
        self.assertNotIn("deploys", context.context_topics(self.ctx), "no new topic was created")
        self.assertEqual(context.context_queue(self.ctx), [])

    def test_into_needs_one_fact_and_an_existing_topic(self):
        self.add()
        context.context_queue_save(self.ctx, [{"topic": "x", "fact": "a", "about": "b", "source": "s"}, {"topic": "y", "fact": "c", "about": "d", "source": "s"}])
        for args in (["accept", "--into", "repos"], ["accept", "--only", "1", "--into", "nope"], ["reject", "--only", "1", "--into", "repos"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.run_cmd(*args)
        self.assertEqual(len(context.context_queue(self.ctx)), 2)

    def test_the_prompt_lists_waiting_topics_and_a_fact_for_one_of_them_reuses_its_description(self):
        self.add()
        context.context_queue_save(self.ctx, [{"topic": "tools", "fact": "we use ripgrep", "about": "tools in use", "source": "s"}])
        with mock.patch.object(ai, "ask_ai", return_value=('[{"topic": "tools", "fact": "we use fd for file search"}]', None)) as ask:
            ops, _ = context.context_propose("USER: hi", "Demo", "haiku")
        prompt = ask.call_args.args[0]
        self.assertIn("- tools (proposed, waiting for review): tools in use", prompt)
        self.assertEqual([(o["topic"], o["about"]) for o in ops], [("tools", "tools in use")])

    def kinds(self):
        return sorted({i["kind"] for i in context.context_check_issues(self.ctx)})

    def test_check_reports_what_is_wrong_and_nothing_when_all_is_well(self):
        self.add()
        self.assertEqual(self.kinds(), [])
        with open(os.path.join(self.ctx, "orphan.md"), "w") as f:
            f.write("# orphan\n\n- a fact nobody can find\n")
        with open(os.path.join(self.ctx, "repos.md"), "a") as f:
            f.write("- see [[missing]]\n- the orders consumer is in ~/orders\n- old thing  _(2020-01-01, from: x)_\n")
        with open(os.path.join(self.ctx, "index.md"), "a") as f:
            f.write("- [gone](gone.md) — a topic whose file was deleted\n")
        self.assertEqual(self.kinds(), ["duplicate", "index-dead", "link-dead", "orphan", "stale"])

    def test_check_flags_long_files(self):
        self.add()
        with open(os.path.join(self.ctx, "repos.md"), "a") as f:
            f.writelines(f"- fact number {n}\n" for n in range(context.CONTEXT_TOPIC_MAX + 1))
        with open(os.path.join(self.ctx, "index.md"), "a") as f:
            f.writelines(f"- [t{n}](t{n}.md) — x\n" for n in range(context.CONTEXT_INDEX_MAX))
        self.assertIn("topic-long", self.kinds())
        self.assertIn("index-long", self.kinds())

    def test_fix_makes_only_the_safe_repairs_after_taking_a_backup(self):
        self.add()
        with open(os.path.join(self.ctx, "orphan.md"), "w") as f:
            f.write("# orphan\n\n- keep me\n")
        with open(os.path.join(self.ctx, "repos.md"), "a") as f:
            f.write("- the orders consumer is in ~/orders\n- [[nowhere]] link stays\n")
        with open(os.path.join(self.ctx, "index.md"), "a") as f:
            f.write("- [gone](gone.md) — deleted\n")
        out = self.run_cmd("check", "--fix")
        self.assertIn("fixed:", out)
        index, _ = context.context_index(self.ctx)
        self.assertIn("orphan", index)
        self.assertNotIn("gone", index)
        self.assertEqual(len(context.context_facts(self.ctx, "repos")), 2, "the duplicate is gone, the other facts stay")
        self.assertEqual(self.kinds(), ["link-dead"], "a dead link is reported, never edited away")
        backups = os.listdir(os.path.join(self.ctx, ".backup"))
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.ctx, ".backup", backups[0], "repos.md")) as f:
            self.assertEqual(f.read().count("the orders consumer"), 2, "the backup is the store as it was")

    def test_the_background_check_runs_at_most_once_a_day_and_only_with_a_store(self):
        with mock.patch.object(subprocess, "Popen") as popen:
            context.context_check_if_due()
            popen.assert_not_called()
            self.add()
            context.context_check_if_due()
            context.context_check_if_due()
            self.assertEqual(popen.call_count, 1, "the second launch within a day does nothing")
            self.assertEqual(popen.call_args.args[0][-3:], ["check", "--background", "--fix"])
            old = os.path.join(self.ctx, ".last-check")
            os.utime(old, (0, 0))
            context.context_check_if_due()
            self.assertEqual(popen.call_count, 2)

    def test_a_background_check_leaves_a_report_and_a_hint_is_shown_once(self):
        self.add()
        with open(os.path.join(self.ctx, "repos.md"), "a") as f:
            f.write("- see [[missing]]\n")
        context.context_check(["--background"])
        with open(os.path.join(self.ctx, ".report.json")) as f:
            self.assertEqual(json.load(f)["issues"][0]["kind"], "link-dead")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            context.context_hint()
            context.context_hint()
        self.assertEqual(err.getvalue().count("thing(s) to look at"), 1)

    def test_learn_proposes_facts_and_accept_saves_only_the_chosen_ones(self):
        self.add()
        proposal = json.dumps([
            {"topic": "repos", "fact": "the billing service is in ~/billing", "source": "chat"},
            {"topic": "stack", "fact": "builds use Gradle", "about": "build tools"},
            {"topic": "stack", "fact": "the token: abc123 is x"},
            {"topic": "newtopic", "fact": "has no description"},
            {"topic": "repos", "fact": "the orders consumer is in ~/orders"}])
        docs = [{"space_id": "s1", "space": "Work", "state": "open", "agent": "claude", "session": "x", "path": "/none"}]
        with mock.patch.object(reading, "find_conversations", return_value=docs), \
                mock.patch.object(reading, "conversation_text", return_value=("USER: hi", 1, 0)), \
                mock.patch.object(ai, "ask_claude", return_value=(proposal, 0.04)) as ask:
            out = self.run_cmd("learn", "Work")
        self.assertIn("2 fact(s) proposed", out)
        self.assertIn("nothing is saved yet", out)
        self.assertIn("- repos (1 fact(s)): where repositories live", ask.call_args.args[0])
        self.assertEqual(len(context.context_facts(self.ctx, "repos")), 1, "nothing saved by learn")
        self.run_cmd("accept", "--only", "2")
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "stack")], ["builds use Gradle"])
        self.assertEqual(len(context.context_facts(self.ctx, "repos")), 1)
        self.assertIn("discarded 1", self.run_cmd("reject"))
        self.assertFalse(os.path.exists(os.path.join(self.ctx, ".proposals.json")))


class ContextLearning(TetherTestCase):
    """The proposal queue and the opt-in background learning: it only ever proposes, it starts from now, and it is capped."""

    def setUp(self):
        super().setUp()
        self.ctx = os.path.join(self.dir, "context")
        patcher = mock.patch.dict(os.environ, {"TETHER_CONTEXT_DIR": self.ctx})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("TETHER_CONTEXT_AUTO", None)
        self.transcripts = []
        for n in range(4):
            path = os.path.join(self.dir, f"t{n}.jsonl")
            with open(path, "w") as f:
                f.write("x")
            os.utime(path, (1, 1))  # untouched for a long time: settled
            self.transcripts.append({"space_id": f"s{n}", "space": f"Space {n}", "state": "open", "agent": "claude", "session": f"sess{n}",
                                     "path": path, "last_used": f"2026-11-2{n}T00:00:00+00:00"})
        self.lines = {t["path"]: [f"USER: message {i}" for i in range(2)] for t in self.transcripts}

    def harvest(self, reply='[{"topic": "repos", "fact": "the billing service is in ~/billing", "about": "where repos live"}]', auto=True):
        calls = []

        def fake_ask(prompt, model, budget=2):
            calls.append((prompt, model))
            return reply, 0.02

        with mock.patch.dict(os.environ, {"TETHER_CONTEXT_AUTO": "1" if auto else "0"}), \
                mock.patch.object(search, "search_corpus", return_value=self.transcripts), \
                mock.patch.object(reading, "conversation_text", side_effect=lambda agent, path, cap=0, as_lines=False: self.lines[path]), \
                mock.patch.object(ai, "ask_claude", side_effect=fake_ask), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            context.context_harvest([])
        return calls

    def grow(self, n=8, which=None):
        for t in which or self.transcripts:
            self.lines[t["path"]] = self.lines[t["path"]] + [f"USER: more {i}" for i in range(n)]

    def queue(self):
        return context.context_queue(self.ctx)

    def test_it_is_off_by_default(self):
        self.assertEqual(self.harvest(auto=False), [])
        self.assertFalse(os.path.exists(os.path.join(self.ctx, ".harvested.json")))
        with mock.patch.object(subprocess, "Popen") as popen:
            context.context_harvest_if_due()
        popen.assert_not_called()

    def test_turning_it_on_starts_from_now_without_reading_history(self):
        self.assertEqual(self.harvest(), [], "no model call on the first run")
        with open(os.path.join(self.ctx, ".harvested.json")) as f:
            self.assertEqual(sorted(json.load(f)), ["sess0", "sess1", "sess2", "sess3"])
        self.assertEqual(self.queue(), [])

    def test_conversations_that_grew_are_read_from_the_new_part_and_only_proposed(self):
        self.harvest()
        self.grow()
        calls = self.harvest()
        self.assertEqual(len(calls), context.CONTEXT_HARVEST_MAX, "at most three conversations per run")
        self.assertEqual(calls[0][1], "haiku")
        self.assertIn("more 0", calls[0][0])
        self.assertNotIn("message 0", calls[0][0], "only what is new since the last read is sent")
        self.assertEqual(len(self.queue()), 1, "the same fact proposed again is not queued twice")
        self.assertEqual(context.context_topics(self.ctx), [], "nothing is saved by background learning")
        self.assertEqual(len(self.harvest()), 1, "the fourth conversation is read on the next run")
        self.assertEqual(self.harvest(), [], "and then nothing is left to read")

    def test_a_conversation_is_not_read_again_until_it_has_grown_enough_or_while_it_is_active(self):
        self.harvest()
        self.grow(n=context.CONTEXT_HARVEST_MIN_NEW - 1, which=self.transcripts[:1])
        self.assertEqual(self.harvest(), [])
        self.grow(n=2, which=self.transcripts[:1])
        os.utime(self.transcripts[0]["path"], None)  # written just now: still in use
        self.assertEqual(self.harvest(), [])
        os.utime(self.transcripts[0]["path"], (1, 1))
        self.assertEqual(len(self.harvest()), 1)

    def test_the_daily_spend_cap_stops_it(self):
        self.harvest()
        os.makedirs(self.ctx, exist_ok=True)
        with open(os.path.join(self.ctx, ".harvest-spend.json"), "w") as f:
            json.dump({"date": datetime.now().date().isoformat(), "usd": context.CONTEXT_HARVEST_CAP_USD}, f)
        self.grow()
        self.assertEqual(self.harvest(), [])

    def test_a_failed_model_call_stops_the_run_without_marking_conversations_as_read(self):
        self.harvest()
        self.grow()
        with mock.patch.dict(os.environ, {"TETHER_CONTEXT_AUTO": "1"}), mock.patch.object(search, "search_corpus", return_value=self.transcripts), \
                mock.patch.object(reading, "conversation_text", side_effect=lambda a, p, cap=0, as_lines=False: self.lines[p]), \
                mock.patch.object(ai, "ask_claude", side_effect=core.TetherError("no claude")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            context.context_harvest([])
        self.assertEqual(len(self.harvest()), context.CONTEXT_HARVEST_MAX, "still unread, so the next run tries again")

    def test_auto_accept_saves_only_plain_facts_about_existing_topics_and_can_be_undone(self):
        context.add_fact(self.ctx, "repos", "the api is in ~/api", about="where repos live")
        reply = json.dumps([
            {"topic": "repos", "fact": "the billing service is in ~/billing"},
            {"topic": "repos", "fact": "deploy with `make ship`"},
            {"topic": "repos", "fact": "docs live at https://example.com/docs"},
            {"topic": "tools", "fact": "we use ripgrep", "about": "tools in use"},
        ])
        self.harvest()
        self.grow()
        with mock.patch.dict(os.environ, {"TETHER_CONTEXT_AUTO_ACCEPT": "1"}):
            self.harvest(reply=reply)
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "repos")], ["the api is in ~/api", "the billing service is in ~/billing"])
        self.assertEqual(sorted(op["fact"] for op in self.queue()), ["deploy with `make ship`", "docs live at https://example.com/docs", "we use ripgrep"],
                         "commands, links and new topics still wait for review")
        self.assertIn("auto: ", context.context_facts(self.ctx, "repos")[1][3])
        log = context.context_auto_added(self.ctx)
        self.assertEqual([e["fact"] for e in log], ["the billing service is in ~/billing"])
        with contextlib.redirect_stdout(io.StringIO()):
            context.context_undo(["--only", "1"])
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "repos")], ["the api is in ~/api"])
        self.assertEqual(context.context_auto_added(self.ctx), [])

    def test_list_json_and_remove_give_the_window_what_it_needs(self):
        context.add_fact(self.ctx, "repos", "the api is in ~/api", source="Space 1", about="where repos live")
        context.add_fact(self.ctx, "repos", "the web app is in ~/web", source="auto: Space 2")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            context.context_list(["--json"])
        topics = json.loads(out.getvalue())
        self.assertEqual([(t["topic"], t["about"]) for t in topics], [("repos", "where repos live")])
        self.assertEqual([(f["fact"], f["auto"]) for f in topics[0]["facts"]], [("the api is in ~/api", False), ("the web app is in ~/web", True)])
        with contextlib.redirect_stdout(io.StringIO()):
            context.context_remove(["repos", "--line", str(topics[0]["facts"][1]["line"])])
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "repos")], ["the api is in ~/api"])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            context.context_remove(["repos", "--line", "1"])  # the title line is not a fact

    def test_without_auto_accept_everything_is_queued(self):
        context.add_fact(self.ctx, "repos", "the api is in ~/api", about="where repos live")
        self.harvest()
        self.grow()
        self.harvest(reply='[{"topic": "repos", "fact": "the billing service is in ~/billing"}]')
        self.assertEqual(len(context.context_facts(self.ctx, "repos")), 1)
        self.assertEqual(len(self.queue()), 1)

    def test_the_background_job_is_started_at_most_every_six_hours_when_on(self):
        with mock.patch.dict(os.environ, {"TETHER_CONTEXT_AUTO": "1"}), mock.patch.object(subprocess, "Popen") as popen:
            context.context_harvest_if_due()
            context.context_harvest_if_due()
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(popen.call_args.args[0][-2:], ["harvest", "--background"])
            os.utime(os.path.join(self.ctx, ".last-harvest"), (0, 0))
            context.context_harvest_if_due()
            self.assertEqual(popen.call_count, 2)

    def test_proposals_are_listed_for_the_ui_and_decided_by_number(self):
        os.makedirs(self.ctx)
        context.add_fact(self.ctx, "repos", "the orders consumer is in ~/orders", about="where repos live")
        context.context_queue_save(self.ctx, [
            {"topic": "repos", "fact": "the billing service is in ~/billing", "about": "", "source": "chat"},
            {"topic": "tools", "fact": "builds use Gradle", "about": "build tools", "source": "chat"}])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            context.context_proposals(["--json"])
        rows = json.loads(out.getvalue())
        self.assertEqual([(r["n"], r["topic"], r["new_topic"]) for r in rows], [(1, "repos", False), (2, "tools", True)])
        with contextlib.redirect_stdout(io.StringIO()):
            context.context_decide(["--only", "2"], True)
        self.assertEqual([f[1] for f in context.context_facts(self.ctx, "tools")], ["builds use Gradle"])
        self.assertEqual([op["fact"] for op in self.queue()], ["the billing service is in ~/billing"])
        with contextlib.redirect_stdout(io.StringIO()):
            context.context_decide([], False)
        self.assertEqual(self.queue(), [])

    def test_the_hint_mentions_waiting_proposals(self):
        os.makedirs(self.ctx)
        context.context_queue_save(self.ctx, [{"topic": "repos", "fact": "x is in ~/x", "about": "a", "source": ""}])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            context.context_hint()
        self.assertIn("1 proposed fact(s) to review", err.getvalue())


if __name__ == "__main__":
    unittest.main()
