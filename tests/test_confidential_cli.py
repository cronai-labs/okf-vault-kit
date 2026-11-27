"""Confidential notes stay out of what the CLI hands a model or writes to disk (#52).

`llm ask` drops a confidential hit whole, title included, as the bridge does; the `graph`
commands and `llm ask --graph` read the graph through the same redaction as the bridge's graph
tools, and `--show-confidential` opts back in with the bridge's meaning.
"""
import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from tests.helpers import ROOT, temp_vault

sys.path.insert(0, str(ROOT / "mcp"))
import bridge_policy as bp
import kit
import kitgraph
import vault_bridge as ob

KIT = [sys.executable, str(ROOT / "kit.py")]

# A confidential note whose every field is a marker no other sample note carries, so a marker in
# the output can only have come from this note. Its `project` link gives the open project an
# inverse `has_item` edge: the context pack of the open note would name it.
FALCON_REL = "03-projects/nda-hidden.md"
FALCON = ("---\ntype: decision\ntitle: Codename Falcon\ndescription: HIDDEN-DESC merger talks\n"
          "sensitivity: confidential\nstate: open\ndate: 2026-09-20\n"
          "project: \"[Search relaunch](search-relaunch.md)\"\n---\n# Codename Falcon\n\nHIDDEN-BODY merger terms. The merger terms are agreed; merger talks go on.\n")
FALCON_MARKERS = ("nda-hidden", "Codename Falcon", "HIDDEN-DESC", "HIDDEN-BODY")
# The shipped sample's confidential notes; their description text appears in no other note's
# graph node (the open notes name the person in their own fields, which is theirs to show).
PEOPLE = ("05-people/alex-example.md", "05-people/sam-example.md")
PEOPLE_DESC = ("prefers written context before meetings", "owns the embedding-model evaluation")
QUESTION = "what are the merger terms"
# names the open project too, so its context pack is in the prompt: the pack that used to label the confidential note
GRAPH_QUESTION = "what are the merger terms for the search relaunch"
LEFT_OUT = re.compile(r"^(\d+) matching confidential note\(s\) were left out of the prompt\.$", re.M)


def run(*args, **kw):
    return subprocess.run(KIT + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=False, **kw)


def falcon_vault() -> Path:
    v = temp_vault()
    (v / FALCON_REL).write_text(FALCON, encoding="utf-8", newline="\n")
    return v


def labelled(text: str, rel: str) -> bool:
    """Does `text` cite `rel` as a source line or label it as a graph node? A visible note's own
    link to it is not a label: that is the visible note's content."""
    return bool(re.search(rf"^\[\d+\] {re.escape(rel)} —", text, re.M)) or f"({rel}) [" in text or f"- id: {rel}" in text


class Records(BaseHTTPRequestHandler):
    """An OpenAI-compatible endpoint that answers and keeps every request body."""

    seen: list = []

    def _send(self, body: dict):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        self._send({"data": [{"id": "stub"}]})

    def do_POST(self):
        type(self).seen.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"))
        self._send({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "An answer [1]."}}]})

    def log_message(self, *args):
        pass


class LlmAskLeavesConfidentialNotesOut(unittest.TestCase):
    def setUp(self):
        Records.seen = []
        srv = HTTPServer(("127.0.0.1", 0), Records)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.base = f"http://127.0.0.1:{srv.server_port}/v1"
        self.vault = falcon_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)

    def ask(self, question: str, *extra: str):
        return run("llm", "ask", question, "--vault", str(self.vault), "--base-url", self.base,
                   "--search-provider", "naive", *extra)

    def prompt(self) -> str:
        return "\n".join(m["content"] for m in Records.seen[-1]["messages"])

    def assert_no_confidential_field(self, prompt: str):
        for marker in FALCON_MARKERS:
            self.assertNotIn(marker, prompt, f"a field of {FALCON_REL} reached the prompt")
        for marker in PEOPLE_DESC:
            self.assertNotIn(marker, prompt, "a sample person's description reached the prompt")
        for rel in (FALCON_REL, *PEOPLE):
            self.assertFalse(labelled(prompt, rel), f"{rel} is cited or labelled in the prompt")
        self.assertNotIn("Delegated authority", prompt, "the body of 05-people/alex-example.md reached the prompt")

    def test_a_confidential_hit_is_dropped_whole(self):
        hits = [kit.kitproviders.hit_rel(h["file"]) for h in kit._search_hits(self.vault, QUESTION, 5, None, "naive")]
        self.assertIn(FALCON_REL, hits, "the test needs the confidential note among the hits")
        r = self.ask(QUESTION)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_no_confidential_field(self.prompt())
        m = LEFT_OUT.search(r.stdout)
        self.assertTrue(m, r.stdout)
        self.assertGreaterEqual(int(m.group(1)), 1)
        self.assertNotIn("cited by title", r.stdout, "nothing confidential is cited, by title or otherwise")
        self.assertNotIn(FALCON_REL, r.stdout.split("Sources:", 1)[1], "the Sources list is what was sent")

    def test_the_graph_route_packs_from_the_redacted_graph(self):
        r = self.ask(GRAPH_QUESTION, "--graph")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        prompt = self.prompt()
        self.assertIn("# Search relaunch (example)", prompt, "the open project's pack must still be there")
        self.assertIn("- milestone: Pilot go/no-go", prompt)
        self.assert_no_confidential_field(prompt)

    def test_a_confidential_note_named_in_the_question_is_counted_not_packed(self):
        r = self.ask("what does Alex Example need from me", "--graph")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_no_confidential_field(self.prompt())
        self.assertEqual(LEFT_OUT.search(r.stdout).group(1), "1",
                         "named in the question and found by search, it is one note left out")

    def test_when_every_hit_is_confidential_the_model_is_not_called(self):
        for f in self.vault.rglob("*.md"):
            if f.relative_to(self.vault).as_posix() != FALCON_REL:
                f.unlink()
        r = self.ask("Codename Falcon merger terms")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(Records.seen, [], "an empty prompt is not worth a call, and must not carry a title")
        self.assertEqual(LEFT_OUT.search(r.stdout).group(1), "1")
        for marker in FALCON_MARKERS:
            self.assertNotIn(marker, r.stdout)


class GraphCommandsRedactByDefault(unittest.TestCase):
    def setUp(self):
        self.vault = falcon_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)
        self.hidden = {FALCON_REL, *PEOPLE}

    def graph(self, *args):
        return run("graph", *args, "--vault", str(self.vault))

    def assert_redacted(self, text: str, what: str):
        for marker in (*FALCON_MARKERS, *PEOPLE_DESC):
            self.assertNotIn(marker, text, f"{what}: a confidential field is in it")
        for rel in self.hidden:
            self.assertFalse(labelled(text, rel), f"{what}: {rel} is labelled")

    def node_ids(self, db: Path) -> set[str]:
        con = sqlite3.connect(db)
        try:
            return {r[0] for r in con.execute("select id from nodes")}
        finally:
            con.close()

    def test_build_writes_no_confidential_node_into_any_file(self):
        r = self.graph("build")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("3 confidential note(s) left out", r.stdout)
        kit_dir = self.vault / ".kit"
        for name in ("graph.json", "graph.jsonld", "graph.nt"):
            self.assert_redacted((kit_dir / name).read_text(encoding="utf-8"), name)
        data = json.loads((kit_dir / "graph.json").read_text(encoding="utf-8"))
        ids = {n["id"] for n in data["nodes"]}
        self.assertFalse(ids & self.hidden)
        self.assertIn("03-projects/search-relaunch.md", ids, "the graph still holds the open notes")
        self.assertFalse(any(e["src"] in self.hidden or e["dst"] in self.hidden for e in data["edges"]))
        nt = (kit_dir / "graph.nt").read_text(encoding="utf-8")
        self.assertFalse(any(rel in line.split(" ", 1)[0] for line in nt.splitlines() for rel in self.hidden),
                         "no triple has a confidential note as its subject")
        db = kit_dir / "graph.sqlite"
        self.assertFalse(self.node_ids(db) & self.hidden)
        self.assertEqual(kitgraph.sqlite_confidential(db), kitgraph.CONFIDENTIAL_HIDDEN)

    def test_build_with_show_confidential_writes_everything(self):
        r = self.graph("build", "--show-confidential")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("left out", r.stdout)
        self.assertIn("Codename Falcon", (self.vault / ".kit/graph.json").read_text(encoding="utf-8"))
        db = self.vault / ".kit/graph.sqlite"
        self.assertTrue(self.hidden <= self.node_ids(db))
        self.assertEqual(kitgraph.sqlite_confidential(db), kitgraph.CONFIDENTIAL_SHOWN)

    def test_export_and_pack_hide_by_default_and_show_on_request(self):
        for fmt in ("json", "nt", "jsonld"):
            r = self.graph("export", "--format", fmt)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assert_redacted(r.stdout, f"export --format {fmt}")
            self.assertIn("Codename Falcon", self.graph("export", "--format", fmt, "--show-confidential").stdout)
        out = self.vault.parent / "export.json"
        self.assertEqual(self.graph("export", "--out", str(out)).returncode, 0)
        self.assert_redacted(out.read_text(encoding="utf-8"), "export --out")

        r = self.graph("pack", "search-relaunch")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("## Relations", r.stdout)
        self.assert_redacted(r.stdout, "pack search-relaunch")
        shown = self.graph("pack", "search-relaunch", "--show-confidential").stdout
        self.assertIn(f"Codename Falcon ({FALCON_REL})", shown)
        self.assertIn("(05-people/alex-example.md) [person", shown)
        self.assertIn("No node matches", self.graph("pack", FALCON_REL).stdout)

    def test_neighbors_and_path_do_not_reach_a_confidential_note(self):
        r = self.graph("neighbors", "search-relaunch", "--depth", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_redacted(r.stdout, "neighbors")
        self.assertEqual(self.graph("neighbors", "alex-example").returncode, 1)
        self.assertIn("05-people/alex-example.md", self.graph("neighbors", "alex-example", "--show-confidential").stdout)
        self.assertEqual(self.graph("path", "hybrid-search", "alex-example").returncode, 1)
        self.assertEqual(self.graph("path", "hybrid-search", "alex-example", "--show-confidential").returncode, 0)

    def query_ids(self, *extra: str) -> set[str]:
        r = self.graph("query", "select id from nodes", "--json", "--limit", "1000", *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return {row["id"] for row in json.loads(r.stdout)}

    def test_query_never_answers_a_default_query_from_a_file_with_confidential_nodes(self):
        db = self.vault / ".kit/graph.sqlite"
        self.assertEqual(self.graph("build", "--show-confidential").returncode, 0)
        self.assertFalse(self.query_ids() & self.hidden, "a --show-confidential build answered a default query")
        self.assertEqual(kitgraph.sqlite_confidential(db), kitgraph.CONFIDENTIAL_HIDDEN, "and the file was rewritten")
        self.assertFalse(self.node_ids(db) & self.hidden)

    def test_query_rewrites_a_database_from_a_kit_that_did_not_redact(self):
        self.assertEqual(self.graph("build", "--show-confidential").returncode, 0)
        db = self.vault / ".kit/graph.sqlite"
        con = sqlite3.connect(db)
        con.execute("delete from meta where key='confidential'"); con.commit(); con.close()
        self.assertIsNone(kitgraph.sqlite_confidential(db))
        self.assertFalse(self.query_ids() & self.hidden)
        self.assertFalse(self.node_ids(db) & self.hidden)

    def test_query_with_show_confidential_does_not_write_them_to_disk(self):
        self.assertEqual(self.graph("build").returncode, 0)
        self.assertTrue(self.hidden <= self.query_ids("--show-confidential"))
        db = self.vault / ".kit/graph.sqlite"
        self.assertEqual(kitgraph.sqlite_confidential(db), kitgraph.CONFIDENTIAL_HIDDEN)
        self.assertFalse(self.node_ids(db) & self.hidden, "a query is not a reason to write confidential nodes")


class OneRedactionForTheBridgeAndTheCli(unittest.TestCase):
    """The bridge and the CLI call the same kitgraph.redact, so they hide the same nodes."""

    def setUp(self):
        self.vault = falcon_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)
        # unparsable frontmatter: neither side can tell, so both must hide it
        (self.vault / "03-projects/broken.md").write_text("---\ntype: project\ntitle: [unclosed\n---\nBROKEN-BODY\n",
                                                          encoding="utf-8", newline="\n")

    def bridge(self, **kw) -> bp.Guarded:
        return bp.Guarded(ob.FsBackend(self.vault), bp.Policy(**kw), self.vault)

    def test_the_bridge_and_the_cli_hide_the_same_nodes(self):
        cli = set(kit._graph(self.vault).nodes)
        bridge = set(self.bridge().graph().nodes)
        self.assertEqual(cli, bridge)
        everything = set(kitgraph.build_graph(self.vault).nodes)
        self.assertEqual(everything - bridge, {FALCON_REL, *PEOPLE, "03-projects/broken.md"})
        self.assertEqual(set(kit._graph(self.vault, show_confidential=True).nodes),
                         set(self.bridge(hide_confidential=False).graph().nodes))

    def test_the_bridge_redacts_through_kitgraph(self):
        calls = []
        real = kitgraph.redact

        def spy(g, file_confidential):
            calls.append(g)
            return real(g, file_confidential)

        kitgraph.redact = spy
        try:
            ids = [r[0] for r in self.bridge().graph_query("select id from nodes")["rows"]]
        finally:
            kitgraph.redact = real
        self.assertEqual(len(calls), 1, "the bridge must not keep a redaction of its own")
        self.assertFalse(set(ids) & {FALCON_REL, *PEOPLE})


# Each confidential note below makes the full graph name its id in a visible note's derived value
# or finding. The visible notes refer to them by name, by a typo of a title or by a body link, never
# by a link in their frontmatter: a visible note's own props are exported as written, and a link
# there would put the hidden path into the export for a reason the redaction does not own.
CONF = "sensitivity: confidential\n"
ADOPT = "06-decisions/2026-09-08-adopt-qmd-for-local-search.md"
REFERRING = {
    # supersedes a visible decision: state_conflict on it, and its derived superseded_by
    "06-decisions/zz-hidden-supersedes.md": "---\ntype: decision\ntitle: Hidden successor\n" + CONF
    + "date: 2026-09-20\nstate: decided\narea: \"[Platform engineering](../04-areas/platform-engineering.md)\"\n"
    + "supersedes: \"[Adopt qmd](2026-09-08-adopt-qmd-for-local-search.md)\"\n---\nbody\n",
    # the "did you mean" of an unresolved_ref
    "03-projects/zephyr-hidden.md": "---\ntype: project\ntitle: Zephyr Hidden\nowner: me\n" + CONF + "---\nbody\n",
    # one of the candidates of an ambiguous_ref (the other one is visible)
    "05-people/kim-hidden.md": "---\ntype: person\ntitle: Kim Doppel\n" + CONF + "---\nbody\n",
    "05-people/kim-visible.md": "---\ntype: person\ntitle: Kim Doppel\nsensitivity: internal\n---\nbody\n",
    # the target of a range_violation
    "06-decisions/quartz-hidden.md": "---\ntype: decision\ntitle: Quartz Plan\n" + CONF + "date: 2026-09-21\n---\nbody\n",
    # stale knowledge: at_risk on the note that links to it, project_at_risk on the project it serves
    "07-knowledge/stale-hidden.md": "---\ntype: spec\ntitle: Withered spec\n" + CONF
    + "stale_after: 2020-01-01T00:00:00Z\nproject: \"[Search relaunch](../03-projects/search-relaunch.md)\"\n---\nbody\n",
    "06-decisions/zz-visible-refs.md": "---\ntype: decision\ntitle: Visible refs\nsensitivity: internal\ndate: 2026-09-22\n"
    "area: \"[Platform engineering](../04-areas/platform-engineering.md)\"\n"
    "project: Zephyr Secrt\nowner: Kim Doppel\npeople: [\"Quartz Plan\"]\n---\nbody\n",
    "07-knowledge/visible-relies.md": "---\ntype: concept\ntitle: Relies on the withered spec\nsensitivity: internal\n---\n"
    "See [the old spec](stale-hidden.md).\n",
}
HIDDEN_REFERRED = ("06-decisions/zz-hidden-supersedes.md", "03-projects/zephyr-hidden.md", "05-people/kim-hidden.md",
                   "06-decisions/quartz-hidden.md", "07-knowledge/stale-hidden.md")
INTERPOLATING = ("state_conflict", "unresolved_ref", "ambiguous_ref", "range_violation", "at_risk", "project_at_risk")


def referring_vault() -> Path:
    v = temp_vault()
    for rel, text in REFERRING.items():
        (v / rel).write_text(text, encoding="utf-8", newline="\n")
    return v


def names_hidden(text: str) -> list[str]:
    """The hidden ids `text` names; the stem alone is enough to identify one."""
    return [rel for rel in HIDDEN_REFERRED if Path(rel).stem in text]


class RedactionLeavesNoHiddenIdInVisibleNodes(unittest.TestCase):
    """A visible node's derived values and findings name other nodes; none of them may be hidden."""

    def setUp(self):
        self.vault = referring_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)
        self.full = kitgraph.build_graph(self.vault)
        self.red = kit._graph(self.vault)

    def test_the_full_graph_names_a_hidden_id_in_every_interpolating_finding(self):
        # the fixture's own check: without it, a later change to the sample could make every test below pass vacuously
        for code in INTERPOLATING:
            named = [f for f in self.full.findings if f.code == code and f.node not in HIDDEN_REFERRED
                     and names_hidden(f.message + f.suggestion)]
            self.assertTrue(named, f"no visible {code} finding names a hidden note in the full graph")
        self.assertEqual(self.full.nodes[ADOPT].derived.get("superseded_by"), "06-decisions/zz-hidden-supersedes.md")
        self.assertFalse(set(HIDDEN_REFERRED) & set(self.red.nodes), "the fixture's notes must all be hidden")

    def test_no_export_names_a_hidden_id(self):
        onto = kitgraph.load_ontology(self.vault)
        ctx, base = kitgraph.load_context(self.vault, onto), onto.base_iri
        con = kitgraph.to_sqlite(self.red, ":memory:")
        try:
            dump = "\n".join(con.iterdump())
        finally:
            con.close()
        for what, text in (("to_json", json.dumps(kitgraph.to_json(self.red), ensure_ascii=False)),
                           ("to_ntriples", "\n".join(kitgraph.to_ntriples(self.red, ctx, base))),
                           ("to_jsonld", json.dumps(kitgraph.to_jsonld(self.red, ctx, base), ensure_ascii=False)),
                           ("to_sqlite", dump)):
            self.assertEqual(names_hidden(text), [], f"{what} names a hidden note")

    def test_no_context_pack_names_a_hidden_id(self):
        for nid in self.red.nodes:
            pack = kitgraph.context_pack(self.red, nid, 2)
            self.assertEqual(names_hidden(pack), [], f"the context pack of {nid} names a hidden note")

    def test_the_bridge_names_no_hidden_id(self):
        g = bp.Guarded(ob.FsBackend(self.vault), bp.Policy(audit_path=None), self.vault)
        for nid in (ADOPT, "03-projects/search-relaunch.md", "06-decisions/zz-visible-refs.md", "07-knowledge/visible-relies.md"):
            self.assertEqual(names_hidden(g.graph_context(nid, 2)), [], f"graph_context {nid}")
        for table in ("nodes", "edges", "props", "derived", "findings"):
            rows = g.graph_query(f"select * from {table}", 100000)["rows"]
            self.assertEqual(names_hidden(json.dumps(rows, ensure_ascii=False)), [], f"graph_query {table}")

    def test_the_cli_names_no_hidden_id(self):
        r = run("graph", "build", "--vault", str(self.vault))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(names_hidden(r.stdout), [], "graph build printed a finding that names a hidden note")
        for name in ("graph.json", "graph.jsonld", "graph.nt"):
            self.assertEqual(names_hidden((self.vault / ".kit" / name).read_text(encoding="utf-8")), [], name)
        r = run("graph", "pack", ADOPT, "--vault", str(self.vault))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("superseded_by", r.stdout, "the superseding note is hidden, so is the fact it names")
        self.assertEqual(names_hidden(r.stdout), [])

    def test_redaction_copies_and_keeps_what_names_no_hidden_note(self):
        # llm ask reads mentions() from the full graph and the bridge caches it: redaction must not change it
        red = kitgraph.redact_vault(self.full, self.vault)
        self.assertEqual(self.full.nodes[ADOPT].derived.get("superseded_by"), "06-decisions/zz-hidden-supersedes.md")
        self.assertIsNot(red.nodes[ADOPT], self.full.nodes[ADOPT])
        self.assertNotIn("superseded_by", red.nodes[ADOPT].derived)
        self.assertIn("trust_tier", red.nodes[ADOPT].derived, "only a value naming a hidden note goes")
        kept = {f.render() for f in red.findings}
        for f in self.full.findings:
            if f.node not in red.nodes or names_hidden(f.message + f.suggestion):
                continue
            self.assertIn(f.render(), kept, "a finding about visible notes only must survive the redaction")
        self.assertTrue(kept, "the redacted graph still reports")


class SourcesAndActorsOfHiddenNotesGo(unittest.TestCase):
    """A source or actor only a confidential note names carries that note's content (a title, a URL)."""

    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)
        people = self.vault / "05-people" / "alex-example.md"
        text = people.read_text(encoding="utf-8")
        people.write_text(text.replace("sensitivity: confidential",
                                       "sensitivity: confidential\nsources: [{ id: acq, resource: \"https://deals.example/falcon-term-sheet\", "
                                       "title: \"Falcon term sheet\" }]", 1).replace("generated: { by: human:me,", "generated: { by: \"agent:falcon-dealbot\",", 1),
                          encoding="utf-8")
        visible = self.vault / "03-projects" / "search-relaunch.md"
        vtext = visible.read_text(encoding="utf-8")
        visible.write_text(vtext.replace("---\n", "---\nsources: [{ id: shared, resource: \"https://shared.example/spec\", title: \"Shared spec\" }]\n", 1),
                           encoding="utf-8")
        self.full = kitgraph.build_graph(self.vault)

    def test_the_fixture_creates_both_nodes(self):
        ids = " ".join(self.full.nodes)
        self.assertIn("falcon-term-sheet", ids)
        self.assertIn("falcon-dealbot", ids)

    def test_redaction_drops_them_and_keeps_a_visible_notes_source(self):
        red = kitgraph.redact_vault(self.full, self.vault)
        dump = json.dumps(kitgraph.to_json(red), ensure_ascii=False)
        for marker in ("falcon-term-sheet", "Falcon term sheet", "falcon-dealbot"):
            self.assertNotIn(marker, dump)
        self.assertIn("Shared spec", dump, "a source a visible note names stays")

    def test_the_bridge_query_does_not_list_them(self):
        g = bp.Guarded(ob.FsBackend(self.vault), bp.Policy(audit_path=None), self.vault)
        rows = g.graph_query("select id, title from nodes where kind in ('source', 'actor')", 1000)["rows"]
        self.assertNotIn("falcon", json.dumps(rows).lower())


class GraphQueryFollowsTheNotes(unittest.TestCase):
    """`graph query` answers from .kit/graph.sqlite only while the notes are those it was built from."""

    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)
        self.db = self.vault / ".kit/graph.sqlite"

    def query_ids(self) -> set[str]:
        r = run("graph", "query", "select id from nodes", "--json", "--limit", "1000", "--vault", str(self.vault))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return {row["id"] for row in json.loads(r.stdout)}

    def meta(self, key: str) -> str:
        con = sqlite3.connect(self.db)
        try:
            return con.execute("select value from meta where key=?", (key,)).fetchone()[0]
        finally:
            con.close()

    def test_a_note_marked_confidential_after_the_build_is_not_answered(self):
        rel = "03-projects/search-relaunch.md"
        self.assertEqual(run("graph", "build", "--vault", str(self.vault)).returncode, 0)
        self.assertIn(rel, self.query_ids())
        p = self.vault / rel
        p.write_text(p.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: confidential", 1),
                     encoding="utf-8", newline="\n")
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))   # a coarse clock must not hide the edit
        self.assertNotIn(rel, self.query_ids(), "graph query answered from a database built before the note turned confidential")
        con = sqlite3.connect(self.db)
        try:
            self.assertIsNone(con.execute("select 1 from nodes where id=?", (rel,)).fetchone(), "and the file was rewritten")
        finally:
            con.close()

    def test_an_unchanged_vault_is_answered_from_the_file(self):
        self.assertEqual(run("graph", "build", "--vault", str(self.vault)).returncode, 0)
        built = self.meta("built_at")
        self.query_ids(); self.query_ids()
        self.assertEqual(self.meta("built_at"), built, "a query on unchanged notes must not rebuild the graph")

    def test_the_signature_is_the_bridges(self):
        self.assertEqual(kitgraph.note_set_signature(self.vault), ob.FsBackend(self.vault)._note_set_signature())
        # the database stores a digest, never the paths: a hidden build must not list confidential paths in meta
        self.assertEqual(run("graph", "build", "--vault", str(self.vault)).returncode, 0)
        self.assertNotIn("alex-example", self.meta("notes"))


class TodosLeaveConfidentialTasksOut(unittest.TestCase):
    HIDDEN_TASK = "- [ ] HIDDEN-TASK sign the term sheet — me, due 2026-09-25\n"
    # one commitment in two notes: done in a visible note, still open in the confidential one
    SHARED_OPEN = "- [ ] Renew the shared certificate bundle — me, due 2026-10-01\n"
    SHARED_DONE = "- [x] Renew the shared certificate bundle — me, due 2026-10-01\n"
    LEFT_OUT = re.compile(r"^(\d+) task\(s\) in confidential notes left out "
                          r"\(--show-confidential includes them\)$", re.M)

    def setUp(self):
        self.vault = falcon_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)
        with (self.vault / FALCON_REL).open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n" + self.HIDDEN_TASK + self.SHARED_OPEN)
        with (self.vault / "03-projects/search-relaunch.md").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n" + self.SHARED_DONE)

    def todos(self, *args):
        r = run("todos", "--vault", str(self.vault), *args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def confidential_tasks(self) -> int:
        """Tasks in confidential notes: the Falcon note's two and those of the sample's people notes."""
        everything = json.loads(self.todos("--json", "--all", "--show-confidential").stdout)
        n = sum(1 for t in everything if t["file"] in (FALCON_REL, *PEOPLE))
        self.assertGreater(n, 2, "the sample's people notes carry tasks too")
        return n

    def test_the_summary_leaves_them_out_and_counts_them(self):
        r = self.todos()
        self.assertNotIn("HIDDEN-TASK", r.stdout)
        for rel in (FALCON_REL, *PEOPLE):
            self.assertNotIn(rel, r.stdout)
        self.assertEqual(int(self.LEFT_OUT.search(r.stdout).group(1)), self.confidential_tasks())
        shown = self.todos("--show-confidential")
        self.assertIn("HIDDEN-TASK", shown.stdout)
        self.assertIsNone(self.LEFT_OUT.search(shown.stdout))

    def test_json_leaves_them_out_and_counts_them_on_stderr(self):
        r = self.todos("--json", "--all")
        files = {t["file"] for t in json.loads(r.stdout)}
        self.assertFalse(files & {FALCON_REL, *PEOPLE})
        self.assertEqual(int(self.LEFT_OUT.search(r.stderr).group(1)), self.confidential_tasks(),
                         "the count goes to stderr: stdout stays JSON")
        shown = self.todos("--json", "--all", "--show-confidential")
        self.assertIn(FALCON_REL, {t["file"] for t in json.loads(shown.stdout)})

    def test_sync_done_still_ticks_a_task_in_a_confidential_note(self):
        r = self.todos("--sync-done")
        self.assertIn("synced 1 task(s) to done", r.stdout)
        self.assertNotIn("nda-hidden", r.stdout)
        self.assertIn(self.SHARED_DONE, (self.vault / FALCON_REL).read_text(encoding="utf-8"),
                      "--sync-done works on every task, whatever the listing shows")


class LlmAskKeepsNSources(unittest.TestCase):
    """Confidential hits that rank first do not use up the -n slots, as on the bridge's search."""

    def setUp(self):
        Records.seen = []
        srv = HTTPServer(("127.0.0.1", 0), Records)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.base = f"http://127.0.0.1:{srv.server_port}/v1"
        self.vault = falcon_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)

    def sources(self) -> list[str]:
        prompt = "\n".join(m["content"] for m in Records.seen[-1]["messages"])
        return re.findall(r"^\[(\d+)\] ", prompt, re.M)

    def test_n_visible_sources_when_a_confidential_note_ranks_first(self):
        # a word many open notes carry, and a confidential note that says little else
        (self.vault / "03-projects/search-heavy.md").write_text(
            "---\ntype: project\ntitle: Search search search\nsensitivity: confidential\n---\n" + "search " * 40 + "\n",
            encoding="utf-8", newline="\n")
        ranked = [kit.kitproviders.hit_rel(h["file"]) for h in kit._search_hits(self.vault, "search", 20, None, "naive")]
        self.assertEqual(ranked[0], "03-projects/search-heavy.md", "the test needs the confidential note to rank first")
        self.assertGreater(len(ranked), 3, "and enough open notes behind it")
        for n in ("1", "3"):
            r = run("llm", "ask", "search", "--vault", str(self.vault), "--base-url", self.base,
                    "--search-provider", "naive", "-n", n)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(len(self.sources()), int(n), f"-n {n} must send {n} visible source(s)")
            self.assertEqual(LEFT_OUT.search(r.stdout).group(1), "1", "only what ranked ahead of the last source is counted")


class MinutesPassesTheConfidentialChoice(unittest.TestCase):
    def test_raise_by_default_and_plain_with_keep_open(self):
        seen = []

        def record(*args, **kw):
            seen.append(kw)
            return types.SimpleNamespace(path="02-meetings/x.md", people=[], names=[], project=None, unresolved=[],
                                         confidential_links=[], outcomes=[], decisions=[], actions=[], skipped=[])

        tmp = Path(tempfile.mkdtemp(prefix="okf-minutes-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "vault").mkdir()
        recap = tmp / "recap.txt"
        recap.write_text("We met.\n", encoding="utf-8")
        real = kit.kitrecon.file_minutes
        kit.kitrecon.file_minutes = record
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                for extra in ((), ("--keep-open",)):
                    self.assertEqual(kit.main(["minutes", str(recap), "--title", "Sync", "--vault", str(tmp / "vault"),
                                               "--dry-run", *extra]), 0)
        finally:
            kit.kitrecon.file_minutes = real
        self.assertEqual([kw.get("mode") for kw in seen], ["raise", "plain"])

    def test_the_raise_is_announced_and_keep_open_files_it_open(self):
        v = temp_vault()
        self.addCleanup(shutil.rmtree, v.parent, True)
        recap = v.parent / "recap.txt"
        recap.write_text("Alex and Sam met.\nDecision: ship it\n", encoding="utf-8")

        def run(*extra: str) -> str:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(kit.main(["minutes", str(recap), "--vault", str(v), "--date", "2026-09-14", *extra]), 0)
            return out.getvalue()

        raised = run("--title", "Raised")
        self.assertIn("marked confidential because it links 2 note(s) marked confidential", raised)
        self.assertIn("--keep-open", raised)
        self.assertIn("would be marked confidential", run("--title", "Dry", "--dry-run"))
        kept = run("--title", "Kept", "--keep-open")
        self.assertNotIn("marked confidential", kept)
        fm = kit.kitlib.parse_note(v / "02-meetings/2026-09-14-kept.md", v).frontmatter
        # the recap's confidential names are not listed in plain mode; the recap keeps them in the body
        self.assertEqual((fm["people"], fm.get("attendees"), fm["sensitivity"]), ([], None, "internal"))


if __name__ == "__main__":
    unittest.main()


class AColonInANoteNameIsNotAUri(unittest.TestCase):
    """`llm ask` screens each search hit with `kit._confidential`. A hit path that only looks like
    a URI scheme (`Re: plan.md`, legal on macOS and Linux) is a note, and must still be judged."""

    @unittest.skipIf(sys.platform.startswith("win"), "Windows cannot hold a colon in a file name")
    def test_a_confidential_note_named_like_a_scheme_stays_confidential(self):
        v = temp_vault()
        self.addCleanup(shutil.rmtree, v.parent, ignore_errors=True)
        (v / "Re: plan.md").write_text("---\ntype: note\ndescription: x\nsensitivity: confidential\n---\n",
                                       encoding="utf-8")
        self.assertTrue(kit._confidential(v, "Re: plan.md"))
