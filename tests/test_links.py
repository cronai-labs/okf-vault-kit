"""Every markdown link, wikilink and Bases embed in the vault resolves."""
import shutil
import unittest

from tests.helpers import VAULT, kitlib, temp_vault


class Links(unittest.TestCase):
    def test_all_links_resolve(self):
        rep = kitlib.check_links(VAULT)
        self.assertEqual(rep.errors, [], rep.render())

    def test_every_markdown_file_is_counted_once(self):
        """`checked` is what the CLI prints; a second read of the same file doubled it."""
        rep = kitlib.check_links(VAULT)
        self.assertEqual(rep.checked, len(list(kitlib.iter_markdown(VAULT))))

    def test_link_checker_detects_broken_links(self):
        v = temp_vault()
        try:
            (v / "02-meetings/x.md").write_text("---\ntype: meeting\ndescription: x\n---\n[gone](../03-projects/nope.md) [[Nope]] ![[overview.base#Nope]]\n", encoding="utf-8")
            rep = kitlib.check_links(v)
            joined = "\n".join(rep.errors)
            self.assertIn("broken link -> ../03-projects/nope.md", joined)
            self.assertIn("broken wikilink [[Nope]]", joined)
            self.assertIn("view 'Nope' not in overview.base", joined)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_wikilinks_match_regardless_of_case_and_extension(self):
        v = temp_vault()
        try:
            (v / "05-people/Jane Doe.md").write_text("---\ntype: person\ndescription: x\n---\n", encoding="utf-8")
            (v / "02-meetings/z.md").write_text(
                "---\ntype: meeting\ndescription: x\n---\n[[jane doe]] [[Hybrid-Search.md]]\n", encoding="utf-8")
            self.assertEqual(kitlib.check_links(v).errors, [], "Obsidian resolves both spellings")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_links_inside_code_are_ignored(self):
        v = temp_vault()
        try:
            (v / "02-meetings/y.md").write_text("---\ntype: meeting\ndescription: x\n---\n`[[Nope]]` and ```\n[x](nope.md)\n```\n", encoding="utf-8")
            self.assertEqual(kitlib.check_links(v).errors, [])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)
