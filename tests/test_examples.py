"""The sample set is machine-defined and removable without breaking the vault (#10).

`make lint` only ever validated the *intact* template, so CI was structurally blind to the state
the documentation told users to create. These tests cover that state.
"""
import shutil
import subprocess
import sys
import unittest

from tests.helpers import ROOT, VAULT, kitlib, temp_vault

KIT = [sys.executable, str(ROOT / "kit.py")]


def run(*args, **kw):
    return subprocess.run(KIT + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, **kw)


def marked(vault):
    return {n.rel for n in kitlib.load_vault(vault)
            if isinstance(n.frontmatter, dict) and n.frontmatter.get("example") is True}


class TheSetIsMachineDefined(unittest.TestCase):
    def test_every_note_titled_example_is_marked(self):
        """The title suffix is the human rendering; `example: true` is what tools read."""
        for note in kitlib.load_vault(VAULT):
            title = str((note.frontmatter or {}).get("title", ""))
            if "(example)" in title:
                self.assertIs((note.frontmatter or {}).get("example"), True,
                              f"{note.rel} reads as a sample but is not marked")

    def test_the_example_named_people_are_marked(self):
        """They carried their marker in the filename only, so a reader kept two fake colleagues."""
        for rel in ("05-people/alex-example.md", "05-people/sam-example.md"):
            note = kitlib.parse_note(VAULT / rel, VAULT)
            self.assertIs((note.frontmatter or {}).get("example"), True, rel)

    def test_nothing_unexpected_is_marked(self):
        """A hub that merely links to samples must not be swept away with them."""
        for rel in marked(VAULT):
            self.assertFalse(rel.startswith(("00-home/", "99-system/")),
                             f"{rel} is a hub, not a sample")


class RemovalLeavesAWorkingVault(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, ignore_errors=True)

    def test_validate_strict_is_green_after_removal(self):
        """The finding: doing this by hand left 15 broken links and a red validate."""
        self.assertTrue(marked(self.vault), "nothing marked — the test would prove nothing")
        r = run("examples", "remove", "--vault", str(self.vault))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        v = run("validate", "--vault", str(self.vault), "--strict")
        self.assertEqual(v.returncode, 0, v.stdout)

    def test_reconcile_is_clean_after_removal(self):
        run("examples", "remove", "--vault", str(self.vault))
        r = run("reconcile", "--vault", str(self.vault))
        self.assertIn("nothing to reconcile", r.stdout, r.stdout)

    def test_every_marked_note_is_gone(self):
        before = marked(self.vault)
        run("examples", "remove", "--vault", str(self.vault))
        self.assertEqual(marked(self.vault), set())
        for rel in before:
            self.assertFalse((self.vault / rel).exists(), rel)

    def test_frontmatter_lists_are_pruned(self):
        """`people: [Alex Example]` resolves by title, so a dangling entry turns --strict red."""
        team = self.vault / "05-people/team-platform-engineering.md"
        self.assertIn("Alex Example", team.read_text(encoding="utf-8"))
        run("examples", "remove", "--vault", str(self.vault))
        people = (kitlib.parse_note(team, self.vault).frontmatter or {}).get("people", [])
        self.assertNotIn("Alex Example", people)

    def test_a_filename_taught_in_a_code_span_survives(self):
        """99-system/conventions.md names a sample file to teach kebab-case. It is not a link."""
        run("examples", "remove", "--vault", str(self.vault))
        text = (self.vault / "99-system/conventions.md").read_text(encoding="utf-8")
        self.assertIn("2026-09-11-search-relaunch-sync.md", text)

    def test_dry_run_changes_nothing(self):
        before = {p: p.read_bytes() for p in self.vault.rglob("*.md")}
        r = run("examples", "remove", "--vault", str(self.vault), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry run", r.stdout)
        after = {p: p.read_bytes() for p in self.vault.rglob("*.md")}
        self.assertEqual(before, after)

    def test_a_second_run_finds_nothing_to_do(self):
        run("examples", "remove", "--vault", str(self.vault))
        r = run("examples", "remove", "--vault", str(self.vault))
        self.assertEqual(r.returncode, 0)
        self.assertIn("nothing to remove", r.stdout)


class TheShippedViewsAreNotEmpty(unittest.TestCase):
    def test_an_open_decision_exists_for_the_open_decisions_view(self):
        """The flagship "views are filters over properties" feature looked broken on screen one."""
        states = [str((n.frontmatter or {}).get("state", "")).lower()
                  for n in kitlib.load_vault(VAULT)
                  if (n.frontmatter or {}).get("type") == "decision"]
        self.assertIn("open", states,
                      "00-home/bases/overview.base filters state == open and would render empty")


if __name__ == "__main__":
    unittest.main()
