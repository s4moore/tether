"""`tether doctor`. Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import link, TetherTestCase
from tetherlib import core, doctor, store


class Doctor(TetherTestCase):
    def test_reports_problems_and_never_crashes_without_herdr(self):
        self.write_state({"spOLD": {"name": "work", "terminals": {"t1": link()}}})
        with mock.patch.object(core, "find_tool", return_value=None), \
                mock.patch.object(store, "session_exists", return_value=False), \
                mock.patch.dict(os.environ, {"TETHER_BACKUP_DIR": os.path.join(self.dir, "none")}):
            results = doctor.doctor([])
        levels = {t: l for l, t, _ in results}
        self.assertEqual(levels["herdr-app not found"], "bad")
        self.assertEqual(levels["Herdr is not running"], "warn")
        self.assertTrue(any("no transcript anywhere" in t for _, t, _ in results))
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(doctor, "doctor", return_value=results):
                doctor.doctor_cmd([])
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
