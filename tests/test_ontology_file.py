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


class StructureFindingsStayHygiene(OntologyFileCase):
    """#121 made the sensitivity refusal fail plain `validate`; the other findings did not move."""

    def test_a_class_under_edges_or_an_unknown_key_fails_only_strict(self):
        for name, edit in (("class under edges", lambda: self.insert_after(
                               "edges:\n", "  widget:\n    extends: note\n    props:\n      colour: text\n")),
                           ("unknown top-level key", lambda: self.write(
                               self.shipped + "\nclasess:\n  widget: {extends: note}\n"))):
            with self.subTest(name):
                edit()
                r = validate(self.vault)
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertIn("do not set the exit code", r.stdout)
                self.assertNotIn("with or without --strict", r.stdout)
                self.assertEqual(validate(self.vault, "--strict").returncode, 1)


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

    def test_yaml_that_scans_but_cannot_be_constructed(self):
        """PyYAML raises these as ValueError, KeyError, IndexError or AttributeError, not YAMLError.

        Only the kit's own words are asserted: the reason after them is CPython's and may change.
        """
        cases = {
            "impossible date": ("version: 2026-02-30\n", "ValueError"),
            "impossible timestamp": ("version: 2026-13-01T00:00:00\n", "ValueError"),
            "bad int tag": ("version: !!int 0b\n", "not valid YAML"),
            "bad bool tag": ("version: !!bool x\n", "not valid YAML"),
            "empty int tag": ("version: !!int ''\n", "not valid YAML"),
            "bad timestamp tag": ("version: !!timestamp x\n", "not valid YAML"),
        }
        for name, (text, needle) in cases.items():
            with self.subTest(name):
                self.write(self.shipped + text)
                self.assert_named_and_survived(needle)
                r = subprocess.run(KIT + ["validate", "--vault", str(self.vault), "--fix-aliases"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
                self.assertNotIn("Traceback", r.stderr + r.stdout, name)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

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
            "prop spec number": "classes:\n  node: {props: {title: 3}}\n",
            "prop values number": "classes:\n  node: {props: {health: {type: enum, values: 3}}}\n",
            "prop range number": "classes:\n  node: {props: {owner: {type: ref, range: 3}}}\n",
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
    alias, the graph ignores it (`TheGraphIgnoresAliasesOntoSensitivity`), and the raw
    `sensitivity:` line stays the only one any reader judges.
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

    def test_a_property_alias_that_differs_from_sensitivity_only_in_case_or_spacing(self):
        """`Sensitivity: confidential` was hidden by the graph and served by every other reader,
        which read the exact key `sensitivity`; the identity exemption let it through."""
        for alias in ("Sensitivity", "SENSITIVITY", "'sensitivity '"):
            with self.subTest(alias):
                self.insert_after("property_aliases:\n", f"  {alias}: sensitivity\n")
                self.assert_refused(f"`property_aliases.{alias.strip(chr(39))}`")

    def test_the_identity_entry_is_not_refused(self):
        self.insert_after("property_aliases:\n", "  sensitivity: sensitivity\n")
        self.assertEqual(self.problems(), [])

    def test_the_readers_judge_the_raw_line_only(self):
        """The shared predicate is unchanged: no ontology, no alias, the raw value."""
        self.insert_after("value_aliases:\n", "  sensitivity: { hidden: confidential }\n")
        path = self.vault / "07-knowledge" / "hidden-probe.md"
        path.write_text(self.NOTE, encoding="utf-8")
        note = kitgraph.kitlib.parse_note(path, self.vault)
        self.assertFalse(kitgraph.kitlib.note_confidential(note))

    def test_plain_validate_fails_on_it_too(self):
        """The owner's decision on #121: a security misconfiguration, not hygiene, so the exit code
        does not wait for `--strict`, and the note that excuses hygiene findings does not claim it."""
        for section, entry, needle in (("value_aliases:\n", "  sensitivity: { hidden: confidential }\n",
                                        "`value_aliases.sensitivity`"),
                                       ("property_aliases:\n", "  hidden_level: sensitivity\n",
                                        "`property_aliases.hidden_level`")):
            with self.subTest(needle):
                self.insert_after(section, entry)
                r = validate(self.vault)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(needle, r.stdout)
                self.assertIn("fails `validate` with or without --strict", r.stdout)
                self.assertNotIn("do not set the exit code", r.stdout,
                                 "the only finding sets the exit code; the hygiene note would say it does not")

    def test_with_no_ontology_too(self):
        """`--no-ontology` skips the graph's report, not the file check: the bridge still applies it."""
        self.insert_after("value_aliases:\n", "  sensitivity: { hidden: confidential }\n")
        self.assertEqual(validate(self.vault, "--no-ontology").returncode, 1)

    def test_alongside_a_hygiene_finding_both_notes_are_printed(self):
        self.insert_after("value_aliases:\n", "  sensitivity: { hidden: confidential }\n")
        self.write(self.path.read_text(encoding="utf-8") + "\nclasess:\n  widget: {extends: note}\n")
        r = validate(self.vault)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("fails `validate` with or without --strict", r.stdout)
        self.assertIn("do not set the exit code", r.stdout)

    def test_the_graph_keeps_its_props_check_as_defence_in_depth(self):
        node = kitgraph.Node(id="n.md", kind="note", type="concept", title="n", path="n.md",
                             props={"sensitivity": "confidential"})
        self.assertTrue(kitgraph.node_confidential(node, lambda _rel: False))


class TheGraphIgnoresAliasesOntoSensitivity(OntologyFileCase):
    """#121, the owner's decision: the ontology reserves `sensitivity`.

    `validate` refused an alias onto it, but the graph applied it until it was removed, so the
    graph hid `sensitivity: hidden` (with `hidden → confidential`) and `classification:
    confidential` (with `classification → sensitivity`) while every other reader, judging the raw
    `sensitivity:` line through kitlib, served them. The graph now drops such entries, so both
    judge the same line; `validate` still fails and names each entry.
    """

    ENTRIES = (("value_aliases:\n", "  sensitivity: { hidden: confidential }\n", "`value_aliases.sensitivity`"),
               ("property_aliases:\n", "  classification: sensitivity\n", "`property_aliases.classification`"),
               ("property_aliases:\n", "  Sensitivity: sensitivity\n", "`property_aliases.Sensitivity`"))
    PROBES = {
        "07-knowledge/value-probe.md": "sensitivity: hidden\n",
        "07-knowledge/classification-probe.md": "classification: confidential\n",
        "07-knowledge/case-probe.md": "Sensitivity: confidential\n",
    }

    def setUp(self):
        super().setUp()
        text = self.shipped
        for section, entry, _needle in self.ENTRIES:
            text = text.replace(section, section + entry, 1)
        self.write(text)
        for rel, line in self.PROBES.items():
            self.note(rel, f"---\ntype: concept\ntitle: {rel}\ndescription: d\n{line}---\nbody\n")

    def note(self, rel: str, text: str) -> None:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_the_ontology_drops_the_entries(self):
        onto = kitgraph.load_ontology(self.vault)
        self.assertNotIn("sensitivity", onto.value_aliases)
        self.assertNotIn("classification", onto.property_aliases)
        self.assertNotIn("Sensitivity", onto.property_aliases)
        self.assertEqual(onto.property_aliases.get("summary"), "description", "the other aliases still apply")

    def test_the_graph_keeps_the_raw_value_and_serves_what_kitlib_serves(self):
        g = kitgraph.build_graph(self.vault)
        self.assertEqual(g.nodes["07-knowledge/value-probe.md"].props.get("sensitivity"), "hidden")
        self.assertNotIn("sensitivity", g.nodes["07-knowledge/classification-probe.md"].props)
        self.assertNotIn("sensitivity", g.nodes["07-knowledge/case-probe.md"].props)
        served = kitgraph.redact_vault(g, self.vault).nodes
        for rel in self.PROBES:
            with self.subTest(rel):
                self.assertFalse(kitgraph.kitlib.is_confidential(self.vault, rel))
                self.assertIn(rel, served)

    def test_plain_validate_fails_naming_each_entry(self):
        r = validate(self.vault)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 1, r.stdout)
        for _section, _entry, needle in self.ENTRIES:
            with self.subTest(needle):
                line = next((ln for ln in r.stdout.splitlines() if needle in ln), "")
                self.assertIn("reserves `sensitivity`", line, r.stdout)
                self.assertIn("ignored", line)

    def test_every_node_gets_the_answer_kitlib_gives(self):
        """Parity over the whole vault, with each way a note can be confidential or not."""
        config = self.vault / kitgraph.kitlib.CONFIG_REL
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('confidentiality:\n  confidential_folders: ["05-people"]\n', encoding="utf-8")
        self.note("07-knowledge/marked.md", "---\ntype: concept\ntitle: M\ndescription: d\nsensitivity: confidential\n---\n")
        self.note("07-knowledge/marked-odd.md",
                  "---\ntype: concept\ntitle: O\ndescription: d\nsensitivity: ' Confidential '\n---\n")
        self.note("07-knowledge/broken.md", "---\ntype: [concept\ntitle: B\n---\n")
        self.note("05-people/floored.md", "---\ntype: person\ntitle: F\ndescription: d\nsensitivity: internal\n---\n")
        g = kitgraph.build_graph(self.vault)
        served = kitgraph.redact_vault(g, self.vault).nodes
        notes = {nid: n for nid, n in g.nodes.items() if n.kind == "note"}
        decisions = {}
        for nid, n in notes.items():
            with self.subTest(nid):
                by_kitlib = kitgraph.kitlib.is_confidential(self.vault, n.path)
                self.assertEqual(nid not in served, by_kitlib)
                self.assertEqual(kitgraph.node_confidential(n, lambda rel: kitgraph.kitlib.is_confidential(self.vault, rel)),
                                 by_kitlib)
                decisions[nid] = by_kitlib
        self.assertTrue({True, False} <= set(decisions.values()), "the fixture must hold both outcomes")
        for rel in ("07-knowledge/marked.md", "07-knowledge/marked-odd.md", "05-people/floored.md",
                    "07-knowledge/broken.md"):
            self.assertTrue(decisions.get(rel), rel)


class TheGraphIgnoresAnAliasRenamingSensitivityAway(OntologyFileCase):
    """#121: reserved both ways. `sensitivity: classification` under `property_aliases` renamed the
    raw line to another key in the graph, so `props["sensitivity"]` was no longer what kitlib
    judges; the loader now drops it and plain `validate` refuses it, by the same check."""

    def setUp(self):
        super().setUp()
        self.insert_after("property_aliases:\n", "  sensitivity: classification\n")
        for name, value in (("marked", "confidential"), ("open", "internal")):
            (self.vault / "07-knowledge" / f"{name}-probe.md").write_text(
                f"---\ntype: concept\ntitle: {name}\ndescription: d\nsensitivity: {value}\n---\nbody\n",
                encoding="utf-8")

    def test_the_ontology_drops_it(self):
        self.assertNotIn("sensitivity", kitgraph.load_ontology(self.vault).property_aliases)

    def test_the_graph_keeps_the_raw_value_and_agrees_with_kitlib(self):
        g = kitgraph.build_graph(self.vault)
        self.assertEqual(g.nodes["07-knowledge/marked-probe.md"].props.get("sensitivity"), "confidential")
        self.assertEqual(g.nodes["07-knowledge/open-probe.md"].props.get("sensitivity"), "internal")
        self.assertNotIn("classification", g.nodes["07-knowledge/marked-probe.md"].props)
        served = kitgraph.redact_vault(g, self.vault).nodes
        decisions = {}
        for nid, n in g.nodes.items():
            if n.kind != "note":
                continue
            with self.subTest(nid):
                by_kitlib = kitgraph.kitlib.is_confidential(self.vault, n.path)
                self.assertEqual(nid not in served, by_kitlib)
                decisions[nid] = by_kitlib
        self.assertTrue(decisions["07-knowledge/marked-probe.md"])
        self.assertFalse(decisions["07-knowledge/open-probe.md"])

    def test_plain_validate_fails_naming_it(self):
        r = validate(self.vault)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 1, r.stdout)
        line = next((ln for ln in r.stdout.splitlines() if "`property_aliases.sensitivity`" in ln), "")
        self.assertIn("reserves `sensitivity`", line, r.stdout)
        self.assertIn("ignored", line)


if __name__ == "__main__":
    unittest.main()
