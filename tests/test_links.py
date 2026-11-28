"""Every markdown link, wikilink and Bases embed in the vault resolves."""
import shutil
import unittest

import kitgraph
from tests.helpers import ROOT, VAULT, kitlib, temp_vault


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

    def test_links_inside_comments_are_ignored(self):
        """An HTML or Obsidian comment is not rendered, so a link in it is not a link (#81)."""
        v = temp_vault()
        try:
            (v / "02-meetings/c.md").write_text(
                "---\ntype: meeting\ndescription: x\n---\n"
                "<!-- see [x](nope.md) and,\non the next line, [[Nope]] -->\n"
                "%% [y](nope2.md) [[Nope2]] %%\n"
                "after both comments: [[Still-Nope]]\n", encoding="utf-8")
            self.assertEqual(kitlib.check_links(v).errors, ["02-meetings/c.md: broken wikilink [[Still-Nope]]"],
                             "comments are skipped, and the text after them is still checked")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class UriSchemes(unittest.TestCase):
    """A link with a URI scheme points outside the vault, whatever the scheme and its case (#114, #98).

    The checker kept an allowlist of five schemes, so `tel:`, `zotero://`, `sftp://`, `HTTPS://`
    and the CommonMark angle-bracket form `[t](<https://…>)` were reported as broken local links.
    """

    def check(self, body: str) -> list[str]:
        v = temp_vault()
        try:
            (v / "02-meetings/s.md").write_text("---\ntype: meeting\ndescription: x\n---\n" + body, encoding="utf-8")
            (v / "07-knowledge/a note.md").write_text("---\ntype: concept\ndescription: x\n---\n", encoding="utf-8")
            return [e for e in kitlib.check_links(v).errors if e.startswith("02-meetings/s.md")]
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_any_scheme_in_any_case_is_external(self):
        body = ("[call](tel:+49301234567) [paper](zotero://select/items/ABC) [box](sftp://host/x) "
                "[web](HTTPS://example.com/page) [mail](MailTo:a@example.com) [x](x-devonthink-item://1A2B) "
                "[urn](urn:isbn:0451450523) [file](file:///etc/hosts)\n")
        self.assertEqual(self.check(body), [])

    def test_the_angle_bracket_form_is_external_too(self):
        self.assertEqual(self.check("Source: [https://example.com/page](<https://example.com/page>).\n"), [])

    def test_an_angle_bracketed_local_target_is_still_checked(self):
        self.assertEqual(self.check("[ok](<../07-knowledge/a note.md>) [also](<../07-knowledge/hybrid-search.md>)\n"), [])
        self.assertEqual(self.check("[gone](<../07-knowledge/no such note.md>)\n"),
                         ["02-meetings/s.md: broken link -> ../07-knowledge/no such note.md"])

    def test_a_drive_letter_is_a_path_not_a_scheme(self):
        """RFC 3986 allows a one-letter scheme, but none is registered, and `C:` is a Windows drive."""
        self.assertFalse(kitlib.is_external_link("C:/Users/me/notes/x.md"))
        self.assertEqual(self.check("[win](C:/Users/me/notes/x.md)\n"), ["02-meetings/s.md: broken link -> C:/Users/me/notes/x.md"])

    def test_what_is_not_a_scheme(self):
        for target in ("../03-projects/x.md", "x.md#Heading", "1http://x", "+x:y", "#anchor", ""):
            self.assertFalse(kitlib.is_external_link(target), target)

    def test_the_graph_reads_property_links_the_same_way(self):
        """A reference property: any scheme is external, and `<...>` is a link to the note inside."""
        v = temp_vault()
        try:
            (v / "07-knowledge/p.md").write_text(
                "---\ntype: spec\ntitle: P\ndescription: x\n"
                "owner: \"[mail](MailTo:a@example.com)\"\nproject: \"[Search](<../03-projects/search-relaunch.md>)\"\n---\n",
                encoding="utf-8")
            g = kitgraph.build_graph(v)
            nid = next(n for n, node in g.nodes.items() if node.path == "07-knowledge/p.md")
            self.assertIn((nid, "project"), {(s, p) for s, p, _d, _o in g.edges if s == nid})
            self.assertEqual([f.render() for f in g.findings if f.node == nid and f.code == "unresolved_ref"], [])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_one_definition(self):
        """The allowlist had copies in four modules; a fifth copy is how the next scheme goes missing."""
        for name in ("kit.py", "kitlib.py", "kitgraph.py", "kitrecon.py", "mcp/vault_bridge.py", "mcp/bridge_policy.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn('"mailto:"', text, name)
            self.assertNotIn('"obsidian://"', text, name)


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
            self.assertEqual(kitlib.template_folder(v), ("meta/templates", None))
            self.assertEqual([e for e in kitlib.check_links(v).errors if e.startswith("meta/")], [])
            warnings = kitlib.validate_okf(v).warnings
            self.assertFalse([w for w in warnings if w.startswith("meta/templates/t.md")], warnings)
            self.assertTrue([w for w in warnings if w.startswith("90-templates/")],
                            "only the configured folder is exempt; the name 90-templates means nothing by itself")
            self.assertNotIn("meta/templates/t.md", kitgraph.build_graph(v).nodes)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_without_templates_json_no_folder_is_exempt(self):
        """Obsidian has no template folder then, so the kit must not guess one."""
        v = temp_vault()
        try:
            (v / ".obsidian/templates.json").unlink()
            self.assertEqual(kitlib.template_folder(v), (None, None))
            (v / "90-templates/t.md").write_text("---\ntype: note\ndescription:\n---\n" + self.PLACEHOLDERS, encoding="utf-8")
            self.assertIn("90-templates/t.md: broken wikilink [[x/{{date:YYYY-MM}}]]", kitlib.check_links(v).errors)
            self.assertTrue([w for w in kitlib.validate_okf(v).warnings if w.startswith("90-templates/t.md")])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_a_broken_templates_json_is_reported_and_exempts_nothing(self):
        cases = {
            "not JSON": "{folder: 90-templates",
            "not an object": '["90-templates"]',
            "folder not a string": '{"folder": 90}',
            "absolute folder": '{"folder": "/90-templates"}',
            "folder outside the vault": '{"folder": "../90-templates"}',
            "folder does not exist": '{"folder": "no-such-folder"}',
        }
        for case, content in cases.items():
            with self.subTest(case):
                v = temp_vault()
                try:
                    (v / ".obsidian/templates.json").write_text(content, encoding="utf-8")
                    folder, error = kitlib.template_folder(v)
                    self.assertIsNone(folder)
                    self.assertTrue(error and error.startswith(".obsidian/templates.json: "), error)
                    (v / "90-templates/t.md").write_text("---\ntype: note\ndescription:\n---\n" + self.PLACEHOLDERS, encoding="utf-8")
                    errors = kitlib.check_links(v).errors
                    self.assertIn(error, errors)
                    self.assertIn("90-templates/t.md: broken wikilink [[x/{{date:YYYY-MM}}]]", errors,
                                  "a config we cannot trust exempts nothing")
                    kitlib.validate_okf(v); kitgraph.build_graph(v)   # neither may raise
                finally:
                    shutil.rmtree(v.parent, ignore_errors=True)
