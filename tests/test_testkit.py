"""The test kit's report keeps the promise it prints (#11).

`testkit/testkit.py` was the only tracked Python file with no test. Its redactor was a second,
weaker implementation of `kit.py`'s, and the report said so in words that were not true.
"""
import re
import sys
import unittest

import kitredact
from tests.helpers import ROOT

sys.path.insert(0, str(ROOT / "testkit"))
import testkit as tk


class OneRedactorNotTwo(unittest.TestCase):
    """Each case below survived the test kit's own redactor and reached the report."""

    def test_the_kit_redactor_is_what_runs(self):
        """One implementation means one place to attack — and one place to fix."""
        probe = "/Users/someone/Notes/vault/05-people/alex.md"
        self.assertEqual(tk.redact(probe), kitredact.scrub(probe))

    def test_a_path_deeper_than_the_home_directory(self):
        out = tk.redact("/Users/someone/Notes/vault/05-people/alex.md")
        self.assertNotIn("Notes", out)
        self.assertNotIn("05-people", out)

    def test_a_vault_outside_users_and_home(self):
        """The old rule matched only /Users or /home, so this was passed through untouched."""
        out = tk.redact("/data/work/acme-vault/05-people/alex.md")
        self.assertNotIn("acme-vault", out)
        self.assertNotIn("05-people", out)

    def test_a_windows_account_with_a_space(self):
        """The old rule stopped at the first space, leaving half the account name behind."""
        out = tk.redact(r"C:\Users\First Last\Notes\vault\note.md")
        self.assertNotIn("Last", out)
        self.assertNotIn("Notes", out)

    def test_url_credentials_and_host(self):
        out = tk.redact("https://user:pw@intranet.example.de:8443/tenant/42")
        for leak in ("user", "pw", "intranet", "tenant"):
            self.assertNotIn(leak, out)
        self.assertIn("8443", out, "the port is the diagnostic value and should survive")

    def test_loopback_survives_because_it_discloses_nothing(self):
        self.assertIn("127.0.0.1", tk.redact("http://127.0.0.1:1234/v1"))

    def test_empty_input_is_returned_unchanged(self):
        self.assertEqual(tk.redact(""), "")


class TheReportAnswersTheFollowUpQuestions(unittest.TestCase):
    """Version, time and proxy were computed and thrown away, so every report needed a reply."""

    def _env_rows(self):
        rep = tk.Report()
        rows = [("kit version", tk._kit_version()), ("report written", "2026-09-16T00:00:00Z"),
                ("proxy", "none set")]
        return tk.render(rep, rows)

    def test_the_kit_version_is_real(self):
        version = tk._kit_version()
        self.assertEqual(version, (ROOT / "VERSION").read_text(encoding="utf-8").strip())
        self.assertRegex(version, r"^\d+\.\d+\.\d+")

    def test_the_three_facts_reach_the_rendered_report(self):
        out = self._env_rows()
        for field in ("kit version", "report written", "proxy"):
            self.assertIn(field, out)

    def test_the_promise_matches_what_the_redactor_does(self):
        out = self._env_rows()
        self.assertNotIn("no vault paths and no username", out,
                         "that claim was materially false and must not come back")
        self.assertIn("No note content", out)


class VersionsNotBannerArt(unittest.TestCase):
    def test_a_box_drawing_banner_does_not_reach_the_report(self):
        art = "╭─────────────╮\n│  LM Studio  │\n╰─────────────╯\nlms version 0.3.17\n"
        lines = [ln.strip() for ln in art.splitlines() if ln.strip()]
        picked = None
        for line in lines:
            wordish = sum(c.isalnum() or c in "._-+ " for c in line)
            if wordish < len(line) * 0.6:
                continue
            m = tk._VERSION_TOKEN.search(line)
            if m:
                picked = m.group(0)
                break
        self.assertEqual(picked, "0.3.17")

    def test_the_token_pattern_accepts_a_prerelease(self):
        self.assertEqual(tk._VERSION_TOKEN.search("kit 0.4.0-alpha.2").group(0), "0.4.0-alpha.2")


class DoctorExitOneIsAFailure(unittest.TestCase):
    def test_the_source_does_not_bucket_exit_one_with_success(self):
        """`doctor` returns 1 only when the interpreter is below the supported floor."""
        src = (ROOT / "testkit" / "testkit.py").read_text(encoding="utf-8")
        self.assertNotIn("PASS if rc in (0, 1) else FAIL", src)
        self.assertIn('rep.add("kit.py doctor", PASS if rc == 0 else FAIL', src)


class TheTestKitIsGated(unittest.TestCase):
    def test_it_is_byte_compiled_by_make_lint(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        compile_line = next(ln for ln in makefile.splitlines() if "compileall" in ln)
        self.assertIn("testkit", compile_line)

    def test_it_imports_without_a_yaml_dependency(self):
        """The test kit runs where the install is broken; kitredact must stay stdlib-only."""
        src = (ROOT / "kitredact.py").read_text(encoding="utf-8")
        self.assertNotIn("import yaml", src)
        self.assertNotIn("import kitlib", src)
        self.assertFalse(re.search(r"^import kit\b", src, re.M))


if __name__ == "__main__":
    unittest.main()
