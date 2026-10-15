"""Every markdown link, wikilink and Bases embed in the vault resolves."""
import shutil
import unittest

import kitgraph
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


class TemplateFolder(unittest.TestCase):
    """A template's placeholder link is filled in when the note is created, not when it is checked."""

    PLACEHOLDERS = "[[x/{{date:YYYY-MM}}]] [m](notes/{{title}}.md)\n"

    def test_placeholder_links_in_a_template_are_not_broken(self):
        v = temp_vault()
        try:
            (v / "90-templates/t.md").write_text("---\ntype: note\ndescription:\n---\n" + self.PLACEHOLDERS, encoding="utf-8")
            self.assertEqual(kitlib.check_links(v).errors, [])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_a_real_broken_link_in_a_template_is_still_reported(self):
        v = temp_vault()
        try:
            (v / "90-templates/t.md").write_text("---\ntype: note\ndescription:\n---\n[[Nope]]\n", encoding="utf-8")
            self.assertIn("90-templates/t.md: broken wikilink [[Nope]]", kitlib.check_links(v).errors)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_a_placeholder_link_outside_the_template_folder_is_still_broken(self):
        v = temp_vault()
        try:
            (v / "02-meetings/p.md").write_text("---\ntype: meeting\ndescription: x\n---\n" + self.PLACEHOLDERS, encoding="utf-8")
            joined = "\n".join(kitlib.check_links(v).errors)
            self.assertIn("02-meetings/p.md: broken wikilink [[x/{{date:YYYY-MM}}]]", joined)
            self.assertIn("02-meetings/p.md: broken link -> notes/{{title}}.md", joined)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_the_folder_comes_from_obsidian_templates_json(self):
        v = temp_vault()
        try:
            (v / ".obsidian/templates.json").write_text('{"folder": "meta/templates"}', encoding="utf-8")
            (v / "meta/templates").mkdir(parents=True)
            (v / "meta/templates/t.md").write_text("---\ntype: note\ndescription:\n---\n" + self.PLACEHOLDERS, encoding="utf-8")
            self.assertEqual(kitlib.template_dir(v), "meta/templates")
            self.assertEqual([e for e in kitlib.check_links(v).errors if e.startswith("meta/")], [])
            warnings = kitlib.validate_okf(v).warnings
            self.assertFalse([w for w in warnings if w.startswith("meta/templates/t.md")], warnings)
            self.assertTrue([w for w in warnings if w.startswith("90-templates/")],
                            "the configured folder replaces the default; 90-templates is an ordinary folder now")
            self.assertNotIn("meta/templates/t.md", kitgraph.build_graph(v).nodes)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_without_templates_json_the_default_folder_applies(self):
        v = temp_vault()
        try:
            (v / ".obsidian/templates.json").unlink()
            self.assertEqual(kitlib.template_dir(v), "90-templates")
            (v / "90-templates/t.md").write_text("---\ntype: note\ndescription:\n---\n" + self.PLACEHOLDERS, encoding="utf-8")
            self.assertEqual(kitlib.check_links(v).errors, [])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)
