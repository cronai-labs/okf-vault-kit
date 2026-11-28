"""`validate`'s exit code separates OKF conformance from this kit's own rules (#22).

OKF v0.2 §11 names what a consumer MUST NOT reject a bundle for — broken cross-links and a
missing `index.md` among them. The reports always print; only the exit code is in question,
and it is the half CI reads. One finding outside conformance fails it too: an ontology alias onto
`sensitivity` (#121), tested in test_ontology_file.py.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT, temp_vault

KIT = [sys.executable, str(ROOT / "kit.py")]


def validate(vault, *flags):
    return subprocess.run(KIT + ["validate", "--vault", str(vault), *flags],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)


class ConformanceDecidesTheDefaultExitCode(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, ignore_errors=True)

    def test_the_untouched_template_is_green_either_way(self):
        self.assertEqual(validate(self.vault).returncode, 0)
        self.assertEqual(validate(self.vault, "--strict").returncode, 0)

    def test_a_broken_cross_link_does_not_fail_validate(self):
        """§11: a consumer MUST NOT reject a bundle for broken cross-links."""
        note = self.vault / "04-knowledge" / "broken-link-probe.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\ntype: concept\ntitle: Probe\n---\n\n[gone](./nowhere-at-all.md)\n",
                        encoding="utf-8")

        r = validate(self.vault)
        self.assertIn("broken link", r.stdout, "the finding must still be reported")
        self.assertEqual(r.returncode, 0,
                         "a conformant bundle was rejected for something §11 forbids rejecting")

    def test_but_strict_still_fails_on_it(self):
        """This is what `make lint` and CI run, so the repo gate is unchanged."""
        note = self.vault / "04-knowledge" / "broken-link-probe.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\ntype: concept\ntitle: Probe\n---\n\n[gone](./nowhere-at-all.md)\n",
                        encoding="utf-8")
        self.assertEqual(validate(self.vault, "--strict").returncode, 1)

    def test_a_missing_index_does_not_fail_validate(self):
        """§11 again: missing index.md files are explicitly not grounds for rejection."""
        (self.vault / "index.md").unlink()
        self.assertEqual(validate(self.vault).returncode, 0)

    def test_a_missing_type_still_fails(self):
        """§11's one hard rule: every non-reserved note carries a non-empty `type`."""
        note = self.vault / "04-knowledge" / "untyped-probe.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\ntitle: No type\n---\n\nbody\n", encoding="utf-8")

        self.assertEqual(validate(self.vault).returncode, 1)
        self.assertEqual(validate(self.vault, "--strict").returncode, 1)

    def test_the_exit_code_explains_itself_when_it_ignores_findings(self):
        """Silence would read as 'the links are fine', which is the opposite of what happened."""
        note = self.vault / "04-knowledge" / "broken-link-probe.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\ntype: concept\ntitle: Probe\n---\n\n[gone](./nowhere-at-all.md)\n",
                        encoding="utf-8")

        out = validate(self.vault).stdout
        self.assertIn("do not set the exit code", out)
        self.assertIn("--strict", out)

    def test_a_clean_vault_prints_no_such_note(self):
        self.assertNotIn("do not set the exit code", validate(self.vault).stdout)


class AMisplacedDayNoteIsAHygieneFinding(unittest.TestCase):
    """The year/month rule is this kit's, not the spec's: reported always, fatal only under --strict."""

    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, ignore_errors=True)
        note = self.vault / "01-journal" / "daily" / "2026-10-19.md"
        note.write_text("---\ntype: daily\ndescription: probe\ndate: 2026-10-19\n---\n",
                        encoding="utf-8", newline="\n")

    def test_it_is_reported_and_does_not_fail_validate(self):
        r = validate(self.vault)
        self.assertIn("warn   01-journal/daily/2026-10-19.md: ", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_strict_fails_on_it(self):
        self.assertEqual(validate(self.vault, "--strict").returncode, 1)


class FrontmatterAConstructorCannotBuildIsAFindingNotATraceback(unittest.TestCase):
    """Valid YAML syntax whose value PyYAML's constructor cannot build: `created: 2026-02-30` raised
    a bare ValueError, and an explicit tag on the wrong value (`!!bool x`, `!!int ''`,
    `!!timestamp x`) a KeyError, IndexError or AttributeError. Each stopped `validate`, `index` and
    `graph build` with a traceback (#114, #108); each is now what a YAML error is: a finding."""

    CASES = {
        "07-knowledge/bad-date.md": "created: 2026-02-30",
        "07-knowledge/bad-bool.md": "flag: !!bool x",
        "07-knowledge/bad-int.md": "count: !!int ''",
        "07-knowledge/bad-timestamp.md": "created: !!timestamp x",
    }

    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, ignore_errors=True)
        for rel, line in self.CASES.items():
            (self.vault / rel).write_text(
                f"---\ntype: concept\ntitle: Bad value\ndescription: probe\n{line}\n---\n\nbody\n",
                encoding="utf-8", newline="\n")

    def kit(self, *args):
        return subprocess.run(KIT + [*args, "--vault", str(self.vault)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False)

    def test_validate_names_each_file_and_fails_conformance(self):
        """Its frontmatter cannot be read at all, so `type` cannot be checked: an OKF error, exit 1.
        No assertion on PyYAML's or Python's own wording: it differs between Python versions."""
        r = validate(self.vault)
        self.assertNotIn("Traceback", r.stderr)
        for rel in self.CASES:
            with self.subTest(rel):
                self.assertIn(f"{rel}: frontmatter cannot be read", r.stdout)
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_index_names_each_file_and_leaves_it_out(self):
        r = self.kit("index")
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)
        index = (self.vault / "index.md").read_text(encoding="utf-8")
        for rel in self.CASES:
            with self.subTest(rel):
                self.assertIn(rel, r.stderr)
                self.assertNotIn(rel.rpartition("/")[2].removesuffix(".md"), index,
                                 "frontmatter we cannot read counts as confidential")

    def test_graph_build_still_builds(self):
        r = self.kit("graph", "build")
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)


class AVaultWithNoMarkdownIsNamed(unittest.TestCase):
    """#100: `--vault` pointing at an existing but wrong directory used to print `OK`, exit 0."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="okf-empty-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_it_warns_and_names_the_path(self):
        r = validate(self.dir)
        self.assertIn(f"no markdown files under {self.dir.resolve()}; is --vault right?", r.stdout)
        self.assertEqual(r.returncode, 0, "a warning, as for the kit's other rules")

    def test_strict_fails_on_it(self):
        self.assertEqual(validate(self.dir, "--strict").returncode, 1)

    def test_a_vault_of_undecodable_notes_is_not_empty(self):
        """Those are named as errors of their own; 'no markdown files' would mislead."""
        (self.dir / "note.md").write_bytes("---\ntype: concept\n---\n".encode("utf-16"))
        r = validate(self.dir)
        self.assertNotIn("no markdown files", r.stdout)
        self.assertIn("not valid UTF-8", r.stdout)


if __name__ == "__main__":
    unittest.main()
