"""Confidential notes stay out of what the CLI hands a model or writes to disk (#52).

`llm ask` drops a confidential hit whole, title included, as the bridge does; the `graph`
commands and `llm ask --graph` read the graph through the same redaction as the bridge's graph
tools, and `--show-confidential` opts back in with the bridge's meaning.
"""
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
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
FALCON_REL = "03-projects/nda-secret.md"
FALCON = ("---\ntype: decision\ntitle: Codename Falcon\ndescription: SECRET-DESC merger talks\n"
          "sensitivity: confidential\nstate: open\ndate: 2026-09-20\n"
          "project: \"[Search relaunch](search-relaunch.md)\"\n---\n# Codename Falcon\n\nSECRET-BODY merger terms. The merger terms are agreed; merger talks go on.\n")
FALCON_MARKERS = ("nda-secret", "Codename Falcon", "SECRET-DESC", "SECRET-BODY")
# The shipped sample's confidential notes; their description text appears in no other note's
# graph node (the open notes name the person in their own fields, which is theirs to show).
PEOPLE = ("05-people/alex-example.md", "05-people/sam-example.md")
PEOPLE_DESC = ("prefers written context before meetings", "owns the embedding-model evaluation")
QUESTION = "what are the merger terms"
# names the open project too, so its context pack is in the prompt: the pack that used to label the confidential note
GRAPH_QUESTION = "what are the merger terms for the search relaunch"
LEFT_OUT = re.compile(r"^(\d+) matching note\(s\) marked sensitivity: confidential were left out of the prompt\.$", re.M)


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
        self.assertIn("3 note(s) marked sensitivity: confidential left out", r.stdout)
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


if __name__ == "__main__":
    unittest.main()
