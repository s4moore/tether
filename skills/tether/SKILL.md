---
name: tether
description: Persistent agent sessions per herdr-app space (tether). Use when the user mentions herdr, herdr spaces, tether, linking or resuming a Claude/Copilot session to a space or terminal, restoring a space after a restart, a session surviving a restart, or when running inside a herdr terminal ($HC_TERMINAL_ID is set) and asked to link, unlink, list or inspect sessions. Also use when the user refers to ANOTHER conversation, chat, thread, session or space ("the other chat", "in my Grafana space", "what we discussed in X", "check the X conversation for context"): find it with tether list / tether search and read it with tether read.
---

# tether

`~/.local/bin/tether` (Python 3.9+, stdlib only) keeps a persistent Claude / Copilot session for
every terminal in a herdr-app **space**. After a restart the user runs one command and every
tab of the space is back in its own session, no picker. `tether --help` prints the manual.

## Using context from another conversation

When the user points at another conversation ("check the Grafana chat", "what did we decide in the X space"):
1. Find the space: `tether list <part of its name>`; if the name is unknown, `tether search <words from what was discussed>`
   (free, local).
2. Read it: `tether read <space> --last 15` (add `--commands` for the shell commands it ran, `--full` for uncut
   messages). More than one space matching is an error that lists them: ask which.
3. Say which space you read, and use what it said as context. Summarise; do not paste long stretches back.
   What was said can hold personal data or secrets, so never copy it into files, tickets or messages unless asked.
Do not guess what another conversation said from its name, and do not read other spaces unless the user asked.

## Prerequisite: herdr-app

tether only works with the herdr desktop app (tested with herdr-app 0.1.16, mods based on 0.1.15+; spaces are an app feature).
Tested on macOS; Linux is untested; it does not run on Windows (the script uses `fcntl` and `ps`).

Before running any tether command, check:

1. `command -v herdr-app` succeeds. If not, herdr is not installed: **ask the user** whether to
   install it. Only on an explicit yes, run the official installer:

   ```
   curl -fsSL https://releases.herdr.dev/app/install.sh | sh
   ```

   (Source: https://herdr.dev/download/. It installs Herdr.app into `~/Applications` or the app
   into `~/.local/share/herdr-app`, links `herdr-app` into `~/.local/bin`, verifies a checksum,
   needs no sudo, and installs herdr-core only if none exists. Re-running it updates the app.)
   Windows: tell the user tether does not support it. Intel Macs are not supported by herdr.
   If `~/.local/bin` is not on PATH, tell the user to add it.
2. For commands that act on "this terminal" (`claude`, `copilot`, `resume`, `link`, `status`,
   `reset`), `$HC_TERMINAL_ID` must be set. If not, you are not inside a herdr terminal.
   Installing herdr does not move this agent: tell the user to open herdr, start a terminal in
   a space, and run the command there. `list` and `open` work from any terminal.

Never install without asking, and never install on the user's behalf in a non-interactive context.
After a fresh install, herdr's own guide suggests pointing an agent at
`~/.config/herdr-app/AGENTS.md` for customising it.

## When invoked directly (e.g. `/tether link`, `/tether list balance`)

Treat the arguments as a tether subcommand and run it with Bash straight away; do not ask what
"tether" means. If `link` or `reset` is given without an agent name, use the agent you are:
`claude` if you are Claude Code, `copilot` if you are Copilot CLI.

For `link` with no session id, **just run `tether link <agent>`. Do not list, search or choose
sessions yourself, and do not work out your own id.** tether detects the session it is running
in: Claude via `$CLAUDE_CODE_SESSION_ID`, Copilot via the `inuse.<pid>.lock` file of its own
process. Report its output. Only if the output says `GUESSED` does it not know which session
this is: say so, show the id it picked, and let the user correct it with
`tether link <agent> <session-id>`.

After a successful link, say only what tether printed: `tether` in this terminal resumes the
session, and after a restart `tether` there (or `tether open <space>` for the whole space)
brings it back. Do not tell the user to run `tether claude` after a restart.

With no arguments, run `tether status`. Exception: `tether open` types into the user's other
terminals, so run it only when the user asked for it, and without `-y` first so they see the
plan (it needs a real terminal to confirm; if you have none, show the plan and let them run it,
or pass `-y` only if they told you to).

## Commands

```
Inside a herdr terminal (act on THIS terminal)
  tether claude|copilot [args...]  start or resume this terminal's session
  tether  /  tether resume         resume whatever this terminal had (agent-agnostic); a
                                   terminal with no link gets an arrow-key menu instead
  tether link claude|copilot [id]  link an EXISTING session to this terminal. No id: the session
                                   you are running in (detected; labelled GUESSED if tether
                                   could only pick the most recent). A session belongs to one
                                   terminal: linking it here unlinks it elsewhere.
  tether status                    every link in this space, per terminal and tab
  tether reset [claude|copilot]    unlink this terminal; transcript kept, its id is printed

Anywhere
  tether                           (outside herdr, or an unlinked terminal) arrow-key menu:
                                   "+ new space..." on top (asks a name and an agent, makes the
                                   space with `herdr-app do space.new`, starts the agent in it),
                                   then every space with a link, closed ones marked (closed)
  tether list [text] [--all]       spaces with last-used time, newest first; closed ones are
                                   marked. --all adds the `--resume <id>` line per session.
  tether open [space] [-y]         restore a space. Open space: types `tether resume` into each
                                   idle linked terminal (`hc send`) after a plan and a prompt.
                                   CLOSED space: recreates it (`space.new`, then a `tab.new`
                                   per linked terminal, same tab names and folders), moves the
                                   links to the new terminals, then resumes each session.
                                   Skips terminals already running an agent, gone, or in
                                   another tab. No name: pick from the menu.
  tether <space name>              shorthand for `tether open <space name>`
  tether delete [space] [--close] [--sessions] [-y]
                                   delete a space (open or closed; `--id <space-id>` for an exact id).
                                   Default: unlink it after taking a backup; transcripts stay where the
                                   agents keep them, so it is recoverable (`tether undelete`). --close
                                   closes the space in herdr first if it is open and waits for its agents
                                   to exit. --sessions makes it PERMANENT: also deletes each linked
                                   session's transcript (Claude: the .jsonl, its folder, file-history/
                                   and session-env/; Copilot: its session-state folder) and the backup
                                   copies. Asks first unless -y; refuses a session that is still running.
                                   Ask the user before using --sessions.
```

```
Conversations (they live in each agent's own folder; tether only stores links)
  tether backup [space] [--quiet]  copy every linked session's transcript into the backup folder
                                   (~/.local/share/tether/backups or $TETHER_BACKUP_DIR; incremental, never deletes).
                                   --id <space-id> for one exact space, --json for the result,
                                   --status [--json] to see what the backup holds instead,
                                   --list [--json] for every backed-up session by space, tab and first message
  tether restore [space | --id X]  copy missing transcripts back from the backup (`tether claude` does
                                   this by itself when a linked transcript has gone).
                                   --session <id> [--agent a] restores one session by its id
  tether continue [claude|copilot] [--tab <tab-id>]   (older name: `tether swap`)
                                   continue this terminal's conversation with the other agent (default: the
                                   other one) in a NEW tab: writes a new session in its store from the
                                   transcript (text; tool calls and results as short notes), links it to the
                                   new tab and starts it there. The original tab and session are untouched
                                   and may keep running, so it can be run from inside the agent
                                   (`! tether continue`). `--tab` is what the tab menu's Continue in Copilot / Claude uses.
  tether config [--json | get <key> | set <key> <value|default>]
                                   every setting with its value and source (default, config.json, or an environment
                                   variable, which wins); the gear in the spaces header edits the same ones. `set`
                                   validates. `$TETHER_DEFAULT_AGENT` (claude|copilot) is what the + button starts.
  tether context add|find|list|check|learn|proposals|accept|reject|harvest
                                   a small knowledge base of facts about the user's environment (where repos
                                   live, the stack, commands that worked). `tether context find <words>` is a free
                                   local search: use it before searching the machine. The `tether-context` skill
                                   explains how agents use it. Plain text: never store secrets. `$TETHER_CONTEXT_DIR`
                                   moves the store. `learn` and the opt-in background learning (`context_auto`,
                                   `$TETHER_CONTEXT_AUTO`, agent `$TETHER_CONTEXT_AGENT`, `$TETHER_CONTEXT_AUTO_ACCEPT` (saves plain facts about existing topics without review; `tether context recent` lists them, `undo` removes),  model `$TETHER_CONTEXT_MODEL`) only PROPOSE facts; the
                                   user accepts them. Never accept for them.
  tether read <space> [--last N] [--commands] [--full] [--id X | --session S] [--json]
                                   what a space's conversation said (user and assistant text, last N messages,
                                   default 12), for bringing in context from another thread. Never tool output.
  tether search <words|question> [--scope open,closed,deleted] [--rank] [--deep [--sessions id,id]] [--json]
                                   find a past conversation by what was said. Plain `search` is free
                                   and local (keyword match, instant): run it first. `--rank` makes one
                                   headless Claude call over short digests; `--deep` reads the top three
                                   (or the --sessions given) in full, one call each. Both cost tokens
                                   (the output prints an estimate): run them only if the user asks or
                                   agrees. `deleted` searches the backup of deleted spaces. Results hold
                                   conversation text: summarise, do not paste or upload them. Model:
                                   $TETHER_SEARCH_MODEL or "search_model" in config.json (default sonnet).
  tether adopt                     link agents running in herdr tabs that were started without tether
                                   (plain `claude` / `copilot`); sessions are read from the running
                                   processes. Also automatic: every 30 s while Herdr is open, on a
                                   space switch, before a backup, and in `fork`.
  tether fork [space] [--id X] [-y] copy a space into a new one ("<name> (fork)", then "(fork 2)"...) with
                                   copies of its sessions, one tab each, from the point you fork. Claude
                                   tabs use --fork-session (the copy gets its own transcript with the first
                                   message you type there); Copilot tabs copy the session folder under a new
                                   id. The original is backed up first and never touched.
  tether open-session <id>         put one session back in a new tab of the space that linked it; a
                                   DELETED space is brought back first (restoring transcripts)
  tether undelete [space] [--list]  bring back a space that was deleted (a plain delete keeps a record in
                                   the backup) as a closed space; then `tether open <name>`
  tether backup --forget --session <id> [--agent a] [--everything]
                                   delete one session's copy from the backup (any session; the agent's
                                   own files are never touched). `--forget --id <space-id>` removes a
                                   deleted space from the backup. --everything also deletes the agent's
                                   own copy (permanent). Deleting cannot be undone: ask the user first.
  tether config [home_dir [path|--unset|--default]]
                                   home_dir: where NEW agents start when launched from the home folder
                                   (default ~/.local/share/tether/home), so Claude's "trust this
                                   folder?" prompt, which it asks for the home folder every time, goes
                                   away. Sessions already started in home still resume there.
  tether --version                 print the version
  tether doctor                    check install, Herdr, links, backups; prints the fix for each problem
  tether stats [--days N] [--json] the local usage log (counts, timings, outcomes; never content): for the user, not for you to act on
                                   ($TETHER_USAGE_LOG=0 or `tether config set usage_log off` stops it)
  tether export <space> [-o file|folder] [--id X] [--json]
                                   one private .tar.gz with the space's links and conversations
  tether import <file> [--name N] [--json]
                                   unpack it as a CLOSED space; then `tether open <name>`
```

Backups and exports hold full conversations: never print, upload or commit them, and never put the
backup folder in a git repo that does not ignore it (`tether backup` refuses).

From inside a running agent, use the shell escape: `! tether link claude`.

## How it works (so you can debug it)

- A link belongs to a **terminal**: space id + terminal address (`t1`, `t1b`, ...) + agent +
  session. The space id (`sp...`) is stable across renames, the name is only a label.
- After a reboot herdr keeps spaces, tabs and splits and gives every lost terminal a plain shell
  with the **same address** (the original command is not re-run). `tether open` finds the
  linked terminals by address and types `tether resume` into the idle ones.
- The tab id is stored with each link. If an address now sits in another tab (address reused),
  tether refuses to resume the old session there and starts a new one.
- State: `~/.local/state/tether/spaces.json` (`$TETHER_STATE_DIR`):
  `{space_id: {name, terminals: {addr: {agent, session, cwd, tab, tab_name, last_used, pid,
  pid_started}}, agents: {...older per-space links}}}`. The first save from an older file keeps
  `spaces.json.v1-backup`. An older per-space link is adopted by the next terminal in that
  space that runs that agent.
- First run in a terminal: `claude --session-id <uuid> --name <label>` /
  `copilot --session-id=<uuid> --name=<label>`. Later runs: `--resume` after `cd`-ing to the
  recorded folder (`TETHER_NO_CD=1` skips that). Label = space name, plus ` · <tab name>` once
  the space has more than one linked terminal.
- Resume works from any folder for both agents; the cd only matters for CLAUDE.md/repo context.
- Passing your own `--resume/-r/--continue/--session-id` makes tether step aside.
- Outside herdr or if the space can't be resolved, the agent commands start the plain agent.
- `tether link` from inside an agent also records that agent's pid, so the running guard and
  `delete --sessions` know the session is live. `tether touch <space-id>` (called by the `tether-spaces`
  mod) records "this space was just used" for the most-recent-first ordering.
- Running guard: refuses to start a session whose launcher process is still alive
  (`TETHER_FORCE=1` overrides). `TETHER_DRY_RUN=1` prints the plan and writes nothing. `TETHER_NO_ADOPT=1`
  turns off linking of agents started without tether; `TETHER_HOME_DIR` overrides the `home_dir` setting;
  `TETHER_SEARCH_MODEL`, `TETHER_BACKUP_DIR`, `TETHER_AUTO_BACKUP` override their `config.json` keys.
- Menu/picker: built-in arrow keys (up/down, ctrl-p/n, enter, esc), typing filters. Needs a
  real terminal; in a pipe bare `tether` prints the help text.
- A space with several terminals and only an older per-space link cannot be restored
  automatically (no way to know which terminal it belonged to): run `tether claude` once in
  the terminal that should have it.

## Known limits

- One agent per terminal link: running `tether copilot` in a terminal linked to Claude replaces
  that link (the old transcript is kept and its id printed).
- `tether open` only types into terminals herdr reports as not running an agent; it cannot see
  a program like vim in a plain shell, hence the confirmation. It never creates tabs: herdr
  restores those itself.
- Claude deletes old transcripts (`cleanupPeriodDays`, default 30; an admin-managed setting overrides
  the user's). Without a backup tether then warns and starts a new session; with one it restores the
  transcript first.
- `link` without an id detects the current session (Claude: `$CLAUDE_CODE_SESSION_ID`; Copilot:
  the lock file of an ancestor process). Outside an agent (a plain shell) it can only guess the
  most recently written session and says so; the guess can be wrong with several active sessions.
- Unnamed spaces get folder-derived labels such as `~`.
- The running guard assumes the agent's name appears in its process command line.
- Not verified: interactive (non -p) launches, `--name` on a Claude resume, and an `open`
  against real restarted terminals.

## Related

herdr-app config lives in `~/.config/herdr-app/` (its `AGENTS.md`, `herdr.d.ts`, `gpui-kit.d.ts`
are rewritten on every start; read them, don't edit them). `hc --skill` and `herdr-app --skill`
print the manuals. Mods go in `mods/` and are checked with `herdr-app check <mod folder>`.
herdr mods that use tether (in `~/.config/herdr-app/mods/`, layout `tether`): `tether-spaces` (sidebar; clock
button opens `tether-history`, archive button opens `tether-backups`, right-click opens `tether-menu`),
`tether-new-space`, `tether-history` (closed spaces only, click to reopen, the archive icon archives it (`tether delete`); archived spaces are in Backups), `tether-menu` (a copy of the stock `menu`: Close does not
ask; adds Fork, Back up now, Export… and Archive, which has no dialog: it is `tether delete`, recoverable; and Delete…, which asks first, then runs `tether delete --sessions --close -y`: the space, its conversations and their backups, gone for good), `tether-settings` (the gear in the spaces header: tether's defaults as toggles and choices, from `tether config --json`, saved with `tether config set`), `tether-context-review` (the popup that lists facts proposed for the context store, each with Accept and Reject; opened from the space menu's Learn from this space and Review learned facts), `tether-search` (the magnifier: search conversations by what was said; Search words (free) or Search with AI, either one first; click a result to expand its matching text with the matches highlighted), `tether-backups` (the Backups popup and the Export dialog), `tether-tabs` and `tether-new-menu` (clones of the
stock `tabs` and `new-menu`: the "+" at the top right starts Claude Code and Copilot through tether).
Overlay roots need an explicit height; `size_full()` collapses to the header in a popup.
