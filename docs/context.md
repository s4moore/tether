# Context: facts every agent can look up

`tether context` keeps what you would otherwise tell every agent again: where your repositories live, your stack and tools,
how your services connect, commands that worked. It is a small tree of plain files in `~/.local/share/tether/context`:

```
index.md        one line per topic: what it covers and when to read it
repos.md        - the orders consumer is in ~/orders  _(2026-11-28, from: setup chat)_
stack.md        - services talk over Kafka; see [[repos]]
```

- **Cheap to read.** An agent searches with `tether context find <words>` (instant, no model call), or reads the index
  and then one or two topic files. Only the index and the matching lines enter its context, however much you store: the
  cost of a lookup depends on how deep it goes, not on how big the store is. The `tether-context` skill tells Claude
  and Copilot to do exactly this. A line in `~/.claude/CLAUDE.md` pointing at it makes agents use it even when they would not
  have guessed to.
- **Build it up by approval, never silently.** Three ways in, and in each the facts are *proposed* and nothing is stored until a
  person accepts it:
  1. *While you work:* the `tether-context` skill tells agents to offer once, in the conversation, when they discover a durable fact
     ("Want me to remember that the orders consumer is in ~/orders?"); a yes runs `tether context add`. No extra model call.
  2. *On demand:* right-click a space → **Learn from this space** (or `tether context learn <space>`) reads that conversation with one
     model call (15 to 60 seconds) and queues proposals. A popup opens at once, shows the progress, and then lists the facts with Accept
     and Reject; **Review learned facts** reopens it later (or use `tether context proposals`, `accept [--only 1,3]`, `reject`).
  3. *In the background, opt-in:* set `"context_auto": true` (or `TETHER_CONTEXT_AUTO=1`). At most every 6 hours, when an agent is
     launched, tether reads conversations that have grown by at least 6 messages and been idle for 30 minutes, sends only the new
     part to the context agent (Claude by default, Copilot is a setting; haiku by default), and queues proposals. At most 3 conversations and $0.50 a day. Turning it on starts from
     now: existing conversations are marked as read, not mined (use *Learn from this space* for old ones). It sends conversation
     text to that agent without you pressing a button, which is why it is off by default.

  `tether context add <topic> "fact" --about "what a new topic covers"` adds one fact by hand. Facts that look like secrets
  (passwords, keys, tokens) are refused in every path, and when a proposal is waiting you get one line at launch, once a day.
- **Tidy by itself.** `tether context check` finds dead links, topics missing from the index, duplicates, over-long
  files (split them) and facts older than a year; `--fix` repairs the safe ones after copying the store to `.backup/`.
  When an agent is launched or a tab is opened through tether, a check runs in the background at most once a day. It reads a few small
  files and calls no model, so it costs a moment of CPU and no tokens; if it finds something, you get one line, once a day.
- **Plain text, on your machine.** Facts are plain Markdown: edit them by hand, put the folder in git, share it. Do not
  store secrets in it, and note that `learn` sends the conversation text to Claude (as the AI search does).

## Browsing and reviewing

- **Context** popup: right-click a space → **Browse context**, or **Browse saved facts** in Settings. Search every saved fact, read them by
  topic, delete one (it asks twice). Edit a fact by editing the Markdown file.
- **Review**: right-click a space → **Review learned facts** lists what is waiting, with Accept and Reject. Facts saved without review
  (see `context_auto_accept` in [configuration](configuration.md)) are listed below, each with **Undo**.
- On the command line: `tether context list [topic]`, `find <words>`, `recent`, `undo --only N`, `remove <topic> --line N`.
