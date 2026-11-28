# Developing tether

A clone is the source of truth for the files; `./install.sh` copies it onto your machine.

## Where things are

```
bin/tether         the command: a dozen lines that run the package
lib/tetherlib/     the code (standard library only), one module per area: see docs/how-it-works.md#the-code
herdr/             the Herdr side: mods/ (the popups and menus) and layouts/
skills/            the two agent skills: tether (the commands) and tether-context (looking up facts)
docs/              what people read (install, using, context, configuration, troubleshooting) and how it works
tests/             unit tests, one file per module (helpers.py is shared), plus the installer, docs and module-structure checks
scripts/           for maintainers: test.sh, gen-help.sh, herdr-drift.sh, release.sh
install.sh get.sh  the installer, and the bootstrap behind the one-line install (both stay in the root: users run them)
VERSION            the version, read by the release workflow and the installer (a test keeps it equal to core.py)
```

The full table, with what each file installs to, is in [docs/how-it-works.md](docs/how-it-works.md#what-is-in-this-repository).

## Day to day

- **Tests:** `python3 -m unittest discover -s tests` (no Herdr or Claude needed; the installer tests use a throwaway `HOME` and a fake
  `herdr-app`). `scripts/test.sh` also runs the unit tests with warnings as errors, and, when Herdr is installed, `herdr-app check` on every
  mod. In a test, patch the module that *defines* a name (`mock.patch.object(herdr, "herdr_ls")`).
- **Changing behaviour?** Update, in the same change: the README and the pages in `docs/`, `skills/tether/SKILL.md` (what agents read),
  `docs/how-it-works.md` and `docs/review.md`, then run `scripts/gen-help.sh` (regenerates `docs/tether-help.txt`).
  `tests/test_docs.py` fails when a command, an environment variable or a mod is missing from the docs.
- **Working on a popup?** `herdr-app do mod.open --name tether-settings` opens an installed mod (a running Herdr reloads a changed `main.js`
  the next time it is opened; a *new* mod folder needs a Herdr restart), `herdr-app shot file.png` saves the window and
  `herdr-app do overlay.close` closes it.
- **After upgrading Herdr:** `scripts/herdr-drift.sh` shows how each cloned mod differs from the Herdr original, and whether any shipped mod
  was edited in place (`--shipped` for only that).

## Releasing

Bump `VERSION` and `__version__` in `lib/tetherlib/core.py` (and the version in the README example), commit, then
`git tag vX.Y.Z && git push --tags`. The `release` workflow runs the tests, builds `tether-X.Y.Z.tar.gz` and its `.sha256` with
`scripts/release.sh`, and publishes them, which is what `get.sh` downloads.
