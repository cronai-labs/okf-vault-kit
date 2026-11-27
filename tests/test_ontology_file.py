"""The vault's ontology file is checked as a file, not only applied (#99, #121).

`99-system/ontology.yml` is plain YAML a person edits. A class block pasted under `edges:` used
to declare nothing and say nothing; a file that did not parse took `validate`, `graph` and the
bridge down with a traceback. Each test edits a copy of the template's ontology and asserts what
a user sees: a finding that names the file and the section, and no traceback.
"""
import shutil
import subprocess
import sys
import unittest

import kitgraph
from tests.helpers import ROOT, temp_vault

KIT = [sys.executable, str(ROOT / "kit.py")]
REL = "99-system/ontology.yml"


def validate(vault, *flags):
    return subprocess.run(KIT + ["validate", "--vault", str(vault), *flags],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)


class OntologyFileCase(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, ignore_errors=True)
        self.path = self.vault / REL
        self.shipped = self.path.read_text(encoding="utf-8")

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def insert_after(self, anchor: str, block: str) -> None:
        self.assertIn(anchor, self.shipped)
        self.write(self.shipped.replace(anchor, anchor + block, 1))

    def problems(self) -> list[str]:
        return kitgraph.check_ontology_file(self.vault).errors


class StructureIsChecked(OntologyFileCase):
    def test_the_shipped_ontology_is_clean(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(kitgraph.read_ontology(kitgraph.DEFAULT_ONTOLOGY)[1], [])

    def test_a_class_block_under_edges_is_named(self):
        """The #99 file: a class pasted between `edges:` and the next section."""
        self.insert_after("edges:\n", "  widget:\n    extends: note\n    props:\n      colour: text\n")
        (self.vault / "07-knowledge" / "w.md").write_text(
            "---\ntype: widget\ntitle: W\ndescription: d\n---\nbody\n", encoding="utf-8")

        r = validate(self.vault, "--strict")
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 1, r.stdout)
        line = next((l for l in r.stdout.splitlines() if "edges.widget" in l), "")
        self.assertIn(REL, line, r.stdout)
        self.assertIn("looks like a class", line)
        self.assertIn("classes:", line)

    def test_an_edge_entry_with_no_edge_key_is_named(self):
        self.insert_after("edges:\n", "  mentors: {}\n")
        found = [p for p in self.problems() if "edges.mentors" in p]
        self.assertEqual(len(found), 1, self.problems())
        self.assertIn("neither `inverse` nor `transitive`", found[0])

    def test_an_unknown_top_level_key_is_named(self):
        self.write(self.shipped + "\nclasess:\n  widget: {extends: note}\n")
        found = [p for p in self.problems() if "clasess" in p]
        self.assertEqual(len(found), 1, self.problems())
        self.assertIn("unknown top-level key", found[0])

    def test_a_real_edge_is_not_flagged(self):
        self.insert_after("edges:\n", "  mentors: { inverse: mentored_by }\n  blocks: { transitive: true }\n")
        self.assertEqual(self.problems(), [])


class MalformedFileIsAFindingNotATraceback(OntologyFileCase):
    def assert_named_and_survived(self, needle: str) -> None:
        onto = kitgraph.load_ontology(self.vault)
        self.assertEqual(onto.source, kitgraph.DEFAULT_ONTOLOGY, "falls back to the kit's own, as for an undecodable file")
        self.assertTrue(kitgraph.build_graph(self.vault, onto).nodes)
        r = validate(self.vault, "--strict")
        self.assertNotIn("Traceback", r.stderr + r.stdout)
        self.assertEqual(r.returncode, 1, r.stdout)
        line = next((l for l in r.stdout.splitlines() if REL in l and needle in l), "")
        self.assertTrue(line, f"no finding naming {REL} and {needle!r}:\n{r.stdout}")

    def test_a_yaml_error(self):
        self.write("classes: [\n")
        self.assert_named_and_survived("not valid YAML")

    def test_a_top_level_that_is_not_a_mapping(self):
        self.write("- classes\n- edges\n")
        self.assert_named_and_survived("must be a mapping")

    def test_an_undecodable_file_is_named_too(self):
        """load_ontology's docstring promised `validate` names it; nothing did."""
        self.path.write_bytes(self.shipped.encode("utf-16"))
        self.assert_named_and_survived("UTF-8")

    def test_sections_of_the_wrong_shape_do_not_crash_the_graph(self):
        cases = {
            "edges list": self.shipped.replace("edges:\n", "edges:\n  - widget\nedges_was:\n", 1),
            "edge null": self.shipped.replace("edges:\n", "edges:\n  widget:\n", 1),
            "edge string": self.shipped.replace("edges:\n", "edges:\n  widget: owns\n", 1),
            "classes list": "classes: [note]\n",
            "props list": "classes:\n  note: {props: [title]}\n",
            "extends list": "classes:\n  note: {extends: [node]}\n",
            "alias table string": "value_aliases:\n  health: amber\n",
            "alias target list": "property_aliases:\n  summary: [description]\n",
            "context file number": "context_file: 3\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.write(text)
                g = kitgraph.build_graph(self.vault)
                self.assertTrue(g.nodes)
                self.assertTrue(self.problems(), f"{name}: no finding")
                r = validate(self.vault)
                self.assertNotIn("Traceback", r.stderr + r.stdout, name)

    def test_empty_sections_are_fine(self):
        self.write("classes:\nedges: {}\nproperty_aliases:\n")
        self.assertTrue(kitgraph.build_graph(self.vault).nodes)
        self.assertEqual(self.problems(), [])


class AliasesOntoSensitivityAreRefused(OntologyFileCase):
    """#121, the owner's decision: one answer from every reader, by refusing the alias.

    The graph read the normalised props, so `sensitivity: hidden` with a value alias
    `hidden → confidential` hid the note from the graph only; `index.md`, the digest, `todos`,
    `llm ask` and the bridge read the raw line and served it. `validate` now refuses such an
    alias, and the raw `sensitivity:` line stays the only one any reader judges.
    """

    NOTE = "---\ntype: concept\ntitle: Hidden probe\ndescription: d\nsensitivity: hidden\n---\nbody\n"

    def assert_refused(self, needle: str) -> None:
        (self.vault / "07-knowledge" / "hidden-probe.md").write_text(self.NOTE, encoding="utf-8")
        found = [p for p in self.problems() if needle in p]
        self.assertEqual(len(found), 1, self.problems())
        self.assertIn("refused", found[0])
        self.assertIn("raw `sensitivity:` line", found[0])
        r = validate(self.vault, "--strict")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(needle, r.stdout)

    def test_a_value_alias_on_sensitivity(self):
        self.insert_after("value_aliases:\n", "  sensitivity: { hidden: confidential }\n")
        self.assert_refused("`value_aliases.sensitivity`")

    def test_a_property_alias_onto_sensitivity(self):
        self.insert_after("property_aliases:\n", "  hidden_level: sensitivity\n")
        self.assert_refused("`property_aliases.hidden_level`")

    def test_the_readers_judge_the_raw_line_only(self):
        """The shared predicate is unchanged: no ontology, no alias, the raw value."""
        self.insert_after("value_aliases:\n", "  sensitivity: { hidden: confidential }\n")
        path = self.vault / "07-knowledge" / "hidden-probe.md"
        path.write_text(self.NOTE, encoding="utf-8")
        note = kitgraph.kitlib.parse_note(path, self.vault)
        self.assertFalse(kitgraph.kitlib.note_confidential(note))

    def test_the_graph_keeps_its_props_check_as_defence_in_depth(self):
        node = kitgraph.Node(id="n.md", kind="note", type="concept", title="n", path="n.md",
                             props={"sensitivity": "confidential"})
        self.assertTrue(kitgraph.node_confidential(node, lambda _rel: False))


if __name__ == "__main__":
    unittest.main()
