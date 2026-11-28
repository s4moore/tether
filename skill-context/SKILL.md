---
name: tether-context
description: The user's stored context: where their repositories and folders live, their stack and tools, how their services connect, commands that worked. Use when you need such a fact (where is X, which repo or service does Y, how do we deploy or query Z) before searching the machine or asking, and when the user asks you to remember a durable fact about their environment.
---

# tether-context

A small knowledge base shared by every agent (`~/.local/share/tether/context`, or `$TETHER_CONTEXT_DIR`). It is kept
cheap on purpose: look up only what you need.

1. **Search first (free, instant):** `tether context find <words>` prints matching one-line facts with their topic. Use
   two or three distinctive words (`tether context find kafka consumer`). Matching is forgiving ("tracking" finds "tracks", a hyphen is a
   space, the facts with most of your words come first), so try another wording before giving up. When nothing matches, it lists the
   topics: read the likely one with `tether context list <topic>` instead of concluding that nothing is stored.
2. **Need more?** `tether context list` shows the topics (one line each, saying when to read it), and
   `tether context list <topic>` prints one topic's facts. Facts may point at related topics with `[[topic]]`. Read at most
   two or three topics; do not read the whole store.
3. **Trust, but check what matters:** each fact carries the date it was added. A path or command that is old or looks
   wrong should be checked before you rely on it. If you find it stale or wrong, tell the user.
4. **Offering to remember (sparingly):** when, during the work, you learn a *durable* fact about the user's environment that
   you had to discover and that will matter again (where a repository or folder lives, which service owns something, a command that
   worked and when to use it), offer **once**: "Want me to remember that the orders consumer is in ~/orders?". At most one or two
   offers in a conversation. Do not offer facts that are only true of this task, are obvious from the code, change often, or
   are secret or personal. If the user ignores or declines, drop it and do not ask again.
5. **Remembering:** only when the user asks you to remember something, or agrees to your offer:
   `tether context add <topic> "one-line fact" --source "where you learned it"`; a new topic also needs
   `--about "what it covers and when to read it"`. Prefer an existing topic. One fact per line, at most 300 characters.
   **Never store secrets** (passwords, tokens, keys), customer or personal data: the store is plain text.
6. `tether context learn <space>` proposes facts from a conversation into a queue (`tether context proposals`); the user
   accepts them (`tether context accept [--only 1,3]`) or in the Herdr space menu (Review learned facts). Never accept for them
   without showing the proposal.

If `tether` is not found or the store is empty, say so and carry on without it.
