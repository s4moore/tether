# How tether works

## The idea

Herdr groups terminals into **spaces** (each with tabs; each tab holds terminals with an address like
`t23`). tether remembers, per terminal, which agent session it was running, so it can be resumed by
command or by click. A link is: space id + terminal address + agent + session id.

The space **id** (`sp…`) is the key; the name is only a label, so renames never break a link. After a reboot
Herdr keeps the spaces and tabs and gives each lost terminal a plain shell with the **same address**; that
is what lets `tether open` find the right terminals again.

## Moving parts

```
Herdr app ── mods (JS) ──exec──▶ tether (Python) ──▶ herdr-app ls / do      (spaces, tabs, terminals)
                                      │         └──▶ hc send <terminal> …   (type into a terminal)
                                      └──▶ ~/.local/state/tether/spaces.json   (the links)
                                      └──▶ claude / copilot --resume <id>
                                      └──▶ ps / ~/.claude/sessions / copilot inuse.<pid>.lock   (adopt: which
                                           session runs in which terminal)
```

- **`tether`** (the `tetherlib` package behind `bin/tether`) does all the work; the mods only call it. The mods call `tether` by absolute path
  (`~/.local/bin/tether`) because the Herdr app runs with a minimal `PATH`.
- **Where am I?** `$HC_TERMINAL_ID` plus `herdr-app ls` gives the space id, tab and terminal. Outside Herdr
  the agent commands simply start the plain agent; tether never blocks you from running the agent.

## The code

`bin/tether` is only an entry point; the tool is the standard-library-only package `lib/tetherlib`, one module per area:

| Module | Holds |
|---|---|
| `core` | constants, `TetherError`, `env`/`say`, `config.json`, option parsing, the terminal `pick` menu |
| `herdr` | everything that talks to the Herdr app: spaces, terminals, commands |
| `store` | the links (`State`, `spaces.json`) and finding agent sessions and processes on this machine |
| `launch` | starting an agent here; `link`, `status`, `reset`, `touch` |
| `workspace` | list, open, fork, create and delete spaces; the menu |
| `ai` | the headless Claude / Copilot calls behind search and learning |
| `search`, `reading`, `continue_` | search; `tether read`; continue in the other agent |
| `context` | the fact store, proposals, background learning |
| `settings` | the settings registry and `tether config` |
| `backups` | backup, restore, export, import, undelete |
| `doctor` | `tether doctor` |
| `usage` | the local usage log (`record`, `tether stats`): counts, timings and outcomes only; `record` drops any field that is not a number, a boolean or a short token |
| `cli` | `main()`: the command table, and the `--help` text |

Three rules keep it workable:

- **Modules call each other by module name**: `herdr.herdr_ls()`, never `from herdr import herdr_ls`. The few exceptions are stable helpers
  from `core` (`env`, `say`, `TetherError`, `AGENTS`, …). Because of this, a test patches the module that *defines* a name
  (`mock.patch.object(herdr, "herdr_ls")`) and every caller sees it.
- **A local variable never takes a module's name** (`store`, `workspace`, `backups`, …): it would hide the module and fail only when that path
  runs. `tests/test_modules.py` checks this, that every module imports on its own, and that no code uses a name nothing defines.
- **A new command** is a function in the module of its area, one branch in `cli.main`, and a line in `--help` (the docs test fails until it is
  documented).

## What gets installed

`install.sh` copies files from a checkout (or an unpacked release) onto the machine; nothing runs from the checkout afterwards.

| Installed file | From | Used by |
|---|---|---|
| `~/.local/bin/tether` | `bin/tether` | you, the mods, the skill: a small entry point that adds `../lib` to the path and runs `tetherlib.cli` |
| `~/.local/lib/tetherlib/` | `lib/tetherlib/` | the code. Replaced whole on every install, so a renamed module cannot linger |
| `~/.claude/skills/tether/SKILL.md` | `skill/SKILL.md` | Claude Code; `~/.agents/skills/tether` is a link to the same folder, for Copilot CLI |
| `~/.config/herdr-app/mods/tether-*/` | `herdr/mods/*` | Herdr |
| `~/.config/herdr-app/layouts/tether.json` | `herdr/layouts/tether.json` | Herdr (`"layout": "tether"` in `shared.json`) |
| `~/.local/state/tether/` | created at run time | the links (`spaces.json`) and `config.json` |
| `~/.local/share/tether/backups/` | created by `tether backup` | conversation backups (never inside a checkout) |

Everything the installer replaces is first copied to `~/.tether-backups/<timestamp>/`.

## The link store

`~/.local/state/tether/spaces.json` (override: `$TETHER_STATE_DIR`), guarded by an exclusive lock file so
read-modify-write is atomic. Shape:

```
{ "<space id>": {
    "name": "...", "accessed": "<iso time of last switch to it>",
    "terminals": { "t23": { "agent": "claude", "session": "<uuid>", "cwd": "...", "tab": "<tab id>",
                            "tab_name": "claude", "created": "...", "last_used": "...",
                            "pid": 123, "pid_started": "<ps lstart>" } },
    "agents": { "claude": { ... } }   // older per-space links, adopted by the next terminal that runs that agent
} }
```

Forks add two optional fields: a link's `fork_of` (the session this tab's session is a copy of, kept until the
tab has a transcript of its own) and a space's `forked_from: {name, id, at}`.

**Configuration** is `<state dir>/config.json` (`tether config` shows it; only `home_dir` can be set with the command, the
others are edited by hand or written by the installer):

| Key | Meaning | Default |
|---|---|---|
| `backup_dir` | where `tether backup` writes | `~/.local/share/tether/backups` |
| `auto` | `true`: a launch starts a background backup, at most every 6 h | `false` |
| `home_dir` | folder where a *new* session started from your home folder is started instead (`null` means off) | `~/.local/share/tether/home` |
| `search_model` | model for the AI steps of `tether search` | `sonnet` |

`pid` + `pid_started` identify the running agent (the start time stops a recycled pid being mistaken for it).
They are written when tether launches an agent, and by `tether link` when run from inside the agent. An
unreadable file is moved aside as `spaces.json.corrupt-<time>` and a fresh one started.

## Flows

- **Launch** (`tether claude`): no link → new uuid, `claude --session-id <uuid> --name <space>`. A link whose
  transcript exists → `claude --resume <uuid>` from the folder it started in. A link whose transcript is gone
  (never used, or cleaned up) → a new session, with a warning. Refuses when the linked session is already
  running (`TETHER_FORCE=1` overrides). Passing your own `--resume/--continue/--session-id` makes it step aside.
- **home_dir.** A *new* session started from the home folder is started in `home_dir` instead, because Claude
  asks "trust this folder?" for the home folder on every start but remembers any other folder. An existing
  session keeps its recorded folder (Claude finds sessions by folder, so moving one is not safe).
- **Restore an open space** (`tether open`): types `tether resume` (via `hc send`) into each linked terminal
  that is idle, in the right tab and still present, after showing a plan.
- **Reopen a closed space**: recreates the space (`herdr-app do space.new` / `tab.new`) with one tab per
  linked terminal, moves the links to the new terminals, then types `tether resume` into each. Tabs that could
  not be recreated keep their link under the closed space. Only agent tabs come back: plain shells and splits
  do not (see the limitations in `review.md`).
- **Adopt** (`tether adopt`, also automatic): agents started with plain `claude` / `copilot` have no link, so
  they would not survive a restart, be backed up or be forked. For every `claude` / `copilot` process whose
  environment holds `HC_TERMINAL_ID` (`ps eww`), the session is read from `~/.claude/sessions/<pid>.json`
  (Claude) or the `inuse.<pid>.lock` in the session folder (Copilot) and linked to that terminal. It never
  changes an existing link and skips a session that is linked somewhere already. It runs every 30 s from the
  spaces mod, on `tether touch` (space switch), before `tether backup`, and at the start of `fork`; any failure
  (Herdr not running, no `ps` access) is ignored. `TETHER_NO_ADOPT=1` turns it off.
- **Fork** (`tether fork`): adopts first, backs up the original, then makes a new space `<name> (fork)` (or
  `(fork 2)`…) with a tab per linked terminal and a copy of each session. Claude: the new link carries
  `fork_of` and the first start runs `claude --resume <original> --fork-session --session-id <new>` (Claude only
  writes the new transcript with the first message; until then a restart forks again). Copilot: the session
  folder is copied under a new id (the id inside `workspace.yaml` / `events.jsonl` is rewritten, `inuse.*.lock`
  dropped); Copilot indexes the copy itself. A fork nobody has typed in is forked from the session it was forked
  from. The original is never modified.
- **Undelete / open-session**: `delete` (unlink only) first takes a backup of the space, so the manifest still
  names it. `tether undelete` recreates the link record as a closed space from the manifest; `tether
  open-session <id>` does that if needed, restores the transcript and opens the space (or adds a tab).
- **Delete** (`tether delete`): backs the space up, then unlinks it; the agents' own files stay, so `undelete` /
  `open-session` can bring it back (the manifest still names it). `--close` first closes a live space in Herdr and waits
  (`close_space_and_wait`: until the space has left `herdr-app ls` and none of its sessions shows in `running_agents()`,
  8 s at most) instead of a fixed pause. `--sessions` is the permanent form: each session's transcript (Claude: the
  `.jsonl`, its folder, `file-history/` and `session-env/`; Copilot: its `session-state` folder) **and its backup copy**
  go, with no backup taken. Only paths derived from a validated uuid are touched; a running session is kept.
  `backup --forget --session <id> --everything` (`purge_session`) does the same for one session and is what the
  archive's bin does on a row whose space was deleted.

## Conversations, backups and moving a space

The agents keep conversations in their own folders; tether stores only the links. `tether backup` mirrors the
files of every linked session into a folder laid out like each agent's home:

```
<backup>/claude/projects/<project>/<session>.jsonl      (+ <session>/ subagent files)
<backup>/claude/file-history/<session>/...  <backup>/claude/session-env/<session>/...
<backup>/copilot/session-state/<session>/...
<backup>/manifest.json   { spaces: { <space id>: { name, backed_up, links: [{agent, session, cwd, tab_name, terminal, fork_of?}] } } }
```

Files are copied atomically (temp file then rename), skipped when size and mtime are unchanged, never deleted
from the backup, and symlinks are ignored. The folder is created `0700`, the manifest `0600`. The folder comes
from `$TETHER_BACKUP_DIR`, else `backup_dir` in `<state dir>/config.json`, else `~/.local/share/tether/backups`;
inside a git repository it must be git-ignored or the command refuses (`git check-ignore`).
`auto: true` in `config.json` (or `TETHER_AUTO_BACKUP=1`) makes `launch` start a detached `tether backup --quiet`,
rate-limited by the mtime of `<state dir>/last-backup` to once per 6 hours.

**Unused forks.** A fork has no transcript of its own until its first message, so `source_session()` stands in
for it: backup copies, counts and lists the session it was forked from, and starting the tab restores that one.
**Deleting a backup.** `tether backup --forget --session <id>` removes one session's copies and its manifest
links (any session, even a live one: the next backup copies it again); `--forget --id <space>` removes a deleted
space. Neither touches the agents' own folders. `deleted_spaces()` lists manifest spaces tether no longer links.

**Restore.** `launch` looks for the backup copy when a linked transcript is missing and copies it back with
`overwrite=False`. `tether restore` does the same for every linked session.

**Export / import.** The bundle is a `.tar.gz` (written `0600`) with `manifest.json` (`{space: {name, links}}`)
and `files/<agent>/<path relative to that agent's home>`. Import is strict because the file comes from outside:
only regular files, no absolute paths or `..`, only paths that belong to a session id listed in the manifest
(`projects/<p>/<id>.jsonl`, `projects/<p>/<id>/…`, `file-history/<id>/…`, `session-env/<id>/…`,
`session-state/<id>/…`), any other entry aborts before anything is written, and an existing file is never
replaced. The space is stored under a synthetic id (`imported-<hex>`), so it appears as a closed space and
`tether open <name>` rebuilds it; its name gets ` (imported)` appended if it clashes.

## Continuing in the other agent

`tether continue [claude|copilot]` (older name: `swap`) continues a terminal's conversation with the other agent **in a new tab**. The two agents
cannot resume each other's sessions, so tether writes a **new session in the target agent's own store** from the old
transcript, opens a tab with `tab.new` (as `open-session` does), links the tab's terminal to the new session and types
`tether resume` into it. The original terminal, its link and its session are not touched, so the agent there may still be
running: only a snapshot of its transcript is read.

- **Reading** (`continue_turns`): the same transcript readers as `read` and search, extended to keep tool calls: user and
  assistant text are kept, a tool call and its result become `[used <tool>: <what>]` / `[result: <text>]` notes (300
  characters each) inside the assistant turn they belong to, consecutive same-role lines are merged so the roles alternate,
  and the result starts with a user turn. Sub-agent side chains, meta lines, thinking blocks, images and attachments are
  dropped.
- **Trimming** (`continue_trim`): at most `CONTINUE_CAP` (150,000) characters, newest whole turns first. If older turns are left out
  the conversation starts with a one-line user note saying how many, answered by "Understood.".
- **Writing Claude** (`write_claude_session`): `<claude home>/projects/<cwd with non-alphanumerics as "-">/<uuid>.jsonl`,
  one `user` or `assistant` line per turn chained by `uuid`/`parentUuid`, with `cwd`, `sessionId`, `version`, `timestamp`.
  Claude accepts this minimal form; none of its bookkeeping lines (queue operations, usage, hooks) are needed.
- **Writing Copilot** (`write_copilot_session`): `<copilot home>/session-state/<uuid>/` with `workspace.yaml`,
  `events.jsonl` and the empty `checkpoints`, `files`, `research` folders. Events: `session.start`, `session.context_changed`,
  then per turn `user.message` (`delivery: "idle"`), `assistant.turn_start`, `assistant.message` (empty `toolRequests`),
  `assistant.turn_end`, chained by `parentId`. **Copilot validates strictly** and names the first bad line and field in its
  error (for example `delivery` must be `idle`, a start time must be an integer, a `session.shutdown` needs about twenty
  statistics fields), which is why no shutdown event is written; a converted session simply looks like one that was not
  closed. Both formats are agent internals and can change: the unit tests pin what is written, and a real Claude and a real
  Copilot were each resumed from a converted session and answered questions about it (including a command that had been run).
- **The new tab** (`continue_cmd`): named `<tab name> · <agent>`, in the same space and folder; its terminal gets a link with
  `continued_from: {agent, session, at}` pointing at the session it came from. If the tab cannot be opened the converted
  session id is printed so it can be resumed by hand.
- **From the tab menu:** `tether-tabs` opens `tether-menu` on a tab's right-click, whose tab menu has **Continue in Copilot** or **Continue in Claude**
  (the mod reads `tether list --json` for the agent linked to the tab); it runs `tether continue --tab <tab id>`, which finds
  the tab's one linked terminal (`terminal_in_tab`).
- **Refusals:** no conversation yet, the same agent as the source, the target not on `PATH`, a tab with no linked terminal or
  several.

## Settings

`SETTINGS` in `lib/tetherlib/settings.py` is the one list of what `config.json` can hold: key, group, label, help, type (`bool`, `choice`, `path`),
choices, default, and the environment variable that overrides it. `config_rows` resolves each to its effective value with the
same functions the rest of the tool uses (`context_auto_on`, `search_model`, `default_agent`, `home_dir_setting`, `backup_dir`,
`context_dir`), and a **source**: `env` if the `TETHER_*` variable is set, `config` if the key is in `config.json`, else `default`.
`config_set` validates by type (a bool is on/off, a choice must be one of its choices, a path must have an existing parent; `home_dir`
must be an existing folder other than the home folder), stores it atomically (`save_config`, mode 0600) and says so if an
environment variable overrides it. The popup (`tether-settings`) renders bools and choices as buttons, shows paths read-only, disables
a setting whose source is `env`, and offers "Reset to the default" for one that is in `config.json`. `default_agent` is read by
`tether-new-space` (the + dialog).

## Context store

`tether context` is a small tree of plain files, read by agents on demand, built so a lookup costs the same however much is
stored. Default `~/.local/share/tether/context` (`$TETHER_CONTEXT_DIR`, or `context_dir` in `config.json`).

- **Files.** `index.md`: a title, then one line per topic `- [topic](topic.md) — when to read it`. `<topic>.md`
  (`[a-z0-9-]`, at most 41 characters): one fact per line, `- fact  _(2026-11-28, from: source)_`, with `[[topic]]` links to
  related topics. Hand-written bullets without the date part are accepted. Anything else in a file is ignored.
- **Lookup.** `find` greps every topic's facts (all words must match, case-insensitive) and the topic descriptions,
  and prints at most 20 lines; no model call. `list` prints the index (or one topic). An agent that follows the
  `tether-context` skill runs `find` first (`context_find` stems words, treats hyphens as spaces, ranks by how many words hit, and lists the topics when nothing does) and reads at most two or three topics. A lookup's cost is the index (limit 40 lines)
  plus the files read (limit about 100 facts each), not the size of the store.
- **Adding.** `add_fact` creates the topic and its index line (a new topic needs `--about`), refuses a line over 300
  characters, an exact duplicate (case, spacing and trailing punctuation ignored) and anything matching `SECRETISH_RE`
  (a password, key or token assignment, a long token-like string, a private-key header). `learn` asks `ask_claude` (no
  tools) for a JSON array of `{topic, fact, about?, source?}` given the existing topic list, drops what fails those same
  rules, and stores the rest in `.proposals.json`; `accept [--only]` applies, `reject` discards. Nothing is saved without accept.
- **Check** (`context_check_issues`): `index-dead`, `orphan`, `link-dead`, `duplicate`, `empty`, `index-long`
  (over `CONTEXT_INDEX_MAX` = 40 lines), `topic-long` (over `CONTEXT_TOPIC_MAX` = 100 facts) and `stale` (dated over
  `CONTEXT_STALE_DAYS` = 365 days ago). `--fix` (`context_fix`) first copies the store to `.backup/<time>` (the newest 5 are
  kept), then adds index lines for orphans, drops index lines whose file is gone, and removes exact duplicates inside a file.
  It never edits a link, a fact or a description. The result is written to `.report.json`.
- **In the background** (`context_check_if_due`, called from `launch` next to `auto_backup_if_due`): if the store has an index and
  `.last-check` is older than a day, the stamp is touched and `tether context check --background --fix` starts detached
  (`start_new_session`, no output). The child takes a non-blocking lock on `.check.lock`, so several tabs opened together run one
  check. `context_hint` prints one line to stderr, at most once a day, if the last report had warnings. `tether doctor`
  also reports the store. No model is ever called by the check.
- **Proposal queue** (`.proposals.json`): `context_propose` is the one model call (`ask_claude`, no tools) that turns conversation text
  into proposals; `learn` (a space's whole conversation, capped at `EXTRACT_CAP`) and the harvester
  both queue with `context_queue_save`, merging by fact; `proposals --json` lists them numbered for the popup; `accept`/`reject
  [--only N]` apply or discard and renumber.
- **Background learning** (`context_harvest`, opt-in `context_auto`): started from `launch` by `context_harvest_if_due` at most every
  `CONTEXT_HARVEST_EVERY` (6 h), detached, under a non-blocking `.harvest.lock`. It reads the open and closed conversations from
  `search_corpus`, newest first. `.harvested.json` holds, per session, how many message lines were already read; the first run
  only records these (history is never mined). A conversation is read when it has `CONTEXT_HARVEST_MIN_NEW` (6) new messages and its
  transcript has not been written for `CONTEXT_HARVEST_IDLE` (30 min); only the new lines (the last `CONTEXT_HARVEST_CHARS`,
  40,000) are sent, to `context_model()` (haiku; `context learn` uses the same setting). The call goes through `context_agent()`: `claude -p`, or `copilot -p -s --available-tools ""` (the prompt is an argument, Copilot reports no cost). `context_propose` shows the model each topic with its fact count plus the new topics already waiting in the queue; `similar_topic()` flags a proposed new topic that resembles another (shown as `similar` by `proposals --json`, filed elsewhere with `accept --only N --into <topic>`). With `context_auto_accept` on, `auto_acceptable()` lets a fact skip the queue only if its topic already exists and it has no command, link or shell characters (`RISKY_FACT_RE`); those are logged in `.auto-added.json` (last 50) for `recent` and `undo`. At most `CONTEXT_HARVEST_MAX` (3) conversations per run and
  `CONTEXT_HARVEST_CAP_USD` ($0.50) a day (`.harvest-spend.json`). A failed call stops the run without marking anything read. It only
  queues; it never calls `add_fact`.

## Reading another conversation

`tether read <space>` prints the last N user and assistant messages of a space's conversation (default 12, each cut at
1,500 characters unless `--full`), so one thread can pull in another's context. `find_conversations` picks the sessions (by
space name, `--id` or `--session`, from the agent's own files or the backup); `conversation_text` extracts the text: Claude
`user` / `assistant` text parts and Copilot `user.message` / `assistant.message` content, never tool output. Shell commands
are included only with `--commands`. The skill's description triggers on phrases like "the other chat" so an agent knows to use
it; if you want that to work even when the skill does not load, add a line to `~/.claude/CLAUDE.md` telling Claude to use
`tether list` / `tether search` / `tether read` for context from another conversation.

## Search

`tether search` finds a conversation by what was said in it, in three tiers so that the costly one only runs on request.

- **Corpus** (`search_corpus`): `open` / `closed` are the linked sessions of live / not-live spaces, read from the
  agent's own file, or from the backup if the agent has swept it (Herdr unreachable counts everything as closed).
  `deleted` is the backup of spaces tether no longer links (`deleted_spaces()`). A session is searched once, so the
  backup of a session that a live space still has is never a second document. An unused fork searches the session it
  was forked from (`source_session`).
- **Text** (`readable_turns`): Claude `user` / `assistant` text parts, Copilot `user.message` / `assistant.message`
  `data.content`. Meta entries, tool results, attachments and anything starting with `<` (system reminders) are
  dropped, which is also what keeps the AI steps small: in testing the text was roughly 1 to 2 % of the raw transcript size.
- **Tier 1** (free, ~0.25 s): terms from the query minus stop words (a possessive `'s` is dropped, and so is any
  one-letter term: "Acme's" used to give "acme" and "s", which is inside nearly every word and decided the ranking).
  A term of four or more characters counts as a substring ("investig" finds "investigation"); one of up to three
  (`SHORT_TERM`, "us", "api") only as a whole word, or "us" would match "status" and "business" (`_term_re`; the
  tether-search mod highlights with the same rule). Score = idf-weighted log term frequency, +50 % for terms in your own
  messages, plus phrase and adjacent-pair bonuses, times the share of the terms the conversation contains (all the words
  beat one word said often), normalised to 0..100.
  The result carries up to three snippets and an estimate of the cost of tiers 2 and 3.
- **Tier 2** (`--rank`): one `claude -p --tools "" --no-session-persistence --disable-slash-commands
  --max-budget-usd 2 --model <model> --output-format json` call, prompt on stdin (nothing written to disk), over a digest of
  each of the top 12 (space, tab, state, dates, first and last prompts, snippets); the answer must be a JSON array
  `[{id, score, why}]`. With no keyword match it judges the 12 most recent conversations instead (`fallback: true`).
- **Tier 3** (`--deep`): the top three (or `--sessions`), full text capped at 60 000 characters (start and end kept),
  one call each in parallel; `{score, why, quote}` each. Cost is reported from Claude's own `total_cost_usd`.

`tether search --json` prints `{query, terms, scopes, tier, model, cost_usd, corpus, estimate:{corpus_sessions,
rank_candidates, rank_tokens, deep_sessions, deep_tokens, model, no_keyword_match}, fallback, results:[{space_id, space,
state, agent, session, src, tab, last_used, via_backup, score, snippets:[{role,text}], title, why?, quote?}]}`; paths
and raw text are never in it. Model: `$TETHER_SEARCH_MODEL`, else `search_model` in `config.json`, else `sonnet`.

## The look of the popups

The popups (`tether-settings`, `tether-search`, `tether-backups`, `tether-history`, `tether-context`, `tether-context-review`) share one look, repeated as a
few small helper functions at the top of each `main.js` because mods are standalone files: a title with a quiet subtitle and an `Esc`
keycap; a **group** is one rounded card whose rows are separated by hairlines (not a box per row); an on/off setting is a **switch** and
a choice is a **segmented control**; a scope filter is a row of **chips**; a **primary** (solid) button is used only for the one main
action of a popup, every row action is a quiet outlined button; warnings are small badges rather than paragraphs of coloured text.
The space and tab menus keep Herdr's own menu look (they are copies of its `menu`), with items grouped by purpose and divided by hairlines.

## Contract between the mods and the CLI

| Mod | Calls | Reads |
|---|---|---|
| `tether-spaces` | `tether touch <space-id>` on every space switch; `tether adopt --json` every 30 s | |
| `tether-new-space` | `tether list --json`; launches `tether claude` as the new space's program | `closed`, `name` |
| `tether-history` | `tether list --json`; `tether open --id <id> -y`; `tether delete --id <id> -y` (the trash icon) | `closed`, `last_used`, `name`, `forked_from`, `links[].agent/transcript/recoverable` |
| `tether-settings` | `tether config --json`; `tether config set <key> <value\|default>` | `key group label help type choices default value source env editable privacy` |
| `tether-new-space` (default agent) | `tether config get default_agent` | one word |
| `tether-settings` (share view, `props.mode = "share"`) | `tether stats --export --json` (the text that would be written, nothing saved); `tether stats --export -y -o <folder>` after you choose a folder | the summary JSON; `wrote <path>` |
| `tether-context` | `tether context list --json` (topics, about, facts with `line`, `date`, `source`, `auto`); `tether context remove <topic> --line N` | `topic about facts[].line/fact/date/source/auto` |
| `tether-context-review` | opened with `props.learn = {id, name}` it runs `tether context learn --id <id> --json` itself and shows the progress; then `tether context proposals --json`; `tether context accept\|reject [--only N]` | `n topic fact about source new_topic` |
| `tether-menu` | (Learn from this space and Review learned facts) opens `tether-context-review`; `tether continue --tab <tab-id>` (tab menu: Continue in Copilot / Claude); `tether list --json` (which agent a tab has); `tether backup --id <id> --json` (Back up now); `tether fork --id <id> -y` (Fork); `tether delete --id <id> -y --close` (Archive; "nothing linked" falls back to a plain close) | `sessions`, `bytes` |
| `tether-backups` (popup) | `backup --status --json`, `backup --list --json`, `backup --json`, `open-session <id> --agent <a>`, `backup --forget --session <id> --agent <a> [--everything] --json`, `import <file> --json` | status: `dir exists auto last_backup bytes linked backed_up missing recoverable`; rows: `space_id space tab agent session title in_agent in_backup linked bytes last_used` |
| `tether-backups` (delete dialog, `props.mode = "delete"`, from the menu's Delete…) | `tether list --json` (what is linked; `tab_open` says whether a link's tab still exists); `tether delete --id <id> --sessions --close -y` (the space, its conversations and their backup copies; nothing is kept) | exit 3 and a message if a session is still running |
| `tether-backups` (export dialog) | `tether list --json`; `tether export --id <id> -o <folder> --json` | `path name sessions files missing` |
| `tether-search` | `tether search <text> --scope <list> --json [--rank]`; `--deep --sessions <ids>`; `tether open-session <id> --agent <a>` (Open), then `space.select` for a result in an open space | `tier estimate results[] corpus model cost_usd fallback` |
| `tether-tabs`, `tether-new-menu` | none: the "+" starts Claude Code / Copilot with argv `[tether, claude\|copilot]` | |

`tether list --json` prints `[{id, name, last_used, closed, forked_from, links:[{agent, tab_name, session, transcript, recoverable}]}]`
(`recoverable`: the agent no longer has it but the backup does),
newest first, and exits non-zero (rather than printing `[]`) if Herdr cannot be read, so a caller never
mistakes "Herdr is down" for "no closed spaces". Changing that shape means changing the mods.

## Environment

| Variable | Effect |
|---|---|
| `TETHER_STATE_DIR` | where links and `config.json` live (default `~/.local/state/tether`) |
| `TETHER_BACKUP_DIR` | backup folder; overrides `backup_dir` |
| `TETHER_AUTO_BACKUP` | `1` / `0`: overrides `auto` |
| `TETHER_HOME_DIR` | overrides `home_dir` |
| `TETHER_SEARCH_MODEL` | overrides `search_model` |
| `TETHER_NO_ADOPT=1` | never link agents that were started without tether |
| `TETHER_NO_CD=1` | do not change to the folder the session was started in |
| `TETHER_FORCE=1` | start, move or delete even if the session looks to be running |
| `TETHER_DRY_RUN=1` | print the plan and write nothing |

Also honoured: `CLAUDE_CONFIG_DIR` and `COPILOT_HOME` (where the agents keep their files, if you moved them). Herdr sets
`HC_TERMINAL_ID` in each terminal; tether reads it to know where it is running.

## What Herdr ships that this relies on

The cloned mods `tether-spaces` (from `spaces`, 0.1.15), `tether-menu` (from `menu`, 0.1.15), `tether-tabs` (from `tabs`, 0.1.16),
`tether-new-menu` (from `new-menu`, 0.1.16) and the layout `tether` (from `main`, 0.1.15) are frozen copies: they keep working
only while the shipped pieces they point at still exist.

- **Mods used by the layout:** `agents`, `changes`, `files`, `pane`, `workspace-chrome`, `workspace-status`.
- **Mods opened by the clones:** `menu` (right-click on a tab, from `tether-tabs`) and `worktree` (the New / Open / Remove worktree items in `tether-menu`).
- **Assets:** `mods/workspace-chrome/{archive,branch,clock,plus,search,spinner}.svg`, `mods/pane/close.svg`, `icons/shell.svg`.

`herdr-app check <mod>` validates a mod; `./sync.sh upstream` diffs each clone against the shipped original after a Herdr upgrade.

## What is in this repository

| Path | Installs to | What it is |
|---|---|---|
| `bin/tether` | `~/.local/bin/tether` | The entry point (a dozen lines). Manual: `docs/tether-help.txt` |
| `lib/tetherlib/` | `~/.local/lib/tetherlib/` | The tool: a Python 3 package, standard library only. See *The code* above |
| `skill/SKILL.md` | `~/.claude/skills/tether/` | Claude Code skill, so an agent can run `tether link/status/...` |
| `skill-context/SKILL.md` | `~/.claude/skills/tether-context/` | Skill that tells agents how to look up and remember facts with `tether context` |
| `herdr/mods/tether-spaces` | `~/.config/herdr-app/mods/` | Clone of Herdr's `spaces` mod: the sidebar list, a "+" that starts a tether Claude space, a history button, a backups button, and `touch` on every space switch |
| `herdr/mods/tether-new-space` | same | Name dialog for a new space; runs `tether claude` in it |
| `herdr/mods/tether-history` | same | Closed spaces tether still has sessions for; click to reopen |
| `herdr/mods/tether-menu` | same | Clone of `menu`: space context menu; Close does not ask; adds Fork, Back up now, Export…, **Archive** and **Delete…** (a tab's menu gets **Continue in Copilot** / **Continue in Claude**) (no dialog: it takes a backup and unlinks the space, the same as `tether delete`, so the archive button can bring it back) |
| `herdr/mods/tether-tabs`, `tether-new-menu` | same | Clones of Herdr's `tabs` and `new-menu`: the "+" at the top right starts Claude Code and Copilot **through tether**, so the tab is linked to its session from the first moment. Other agents start as before |
| `herdr/mods/tether-settings` | same | New: the Settings popup (the gear in the spaces header): toggles and choices for the defaults in `config.json`, each showing whether an environment variable overrides it |
| `herdr/mods/tether-context` | same | New: the Context popup (space menu *Browse context*, or Settings): search every saved fact, read them by topic, delete one (asks twice) |
| `herdr/mods/tether-context-review` | same | New: the popup that lists facts proposed for the context store (see [Context](context.md)), each with Accept and Reject, opened from the space menu |
| `herdr/mods/tether-search` | same | New: the Search popup (magnifier in the spaces header): a text box, checkboxes for open / closed / archived (deleted) spaces, *Search words* and *Search with AI* (either one straight away), an *Open* button per result, and a click anywhere else on a result expands it to all its matching text (up to three snippets) with the matched words highlighted |
| `herdr/mods/tether-backups` | same | New: the Backups popup (status, Back up all, Restore, Import) and the per-space Export dialog |
| `herdr/layouts/tether.json` | `.../layouts/` | Clone of the `main` layout that uses `tether-spaces` |
| `install.sh`, `get.sh` | | The installer (also `--uninstall`), and the bootstrap behind the one-line install |
| `sync.sh`, `release.sh` | | For maintainers: keep this repo and a machine in step; build a release |
| `docs/` | | `install.md`, `using.md`, `context.md`, `configuration.md`, `troubleshooting.md` (for users), `how-it-works.md` (design and contracts), `review.md` (design notes and known limitations) |
| `LICENSE`, `THIRD-PARTY.md` | | MIT for tether; the copied Herdr mods keep Apache-2.0 |
| `tests/`, `test.sh` | | Unit tests (no Herdr or Claude needed), the installer tests, and `test_docs.py`: it fails when a command, an environment variable or a mod is missing from the docs (README and `docs/`), the agent skill or the generated help |

## Not in this repository, on purpose

- `~/.local/state/tether/spaces.json` (the links): it holds space and session ids that only mean
  something on one machine. A new machine starts with none: bring a space across with `tether export` /
  `tether import`.
- Your conversation backups: they live in `~/.local/share/tether/backups`, outside any clone.
- `shared.json` except the layout choice: its other keys are space ids and colours, or a cache of
  detected agents that Herdr rebuilds.
- `gpui-kit.d.ts` and `jsconfig.json` in each mod: editor type aids that `herdr-app check` regenerates.

## Things to know about Herdr and tether

- **The `cloned_from` version in a mod is a label, not a pin.** Herdr records the version a clone started
  from, and says clones "are yours and never updated". Nothing here breaks on a Herdr upgrade by itself, but
  the clones will not pick up upstream fixes either. `./sync.sh upstream` is the way to notice.
- **Names:** the layout is `tether` and every custom mod is `tether-…`, so they are recognisable and cannot
  collide with a mod Herdr ships later.
- **The mods rely on assets and mods Herdr ships:** `mods/workspace-chrome/{plus,clock,branch}.svg`,
  `icons/shell.svg`, and the `workspace-chrome`, `agents`, `tabs`, `pane`, `worktree` mods that `tether`
  and `new-menu` reference. A Herdr upgrade that renames those would break the mods; `herdr-app check` and
  `./sync.sh upstream` will show it.
- Herdr does not re-run commands after a reboot: terminals come back as plain shells. Run `tether` in each,
  or `tether open <space>` once to restore the whole space.
- Claude deletes old transcripts after its retention period (30 days by default). With a backup,
  `tether claude` restores a deleted transcript; without one, a link whose transcript is gone starts a fresh
  session, with a warning.
