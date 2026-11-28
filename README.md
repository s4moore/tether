# tether

Persistent Claude / Copilot sessions for [Herdr](https://herdr.dev) **spaces**. Each terminal in a space stays tied to its own agent
session, so after a restart (or a closed space) one command or one click puts every tab back in its conversation.

On top of that, tether can:

- **back up and restore** your conversations, so a session the agent swept after 30 days is not lost;
- **search** them by what was said, **fork** a space, **continue** a conversation in the other agent, and move a space to another machine;
- **remember facts** about your setup (where your repos live, how you deploy) so every agent can look them up instead of asking again;
- put all of it in the Herdr sidebar: a **+** for a new agent space, **Settings**, **Search**, **Backups** and a right-click menu.

This repo is the whole setup: the `tether` command, the Herdr mods and layout, two agent skills and an installer. tether is an independent
project and is not affiliated with Herdr or Anthropic. Some mods are modified copies of Herdr's own: see [THIRD-PARTY.md](THIRD-PARTY.md).

## Install

You need macOS, [Herdr](https://herdr.dev/download) (launched once, then quit), Python 3.9+ and the `claude` CLI (`copilot` is optional).
Details and the full list: [docs/install.md](docs/install.md).

```
curl -fsSL https://raw.githubusercontent.com/s4moore/tether/main/get.sh | sh
```

Quit Herdr first. The script downloads the release, **checks it against the published SHA-256**, and runs the installer. For a specific
version: `curl -fsSL .../get.sh | TETHER_VERSION=0.5.0 sh`.

**Rather read it first?** Piping a script into a shell asks you to trust it. Read [`get.sh`](get.sh) and `install.sh`, or:

```
git clone https://github.com/s4moore/tether && cd tether
./install.sh
```

**Make sure `~/.local/bin` is on your `PATH`** (Herdr restores tabs by typing `tether resume` into them):

```
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

Start Herdr, and in a space run `tether status`. `tether doctor` checks the whole setup and says how to fix each problem. The installer
only touches the files [listed here](docs/install.md#what-the-installer-changes), backs up what it replaces, and puts everything back if a
mod does not load. Upgrade: quit Herdr and run the same line again. Uninstall: `curl -fsSL .../get.sh | sh -s -- --uninstall`
(your links and backups are kept).

**Tested with** Herdr 0.1.16, macOS, Python 3.9, Claude Code 2.1.

## First steps

```
tether claude        start or resume this terminal's Claude session   (also: tether copilot)
tether               resume what this terminal had; otherwise a menu
tether list          your spaces, most recently used first
tether open <space>  restore a space after a restart
tether search <words>  find a conversation by what was said in it
tether backup        copy every linked conversation to the backup folder
tether doctor        check the install and say how to fix each problem
```

In the Herdr sidebar, press **+** for a new space with an agent session, the **magnifier** to search, the **archive** button for backups
and the **gear** for settings. Everything else is a right-click on a space or a tab. The full list of commands, buttons and menus is in
[docs/using.md](docs/using.md); `tether --help` has every option.

## Privacy and safety

- **Everything is local.** tether has no server and sends nothing anywhere itself.
- **Some features send conversation text to an agent, only when you use them.** *Search with AI* (`tether search --rank` / `--deep`) and
  *Learn from this space* run your own `claude` (or `copilot`) as you, with your account, on short digests or the text of conversations. Only
  what was said is sent, never tool output. **Background learning** does this without a button press, which is why it is off by default.
  Plain *Search words* is local. `tether read` prints conversation text to the terminal.
- **A small usage log stays on this computer.** `~/.local/state/tether/events.jsonl` records that a feature ran, how long it took, its outcome,
  the agent and model, and counts (for example facts proposed and accepted). Never what was said, a fact, a search word, a name or a path.
  It is never sent anywhere; `tether stats` shows it, `tether config set usage_log off` stops it, and deleting the file clears it. `tether stats --export` writes a
  totals-only summary you can read first and share if you choose; nothing is sent for you.
- **Backups and exports are plain copies of your conversations**, which can contain anything you pasted (secrets, customer data). They are
  unencrypted, in a folder only you can read; exports are mode 0600. Do not commit or share them casually.
- **Saved facts are plain text** in `~/.local/share/tether/context`. tether refuses facts that look like secrets, but do not store any.
  Background learning only proposes facts for you to accept, unless you turn on the optional auto-accept, which is limited to plain facts about
  topics you already have, with an undo.
- **`tether adopt` reads other processes' command lines and environments** (to find which session an agent started without tether is using),
  only for agent processes in Herdr terminals. Turn it off with `TETHER_NO_ADOPT=1`.
- The AI steps run the agent with **no tools** and without your claude.ai connectors (Slack, Gmail and so on).

## Documentation

| Page | What is in it |
|---|---|
| [Using tether](docs/using.md) | every command, the sidebar and menus, Search, Backups, Archive and Delete, and where your conversations live |
| [Context](docs/context.md) | the fact store: how agents look things up, and how facts are learned, reviewed and browsed |
| [Configuration](docs/configuration.md) | Settings, `tether config`, every setting and environment variable |
| [Installing](docs/install.md) | requirements, what the installer changes, upgrade, uninstall |
| [Troubleshooting](docs/troubleshooting.md) | common problems and their fixes |
| [How it works](docs/how-it-works.md) | design, contracts between the mods and the command, repository layout |
| [Review notes](docs/review.md) | design decisions and known limitations |
| [Developing](CONTRIBUTING.md) | tests, working on a popup, releasing |

## Licence

MIT, for tether. The copied Herdr mods keep their Apache-2.0 licence: see [LICENSE](LICENSE) and [THIRD-PARTY.md](THIRD-PARTY.md).
