"""Correctness and hygiene fixes in the CLI (#14).

One test per confirmed finding. Each asserts the observable behaviour a user would hit, not the
shape of the fix — a BOM note keeping its type, a usage error instead of a traceback, two vaults
that do not share one search index.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import kitproviders
from tests.helpers import ROOT, kitlib

KIT = [sys.executable, str(ROOT / "kit.py")]


def run(*args, **kw):
    return subprocess.run(KIT + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, **kw)


class BomRoundTrip(unittest.TestCase):
    """A note written by PowerShell 5.1 or legacy Notepad starts with a byte-order mark."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="okf-bom-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_set_frontmatter_does_not_prepend_a_second_block(self):
        note = self.tmp / "note.md"
        note.write_text("---\ntype: project\ntitle: Real\n---\n\nbody\n", encoding="utf-8-sig")
        kitlib.set_frontmatter(note, {"status": "stable"})

        text = note.read_text(encoding="utf-8-sig")
        self.assertEqual(text.count("---\n"), 2, f"frontmatter block duplicated:\n{text}")

        fm = kitlib.parse_note(note, self.tmp).frontmatter
        self.assertEqual(fm.get("type"), "project", "the BOM orphaned the real frontmatter")
        self.assertEqual(fm.get("title"), "Real")
        self.assertEqual(fm.get("status"), "stable")
        self.assertIn("body", text)


class ProposalsInterface(unittest.TestCase):
    """`proposals` is the human-in-the-loop path docs/security.md advertises: it must not crash."""

    def test_show_without_an_id_is_a_usage_error(self):
        r = run("proposals", "show")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("required", r.stderr.lower())

    def test_list_needs_no_id(self):
        r = run("proposals", "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_reject_still_takes_a_reason(self):
        r = run("proposals", "reject", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--reason", r.stdout)


class CollectionNaming(unittest.TestCase):
    """Two vaults must not answer from one index — the citations look right and are wrong."""

    def test_distinct_vaults_get_distinct_names(self):
        a = kitproviders.suggest_collection_name(Path("~/Notes/vault"))
        b = kitproviders.suggest_collection_name(Path("~/work/acme/vault"))
        self.assertNotEqual(a, b)
        self.assertEqual(a, "notes-vault")
        self.assertEqual(b, "acme-vault")

    def test_the_account_name_is_never_the_prefix(self):
        """The doctor report a tester sends back prints the collection name."""
        name = kitproviders.suggest_collection_name(Path.home() / "vault")
        self.assertEqual(name, "vault")
        self.assertNotIn(Path.home().name.lower(), name)

    def test_an_existing_vault_keeps_the_collection_it_registered(self):
        """collection_name still falls back to the old default, so configured vaults keep working."""
        self.assertEqual(kitproviders.collection_name(None), kitproviders.DEFAULT_COLLECTION)


class InitQmdHint(unittest.TestCase):
    """The printed `qmd collection add` line is copy-pasteable, or it is worse than nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="okf init "))   # a space, as in "First Last"
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_hint_quotes_the_path_and_masks_private_folders(self):
        target = self.tmp / "vault"
        r = run("init", "--target", str(target), "--actor", "human:test", "--no-qmd")
        self.assertEqual(r.returncode, 0, r.stderr)

        hint = [ln for ln in r.stdout.splitlines() if "qmd collection add" in ln]
        self.assertTrue(hint, f"no qmd hint printed:\n{r.stdout}")
        line = hint[0]
        # .resolve(): on macOS the temp dir is /var/... which the CLI prints as /private/var/...
        self.assertIn(f'"{target.resolve()}"', line,
                      "an unquoted path breaks for any account with a space")
        self.assertNotIn('--mask "**/*.md"', line,
                         "that mask indexes the `_`-prefixed folders the kit says nothing may read")


class DoctorNamesItsVault(unittest.TestCase):
    def test_doctor_says_when_it_describes_the_shipped_sample(self):
        r = run("doctor")
        self.assertEqual(r.returncode, 0, r.stderr)
        vault_row = [ln for ln in r.stdout.splitlines() if ln.startswith("vault")]
        self.assertTrue(vault_row, r.stdout)
        self.assertIn("sample", vault_row[0],
                      "the row is identical for every tester unless it says which vault it read")


if __name__ == "__main__":
    unittest.main()
