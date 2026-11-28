# Using tether

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
tether continue [claude|copilot]   continue this terminal's conversation with the other agent, in a **new tab** (older name: `tether swap`; also the tab's right-click menu, **Continue in Copilot** / **Continue in Claude**, named for the agent it will open): tether writes a new session in that agent's own store from the transcript (what was said; tool calls and their results become short notes inside the assistant's turns; images and attachments do not come across), opens a tab and starts it there. The original tab and its session are untouched and the agent there can keep running (`claude --resume <id>` still opens the old session). A very long conversation keeps its newest part (the first messages are replaced by a one-line note)
tether context add <topic> "fact" | find <words> | list [topic] | check [--fix] | learn <space> | proposals | accept | reject | harvest   a small knowledge base every agent can look up (see [Context](context.md)): one-line dated facts in topic files behind a short index, searched with no model call
tether read <space> [--last N]   print what a space's conversation said (last N messages, default 12; `--commands` adds the shell commands, `--full` stops the 1,500-character cut). To bring context from another thread into this one; the skill tells agents to use it when you say "the other chat". Never prints tool output
tether search <words or a question>   find a conversation by what was said in it. Free keyword search over open and closed spaces; `--rank` (Claude ranks the best candidates from short digests, one call) and `--deep` (reads the top few in full) cost tokens, and the output says roughly how many first; `--scope open,closed,deleted` (deleted: the backup of deleted spaces). The popup behind the magnifier in the spaces header does the same
tether adopt             link agents running in Herdr tabs that were started without tether (plain `claude` / `copilot`); their sessions are read from the running processes. it also runs by itself every 30 s while Herdr is open, when you switch space, before every backup, and in `fork`; `tether doctor` warns about unlinked agents
tether fork <space>      copy a space into "<name> (fork)": every linked session is copied (Claude: --fork-session; Copilot: its session folder is copied under a new id), so the two continue separately. Also in the space's right-click menu. The original is backed up first and never touched
tether undelete [space]  bring back a space you deleted as a closed space (Delete keeps a record in the backup; in the Backups popup, *Restore & open* on its row does this and opens it)
tether touch <space-id>   record that a space was just used (the spaces list calls this when you switch space; it also links unlinked agents in that space)
tether doctor            check the install, Herdr, links and backups; each problem comes with its fix
tether config home_dir <folder>   new agents started from your home folder start in this folder instead (default `~/.local/share/tether/home`, created on first use), so Claude stops asking "trust this folder?" (it remembers every folder except home). `--unset` turns it off, `--default` resets it. Sessions already started in home still resume there
```

Full reference: `tether --help` or [`docs/tether-help.txt`](tether-help.txt).

## In the Herdr sidebar

| Where | What |
|---|---|
| **+** (header) | a new space with an agent session (Claude, or Copilot: a [setting](configuration.md)). Shift-click: a plain space |
| **Gear** | **Settings**: the defaults in `config.json` as switches and choices |
| **Magnifier** | **Search** (below) |
| **Archive button** | **Backups** (below) |
| **Clock** | closed spaces; click one to reopen it. One whose transcripts were deleted shows "restored from the backup" if a backup has them |
| Right-click a space | Rename, Fork, **Back up now**, **Export…**, **Learn from this space**, **Review learned facts**, **Browse context**, Close, **Archive**, **Delete…** |
| Right-click a tab | **Continue in Copilot** / **Continue in Claude**: the same conversation, in the other agent, in a new tab |

### Search

Type words or describe what you remember.

- **Search words** is a free, local match of your exact words over the conversations of open and closed spaces (and, if ticked, the backup of
  archived ones). A possessive ("Acme's") counts as the word; a word of up to three letters ("US") matches only as a whole word.
- **Search with AI** does the same word match, then sends short digests of the best candidates to one Claude call. You can press it without
  searching words first, and after a words search it shows its token estimate. It only runs when you press it. Only what was said is sent,
  never tool output. If no word matches it looks at your most recent conversations.
- Click a result to expand its matching text, with the matched words highlighted. **Open** puts the conversation back; an archived space
  is restored first. There is no whole-conversation read in the popup, the costliest step (`tether search --deep` stays on the command line).

### Backups

A status line (sessions backed up, size, last backup), **Back up all**, **Import…** (macOS file chooser), then every backed-up session by
space, tab, the first thing you asked, agent, age and size.

- **Open** (or click the row) puts the session in a new tab of its space, restoring the transcript first if the agent lost it.
- A row for an archived space has **Restore & open**: it brings the whole space back.
- The trash icon asks "Are you sure?" on that row, then deletes just that session's backup copy (a session the agent still has is copied
  again by the next backup). On an archived space's row it asks "Delete permanently?" and also removes the agent's own copy: the only place a
  conversation can be wiped for good.
- The folder and a **Show in Finder** link are at the bottom.

### Archive and Delete

**Archive** takes a backup, unlinks the space and leaves the agents' own files: recoverable from the Backups popup (`tether delete`; the CLI
keeps its older name). **Delete…** is permanent: it asks first, then removes the space, its conversations and their backup copies
(`tether delete --sessions`). The dialog says how many conversations are in open tabs and how many are left from tabs you closed.

### Export

**Export…** writes `tether-export-<space>-<date>.tar.gz` (mode 0600) to a folder you choose and reveals it in Finder. Import through the UI is
macOS-only; `tether import <file>` works anywhere.

## Your conversations and backups

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
