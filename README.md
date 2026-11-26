# tether

Persistent Claude / Copilot sessions for [Herdr](https://herdr.dev) **spaces**. Each terminal in a
space stays tied to its own agent session, so after a restart (or a closed space) one command or one
click puts every tab back in its conversation. It also backs up, searches, forks and moves those
conversations. This repo is the whole setup: the `tether` tool, the Herdr mods and layout that put it in
the sidebar, a Claude skill, and an installer.

tether is an independent project and is not affiliated with Herdr or Anthropic. Some mods are modified
copies of Herdr's own mods: see [THIRD-PARTY.md](THIRD-PARTY.md).

## Install

**Requirements**

| Needed | Why | Notes |
|---|---|---|
| macOS | what tether is tested on (see *Tested with*) | Linux is untested; tether uses `fcntl` and `ps`, so Windows is out. Import via the UI uses a macOS file chooser |
| [Herdr](https://herdr.dev/download) | spaces, `herdr-app`, `hc` | `curl -fsSL https://releases.herdr.dev/app/install.sh \| sh`. Launch it once (it creates its config folder), then quit it. The installer shows this command if Herdr is missing |
| Python 3.9+ | runs `tether` (standard library only) | the macOS system `python3` is fine; on a new Mac the first use prompts to install the Xcode Command Line Tools (`xcode-select --install`) |
| `claude` CLI | the agent | `copilot` is optional (only `tether copilot` needs it) |
| `curl`, `tar`, `bash` | the installer | already on macOS |

**One line** (installs the latest release):

```
curl -fsSL https://raw.githubusercontent.com/s4moore/tether/main/get.sh | sh
```

Quit Herdr first: it rewrites its `shared.json` when it quits, and the installer refuses to run while it is
open. The script downloads the release tarball, **checks it against the published SHA-256**, and runs the
installer from it. For a specific version: `curl -fsSL .../get.sh | TETHER_VERSION=0.1.1 sh`.

**Prefer to read it first?** Piping a script into a shell asks you to trust it. Either read `get.sh`
([source](get.sh)) and `install.sh`, or do it by hand:

```
git clone https://github.com/s4moore/tether && cd tether
./install.sh
```

(or download a release from the [releases page](https://github.com/s4moore/tether/releases), check the
`.sha256` file with `shasum -a 256 -c`, unpack it and run `./install.sh`).

**Make sure `tether` is on your `PATH`.** It is installed to `~/.local/bin`. If `tether` is "not found", add
that folder to your shell profile, then open a new terminal. Herdr restores tabs by typing `tether resume`
into them, so this matters:

```
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

**What the installer changes** (nothing outside this list):

| Where | What |
|---|---|
| `~/.local/bin/tether` | the tool |
| `~/.claude/skills/tether/SKILL.md`, link `~/.agents/skills/tether` | the skill, for Claude Code and Copilot CLI |
| `~/.config/herdr-app/mods/tether-*`, `layouts/tether.json` | the mods and the layout |
| `~/.config/herdr-app/shared.json` | sets `"layout": "tether"`; no other key is touched |
| `~/.local/state/tether/config.json` | `auto`: whether to back up in the background (asked; default no without a terminal) |
| `~/.claude/settings.json` | **only if you say yes** to "keep transcripts for 365 days": sets `cleanupPeriodDays` (an admin-managed setting would override it) |
| `~/.tether-backups/<time>/` | a copy of every file it replaced (the newest 5 runs are kept; identical files are not copied again) |

It checks every mod with `herdr-app check`; **if one does not load, it puts your previous mods, layout and
setting back** and tells you. It also warns if your Herdr is not the version tether was tested with. It asks
two optional questions on the terminal (never when there is none, so `curl | sh` in a script asks nothing;
answer ahead of time with `TETHER_AUTO_BACKUP=yes` and `CLAUDE_RETENTION_DAYS=365`). It is safe to re-run.

**Check it worked.** Start Herdr (the sidebar changes to tether's) and, in a space, run `tether status`: you
should see the space name and what is linked (or "nothing linked yet"). `tether claude` starts a session that
the same command resumes later. `tether doctor` checks the whole setup and says how to fix each problem.

**Upgrade.** Quit Herdr, run the install line again (or `git pull` and `./install.sh`). Your links and
conversation backups are not touched.

**Uninstall.** Quit Herdr, then:

```
curl -fsSL https://raw.githubusercontent.com/s4moore/tether/main/get.sh | sh -s -- --uninstall
```

(or `./install.sh --uninstall` from a clone). It removes the tool, the skill, the mods and the layout, and
takes the `"layout"` line out of `shared.json` so Herdr uses its own again. It **keeps** your space links
(`~/.local/state/tether`) and your conversation backups (`~/.local/share/tether/backups`): delete them
yourself if you will not use tether again. A `cleanupPeriodDays` you let it set stays in
`~/.claude/settings.json`.

**Tested with:** Herdr 0.1.16, macOS, Python 3.9, Claude Code 2.1. The mods are modified copies of Herdr's own
(from 0.1.15 and 0.1.16), so a much newer Herdr may change things underneath them: the installer warns, and
`./install.sh --uninstall` puts Herdr's own sidebar back.

## Troubleshooting

Start with **`tether doctor`**: it checks the install, Herdr, links and backups, and prints the fix for each
problem.

| Problem | Fix |
|---|---|
| `tether: command not found` | `~/.local/bin` is not on your `PATH` (see Install). |
| The installer says Herdr is running | Quit Herdr (it rewrites `shared.json` on exit), run it again. |
| `$HERDR_DIR/mods does not exist` | Launch Herdr once so it creates its config folder, quit it, run the installer again. |
| The installer says a mod does not load | Your Herdr is probably newer than the tested one. Nothing was changed. Run `herdr-app check herdr/mods/<name>` in a clone for the details and please open an issue with the Herdr version. |
| The sidebar did not change | Herdr reads its layout at start: restart it. Then check `shared.json` has `"layout": "tether"`. |
| A tab came back as a plain shell | Herdr does not re-run commands after a reboot. Type `tether` in the tab, or run `tether open <space>` once for the whole space. |
| "no transcript anywhere" in `tether doctor` | The session was never used, or Claude swept it before a backup existed. Harmless: it starts fresh. Turn on backups and a longer retention (see Install). |
| An agent started outside tether is not linked | `tether adopt` links running agents; it also runs by itself while Herdr is open. |
| Something else | `tether --help`, `docs/how-it-works.md`, or open an issue with the output of `tether doctor`. |

## Privacy and safety

- **Everything is local.** tether has no server and sends nothing anywhere itself.
- **The AI search sends conversation text to Claude.** `tether search --rank` / `--deep` and the popup's *Search
  with AI* run your own `claude` CLI on short digests (or, for `--deep`, the full text) of conversations, as
  you, with your account. Only what was said is sent, never tool output, and only when you press the button
  or pass the flag. Plain *Search words* is local. `tether read` prints conversation text to the terminal.
- **Backups and exports are plain copies of your conversations**, which can contain anything you pasted
  (secrets, customer data). They are unencrypted, in a folder only you can read; exports are mode 0600. Do not
  commit or share them casually.
- **`tether adopt` reads other processes' command lines and environments** (to find which session an agent
  started without tether is using). It only looks at agent processes in Herdr terminals and only reads. Turn it
  off with `TETHER_NO_ADOPT=1`.
- **The installer edits your files only as listed above**, backing up each one first; `~/.claude/settings.json`
  only if you agree.
- The AI steps run `claude` with **no tools** and without your claude.ai connectors (Slack, Gmail and so on).

## What is in here

| Path | Installs to | What it is |
|---|---|---|
| `bin/tether` | `~/.local/bin/tether` | The tool. Python 3, stdlib only. Manual: `docs/tether-help.txt` |
| `skill/SKILL.md` | `~/.claude/skills/tether/` | Claude Code skill, so an agent can run `tether link/status/...` |
| `herdr/mods/tether-spaces` | `~/.config/herdr-app/mods/` | Clone of Herdr's `spaces` mod: the sidebar list, a "+" that starts a tether Claude space, a history button, a backups button, and `touch` on every space switch |
| `herdr/mods/tether-new-space` | same | Name dialog for a new space; runs `tether claude` in it |
| `herdr/mods/tether-history` | same | Closed spaces tether still has sessions for; click to reopen |
| `herdr/mods/tether-menu` | same | Clone of `menu`: space context menu; Close does not ask; adds Fork, Back up now, Export… and Delete (no dialog: it unlinks after a backup, so the archive can bring it back) |
| `herdr/mods/tether-tabs`, `tether-new-menu` | same | Clones of Herdr's `tabs` and `new-menu`: the "+" at the top right starts Claude Code and Copilot **through tether**, so the tab is linked to its session from the first moment. Other agents start as before |
| `herdr/mods/tether-search` | same | New: the Search popup (magnifier in the spaces header): a text box, checkboxes for open / closed / deleted spaces, *Search words* and *Search with AI* (either one straight away), an *Open* button per result, and a click anywhere else on a result expands it to all its matching text (up to three snippets) with the matched words highlighted |
| `herdr/mods/tether-backups` | same | New: the Backups popup (status, Back up all, Restore, Import) and the per-space Export dialog |
| `herdr/layouts/tether.json` | `.../layouts/` | Clone of the `main` layout that uses `tether-spaces` |
| `install.sh`, `get.sh` | | The installer (also `--uninstall`), and the bootstrap behind the one-line install |
| `sync.sh`, `release.sh` | | For maintainers: keep this repo and a machine in step; build a release |
| `docs/` | | `how-it-works.md` (design and contracts), `review.md` (design notes and known limitations) |
| `LICENSE`, `THIRD-PARTY.md` | | MIT for tether; the copied Herdr mods keep Apache-2.0 |
| `tests/`, `test.sh` | | Unit tests (no Herdr or Claude needed), the installer tests, and `test_docs.py`: it fails when a command, an environment variable or a mod is missing from the README, the agent skill, the generated help or `docs/how-it-works.md` |

## Everyday use

```
tether claude            start or resume this terminal's Claude session   (also: tether copilot)
tether                   resume what this terminal had; otherwise a menu
tether resume            resume what this terminal had, without a menu (what `open` types into restored tabs)
tether status            what is linked in this space
tether link claude       link the session you are running in (from inside Claude: ! tether link claude)
tether reset              unlink this terminal (the transcript is kept)
tether list              spaces, most recently used first (closed ones marked)
tether open <space>      restore a space after a restart, or recreate a closed one
tether delete <space>    delete a space: a backup is taken first and Claude's / Copilot's own files stay, so it is recoverable (`tether undelete`, or Restore & open in the archive). `--close` closes it in Herdr first; `--sessions` makes it permanent (transcripts and backup copies)
tether backup            copy the transcripts of every linked session into the backup folder
tether restore           put back transcripts the agent has deleted (tether claude also does this by itself)
tether backup --list     every backed-up session by space, tab and first message (restore one with: tether restore --session <id>)
tether export <space>    bundle a space's links and conversations into one file (tether import <file> on another machine)
tether open-session <id> put one restored session back in a new tab of its space (the Backups popup's *Restore & open* does this)
tether backup --forget --session <id>  delete one session's backup copy (the Backups popup's trash icon; asks first there). `--forget --id <space-id>` removes a deleted space from the backup
tether read <space> [--last N]   print what a space's conversation said (last N messages, default 12; `--commands` adds the shell commands, `--full` stops the 1,500-character cut). To bring context from another thread into this one; the skill tells agents to use it when you say "the other chat". Never prints tool output
tether search <words or a question>   find a conversation by what was said in it. Free keyword search over open and closed spaces; `--rank` (Claude ranks the best candidates from short digests, one call) and `--deep` (reads the top few in full) cost tokens, and the output says roughly how many first; `--scope open,closed,deleted` (deleted: the backup of deleted spaces). The popup behind the magnifier in the spaces header does the same
tether adopt             link agents running in Herdr tabs that were started without tether (plain `claude` / `copilot`); their sessions are read from the running processes. it also runs by itself every 30 s while Herdr is open, when you switch space, before every backup, and in `fork`; `tether doctor` warns about unlinked agents
tether fork <space>      copy a space into "<name> (fork)": every linked session is copied (Claude: --fork-session; Copilot: its session folder is copied under a new id), so the two continue separately. Also in the space's right-click menu. The original is backed up first and never touched
tether undelete [space]  bring back a space you deleted as a closed space (Delete keeps a record in the backup; in the Backups popup, *Restore & open* on its row does this and opens it)
tether touch <space-id>   record that a space was just used (the spaces list calls this when you switch space; it also links unlinked agents in that space)
tether doctor            check the install, Herdr, links and backups; each problem comes with its fix
tether config home_dir <folder>   new agents started from your home folder start in this folder instead (default `~/.local/share/tether/home`, created on first use), so Claude stops asking "trust this folder?" (it remembers every folder except home). `--unset` turns it off, `--default` resets it. Sessions already started in home still resume there
```

Full reference: `tether --help` or `docs/tether-help.txt`. In Herdr: the "+" in the sidebar header makes a
space with a Claude session (Shift-click: a plain space), the clock button reopens a closed space, the
archive button opens **Backups**, and right-click a space for Fork, Back up now, Export… and Delete (Delete needs no dialog: it is recoverable from the archive).

**In the Herdr UI**

| Where | What |
|---|---|
| Sidebar header, magnifier | **Search**: type words or describe what you remember. *Search words* is a free local match of your exact words over the conversations of open and closed spaces (and, if ticked, the backup of deleted spaces; a session the agent has swept is read from its backup); a possessive ("Acme's") counts as the word, and a word of up to three letters ("US") matches only as a whole word. *Search with AI* does the same word match, then sends short digests of the best candidates to one Claude call; it can be pressed without searching words first, and after a words search it shows its token estimate. The AI search only runs when you press it. The popup has no whole-conversation read, the costliest step (`tether search --deep` remains on the command line). Only what was said is sent, never tool output. If no word matches, *Search with AI* looks at your most recent conversations. Click a result to expand its matching text, with the matched words highlighted. Each result has *Open*; a deleted space is restored first |
| Sidebar header, archive button | **Backups** popup: one status line (sessions backed up, size, last backup), then *Back up all* and *Import…* (a macOS file chooser), then a scrollable list of every backed-up session shown by space, tab, the first thing you asked, agent, age and size. Every row has an *Open* button (or click the row): it puts the session in a new tab of its space, restoring the transcript first if the agent lost it (*Restore & open*). A row whose space you deleted has *Restore & open* too: it brings the whole space back and opens it. On such a row the trash icon is the permanent delete: it asks "Delete permanently?" and removes the backup copy and Claude's / Copilot's own copy, which is the only place a conversation can be wiped for good. Every row has a trash icon: it asks "Are you sure?" on that row only, then deletes just that session's backup copy (a session the agent still has is copied again by the next backup); Claude's and Copilot's own files are never touched. The folder and a *Show in Finder* link are at the bottom |
| Right-click a space → *Back up now* | backs up that space's sessions and shows a toast |
| Right-click a space → *Export…* | a dialog explains the file holds full conversations; *Choose folder…* opens the system folder picker, writes `tether-export-<space>-<date>.tar.gz` there, and reveals it in Finder |
| Clock button (history) | closed spaces; one whose transcripts were deleted shows "restored from the backup" if a backup has them |

Import through the UI is macOS-only (the file chooser uses `osascript`); `tether import <file>` works anywhere.

## Configuration

`tether config` shows the settings kept in `~/.local/state/tether/config.json`. Environment variables override them.

| Setting (`config.json`) | Environment variable | Meaning |
|---|---|---|
| `backup_dir` | `TETHER_BACKUP_DIR` | where conversation backups go (default `~/.local/share/tether/backups`) |
| `auto` | `TETHER_AUTO_BACKUP` (`1`/`0`) | back up in the background when an agent starts, at most every 6 h |
| `home_dir` | `TETHER_HOME_DIR` | folder where new agents start when you are in your home folder (`tether config home_dir`) |
| `search_model` | `TETHER_SEARCH_MODEL` | Claude model for the AI steps of `tether search` (default `sonnet`) |
| | `TETHER_STATE_DIR` | where links and `config.json` live (default `~/.local/state/tether`) |
| | `TETHER_NO_ADOPT=1` | never link agents that were started without tether |
| | `TETHER_NO_CD=1` | do not change to the folder the session was started in |
| | `TETHER_FORCE=1` | start, move or delete even if the session looks to be running |
| | `TETHER_DRY_RUN=1` | print the plan and write nothing |

`CLAUDE_CONFIG_DIR` and `COPILOT_HOME` are honoured if you moved the agents' own folders.

## Developing tether

A clone is the source of truth for the files; `./install.sh` copies it onto your machine.

- `python3 -m unittest discover -s tests` runs the tests (no Herdr or Claude needed, including the installer
  tests, which use a throwaway `HOME` and a fake `herdr-app`). `./test.sh` also runs `herdr-app check` on each mod
  when Herdr is installed.
- Changing behaviour? Update, in the same change: this README, `skill/SKILL.md` (what agents read),
  `docs/how-it-works.md` and `docs/review.md`, then run `./sync.sh meta` (regenerates `docs/tether-help.txt`).
  `tests/test_docs.py` fails when a command, an environment variable or a mod is missing from the docs.
- Edited a file directly on a machine? `./sync.sh pull` copies it back (it refuses if the repo has uncommitted
  changes). After upgrading Herdr, `./sync.sh upstream` shows how each copied mod differs from the shipped
  original, and whether any shipped mod was edited in place.
- Releasing: bump `VERSION` and `__version__` in `bin/tether`, commit, `git tag vX.Y.Z && git push --tags`. The
  `release` workflow runs the tests, builds `tether-X.Y.Z.tar.gz` and its `.sha256` with `./release.sh`, and
  publishes them, which is what `get.sh` downloads.

## Your conversations

| What | Where it lives |
|---|---|
| Claude conversations | `~/.claude/projects/...` (plus `file-history/`, `session-env/`): Claude's own folder |
| Copilot conversations | `~/.copilot/session-state/...`: Copilot's own folder |
| tether's links (which terminal runs which session) | `~/.local/state/tether/spaces.json` |
| Backup of the linked conversations | `~/.local/share/tether/backups/`, or `$TETHER_BACKUP_DIR` |

tether never moves a conversation: the agents resume from their own folders, so centralising them would
break `--resume`. Instead `tether backup` keeps a copy, laid out like each agent's home, plus a
`manifest.json` saying which space each session belongs to. It is incremental, never deletes from the
backup, and creates the folder with owner-only permissions. With automatic backups on, launching an agent
starts one in the background at most every 6 hours.

- **Restore:** if a linked transcript has gone (Claude sweeps old ones, 30 days by default), `tether claude`
  copies it back from the backup before resuming. `tether restore` does the same for every linked session.
  It never overwrites a file the agent still has.
- **Move a space to another machine:** `tether export <space>` writes one `.tar.gz` (mode 0600) with the
  links and the conversations (from the agent, or from the backup if the agent has deleted them).
  On the other machine `tether import <file>` unpacks it as a closed space, then `tether open <name>` rebuilds
  it. Import only accepts files for the sessions listed in the bundle and never overwrites an existing one.
- A fork nobody has typed in yet has no transcript of its own (Claude writes one with the first message), so it is backed up through the session it was forked from; the manifest records `fork_of`, and starting the tab restores that session if it has gone.
- **The backup holds full conversations**, which can contain anything you pasted. It sits in a folder only you can
  read, and `tether backup` refuses to write into a git repository where the folder is not git-ignored. Do not
  commit it, and treat exports like the sensitive files they are.
- **Retention is a bonus, not the safeguard.** The installer can set `cleanupPeriodDays` in your
  `~/.claude/settings.json`, but an organisation's managed settings (a local managed file, or server-managed
  settings from the admin console) take precedence and cannot be overridden by you. The backup is the part you
  control.

## Not in this repo, on purpose

- `~/.local/state/tether/spaces.json` (the links): it holds space and session ids that only mean
  something on one machine. A new machine starts with none: bring a space across with `tether export` /
  `tether import`.
- Your conversation backups: they live in `~/.local/share/tether/backups`, outside any clone.
- `shared.json` except the layout choice: its other keys are space ids and colours, or a cache of
  detected agents that Herdr rebuilds.
- `gpui-kit.d.ts` and `jsconfig.json` in each mod: editor type aids that `herdr-app check` regenerates.

## Things to know

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
