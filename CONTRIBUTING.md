# Developing tether

A clone is the source of truth for the files; `./install.sh` copies it onto your machine.

- The code is the `tetherlib` package in `lib/` (modules per area, listed in [docs/how-it-works.md](docs/how-it-works.md#the-code)); `bin/tether` is only its entry point. In a test, patch the module that defines a name.
- `python3 -m unittest discover -s tests` runs the tests (no Herdr or Claude needed, including the installer
  tests, which use a throwaway `HOME` and a fake `herdr-app`). `./test.sh` also runs `herdr-app check` on each mod
  when Herdr is installed.
- Changing behaviour? Update, in the same change: the README and the pages in `docs/`, `skill/SKILL.md` (what agents read),
  `docs/how-it-works.md` and `docs/review.md`, then run `./sync.sh meta` (regenerates `docs/tether-help.txt`).
  `tests/test_docs.py` fails when a command, an environment variable or a mod is missing from the docs.
- Working on a popup? `herdr-app do mod.open --name tether-settings` opens an installed mod (a running Herdr reloads a changed `main.js` the next
  time it is opened; a *new* mod folder needs a Herdr restart), `herdr-app shot file.png` saves the window, `herdr-app do overlay.close` closes it.
- Edited a file directly on a machine? `./sync.sh pull` copies it back (it refuses if the repo has uncommitted
  changes). After upgrading Herdr, `./sync.sh upstream` shows how each copied mod differs from the shipped
  original, and whether any shipped mod was edited in place.
- Releasing: bump `VERSION` and `__version__` in `lib/tetherlib/core.py`, commit, `git tag vX.Y.Z && git push --tags`. The
  `release` workflow runs the tests, builds `tether-X.Y.Z.tar.gz` and its `.sha256` with `./release.sh`, and
  publishes them, which is what `get.sh` downloads.
