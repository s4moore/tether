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
| `context_agent` | `TETHER_CONTEXT_AGENT` | which agent does the learning, for *Learn from this space* and background learning alike: `claude` (default) or `copilot` (headless, no tools; its model ids are mapped from the model below) |
| `context_model` | `TETHER_CONTEXT_MODEL` | model for learning: *Learn from this space* (`tether context learn`) and background learning (default `haiku`; `--model` on the command overrides it once) |
| `usage_log` | `TETHER_USAGE_LOG` (`1`/`0`) | the local usage log (see below): on by default |
| `home_dir` | `TETHER_HOME_DIR` | folder where new agents start when you are in your home folder (`tether config home_dir`) |
| `search_model` | `TETHER_SEARCH_MODEL` | Claude model for the AI steps of `tether search` (default `sonnet`) |
| | `TETHER_STATE_DIR` | where links and `config.json` live (default `~/.local/state/tether`) |
| | `TETHER_NO_ADOPT=1` | never link agents that were started without tether |
| | `TETHER_NO_CD=1` | do not change to the folder the session was started in |
| | `TETHER_FORCE=1` | start, move or delete even if the session looks to be running |
| | `TETHER_DRY_RUN=1` | print the plan and write nothing |

`CLAUDE_CONFIG_DIR` and `COPILOT_HOME` are honoured if you moved the agents' own folders.

## The usage log

So that the features can be judged by what happens rather than by feel, tether appends one line per use of a feature to
`~/.local/state/tether/events.jsonl` (mode 0600; at about 1 MB it rotates to `events.jsonl.1`, replacing the older copy). A line looks like:

```
{"t":"2026-11-28T12:30:00+00:00","v":"0.4.0","feature":"context.learn","outcome":"ok","agent":"claude","model":"haiku","proposed":3,"similar":1,"seconds":6.2,"cost":0.09}
```

- **What is recorded:** the time, the tether version, the feature, the outcome, how long it took, the agent and model, counts (facts proposed,
  accepted, rejected, saved without review; search results; sessions backed up) and the cost where the agent reports one.
- **What it can tell you** (`tether stats`):
  - *Learning:* proposed, accepted, rejected and undone facts per agent and model, so you can compare Claude with Copilot and Haiku with Sonnet.
  - *Lookups:* how often `context find` found nothing, and how often it was followed by a look at a topic list.
  - *AI calls:* time and cost per agent and model, and why a call failed, as one label (`timeout`, `auth`, `limit`, `model`, `missing`, `other`),
    never the error message.
  - *Features used:* which commands ran (fork, delete, restore, export, import, open-session, doctor, ...), and any that were slow or failed.
    A command typed as a space name is logged as `open`, never by name.
  - *Background jobs:* when learning, backup and the store check last ran, and why a run did nothing (`cap`, `locked`, `none_ready`, `first_run`).
  - *The store now:* topics, facts, the biggest topic, the index length (the token cost of every lookup), facts older than a year, and how many
    proposals are waiting. These are counted when you run the command, not logged.
- **What is never recorded:** what was said, a fact, a search word, a topic or space name, an id or a path. The writer drops any field that is
  not a number, a boolean or a short token of letters, digits and `_.:+-`, so free text cannot get in by accident.
- **Where it goes:** nowhere. There is no upload. It exists so that you can read it, and so that you can choose to share a summary.
- **Reading it:** `tether stats [--days N] [--json]` prints a summary: runs and errors per feature, the average time and total cost of the
  AI calls, how many learned facts you accepted against rejected, and how often `context find` found nothing.
- **Sharing a summary:** `tether stats --export [-o file] [--days N] [-y]` prints, then (after you confirm) writes one small JSON file: the
  tether, Python, macOS, `claude`, `copilot` and `herdr-app` version numbers, the store figures above, and totals per feature. It has no event lines, no times finer
  than a day, and nothing that names a person, space, conversation, fact or path; the command refuses to write anything that is not a count, a
  boolean or a short token. Nothing is sent: if you want to share it, attach it to an issue
  (**Share a usage summary…** under *Diagnostics* in Settings shows the same text first and saves it where you choose; [the Usage summary template](https://github.com/s4moore/tether/issues/new?template=usage-summary.md)).
- **Turning it off or clearing it:** `tether config set usage_log off` (or `TETHER_USAGE_LOG=0`, or the switch under *Diagnostics* in Settings);
  delete the file to clear it.
