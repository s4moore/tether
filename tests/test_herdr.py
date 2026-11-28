"""Finding where this terminal is, and whether this Herdr offers what tether uses. Run: python3 -m unittest discover -s tests"""
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts lib/ on the path)
from helpers import link, SESSION2, space, TetherTestCase
from tetherlib import core, doctor, herdr, workspace


class FindingTerminals(TetherTestCase):
    def test_exact_then_machine_prefixed(self):
        spaces = [space("t1", "box/t5")]
        self.assertEqual(herdr.find_terminal(spaces, "t1")[2]["terminal"], "t1")
        self.assertEqual(herdr.find_terminal(spaces, "t5")[2]["terminal"], "box/t5")
        self.assertIsNone(herdr.find_terminal(spaces, "t9"))

    def test_same_address_in_two_spaces_is_refused(self):
        a, b = space("m1/t1"), dict(space("m2/t1"), id="spOTHER")
        with self.assertRaises(core.TetherError):
            herdr.find_terminal([a, b], "t1")

    def test_plan_space_says_why_a_terminal_is_skipped(self):
        sp = {"id": "spLIVE", "name": "work", "tabs": [
            {"id": "tab1", "name": "a", "terminals": [{"terminal": "t1"}]},
            {"id": "tab2", "name": "b", "terminals": [{"terminal": "t2", "app": "claude"}]},
            {"id": "tab9", "name": "c", "terminals": [{"terminal": "t3"}]},
        ]}
        data = {"terminals": {"t1": link(tab="tab1"), "t2": link(SESSION2, tab="tab2"),
                              "t3": link(tab="tab3"), "t4": link(tab="tab4")}}
        _, _, _, plan, skipped = workspace.plan_space(data, sp)
        self.assertEqual([p[0] for p in plan], ["t1"])
        reasons = {addr: why for addr, _, _, why in skipped}
        self.assertIn("already running", reasons["t2"])
        self.assertIn("different tab", reasons["t3"])
        self.assertIn("no longer exists", reasons["t4"])



COMMANDS = [{"name": "space.new", "args": [{"name": n} for n in ("name", "folder", "tab", "argv", "task", "machine", "focus")]},
            {"name": "tab.new", "args": [{"name": n} for n in ("space", "folder", "name")]},
            {"name": "space.close", "args": [{"name": n} for n in ("id", "confirmed")]}]


class HerdrContract(TetherTestCase):
    """Compatibility is asked of Herdr (its commands and the shape of `ls`), not inferred from a version number."""

    def spaces(self):
        return [{"id": "sp1", "name": "work", "tabs": [{"id": "tb1", "name": "1", "terminals": [{"terminal": "t1"}]}]}]

    def test_a_herdr_that_offers_everything_tether_uses_has_no_problems(self):
        self.assertEqual(herdr.contract_problems(COMMANDS, self.spaces()), [])

    def test_a_missing_command_or_option_is_named(self):
        without = [c for c in COMMANDS if c["name"] != "tab.new"]
        self.assertEqual(herdr.contract_problems(without, self.spaces()), ["Herdr has no `tab.new` command"])
        renamed = [dict(c, args=[a for a in c["args"] if a["name"] != "folder"]) if c["name"] == "space.new" else c for c in COMMANDS]
        self.assertEqual(herdr.contract_problems(renamed, self.spaces()), ["`space.new` no longer takes --folder"])

    def test_a_changed_ls_shape_is_named_and_an_empty_herdr_proves_nothing(self):
        broken = [{"id": "sp1", "name": "work", "tabs": [{"id": "tb1", "name": "1", "terminals": [{"id": "t1"}]}]}]
        self.assertEqual(herdr.contract_problems(COMMANDS, broken), ["`herdr-app ls` terminals have no `terminal`"])
        self.assertEqual(herdr.contract_problems(COMMANDS, []), [])

    def test_doctor_reports_it_and_names_what_is_wrong(self):
        with mock.patch.object(herdr, "contract_problems", return_value=["Herdr has no `tab.new` command"]), \
                mock.patch.object(core, "find_tool", return_value="/bin/true"), mock.patch.object(herdr, "herdr_ls", return_value=[]):
            results = doctor.doctor([])
        bad = [r for r in results if "does not match what tether expects" in r[1]]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0][0], "bad")
        self.assertIn("tab.new", bad[0][1])


if __name__ == "__main__":
    unittest.main()
