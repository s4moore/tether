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

- **`bin/tether`** does all the work; the mods only call it. The mods call `tether` by absolute path
  (`~/.local/bin/tether`) because the Herdr app runs with a minimal `PATH`.
- **Where am I?** `$HC_TERMINAL_ID` plus `herdr-app ls` gives the space id, tab and terminal. Outside Herdr
  the agent commands simply start the plain agent; tether never blocks you from running the agent.

## What gets installed

`install.sh` copies files from a checkout (or an unpacked release) onto the machine; nothing runs from the checkout afterwards.

| Installed file | From | Used by |
|---|---|---|
| `~/.local/bin/tether` | `bin/tether` | you, the mods, the skill |
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

## Contract between the mods and the CLI

| Mod | Calls | Reads |
|---|---|---|
| `tether-spaces` | `tether touch <space-id>` on every space switch; `tether adopt --json` every 30 s | |
| `tether-new-space` | `tether list --json`; launches `tether claude` as the new space's program | `closed`, `name` |
| `tether-history` | `tether list --json`; `tether open --id <id> -y`; `tether delete --id <id> -y` (the trash icon) | `closed`, `last_used`, `name`, `forked_from`, `links[].agent/transcript/recoverable` |
| `tether-menu` | `tether backup --id <id> --json` (Back up now); `tether fork --id <id> -y` (Fork); `tether delete --id <id> -y --close` (Delete; "nothing linked" falls back to a plain close) | `sessions`, `bytes` |
| `tether-backups` (popup) | `backup --status --json`, `backup --list --json`, `backup --json`, `open-session <id> --agent <a>`, `backup --forget --session <id> --agent <a> [--everything] --json`, `import <file> --json` | status: `dir exists auto last_backup bytes linked backed_up missing recoverable`; rows: `space_id space tab agent session title in_agent in_backup linked bytes last_used` |
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
