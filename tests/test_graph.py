"""Ontology validation, graph build, derived facts, exports, SQLite queries — on the template and on injected faults."""
import datetime as dt
import json
import re
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT, VAULT, kitlib, temp_vault

import kitgraph

# the N-Triples IRIREF charset: a space — or anything else the grammar forbids — inside an IRI fails here
IRIREF = r'<[^<>"{}|^`\\\x00-\x20]*>'
NT_LINE = re.compile(rf'^{IRIREF} {IRIREF} ({IRIREF}|"(?:[^"\\]|\\.)*"(?:\^\^{IRIREF})?) \.$')


class Ontology(unittest.TestCase):
    def test_loads_and_inherits(self):
        onto = kitgraph.load_ontology(VAULT)
        self.assertTrue(onto.source and onto.source.name == "ontology.yml")
        self.assertTrue(onto.is_a("runbook", "knowledge") and onto.is_a("runbook", "node"))
        props = onto.props("spec")
        self.assertEqual(props["status"].values, ["draft", "stable", "deprecated"])
        self.assertEqual(props["project"].range, ["project", "area"])
        self.assertTrue(onto.props("project")["owner"].required)
        self.assertEqual(onto.inverse("owner"), "owns"); self.assertTrue(onto.transitive("supersedes"))

    def test_property_and_value_aliases(self):
        onto = kitgraph.load_ontology(VAULT)
        fm, notes = onto.normalise({"summary": "x", "health": "amber", "type": "project"})
        self.assertEqual(fm["description"], "x"); self.assertEqual(fm["health"], "yellow"); self.assertEqual(len(notes), 2)

    def test_template_vault_is_clean(self):
        g = kitgraph.build_graph(VAULT)
        self.assertEqual([f.render() for f in g.findings], [])

    def test_detects_range_enum_required_and_unresolved(self):
        v = temp_vault()
        try:
            (v / "03-projects/bad.md").write_text("---\ntype: project\ndescription: x\nhealth: purple\nowner: \"[Hybrid search](../07-knowledge/hybrid-search.md)\"\npeople: [Nobody Known, Alex Example]\n---\n", encoding="utf-8")
            (v / "06-decisions/2026-09-20-orphan.md").write_text("---\ntype: decision\ndescription: x\nstate: open\n---\n", encoding="utf-8")
            (v / "07-knowledge/legacy.md").write_text("---\ntype: concept\nsummary: old key\n---\n", encoding="utf-8")
            g = kitgraph.build_graph(v)
            codes = {(f.code, f.node) for f in g.findings}
            self.assertIn(("enum_violation", "03-projects/bad.md"), codes)
            self.assertIn(("range_violation", "03-projects/bad.md"), codes)
            self.assertIn(("unresolved_ref", "03-projects/bad.md"), codes)
            self.assertIn(("missing_required", "06-decisions/2026-09-20-orphan.md"), codes)   # date
            self.assertIn(("decision_without_context", "06-decisions/2026-09-20-orphan.md"), codes)
            self.assertIn(("property_alias", "07-knowledge/legacy.md"), codes)
            self.assertEqual(g.nodes["07-knowledge/legacy.md"].props["description"], "old key", "property alias summary→description applied")
            rep = kitgraph.ontology_report(g)
            self.assertTrue(rep.errors and rep.warnings)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_resolver_methods(self):
        g = kitgraph.build_graph(VAULT)
        r = kitgraph.Resolver(g.nodes, kitgraph.load_ontology(VAULT), "human:me")
        self.assertEqual(r.resolve("[Alex Example](../05-people/alex-example.md)", ["person"], "03-projects/x.md")[0], "05-people/alex-example.md")
        self.assertEqual(r.resolve("Alex", ["person"])[0], "05-people/alex-example.md", "unique first name resolves")
        self.assertEqual(r.resolve("[[hybrid-search]]")[0], "07-knowledge/hybrid-search.md")
        self.assertEqual(r.resolve("[[hybrid-search.md]]")[0], "07-knowledge/hybrid-search.md", "an explicit extension resolves, as in Obsidian")
        self.assertEqual(r.resolve("me")[0], kitgraph.SELF_ID)
        nid, method, cands = r.resolve("Platform engineering", ["team"])
        self.assertEqual(nid, "05-people/team-platform-engineering.md", "range preference disambiguates team vs area title")
        nid, method, cands = r.resolve("Alx Exampel", ["person"])
        self.assertIsNone(nid); self.assertIn("05-people/alex-example.md", cands, "fuzzy suggestion")


class GraphBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = kitgraph.build_graph(VAULT)

    def test_nodes_and_edges(self):
        g = self.g
        self.assertIn("self", g.nodes); self.assertIn("source:https://github.com/tobi/qmd", g.nodes)
        preds = {p for _, p, _, _ in g.edges}
        for p in ("owner", "owns", "people", "involved_in", "project", "has_item", "links_to", "linked_from", "cites", "has_source", "generated_by", "verified_by", "team", "has_member"):
            self.assertIn(p, preds, p)
        self.assertIn(("03-projects/search-relaunch.md", "owner", "05-people/alex-example.md", "frontmatter"), g.edges)
        self.assertIn(("05-people/alex-example.md", "owns", "03-projects/search-relaunch.md", "derived"), g.edges)
        self.assertIn(("07-knowledge/hybrid-search.md", "cites", "source:https://github.com/tobi/qmd", "body"), g.edges)

    def test_derived_facts(self):
        n = self.g.nodes["07-knowledge/hybrid-search.md"]
        self.assertEqual(n.derived["trust_tier"], "human-reviewed"); self.assertFalse(n.derived["stale"]); self.assertFalse(n.derived["at_risk"])
        self.assertEqual(self.g.nodes["07-knowledge/search-relaunch-spec.md"].derived["trust_tier"], "unverified")

    def test_neighbors_path_pack(self):
        g = self.g
        rows = kitgraph.neighbors(g, "alex-example", 1, ["owns"])
        self.assertIn(("05-people/alex-example.md", "owns", "03-projects/search-relaunch.md", 1), rows)
        path = kitgraph.shortest_path(g, "hybrid-search", "alex-example")
        self.assertTrue(path and path[-1][2] == "05-people/alex-example.md")
        self.assertNotIn("generated_by", [p for _, p, _ in path], "provenance edges are skipped")
        pack = kitgraph.context_pack(g, "search-relaunch")
        self.assertIn("- owner: Alex Example", pack); self.assertIn("health: yellow", pack)

    def test_transitive_supersedes_and_state_conflict(self):
        v = temp_vault()
        try:
            for i, sup in ((1, ""), (2, "2026-09-01-d1.md"), (3, "2026-09-02-d2.md")):
                (v / f"06-decisions/2026-09-0{i}-d{i}.md").write_text(
                    f"---\ntype: decision\ntitle: d{i}\ndescription: x\ndate: 2026-09-0{i}\nstate: decided\nproject: \"[Search relaunch](../03-projects/search-relaunch.md)\"\nsupersedes: \"{('[' + sup + '](' + sup + ')') if sup else ''}\"\n---\n", encoding="utf-8")
            g = kitgraph.build_graph(v)
            self.assertIn(("06-decisions/2026-09-03-d3.md", "supersedes", "06-decisions/2026-09-01-d1.md", "derived"), g.edges)
            self.assertIn("state_conflict", {f.code for f in g.findings})
            self.assertEqual(g.nodes["06-decisions/2026-09-01-d1.md"].derived["superseded_by"], "06-decisions/2026-09-02-d2.md",
                             "the direct successor, not the transitive latest")
            self.assertEqual(sorted(f.node for f in g.findings if f.code == "state_conflict"),
                             ["06-decisions/2026-09-01-d1.md", "06-decisions/2026-09-02-d2.md"], "one finding per superseded decision")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_body_wikilink_with_extension_links(self):
        v = temp_vault()
        try:
            (v / "07-knowledge/citer.md").write_text(
                "---\ntype: concept\ndescription: x\n---\n\n# Citer\n\nsee [[hybrid-search.md]]\n", encoding="utf-8")
            g = kitgraph.build_graph(v)
            self.assertIn(("07-knowledge/citer.md", "links_to", "07-knowledge/hybrid-search.md", "body"), g.edges)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_stale_and_at_risk_propagate(self):
        v = temp_vault()
        try:
            kitlib.set_frontmatter(v / "07-knowledge/hybrid-search.md", {"stale_after": "2020-01-01T00:00:00Z"})
            g = kitgraph.build_graph(v)
            self.assertTrue(g.nodes["07-knowledge/hybrid-search.md"].derived["stale"])
            self.assertTrue(g.nodes["07-knowledge/search-relaunch-spec.md"].derived["at_risk"], "spec links to the stale concept")
            self.assertTrue(g.nodes["03-projects/search-relaunch.md"].derived["at_risk"], "project has an at-risk spec")
            self.assertIn("project_at_risk", {f.code for f in g.findings})
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class Exports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.onto = kitgraph.load_ontology(VAULT)
        cls.g = kitgraph.build_graph(VAULT, cls.onto)
        cls.ctx = kitgraph.load_context(VAULT, cls.onto)

    def test_json_is_deterministic(self):
        a = json.dumps(kitgraph.to_json(self.g), sort_keys=True); b = json.dumps(kitgraph.to_json(kitgraph.build_graph(VAULT, self.onto)), sort_keys=True)
        self.assertEqual(json.loads(a)["edges"], json.loads(b)["edges"])

    def test_ntriples_well_formed_and_mapped(self):
        lines = kitgraph.to_ntriples(self.g, self.ctx, self.onto.base_iri)
        self.assertTrue(lines)
        bad = [l for l in lines if not NT_LINE.match(l)]
        self.assertEqual(bad, [])
        joined = "\n".join(lines)
        self.assertIn("<http://purl.org/dc/terms/title>", joined)
        self.assertIn("<https://schema.org/accountablePerson>", joined)
        self.assertIn("<https://schema.org/Person>", joined)
        self.assertIn("<http://www.w3.org/ns/prov#wasAttributedTo>", joined)

    def test_ntriples_escapes_spaced_types_and_keys(self):
        v = temp_vault()
        try:
            (v / "07-knowledge/spaced.md").write_text(
                "---\ntype: meeting note\ndescription: x\nmy key: hello\n---\n", encoding="utf-8")
            onto = kitgraph.load_ontology(v)
            lines = kitgraph.to_ntriples(kitgraph.build_graph(v, onto), kitgraph.load_context(v, onto), onto.base_iri)
            self.assertEqual([l for l in lines if not NT_LINE.match(l)], [], "spaced terms must not leak into IRIs")
            joined = "\n".join(lines)
            self.assertIn("<urn:okf-vault-kit:Meeting%20note>", joined)
            self.assertIn("<urn:okf-vault-kit:my%20key>", joined)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_verification_timestamp_is_exported(self):
        self.assertIn("verified_at", self.ctx, "context.jsonld must map verified_at")
        nid = "07-knowledge/hybrid-search.md"
        at = self.g.nodes[nid].props["verified"]["at"]
        joined = "\n".join(kitgraph.to_ntriples(self.g, self.ctx, self.onto.base_iri))
        self.assertIn(f'<{self.onto.base_iri}{nid}> <http://www.w3.org/ns/prov#atTime> "{at}" .', joined)
        node = next(n for n in kitgraph.to_jsonld(self.g, self.ctx, self.onto.base_iri)["@graph"] if n["@id"].endswith(nid))
        self.assertEqual(node["verified_at"], at)

    def test_unresolved_ref_survives_as_a_literal(self):
        v = temp_vault()
        try:
            (v / "06-decisions/2026-09-21-gone.md").write_text(
                "---\ntype: decision\ndescription: x\ndate: 2026-09-21\nstate: open\n"
                "project: \"[Search relaunch](../03-projects/search-relaunch.md)\"\n"
                "owner: \"[Gone Person](../05-people/gone-person.md)\"\n---\n", encoding="utf-8")
            onto = kitgraph.load_ontology(v)
            g = kitgraph.build_graph(v, onto)
            ctx = kitgraph.load_context(v, onto)
            nid = "06-decisions/2026-09-21-gone.md"
            self.assertEqual([e for e in g.edges if e[0] == nid and e[1] == "owner"], [], "the owner never resolved")
            joined = "\n".join(kitgraph.to_ntriples(g, ctx, onto.base_iri))
            self.assertIn('<https://schema.org/accountablePerson> "[Gone Person](../05-people/gone-person.md)"', joined)
            node = next(n for n in kitgraph.to_jsonld(g, ctx, onto.base_iri)["@graph"] if n["@id"].endswith(nid))
            self.assertEqual(node["owner"], {"@value": "[Gone Person](../05-people/gone-person.md)"})
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_jsonld_shape(self):
        doc = kitgraph.to_jsonld(self.g, self.ctx, self.onto.base_iri)
        self.assertIn("@context", doc); ids = {n["@id"] for n in doc["@graph"]}
        self.assertIn(self.onto.base_iri + "03-projects/search-relaunch.md", ids)
        proj = next(n for n in doc["@graph"] if n["@id"].endswith("search-relaunch.md"))
        self.assertEqual(proj["@type"], "Project")
        self.assertEqual(proj["owner"][0]["@id"], self.onto.base_iri + "05-people/alex-example.md")
        self.assertNotIn("Alex", json.dumps(proj.get("owner")))

    def test_sqlite_queries(self):
        con = kitgraph.to_sqlite(self.g, ":memory:")
        cols, rows = kitgraph.query_sqlite(con, "select count(*) from nodes where kind='note'")
        self.assertEqual(rows[0][0], sum(1 for n in self.g.nodes.values() if n.kind == "note"))
        cols, rows = kitgraph.query_sqlite(con, "select trust_tier from v_trust where id='07-knowledge/hybrid-search.md'")
        self.assertEqual(rows[0][0], "human-reviewed")
        cols, rows = kitgraph.query_sqlite(con, "with recursive dep(id) as (select '07-knowledge/hybrid-search.md' union select e.src from edges e join dep on e.dst=dep.id where e.pred in ('links_to','cites')) select count(*) from dep")
        self.assertGreater(rows[0][0], 2)
        with self.assertRaises(ValueError):
            kitgraph.query_sqlite(con, "delete from nodes")

    def test_sqlite_rebuild_is_atomic_and_reports_a_locked_file(self):
        v = temp_vault()
        try:
            db = v / ".kit" / "graph.sqlite"
            kitgraph.to_sqlite(self.g, db).close()
            kitgraph.to_sqlite(self.g, db).close()   # over an existing database
            self.assertTrue(db.exists())
            self.assertEqual(sorted(p.name for p in db.parent.glob("*.tmp")), [])
            before = db.read_bytes()
            with mock.patch("kitgraph.os.replace", side_effect=PermissionError(13, "in use")):
                with self.assertRaises(SystemExit) as caught:
                    kitgraph.to_sqlite(self.g, db)
            self.assertIn("pause syncing", str(caught.exception))
            self.assertEqual(db.read_bytes(), before, "a failed build leaves the old database in place")
            self.assertEqual(sorted(p.name for p in db.parent.glob("*.tmp")), [], "no temporary database is left behind")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_write_artifacts(self):
        v = temp_vault()
        try:
            paths = kitgraph.write_artifacts(v, kitgraph.build_graph(v), kitgraph.load_ontology(v))
            for p in paths.values():
                self.assertTrue(p.exists() and p.stat().st_size > 100, p)
            con = sqlite3.connect(str(paths["sqlite"]))
            self.assertEqual(con.execute("select value from meta where key='nodes'").fetchone()[0], str(len(kitgraph.build_graph(v).nodes)))
            self.assertEqual(kitlib.validate_okf(v).errors, [], ".kit is ignored by the validator")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)
