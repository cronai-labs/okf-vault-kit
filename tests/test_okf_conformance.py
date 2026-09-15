"""OKF v0.2 conformance of the template vault (and of the validator itself)."""
import shutil
import unittest
from pathlib import Path

from tests.helpers import VAULT, kitlib, temp_vault


class OkfConformance(unittest.TestCase):
    def test_template_vault_is_conformant(self):
        rep = kitlib.validate_okf(VAULT)
        self.assertEqual(rep.errors, [], rep.render())
        self.assertGreater(rep.checked, 30)

    def test_template_vault_has_no_warnings(self):
        rep = kitlib.validate_okf(VAULT)
        self.assertEqual(rep.warnings, [], rep.render())

    def test_every_non_reserved_note_has_type(self):
        for note in kitlib.load_vault(VAULT):
            if note.path.name in kitlib.RESERVED_FILENAMES or kitlib.is_raw_root(note.rel):
                continue
            self.assertIsNotNone(note.frontmatter, note.rel)
            self.assertTrue(note.frontmatter.get("type"), note.rel)

    def test_index_only_carries_okf_version(self):
        idx = VAULT / "index.md"
        self.assertTrue(idx.exists(), "run `kit.py index`")
        note = kitlib.parse_note(idx, VAULT)
        self.assertEqual(set(note.frontmatter or {}), {"okf_version"})
        self.assertEqual(note.frontmatter["okf_version"], kitlib.OKF_VERSION)

    def test_index_is_up_to_date(self):
        self.assertEqual((VAULT / "index.md").read_text(encoding="utf-8"), kitlib.build_index(VAULT))

    def test_log_headings_are_iso_dates(self):
        rep = kitlib.Report()
        kitlib.check_reserved(kitlib.parse_note(VAULT / "log.md", VAULT), rep)
        self.assertEqual(rep.errors, [])

    def test_validator_catches_missing_type_and_bad_status(self):
        v = temp_vault()
        try:
            (v / "07-knowledge/bad.md").write_text("---\ntitle: no type\nstatus: waiting\n---\nbody\n", encoding="utf-8")
            (v / "02-meetings/nofm.md").write_text("# no frontmatter at all\n", encoding="utf-8")
            (v / "07-knowledge/badtime.md").write_text("---\ntype: concept\ndescription: x\nstale_after: 2026-12-31\ngenerated: { by: me, at: yesterday }\n---\n", encoding="utf-8")
            rep = kitlib.validate_okf(v)
            joined = "\n".join(rep.errors)
            self.assertIn("bad.md: `type` is required", joined)
            self.assertIn("bad.md: `status` is the OKF lifecycle field", joined)
            self.assertIn("nofm.md: missing YAML frontmatter", joined)
            self.assertIn("badtime.md: `stale_after` must be an ISO-8601", joined)
            self.assertIn("generated.by must follow the actor convention", joined)
            self.assertIn("generated.at must be ISO-8601", joined)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_validator_accepts_verified_list_and_bare_mapping(self):
        v = temp_vault()
        try:
            (v / "07-knowledge/v1.md").write_text("---\ntype: concept\ndescription: x\nverified: { by: human:a, at: 2026-09-01T10:00:00Z }\n---\n", encoding="utf-8")
            (v / "07-knowledge/v2.md").write_text("---\ntype: concept\ndescription: x\nverified:\n  - { by: human:a, at: 2026-09-01T10:00:00Z }\n  - { by: process:nightly, at: 2026-09-02T02:00:00+02:00 }\n---\n", encoding="utf-8")
            rep = kitlib.validate_okf(v)
            self.assertEqual(rep.errors, [], rep.render())
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)
