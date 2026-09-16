"""`validate`'s exit code separates OKF conformance from this kit's own rules (#22).

OKF v0.2 §11 names what a consumer MUST NOT reject a bundle for — broken cross-links and a
missing `index.md` among them. The reports always print; only the exit code is in question,
and it is the half CI reads.
"""
import shutil
import subprocess
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
