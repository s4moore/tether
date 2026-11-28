# Configuration

The gear in the spaces header opens **Settings**: a toggle or a row of choices for each default below, with a short explanation (and a
privacy note on background learning). A setting an environment variable overrides is shown as such and cannot be changed there. On the
command line, `tether config` lists every setting with its value and where it comes from (default, `config.json` or the environment),
`tether config set <key> <value>` validates and saves it (`set <key> default` resets), and `tether config --json` is what the popup reads.
Changes apply to the next agent you start or the next background run. The settings live in `~/.local/state/tether/config.json`.
Environment variables override them.

| Setting (`config.json`) | Environment variable | Meaning |
|---|---|---|
| `default_agent` | `TETHER_DEFAULT_AGENT` | `claude` or `copilot`: what the + button starts a new space with (default `claude`) |
| `backup_dir` | `TETHER_BACKUP_DIR` | where conversation backups go (default `~/.local/share/tether/backups`) |
| `auto` | `TETHER_AUTO_BACKUP` (`1`/`0`) | back up in the background when an agent starts, at most every 6 h |
| `context_dir` | `TETHER_CONTEXT_DIR` | where the context store lives (default `~/.local/share/tether/context`) |
| `context_auto` | `TETHER_CONTEXT_AUTO` (`1`/`0`) | background learning from conversations: off by default (see [Context](context.md)) |
| `context_auto_accept` | `TETHER_CONTEXT_AUTO_ACCEPT` (`1`/`0`) | background learning saves plain facts about topics you already have without review: off by default. Never a command, link, backtick, secret-looking text or new topic (those still wait for you). The review window lists what it added, each with Undo (`tether context recent`, `tether context undo --only N`) |
| `context_agent` | `TETHER_CONTEXT_AGENT` | which agent does the background learning: `claude` (default) or `copilot` (headless, no tools; its model ids are mapped from the model below) |
| `context_model` | `TETHER_CONTEXT_MODEL` | model for background learning (default `haiku`) |
| `home_dir` | `TETHER_HOME_DIR` | folder where new agents start when you are in your home folder (`tether config home_dir`) |
| `search_model` | `TETHER_SEARCH_MODEL` | Claude model for the AI steps of `tether search` (default `sonnet`) |
| | `TETHER_STATE_DIR` | where links and `config.json` live (default `~/.local/state/tether`) |
| | `TETHER_NO_ADOPT=1` | never link agents that were started without tether |
| | `TETHER_NO_CD=1` | do not change to the folder the session was started in |
| | `TETHER_FORCE=1` | start, move or delete even if the session looks to be running |
| | `TETHER_DRY_RUN=1` | print the plan and write nothing |

`CLAUDE_CONFIG_DIR` and `COPILOT_HOME` are honoured if you moved the agents' own folders.
