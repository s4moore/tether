"""install.sh and get.sh, run against a throwaway HOME with a fake `herdr-app`: install, re-run, uninstall,
a broken shared.json, a mod that does not load (everything is put back), and running from a pipe."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

FAKE_HERDR = """#!/bin/sh
case "$1" in
  --version) echo "herdr-app ${FAKE_HERDR_VERSION:-0.1.16}" ;;
  check) case "$2" in *"${FAKE_HERDR_FAIL:-NONE}") echo "error: mod did not load"; exit 1 ;; esac
         echo "check passed: $2" ;;
esac
"""


class Sandbox(unittest.TestCase):
    """A throwaway HOME with a fake herdr-app."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tether-install-test-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.fakebin = os.path.join(self.home, "fakebin")
        os.makedirs(self.fakebin)
        with open(os.path.join(self.fakebin, "herdr-app"), "w") as f:
            f.write(FAKE_HERDR)
        os.chmod(os.path.join(self.fakebin, "herdr-app"), 0o755)
        self.herdr = os.path.join(self.home, ".config", "herdr-app")
        os.makedirs(os.path.join(self.herdr, "mods"))
        self.bin = os.path.join(self.home, ".local", "bin")

    def env(self, **extra):
        env = {"HOME": self.home, "PATH": f"{self.fakebin}:{os.path.dirname(sys.executable)}:/usr/bin:/bin:/usr/sbin:/sbin",
               "HERDR_ALLOW_RUNNING": "1", "TETHER_SRC": ROOT}
        env.update(extra)
        return env

    def run_install(self, *args, stdin=None, **extra):
        # start_new_session: no controlling terminal, so /dev/tty is unavailable and the questions default to no
        return subprocess.run(["bash", os.path.join(ROOT, "install.sh"), *args] if stdin is None else ["bash", "-s", "--", *args],
                              input=stdin, capture_output=True, text=True, env=self.env(**extra), start_new_session=True, timeout=120)

    def shared(self):
        with open(os.path.join(self.herdr, "shared.json")) as f:
            return json.load(f)


class Installer(Sandbox):
    def test_version_files_agree(self):
        with open(os.path.join(ROOT, "VERSION")) as f:
            version = f.read().strip()
        out = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "tether"), "--version"], capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), f"tether {version}")

    def test_install_puts_everything_in_place_and_asks_nothing_without_a_terminal(self):
        with open(os.path.join(self.herdr, "shared.json"), "w") as f:
            json.dump({"theme": "dark"}, f)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(os.access(os.path.join(self.bin, "tether"), os.X_OK))
        self.assertTrue(os.path.exists(os.path.join(self.home, ".claude", "skills", "tether", "SKILL.md")))
        self.assertTrue(os.path.islink(os.path.join(self.home, ".agents", "skills", "tether")))
        self.assertTrue(os.path.exists(os.path.join(self.home, ".claude", "skills", "tether-context", "SKILL.md")))
        self.assertTrue(os.path.islink(os.path.join(self.home, ".agents", "skills", "tether-context")))
        for mod in os.listdir(os.path.join(ROOT, "herdr", "mods")):
            self.assertTrue(os.path.exists(os.path.join(self.herdr, "mods", mod, "main.js")), mod)
        self.assertEqual(self.shared(), {"theme": "dark", "layout": "tether"}, "only the layout key may change")
        with open(os.path.join(self.home, ".local", "state", "tether", "config.json")) as f:
            self.assertEqual(json.load(f), {"auto": False}, "no terminal: nothing opted in, and no backup folder inside the source")
        self.assertFalse(os.path.exists(os.path.join(ROOT, "backups")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "settings.json")), "retention is only changed when asked")

    def test_the_installed_tool_runs_from_the_installed_package(self):
        self.assertEqual(self.run_install().returncode, 0)
        lib = os.path.join(self.home, ".local", "lib", "tetherlib")
        self.assertTrue(os.path.isfile(os.path.join(lib, "cli.py")))
        self.assertEqual(sorted(f for f in os.listdir(lib) if f.endswith(".py")),
                         sorted(f for f in os.listdir(os.path.join(ROOT, "lib", "tetherlib")) if f.endswith(".py")))
        # run it from where it was installed, with nothing of the repo on the path
        r = subprocess.run([os.path.join(self.bin, "tether"), "--version"], capture_output=True, text=True, cwd=self.home, env=self.env())
        with open(os.path.join(ROOT, "VERSION")) as f:
            self.assertEqual(r.stdout.strip(), "tether " + f.read().strip(), r.stderr)

    def test_an_upgrade_replaces_the_package_whole_and_drops_modules_that_no_longer_exist(self):
        self.assertEqual(self.run_install().returncode, 0)
        stale = os.path.join(self.home, ".local", "lib", "tetherlib", "old_module.py")
        with open(stale, "w") as f:
            f.write("x = 1\n")
        self.assertEqual(self.run_install().returncode, 0)
        self.assertFalse(os.path.exists(stale))

    def test_rerun_is_safe_and_does_not_copy_identical_files_again(self):
        self.assertEqual(self.run_install().returncode, 0)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        saved = os.path.join(self.home, ".tether-backups")
        for dirpath, _, files in os.walk(saved):
            for name in files:
                self.assertNotIn(name, ("tether", "tetherlib", "SKILL.md", "tether.json"), "identical files must not be backed up again")

    def test_works_when_the_script_comes_from_a_pipe(self):
        with open(os.path.join(ROOT, "install.sh")) as f:
            r = self.run_install(stdin=f.read())
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.bin, "tether")))

    def test_a_broken_shared_json_changes_nothing(self):
        with open(os.path.join(self.herdr, "shared.json"), "w") as f:
            f.write("{not json")
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("shared.json is not valid JSON", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "tether")), "must fail before installing anything")

    def test_a_mod_that_does_not_load_puts_the_previous_state_back(self):
        with open(os.path.join(self.herdr, "shared.json"), "w") as f:
            json.dump({"layout": "main"}, f)
        r = self.run_install(FAKE_HERDR_FAIL="tether-search")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("do not load", r.stderr)
        self.assertEqual(self.shared(), {"layout": "main"})
        self.assertFalse(os.path.exists(os.path.join(self.herdr, "layouts", "tether.json")))
        self.assertFalse(os.path.exists(os.path.join(self.herdr, "mods", "tether-search")))

    def test_missing_herdr_is_explained(self):
        shutil.rmtree(os.path.join(self.herdr, "mods"))
        os.remove(os.path.join(self.fakebin, "herdr-app"))
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("releases.herdr.dev", r.stdout + r.stderr)

    def test_an_older_herdr_gets_a_warning(self):
        r = self.run_install(FAKE_HERDR_VERSION="0.1.2")
        self.assertIn("older than", r.stderr)

    def test_uninstall_removes_what_was_installed_and_keeps_the_data(self):
        self.assertEqual(self.run_install().returncode, 0)
        os.makedirs(os.path.join(self.home, ".local", "share", "tether", "backups"))
        r = self.run_install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "tether")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local", "lib", "tetherlib")), "the package goes with the entry point")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "skills", "tether")))
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".agents", "skills", "tether")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "skills", "tether-context")))
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".agents", "skills", "tether-context")))
        self.assertEqual([m for m in os.listdir(os.path.join(self.herdr, "mods")) if m.startswith("tether-")], [])
        self.assertNotIn("layout", self.shared())
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".local", "share", "tether", "backups")), "backups are never removed")
        self.assertTrue(os.path.exists(os.path.join(self.home, ".local", "state", "tether", "config.json")))


class Bootstrap(Sandbox):
    """get.sh against a local "release" served from a folder (file://)."""

    def release(self, tamper=False):
        rel = os.path.join(self.home, "release")
        stage = os.path.join(self.home, "stage")
        os.makedirs(rel)
        shutil.copytree(ROOT, os.path.join(stage, "tether-0.1.0"),
                        ignore=shutil.ignore_patterns(".git", "backups", "dist", "__pycache__", "gpui-kit.d.ts", "jsconfig.json"))
        tarball = os.path.join(rel, "tether-0.1.0.tar.gz")
        subprocess.run(["tar", "-czf", tarball, "-C", stage, "tether-0.1.0"], check=True)
        out = subprocess.run(["shasum", "-a", "256", tarball], capture_output=True, text=True, check=True).stdout.split()[0]
        with open(tarball + ".sha256", "w") as f:
            f.write(("0" * 64 if tamper else out) + "  tether-0.1.0.tar.gz\n")
        return rel

    def run_get(self, rel, *args):
        return subprocess.run(["sh", os.path.join(ROOT, "get.sh"), *args], capture_output=True, text=True, start_new_session=True,
                              env=self.env(TETHER_VERSION="0.1.0", TETHER_BASE_URL="file://" + rel, TETHER_SRC=""), timeout=120)

    def test_get_sh_downloads_verifies_and_installs(self):
        r = self.run_get(self.release())
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("checking the checksum", r.stdout)
        self.assertTrue(os.access(os.path.join(self.bin, "tether"), os.X_OK))
        out = subprocess.run([os.path.join(self.bin, "tether"), "--version"], capture_output=True, text=True).stdout
        with open(os.path.join(ROOT, "VERSION")) as f:
            self.assertEqual(out.strip(), "tether " + f.read().strip())

    def test_get_sh_installs_nothing_when_the_checksum_is_wrong(self):
        r = self.run_get(self.release(tamper=True))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checksum mismatch", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "tether")))

    def test_get_sh_passes_options_to_the_installer(self):
        rel = self.release()
        self.assertEqual(self.run_get(rel).returncode, 0)
        r = self.run_get(rel, "--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "tether")))

    def test_get_sh_is_posix_and_names_the_published_repo(self):
        with open(os.path.join(ROOT, "get.sh")) as f:
            text = f.read()
        self.assertIn('REPO="s4moore/tether"', text)


if __name__ == "__main__":
    unittest.main()
