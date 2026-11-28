"""Settings, `tether config` and the start folder. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import TetherTestCase
from tetherlib import core, herdr, launch, settings


class Settings(TetherTestCase):
    """`tether config`: every default in config.json as a validated setting, with where its value comes from."""

    def setUp(self):
        super().setUp()
        for k in [k for k in os.environ if k.startswith("TETHER_") and k not in ("TETHER_STATE_DIR", "TETHER_NO_ADOPT")]:
            patcher = mock.patch.dict(os.environ)
            patcher.start()
            self.addCleanup(patcher.stop)
            os.environ.pop(k)
        patcher = mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": "", "TETHER_CONTEXT_DIR": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("TETHER_BACKUP_DIR", "TETHER_CONTEXT_DIR"):
            os.environ.pop(k)

    def cfg(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            settings.config_cmd(list(args))
        return out.getvalue()

    def rows(self):
        return {r["key"]: r for r in json.loads(self.cfg("--json"))}

    def test_every_setting_is_listed_with_its_default_and_where_the_value_comes_from(self):
        rows = self.rows()
        self.assertEqual([r["key"] for r in settings.SETTINGS], list(rows))
        self.assertEqual((rows["auto"]["value"], rows["auto"]["source"]), (False, "default"))
        self.assertEqual((rows["default_agent"]["value"], rows["context_model"]["value"], rows["search_model"]["value"]), ("claude", "haiku", "sonnet"))
        self.assertEqual(rows["context_agent"]["value"], "claude")
        self.assertFalse(rows["home_dir"]["editable"])
        self.assertTrue(rows["context_auto"]["privacy"])

    def test_set_validates_saves_and_resets(self):
        self.cfg("set", "auto", "on")
        self.cfg("set", "default_agent", "copilot")
        self.cfg("set", "context_model", "sonnet")
        rows = self.rows()
        self.assertEqual([(rows[k]["value"], rows[k]["source"]) for k in ("auto", "default_agent", "context_model")],
                         [(True, "config"), ("copilot", "config"), ("sonnet", "config")])
        self.assertEqual(settings.default_agent(), "copilot")
        self.assertEqual(self.cfg("get", "default_agent").strip(), "copilot")
        self.cfg("set", "default_agent", "default")
        self.assertEqual(self.rows()["default_agent"]["source"], "default")
        for bad in (("auto", "maybe"), ("default_agent", "pi"), ("nothing", "x"), ("home_dir", "/no/such/folder"), ("home_dir", os.path.expanduser("~"))):
            with self.assertRaises((core.TetherError, SystemExit), msg=str(bad)):
                self.cfg("set", *bad)

    def test_an_environment_variable_wins_and_is_reported_as_the_source(self):
        self.cfg("set", "default_agent", "copilot")
        with mock.patch.dict(os.environ, {"TETHER_DEFAULT_AGENT": "claude", "TETHER_AUTO_BACKUP": "1"}):
            rows = self.rows()
            self.assertEqual((rows["default_agent"]["value"], rows["default_agent"]["source"], rows["default_agent"]["env"]),
                             ("claude", "env", "TETHER_DEFAULT_AGENT"))
            self.assertEqual((rows["auto"]["value"], rows["auto"]["source"]), (True, "env"))
            self.assertIn("overrides", self.cfg("set", "default_agent", "copilot"))

    def test_the_older_home_dir_syntax_still_works_and_a_bad_default_agent_falls_back_to_claude(self):
        folder = os.path.join(self.dir, "start")
        os.makedirs(folder)
        self.cfg("home_dir", folder)
        self.assertEqual(self.rows()["home_dir"]["value"], folder)
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump({"default_agent": "bogus"}, f)
        self.assertEqual(settings.default_agent(), "claude")


class HomeDir(TetherTestCase):
    def dry(self, cwd, **env):
        where = herdr.Where("spLIVE", "work", "t1", "tab1", "claude")
        out = io.StringIO()
        with mock.patch.dict(os.environ, dict({"TETHER_DRY_RUN": "1"}, **env)), \
                mock.patch.object(herdr, "resolve_where", return_value=(where, None)), \
                mock.patch.object(core, "find_tool", return_value="/bin/claude"), \
                mock.patch.object(os, "getcwd", return_value=cwd), \
                contextlib.redirect_stdout(out):
            launch.launch("claude", [])
        return out.getvalue()

    def test_config_round_trip_and_refuses_home(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        with contextlib.redirect_stdout(io.StringIO()):
            settings.config_cmd(["home_dir", other])
        self.assertEqual(settings.home_dir_setting(), other)
        with self.assertRaises(SystemExit):
            settings.config_cmd(["home_dir", "~"])
        with contextlib.redirect_stdout(io.StringIO()):
            settings.config_cmd(["home_dir", "--unset"])
        self.assertIsNone(settings.home_dir_setting())
        with contextlib.redirect_stdout(io.StringIO()):
            settings.config_cmd(["home_dir", "--default"])
        fake = os.path.join(self.dir, "fakehome")
        with mock.patch.object(settings, "DEFAULT_HOME_DIR", fake):
            self.assertEqual(settings.home_dir_setting(), fake)
            self.assertTrue(os.path.isdir(fake), "created on first use")
        self.assertEqual(oct(os.stat(core.config_path()).st_mode & 0o777), "0o600")

    def test_new_session_started_in_home_goes_to_home_dir(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        out = self.dry(os.path.expanduser("~"), TETHER_HOME_DIR=other)
        self.assertIn(f"would run: cd {other} &&", out)

    def test_other_folders_and_unset_setting_are_left_alone(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        self.assertIn("would run: cd /tmp &&", self.dry("/tmp", TETHER_HOME_DIR=other))
        with open(core.config_path(), "w") as f:
            json.dump({"home_dir": None}, f)
        self.assertIn(f"would run: cd {os.path.expanduser('~')} &&", self.dry(os.path.expanduser("~")))


if __name__ == "__main__":
    unittest.main()
