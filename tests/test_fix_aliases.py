"""A property alias is named as a rename, and `validate --fix-aliases` makes it (#96).

The ontology maps `attendees` onto `people` and `assignee` onto `owner`; `validate` warns once
per note. The warning now says which rename to make, and `--fix-aliases` makes exactly that
rename: a dry run by default, `--apply` to write, the key token and nothing else, a note that
sets both names refused, and a second run a no-op.
"""
import shutil
import subprocess
import sys
import unittest

from tests.helpers import ROOT, temp_vault

KIT = [sys.executable, str(ROOT / "kit.py")]


def kit(*args):
    return subprocess.run(KIT + list(args), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)


# Comments, quoting, order, a nested key and a body line of the same name all stay as written.
MEETING = (
    "---\n"
    "type: meeting\n"
    "title: \"Alias probe\"   # quoted on purpose\n"
    "description: d\n"
    "date: 2026-11-20\n"
    "attendees: [Alex Example]   # from the old template\n"
    "extra:\n"
    "  attendees: nested, not top level\n"
    "tags: [probe]\n"
    "---\n"
    "\n"
    "attendees: a body line, not frontmatter\n"
)


class Case(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, ignore_errors=True)
        self.dir = self.vault / "02-meetings"

    def note(self, name: str, text: str, encoding: str = "utf-8", newline: str = "\n"):
        path = self.dir / name
        path.write_bytes(text.replace("\n", newline).encode(encoding))
        return path

    def fix(self, *flags):
        return kit("validate", "--vault", str(self.vault), "--fix-aliases", *flags)


class TheWarningNamesTheRename(Case):
    def test_rename_attendees_to_people(self):
        self.note("alias-probe.md", MEETING)
        r = kit("validate", "--vault", str(self.vault))
        line = next((l for l in r.stdout.splitlines() if "property_alias" in l and "alias-probe.md" in l), "")
        self.assertIn("rename `attendees` to `people`", line, r.stdout)
        self.assertIn("--fix-aliases", line)


class FixAliases(Case):
    def test_a_dry_run_lists_the_rename_and_writes_nothing(self):
        path = self.note("alias-probe.md", MEETING)
        before = path.read_bytes()
        r = self.fix()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("02-meetings/alias-probe.md: attendees → people", r.stdout)
        self.assertIn("--apply", r.stdout)
        self.assertEqual(path.read_bytes(), before)

    def test_apply_changes_the_key_token_only(self):
        path = self.note("alias-probe.md", MEETING)
        r = self.fix("--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        expected = MEETING.replace("\nattendees: [Alex", "\npeople: [Alex", 1)
        self.assertEqual(path.read_text(encoding="utf-8"), expected)

    def test_crlf_and_a_byte_order_mark_survive(self):
        crlf = self.note("crlf.md", MEETING, newline="\r\n")
        bom = self.note("bom.md", MEETING, encoding="utf-8-sig")
        crlf_before, bom_before = crlf.read_bytes(), bom.read_bytes()
        r = self.fix("--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(crlf.read_bytes(), crlf_before.replace(b"\r\nattendees: [", b"\r\npeople: [", 1))
        self.assertEqual(bom.read_bytes(), bom_before.replace(b"\nattendees: [", b"\npeople: [", 1))
        self.assertTrue(bom.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_spacing_and_quoting_of_the_key_are_kept(self):
        spaced = self.note("spaced.md", MEETING.replace("\nattendees: [", "\nattendees : [", 1))
        quoted = self.note("quoted.md", MEETING.replace("\nattendees: [", '\n"attendees": [', 1))
        r = self.fix("--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("\npeople : [Alex", spaced.read_text(encoding="utf-8"))
        self.assertIn('\n"people": [Alex', quoted.read_text(encoding="utf-8"))

    def test_a_note_with_alias_and_canonical_is_refused(self):
        both = self.note("both.md", MEETING.replace("tags: [probe]\n", "tags: [probe]\npeople: [Sam Example]\n", 1))
        two = self.note("two.md", "---\ntype: task\ntitle: T\ndescription: d\nassignee: me\nresponsible: me\n---\n")
        good = self.note("good.md", MEETING)
        both_before, two_before = both.read_bytes(), two.read_bytes()
        r = self.fix("--apply")
        self.assertEqual(r.returncode, 1, "a refused note is not a clean run")
        self.assertIn("02-meetings/both.md", r.stdout)
        self.assertIn("both `attendees` and `people`", r.stdout)
        self.assertIn("02-meetings/two.md", r.stdout)
        self.assertEqual(both.read_bytes(), both_before)
        self.assertEqual(two.read_bytes(), two_before)
        self.assertIn("\npeople: [Alex", good.read_text(encoding="utf-8"), "the other notes are still fixed")

    def test_it_is_idempotent(self):
        path = self.note("alias-probe.md", MEETING)
        self.assertEqual(self.fix("--apply").returncode, 0)
        after, mtime = path.read_bytes(), path.stat().st_mtime_ns
        r = self.fix("--apply")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("no property aliases to rename", r.stdout)
        self.assertEqual(path.read_bytes(), after)
        self.assertEqual(path.stat().st_mtime_ns, mtime)
        v = kit("validate", "--vault", str(self.vault))
        self.assertNotIn("property_alias", v.stdout)

    def test_apply_without_fix_aliases_is_a_usage_error(self):
        r = kit("validate", "--vault", str(self.vault), "--apply")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--fix-aliases", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_it_never_renames_sensitivity_away(self):
        """Renaming the raw `sensitivity:` line would make every reader serve the note."""
        onto = self.vault / "99-system" / "ontology.yml"
        onto.write_text(onto.read_text(encoding="utf-8").replace(
            "property_aliases:\n", "property_aliases:\n  sensitivity: classification\n", 1), encoding="utf-8")
        path = self.note("hidden.md", "---\ntype: concept\ntitle: H\ndescription: d\nsensitivity: confidential\n---\n")
        before = path.read_bytes()
        r = self.fix("--apply")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("02-meetings/hidden.md", r.stdout)
        self.assertIn("`sensitivity`", r.stdout)
        self.assertEqual(path.read_bytes(), before)

    def test_a_symlinked_note_is_refused_not_replaced_by_a_copy(self):
        target = self.note("alias-probe.md", MEETING)
        link = self.vault / "07-knowledge" / "linked-probe.md"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable here")
        r = self.fix("--apply")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(link.is_symlink(), "the link was replaced by a regular file")
        self.assertIn("07-knowledge/linked-probe.md", r.stdout)
        self.assertIn("\npeople: [Alex", target.read_text(encoding="utf-8"), "the file itself is still fixed")

    def test_it_refuses_while_the_vault_ontology_is_unusable(self):
        """The fallback is the kit's own alias table, not the one this vault declared."""
        (self.vault / "99-system" / "ontology.yml").write_text("property_aliases: [\n", encoding="utf-8")
        path = self.note("alias-probe.md", MEETING)
        before = path.read_bytes()
        r = self.fix("--apply")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("99-system/ontology.yml", r.stdout + r.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_a_shortened_flag_is_refused(self):
        path = self.note("alias-probe.md", MEETING)
        before = path.read_bytes()
        r = kit("validate", "--vault", str(self.vault), "--fix-aliases", "--app")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
