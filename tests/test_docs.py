"""The docs must not drift from the code: every command the CLI dispatches, and every TETHER_* variable it
reads, has to be in the docs (the README and the pages under docs/), the agent skill and the generated help text; the generated files must be
current. Fails with the name of whatever is missing, so a new feature cannot be added without its docs."""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def user_docs():
    """The README and every page under docs/ that people read (not the generated help): a command or variable may be documented on any of them."""
    pages = ["README.md"] + [os.path.join("docs", f) for f in sorted(os.listdir(os.path.join(ROOT, "docs"))) if f.endswith(".md")]
    return "\n".join(read(*p.split("/")) for p in pages)


LIB = os.path.join(ROOT, "lib", "tetherlib")
SCRIPT = "\n".join(read("lib", "tetherlib", f) for f in sorted(os.listdir(LIB)) if f.endswith(".py"))  # the whole tool, whichever module holds it
# commands main() dispatches by name (the agent names, claude / copilot, are covered by their own docs)
COMMANDS = sorted(set(re.findall(r'(?:elif|if) cmd == "([a-z-]+)"', SCRIPT)))
ENV_VARS = sorted(set("TETHER_" + n for n in re.findall(r'\benv\("([A-Z_]+)"\)', SCRIPT)))


class DocsStayInSync(unittest.TestCase):
    def test_every_command_is_in_the_readme_the_skill_and_the_help(self):
        self.assertGreater(len(COMMANDS), 10, "the command scan found too little: update the regex")
        for doc, text in (("the README and docs/", user_docs()), ("skill/SKILL.md", read("skill", "SKILL.md")),
                          ("docs/tether-help.txt", read("docs", "tether-help.txt"))):
            missing = [c for c in COMMANDS if f"tether {c}" not in text]
            self.assertEqual(missing, [], f"{doc} does not mention these commands (add them): {missing}")

    def test_every_environment_variable_is_in_the_help_the_readme_and_the_skill(self):
        self.assertGreater(len(ENV_VARS), 5, "the env var scan found too little: update the regex")
        for doc, text in (("docs/tether-help.txt", read("docs", "tether-help.txt")), ("the README and docs/", user_docs()),
                          ("skill/SKILL.md", read("skill", "SKILL.md"))):
            missing = [v for v in ENV_VARS if v not in text]
            self.assertEqual(missing, [], f"{doc} does not mention these environment variables: {missing}")

    def test_the_version_is_documented_by_one_number(self):
        version = read("VERSION").strip()
        self.assertIn(f'__version__ = "{version}"', read("lib", "tetherlib", "core.py"))
        self.assertIn(version, read("README.md"), "README should show the pinned-version example with the current version")

    def test_no_machine_specific_paths_ship(self):
        banned = re.compile(r"/Users/(?!me/)[a-z]+/")
        for rel in ("README.md", "CONTRIBUTING.md", "skill/SKILL.md", "bin/tether", "install.sh", *("lib/tetherlib/" + f for f in sorted(os.listdir(LIB)) if f.endswith(".py")),
                    *("docs/" + f for f in sorted(os.listdir(os.path.join(ROOT, "docs"))) if f.endswith(".md"))):
            hit = banned.search(read(*rel.split("/")))
            self.assertIsNone(hit, f"{rel} contains {hit.group(0) if hit else ''!r}")

    def test_generated_help_is_current(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "tether"), "--help"],
                             capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), read("docs", "tether-help.txt").strip(),
                         "docs/tether-help.txt is stale: run ./sync.sh meta")

    def test_every_mod_is_described_in_the_docs(self):
        mods = sorted(d for d in os.listdir(os.path.join(ROOT, "herdr", "mods")) if d.startswith("tether-"))
        text = read("docs", "how-it-works.md") + read("skill", "SKILL.md")
        missing = [m for m in mods if m not in text]
        self.assertEqual(missing, [], f"mods missing from docs/how-it-works.md or the skill: {missing}")


if __name__ == "__main__":
    unittest.main()
