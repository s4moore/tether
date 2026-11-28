# Design notes and known limitations

Why tether is built the way it is, what has been checked, and where it can let you down. How the pieces work is in
[`how-it-works.md`](how-it-works.md); the commands are in [`tether-help.txt`](tether-help.txt).

## Design decisions

- **Key on the space id, not its name.** A rename never breaks a link. The tab is stored with each link, so a closed space
  can be rebuilt tab by tab.
- **The agents' own folders stay the source of truth.** tether stores only links (`spaces.json`); conversations are resumed
  from where Claude and Copilot keep them. Backups are copies, never moves.
- **The mods only call the CLI.** All logic is in one stdlib-only Python package, so everything the UI does can be run and
  tested from a terminal, and a mod can be replaced without touching behaviour.
- **Guards before anything destructive.** A delete only touches paths built from a validated session uuid; a session that is
  running (matched by pid and process start time) is never started a second time or deleted; the link file is written under
  an exclusive lock, and an unreadable one is moved aside rather than overwritten.
- **Fail quiet where the input is not an interface.** Adopt and the Copilot fork depend on agent internals that can change. When
  they stop working they find nothing rather than guess, and `tether doctor` says so.

## Conversations: backup, restore, export, import

- `backup` mirrors each linked session's files into a folder laid out like the agent's home. Files are copied atomically,
  skipped when unchanged, and never deleted from the backup. A missing transcript is restored automatically on launch, or for
  all sessions with `tether restore`.
- **Import treats the bundle as untrusted input.** Only regular files belonging to a session listed in the manifest are
  accepted; absolute paths, `..`, symlinks, other files and non-export archives abort before anything is written, and an
  existing file is never replaced. Unit tests cover each case.
- **A backup only protects what it has copied.** It runs on demand, or at most every 6 hours while you launch agents, so a
  conversation untouched between backups could still be swept by Claude's retention before it is first copied. Run
  `tether backup` once after installing.
- **Claude deletes transcripts after 30 days by default** (`cleanupPeriodDays`). The installer can raise that to 365, but an
  administrator's managed settings outrank `~/.claude/settings.json`, so treat the backup as the safeguard and the setting as a
  bonus.
- **Backups are not encrypted.** They rely on folder permissions (`0700`, manifest `0600`). If the disk is not encrypted, or the
  folder is synced somewhere shared, treat it as sensitive. A backup inside a git repository must be git-ignored, or the command
  refuses.

## Delete, fork, adopt

- **Delete (shown as Archive in the UI) is one step and recoverable:** back up, then unlink. The conversation is restored with *Restore & open* in the
  Backups popup or `tether undelete`. Permanent deletion is explicit: the bin on a deleted space's archive row, or
  `tether delete --sessions` (which removes the agent's copy and the backup copy). Until then a deleted conversation exists
  twice on disk (the agent's folder and the backup); for privacy, wipe it from the archive.
- **Fork** copies a space and its sessions; the original is never modified. Claude: `--resume <id> --fork-session`. Copilot: the
  session folder is copied under a new id with the id rewritten inside it. Risks: the Copilot copy depends on its on-disk layout,
  which is not an interface; a running session is copied as it is at that instant; a Claude fork has no transcript until its
  first message (handled through `fork_of`: backup, restore and a repeated fork all follow it), so exporting an unused fork gives
  an empty bundle.
- **Adopt** links agents that were started without tether. It runs every 30 s from the spaces mod, on a space switch, before a
  backup and in `fork`. It reads *other processes'* environment (`ps eww`) and two undocumented agent internals
  (`~/.claude/sessions/<pid>.json`, Copilot's `inuse.<pid>.lock`). It cannot see a session you switched to with `/resume` inside a
  running agent (the link keeps the old id), and an agent started and quit within 30 s is missed. `TETHER_NO_ADOPT=1` turns it off.
- **`home_dir`.** Claude asks "trust this folder?" for the home folder on every start but remembers any other folder, so new
  agents started from `~` start in `home_dir` instead. Existing home sessions keep resuming in home (Claude looks sessions up by
  folder, so moving one is unsafe). Side effect: Claude's auto-memory is per folder.

## Continue in the other agent

- **Continuing is a translation, not a resume.** The new agent has the conversation as its history, but not the old agent's tool
  history: tool calls and results are short notes, not real tool turns, and images, attachments and thinking are dropped. It
  may read the notes differently from the agent that ran the tools.
- **Different agents behave differently** (models, tools, permissions, system prompts); the conversation carries over, the
  behaviour does not.
- **Both transcript formats are agent internals.** Copilot refuses a session file that does not match its schema, and either
  agent may change. Failures are loud (Copilot names the field), and the tests pin the written format.
- **Context size:** a long conversation keeps only its newest 150,000 characters, which can still exceed the target agent's
  window.
- **It reads a snapshot.** The original agent may still be running; messages it has not written to its transcript yet are
  not carried over, and nothing flows back afterwards: the two tabs are separate conversations from the moment you continue.
- **Copilot sessions written by `continue` have no shutdown event** (it needs statistics tether does not have).

## Context store

- **Facts go stale.** Nothing verifies them against the machine; dates and sources on each line, and the "older than a year"
  report, are how stale ones are spotted. A path in the store should be checked before it is relied on.
- **Retrieval is only as good as the index lines and the wording of facts.** `find` is a plain word match: "kafka consumer"
  finds a fact that says those words, not one that says "event listener". Topic descriptions are searched too.
- **Not secret-safe by design.** The filter catches obvious assignments and long token-like strings, not everything. The store
  is plain text; the skill tells agents never to put secrets in it.
- **`learn` sends conversation text to Claude** and can propose things that are true only for one task; every fact is
  reviewed by a person before it is saved.
- **Background learning reads your conversations without a button press**, so it is opt-in. It sends only text (never tool output) of
  new parts of conversations to Claude, within a daily cap, and only queues proposals. Proposals from a small, cheap model can be
  wrong, one-off or oddly worded; every one is reviewed before it is saved. It starts from the moment it is turned on.
- **The background check only fixes what is mechanical.** It never rewrites facts or merges topics; a long topic has to be split by
  hand (or with an agent's help).

## Search and read

- **Three tiers so the costly one only runs on request.** Tier 1 is a free local keyword match (well under a second on a large
  history). `--rank` makes one headless Claude call over short digests of the top candidates. `--deep` reads a few whole
  conversations, one call each. Each step prints a token estimate first; nothing paid runs until you ask.
- **Matching rules came from real misses.** A possessive `'s` and one-letter terms are dropped, a term of up to three characters
  matches only as a whole word (so "us" does not match "status"), and a conversation containing all the words outranks one that
  repeats a single word.
- **Quality limits.** Tier 1 matches words, not meaning, so a vague question may find nothing; the AI step then judges the 12 most
  recent conversations, which will miss an old one. Ranking from digests can miss a detail buried mid-conversation, which is what
  `--deep` is for. Only Claude does the AI steps; Copilot conversations are searched and shown.
- **Privacy and cost.** The AI steps send conversation text (never tool output) to Claude. Estimates are rough (4 characters a
  token, input only); the real cost comes back from Claude and is shown afterwards. The `deleted` scope is off by default because
  it grows the corpus.
- **`tether read`** prints what a conversation said (user and assistant text, never tool output, commands only with `--commands`).
  The text can hold personal data or secrets, so the skill tells agents to summarise it rather than paste it.
- Headless Claude calls are started without claude.ai connectors (`ENABLE_CLAUDEAI_MCP_SERVERS=false`): they never need them and
  their tool definitions made a trivial call cost many times more.

## Mods and UI

- Four mods are copies of shipped Herdr mods (`tether-spaces`, `tether-menu`, `tether-tabs`, `tether-new-menu`) and the layout is a
  copy of Herdr's `main`; they do not follow Herdr upgrades. `./sync.sh upstream` shows the drift and `./sync.sh shipped` checks
  that no shipped mod was edited in place.
- **Async work in a popup must run in `cx.spawn`.** A click handler's context is invalidated after an `await`; a popup that
  notified through it stayed on "Searching…" forever while the CLI was fine. Unit tests cannot see this class of bug.
- **Testing status.** Every CLI path the mods call is unit-tested; each mod passes `herdr-app check`; a TypeScript pass finds no
  unknown names or wrong argument counts; the installer is run in a scratch home. Click-through behaviour (popup sizes, the
  Finder reveal, the macOS file chooser, the "Are you sure?" rows, Open for a result in an already-open space) is checked by hand,
  not automated. Overlay sizes are estimates; adjust `POPUP_HEIGHT` / `DIALOG_HEIGHT` in `tether-backups/main.js` and the sizes
  passed to `openOverlay` if anything is clipped.

## Known limitations

1. **Closed spaces come back with agent tabs only.** Plain shells (a dev server tab), splits and layout are not stored.
2. **Tab order after reopening follows the terminal address as text** (`t10` before `t9`), not the original tab order.
3. **The "+" in the sidebar header always starts Claude**; the "+" in the tab bar offers Claude and Copilot.
4. **Close and Archive in the space menu do not ask.** Closing a space with a running dev server or build needs no confirmation;
   Archive is recoverable.
5. **macOS only in practice.** Import in the UI uses `osascript`, adopt uses `ps eww`, and Linux is untested.
6. **A path containing `'` breaks the `tether resume` line typed into terminals** (`open`, reopen), and `hc send` failures only
   print a warning.
7. **Cloned mods do not follow Herdr upgrades**; a different Herdr version can break the sidebar until the clones are refreshed.

## Improvement ideas

1. A scheduled backup (launchd) that does not depend on launching an agent, and optional encryption of backups and exports.
2. Remember a space's layout (tab names, folders, splits, plain shells) and rebuild it on reopen; fixes limitations 1 and 2.
3. An agent choice in the "+" dialog, remembering the last one.
4. `tether prune`: remove links of closed spaces whose transcripts are all gone.
5. Retire the pre-terminal link format (`agents` per space, `.v1-backup`, adoption logic) once every machine has been migrated.
6. One implementation of the "space name must be unique" rule (it exists in both the dialog and the CLI); the dialog could ask
   `tether`.
7. CI that runs `./test.sh`; `tests/test_docs.py` already fails if a command, environment variable or mod is undocumented.
8. Make `doctor` run `herdr-app check` on the mods (today only `./test.sh` does).
