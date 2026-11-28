#!/usr/bin/env python3
"""tether: persistent Claude / Copilot sessions for herdr-app spaces.

  Inside a herdr terminal
    tether claude  [agent args...]   start or resume THIS terminal's Claude session
    tether copilot [agent args...]   start or resume THIS terminal's Copilot session
    tether                           resume whatever this terminal had (agent-agnostic);
                                     if it has none: a menu (start here / new space / open space)
    tether resume                    the same, without the menu (what `tether open` types into idle tabs)
    tether link claude|copilot [id]  link an existing session to this terminal (no id: the one you are in)
    tether status                    what is linked in this space
    tether reset [claude|copilot]    unlink this terminal (transcript is kept; id printed)

  Anywhere (also outside herdr)
    tether list [text] [--all]       spaces, with when you last used them (--all: also the
                                     `--resume <id>` command for every closed session)
    tether list --json               the same, machine-readable (used by the herdr spaces mod)
    tether touch <space-id>          record that a space was just switched to (sort order)
    tether open --id <space-id> [-y] open by exact id (no name matching, no menu)
    tether open [space] [-y]         resume every linked terminal of a space, by typing
                                     `tether resume` into the idle ones (asks first). A
                                     CLOSED space is recreated: a tab per linked terminal.
                                     No name: pick from a list.
    tether  (outside herdr)          menu: + new space, or open an existing space
    tether delete [space] [--close] [--sessions] [-y]
                                     delete a space: unlink it. A backup is taken first and Claude's / Copilot's
                                     own files stay, so `tether undelete` (or the Backups popup) brings it
                                     back. --close first closes the space in Herdr if it is open and waits
                                     for its agents to exit. --sessions makes it permanent: also deletes the
                                     sessions' transcripts AND their backup copies. Asks first unless -y.
                                     --id <space-id> picks by exact id.
    tether <space name>              same as `tether open <space name>`
    tether search <words or a question> [--scope open,closed,deleted] [--rank] [--deep] [--json]
                                     find a conversation by what was said in it. Default: a free local
                                     keyword search over open and closed spaces. --rank asks Claude to
                                     rank the best candidates from short digests (cheap, one call);
                                     --deep reads the full text of the top few (--sessions id,id for
                                     chosen ones) with one Claude call each. `deleted` also searches
                                     the backup of deleted spaces. Prints an estimate of the token cost.
    tether continue [claude|copilot] [--tab <tab-id>]   (alias: tether swap)
                                     continue this terminal's conversation with the other agent, in a NEW tab:
                                     a new session is written in its store from the transcript (text, with
                                     tool calls and results as short notes), linked to the new tab, which
                                     starts it. The original tab and session are untouched (the agent there
                                     may keep running). --tab (the tab menu's "Continue in ...") names the tab.
    tether context add <topic> "fact" [--about "what a new topic covers"] [--source "where from"]
    tether context find <words> | list [topic] | check [--fix] | learn <space> | proposals | accept [--only 1,3] | reject
                                     a small knowledge base every agent can look up (default
                                     ~/.local/share/tether/context): index.md has one line per topic, each
                                     topic file has one-line dated facts that can link to other topics with
                                     [[topic]]. `find` searches it all with no model call. `check` reports
                                     dead links, topics missing from the index, duplicates, long files and
                                     stale facts (--fix repairs the safe ones after a backup); it also runs by
                                     itself in the background, at most once a day, when an agent is launched.
                                     `learn` proposes facts from a conversation (one model call) into a queue
                                     (`proposals`); nothing is saved until `accept`. With "context_auto" on, a
                                     background job (at most every 6 h) reads conversations that have grown and
                                     queues proposals the same way (haiku, at most 3 conversations and $0.50 a
                                     day; enabling it starts from now). "context_auto_accept" lets that job save plain facts about
                                     topics you already have without review (never a command, link or new
                                     topic); `recent` lists them and `undo --only N|--all` removes them.
                                     `list --json` gives every topic with its facts and line numbers, and
                                     `remove <topic> --line N` deletes one fact (the Context window uses both).
                                     Facts are plain text: never store secrets.
    tether read <space> [--last N] [--commands] [--full] [--id X | --session S] [--json]
                                     print what a space's conversation said (the last N messages, default
                                     12, each cut at 1,500 characters unless --full): to bring context from
                                     another thread into this one. Never tool output. Find the space with
                                     `tether list` or `tether search`.
    tether adopt [--id <space-id>] [--json]
                                     link agents that are running in herdr terminals but were started
                                     without tether (plain `claude` / `copilot`): their sessions are
                                     read from the running processes. `fork` does this first.
    tether fork [space] [--id X] [-y]
                                     copy a space into a new one named "<name> (fork)": every linked
                                     session is copied, so the two continue separately from the point
                                     you forked. The original is untouched (and backed up first).
    tether undelete [space] [--list] [--json]
                                     bring back a space you deleted (unlinked), from the backup's record
                                     of it; it shows up as a closed space again. --id <space-id> picks
                                     by exact id. Deleting with "Unlink only" takes a backup first.
    tether open-session <id> [--agent claude|copilot]
                                     put one session back in a new tab of the space that linked it
                                     (restoring its transcript from the backup if it has gone)

  Setup and health
    tether --version                 print the version of tether
    tether doctor [--json]           check the install, Herdr, links and backups; says how to fix each
                                     problem. Exit status 1 if something needs attention.
    tether stats [--days N] [--json | --export [--json] [-o file|folder] [-y]]
                                     what the local usage log says: how often each feature ran, how long it took,
                                     what it cost, how many proposed facts you accepted. The log holds counts, timings
                                     and outcomes only, never what was said, and stays on this computer
                                     (~/.local/state/tether/events.jsonl); `tether config set usage_log off` stops it.
                                     --export writes a summary you can share: totals and versions only, shown to
                                     you in full first (with --json it only prints it), never sent anywhere.
    tether config [--json | get <key> | set <key> <value|default> | home_dir [path|--unset|--default]]
                                     show or change settings (the Herdr settings popup, the gear in the
                                     spaces header, does the same): `tether config` lists each with its value
                                     and where it comes from (default, config.json or an environment
                                     variable, which wins); `set` validates and saves, `set <key> default`
                                     resets; `--json` is what the popup reads. home_dir: a folder to start
                                     NEW agents in when you are in your home folder (default
                                     ~/.local/share/tether/home; --unset turns it off). (Claude asks "trust
                                     this folder?" for home on every start, but remembers other folders)

  Backups (conversations live in each agent's own folder; Claude deletes old ones)
    tether backup [space] [--quiet]  copy the transcripts of every linked session into the backup
                                     folder (incremental; never deletes from it). --id <space-id> picks
                                     one space exactly; --json prints the result; --status [--json]
                                     shows what the backup holds instead of copying; --list [--json]
                                     lists every backed-up session by space, tab and first message.
    tether backup --forget --session <id> [--agent claude|copilot] [--everything]
                                     delete one session's copy from the backup (any session; a session the
                                     agent still has is copied again by the next backup). --everything
                                     also deletes the agent's own copy: permanent, refuses a running session.
    tether backup --forget --id <space-id>
                                     remove a DELETED space from the backup. Refuses a space tether still links; never touches Claude's
                                     or Copilot's own files.
    tether restore [space | --id X]  copy missing transcripts back from the backup folder. `tether claude`
                                     does this by itself when a linked transcript has gone.
                                     --session <id> [--agent claude|copilot] restores that one session
                                     (even if no space links it any more).
    tether export <space> [-o file]  bundle a space's links + transcripts into one .tar.gz (private, 0600).
                                     -o may be a folder; --id <space-id>; --json prints the file's path.
    tether import <file> [--name N]  restore such a bundle as a closed space; then `tether open <name>`

How it works
  * A link belongs to a terminal: space id + terminal address (t1, t5, ...) + agent + session.
    The space id is stable across renames and restarts, so the name is only a label. After a
    restart herdr keeps tabs and gives each lost terminal a plain shell with the same address,
    which is what lets `tether open` put every terminal back.
  * First run in a terminal: a new UUID, `--session-id <uuid>`. Later: `--resume <uuid>` from
    the folder the session was started in.
  * Outside herdr, or if the space cannot be resolved, a plain agent is started.
    This script never stops you from running the agent.

Environment
  TETHER_STATE_DIR   where links are stored (default ~/.local/state/tether)
  TETHER_HOME_DIR    overrides "home_dir" in config.json (see `tether config`)
  TETHER_SEARCH_MODEL  model for the AI steps of `tether search` (default: "search_model" in
                     config.json, else sonnet)
  TETHER_CONTEXT_DIR where the context store lives (default: "context_dir" in config.json, else
                     ~/.local/share/tether/context)
  TETHER_CONTEXT_AUTO 1/0: background learning on or off (default: "context_auto" in config.json, off)
  TETHER_USAGE_LOG     1/0: the local usage log on or off (default: "usage_log" in config.json, on)
  TETHER_CONTEXT_AUTO_ACCEPT  1 lets background learning save plain facts about existing topics without review (undo: tether context undo)
  TETHER_CONTEXT_AGENT agent for background learning: claude or copilot (default: "context_agent" in config.json, else claude)
  TETHER_CONTEXT_MODEL model for learning, both `context learn` and background learning (default: "context_model" in config.json, else haiku)
  TETHER_DEFAULT_AGENT claude|copilot: the agent the + button starts a new space with (default: "default_agent"
                     in config.json, else claude)
  TETHER_NO_ADOPT=1  never link agents started without tether (see `tether adopt`)
  TETHER_NO_CD=1     do not change to the folder the session was started in
  TETHER_FORCE=1     start even if this session looks to be running already
  TETHER_DRY_RUN=1   print what would run; write nothing, start nothing
  TETHER_BACKUP_DIR  backup folder (default: "backup_dir" in <state dir>/config.json, else
                     ~/.local/share/tether/backups). If it is inside a git repo it must be git-ignored.
  TETHER_AUTO_BACKUP 1/0: back up in the background when an agent is launched (at most every 6 h);
                     default: "auto" in config.json
"""

import sys
import time

from tetherlib.core import AGENTS, TetherError

from tetherlib import backups, context, continue_, core, doctor, launch, reading, search, settings, store, usage, workspace


def main(argv):
    if not argv:
        return workspace.menu()
    cmd, rest = argv[0], argv[1:]
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
    elif cmd in ("--version", "-V", "version"):
        print(f"tether {core.__version__}")
    elif cmd in AGENTS:
        launch.launch(cmd, rest)
    elif cmd == "resume":
        launch.resume_here()
    elif cmd == "link":
        if not rest or rest[0] not in AGENTS or len(rest) > 2:
            sys.exit("usage: tether link claude|copilot [session-id]")
        launch.link(rest[0], rest[1] if len(rest) == 2 else None)
    elif cmd == "status":
        launch.status()
    elif cmd == "list":
        workspace.list_all(rest)
    elif cmd == "touch":
        if len(rest) != 1:
            sys.exit("usage: tether touch <space-id>")
        launch.touch(rest[0])
    elif cmd == "open":
        yes = "-y" in rest or "--yes" in rest
        words = [a for a in rest if a not in ("-y", "--yes")]
        if "--id" in words:  # exact space id: no name matching, no menu (used by the herdr mod)
            i = words.index("--id")
            if i + 1 >= len(words):
                sys.exit("usage: tether open --id <space-id> [-y]")
            sid = words[i + 1]
            with store.State() as st:
                if sid not in st.data:
                    sys.exit(f"tether: no linked space with id {sid}")
            workspace.open_space(None, yes, sid=sid)
        else:
            workspace.open_space(" ".join(words), yes)
    elif cmd == "delete":
        workspace.delete_space(rest)
    elif cmd == "search":
        search.search_cmd(rest)
    elif cmd == "read":
        reading.read_cmd(rest)
    elif cmd == "context":
        context.context_cmd(rest)
    elif cmd == "continue":
        continue_.continue_cmd(rest)
    elif cmd == "swap":  # the first name of `continue`
        continue_.continue_cmd(rest)
    elif cmd == "adopt":
        store.adopt_cmd(rest)
    elif cmd == "fork":
        workspace.fork_space(rest)
    elif cmd == "undelete":
        backups.undelete_cmd(rest)
    elif cmd == "open-session":
        workspace.open_session(rest)
    elif cmd == "doctor":
        doctor.doctor_cmd(rest)
    elif cmd == "stats":
        usage.stats_cmd(rest)
    elif cmd == "config":
        settings.config_cmd(rest)
    elif cmd == "backup":
        backups.backup_cmd(rest)
    elif cmd == "restore":
        backups.restore_cmd(rest)
    elif cmd == "export":
        backups.export_space(rest)
    elif cmd == "import":
        backups.import_space(rest)
    elif cmd == "reset":
        if rest and rest[0] not in AGENTS:
            sys.exit("usage: tether reset [claude|copilot]")
        launch.reset(rest[0] if rest else None)
    else:
        workspace.open_by_name_or_fail(" ".join(argv))


# Commands whose use is worth knowing (which features people use), as a fixed list: what a person typed as a space name is never logged.
LOGGED_COMMANDS = {"fork", "delete", "undelete", "restore", "export", "import", "open-session", "open", "read", "doctor", "link",
                   "reset", "continue", "swap", "search", "context", "backup", "adopt", "touch", "stats", "claude", "copilot"}
SLOW_SECONDS = 1.5   # any logged command slower than this is flagged: the commands that run all the time (touch, adopt) must stay quick


def run():
    """What bin/tether does: run main() on the command line, turning errors into a message and an exit status. A few commands also leave a
    line in the usage log: that they ran, how long they took, whether they worked (never their arguments)."""
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "menu"
    t0, outcome = time.time(), "ok"
    try:
        main(argv)
    except TetherError as e:
        outcome = "error"
        sys.exit(f"tether: {e}")
    except KeyboardInterrupt:
        outcome = "interrupted"
        sys.exit(130)
    except SystemExit as e:
        outcome = "ok" if e.code in (None, 0) else "error"
        raise
    finally:
        seconds = usage.since(t0)
        # anything that is not a command is a space name (the bare `tether <space>` form): logged as "open", never by name
        name = cmd if cmd in LOGGED_COMMANDS or cmd in core.COMMANDS or cmd in ("menu", "adopt", "--version", "-V", "version") else "open"
        if name in LOGGED_COMMANDS:
            noisy = name in ("touch", "adopt", "claude", "copilot", "search", "context", "backup", "continue", "swap", "stats")
            # the commands that have their own detailed event, or run constantly, are logged only when they are slow or fail
            if not noisy or seconds > SLOW_SECONDS or outcome != "ok":
                usage.record("command", outcome, name=name, seconds=seconds, slow=seconds > SLOW_SECONDS,
                             permanent=("--sessions" in argv) if name == "delete" else None)
