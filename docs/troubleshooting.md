# Troubleshooting

Start with **`tether doctor`**: it checks the install, Herdr, links and backups, and prints the fix for each
problem.

| Problem | Fix |
|---|---|
| `tether: command not found` | `~/.local/bin` is not on your `PATH` (see [Installing tether](install.md)). |
| The installer says Herdr is running | Quit Herdr (it rewrites `shared.json` on exit), run it again. |
| `$HERDR_DIR/mods does not exist` | Launch Herdr once so it creates its config folder, quit it, run the installer again. |
| The installer says a mod does not load | Your Herdr probably changed something the mods use. Nothing was changed. Run `herdr-app check herdr/mods/<name>` in a clone for the details and please open an issue with the Herdr version. |
| The sidebar did not change | Herdr reads its layout at start: restart it. Then check `shared.json` has `"layout": "tether"`. |
| A tab came back as a plain shell | Herdr does not re-run commands after a reboot. Type `tether` in the tab, or run `tether open <space>` once for the whole space. |
| "no transcript anywhere" in `tether doctor` | The session was never used, or Claude swept it before a backup existed. Harmless: it starts fresh. Turn on backups and a longer retention (see [Installing tether](install.md)). |
| An agent started outside tether is not linked | `tether adopt` links running agents; it also runs by itself while Herdr is open. |
| Something else | `tether --help`, `docs/how-it-works.md`, or open an issue with the output of `tether doctor`. |
