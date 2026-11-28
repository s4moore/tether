# Installing tether

The short version is in the [README](../README.md#install). This page has the details.

## Requirements

| Needed | Why | Notes |
|---|---|---|
| macOS | what tether is tested on (see *Tested with*) | Linux is untested; tether uses `fcntl` and `ps`, so Windows is out. Import via the UI uses a macOS file chooser |
| [Herdr](https://herdr.dev/download) | spaces, `herdr-app`, `hc` | `curl -fsSL https://releases.herdr.dev/app/install.sh \| sh`. Launch it once (it creates its config folder), then quit it. The installer shows this command if Herdr is missing |
| Python 3.9+ | runs `tether` (standard library only) | the macOS system `python3` is fine; on a new Mac the first use prompts to install the Xcode Command Line Tools (`xcode-select --install`) |
| `claude` CLI | the agent | `copilot` is optional (only `tether copilot` needs it) |
| `curl`, `tar`, `bash` | the installer | already on macOS |

## Install

**One line** (installs the latest release):

```
curl -fsSL https://raw.githubusercontent.com/s4moore/tether/main/get.sh | sh
```

Quit Herdr first: it rewrites its `shared.json` when it quits, and the installer refuses to run while it is
open. The script downloads the release tarball, **checks it against the published SHA-256**, and runs the
installer from it. For a specific version: `curl -fsSL .../get.sh | TETHER_VERSION=0.3.0 sh`.

**Prefer to read it first?** Piping a script into a shell asks you to trust it. Either read `get.sh`
([source](../get.sh)) and `install.sh`, or do it by hand:

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

## What the installer changes

**What the installer changes** (nothing outside this list):

| Where | What |
|---|---|
| `~/.local/bin/tether` | the tool |
| `~/.claude/skills/tether/`, `~/.claude/skills/tether-context/`, links in `~/.agents/skills/` | the two skills, for Claude Code and Copilot CLI |
| `~/.config/herdr-app/mods/tether-*`, `layouts/tether.json` | the mods and the layout |
| `~/.config/herdr-app/shared.json` | sets `"layout": "tether"`; no other key is touched |
| `~/.local/state/tether/config.json` | `auto`: whether to back up in the background (asked; default no without a terminal) |
| `~/.claude/settings.json` | **only if you say yes** to "keep transcripts for 365 days": sets `cleanupPeriodDays` (an admin-managed setting would override it) |
| `~/.tether-backups/<time>/` | a copy of every file it replaced (the newest 5 runs are kept; identical files are not copied again) |

It checks every mod with `herdr-app check`; **if one does not load, it puts your previous mods, layout and
setting back** and tells you. It also warns if your Herdr is not the version tether was tested with. It asks
two optional questions on the terminal (never when there is none, so `curl | sh` in a script asks nothing;
answer ahead of time with `TETHER_AUTO_BACKUP=yes` and `CLAUDE_RETENTION_DAYS=365`). It is safe to re-run.

## Check it worked, upgrade, uninstall

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

Trouble? See [troubleshooting](troubleshooting.md).
