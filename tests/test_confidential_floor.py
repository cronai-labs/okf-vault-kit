"""Folder floors and path rules from `.kit/config.yml` (#120).

A folder (or a path rule) sets the floor; a note's own `sensitivity` may raise it, never lower it.
The floor lives in the one shared predicate, `kitlib.confidentiality`, so the bridge, `index.md`,
the graph, the todo digest and `llm ask` hide the same notes without computing anything of their
own; the parity test at the end checks exactly that. A config that cannot be read or parsed hides
everything: "could not tell" is not "nothing is confidential".
"""
import datetime as dt
import json
import re
import shutil
import sys
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path

import kit
import kitgraph
import kitrecon
from tests.helpers import ROOT, kitlib, temp_vault
from tests.test_confidential_cli import Records, run

sys.path.insert(0, str(ROOT / "mcp"))
import bridge_policy as bp
import vault_bridge as ob

# The folder rule is spelled in another case than the folder on disk: matching is case-insensitive.
CONFIG = """confidentiality:
  confidential_folders: ["04-Areas/Board"]
  confidential_paths: ["08-resources/exports/*.csv", "07-knowledge/**/harrier-*.md"]
"""
# No frontmatter at all: only the floor can hide it.
NO_FM_REL = "04-areas/board/kestrel-plan.md"
NO_FM = "# Kestrel plan\n\nKESTREL-BODY board pack, kestrel terms.\n\n- [ ] KESTREL-TASK review the kestrel board pack — me, due 2026-09-15\n"
# Inside the floor, but its own label says internal: it stays confidential and validate warns.
LABELLED_REL = "04-areas/board/osprey-minutes.md"
LABELLED = ("---\ntype: meeting\ntitle: Osprey minutes\ndescription: OSPREY-DESC kestrel board minutes\n"
            "sensitivity: internal\n---\n# Osprey minutes\n\nOSPREY-BODY kestrel.\n\n"
            "- [ ] OSPREY-TASK circulate the kestrel minutes — me, due 2026-09-16\n")
# Hidden by a `**` path rule, nested two folders down, with no sensitivity of its own.
HARRIER_REL = "07-knowledge/deep/nested/harrier-notes.md"
HARRIER = ("---\ntype: concept\ntitle: Harrier notes\ndescription: HARRIER-DESC kestrel background\n---\n"
           "# Harrier notes\n\nHARRIER-BODY kestrel.\n\n- [ ] HARRIER-TASK file the kestrel background — me, due 2026-09-17\n")
# Raises itself inside an open folder.
RAISED_REL = "04-areas/heron.md"
RAISED = ("---\ntype: area\ntitle: Heron\ndescription: HERON-DESC kestrel adjacent\nsensitivity: confidential\n---\n"
          "# Heron\n\nHERON-BODY kestrel.\n\n- [ ] HERON-TASK keep heron quiet — me, due 2026-09-18\n")
# An open note the same question finds, so `llm ask` has something to send.
OPEN_REL = "04-areas/plover.md"
OPEN = ("---\ntype: area\ntitle: Plover\ndescription: PLOVER-DESC kestrel public summary\nsensitivity: internal\n---\n"
        "# Plover\n\nPLOVER-BODY kestrel public summary.\n\n- [ ] PLOVER-TASK publish the kestrel summary — me, due 2026-09-19\n")
CSV_REL = "08-resources/exports/payroll.csv"
CSV_OPEN_REL = "08-resources/public.csv"

FLOOR_NOTES = (NO_FM_REL, LABELLED_REL, HARRIER_REL)
HIDDEN_NOTES = (*FLOOR_NOTES, RAISED_REL)
MARKERS = ("kestrel-plan", "KESTREL-BODY", "KESTREL-TASK", "Osprey minutes", "osprey-minutes", "OSPREY-DESC", "OSPREY-BODY",
           "OSPREY-TASK", "Harrier notes", "harrier-notes", "HARRIER-DESC", "HARRIER-BODY", "HARRIER-TASK",
           "HERON-DESC", "HERON-BODY", "HERON-TASK", "payroll.csv")
QUESTION = "kestrel board pack terms minutes background summary"


def write(vault: Path, rel: str, text: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def set_config(vault: Path, text: str) -> None:
    write(vault, kitlib.CONFIG_REL, text)


def floor_vault() -> Path:
    v = temp_vault()
    set_config(v, CONFIG)
    for rel, text in ((NO_FM_REL, NO_FM), (LABELLED_REL, LABELLED), (HARRIER_REL, HARRIER), (RAISED_REL, RAISED),
                      (OPEN_REL, OPEN), (CSV_REL, "name,salary\nCSV-ROW,1\n"), (CSV_OPEN_REL, "a,b\n1,2\n")):
        write(v, rel, text)
    return v


def guarded(vault: Path, backend=None) -> bp.Guarded:
    return bp.Guarded(backend or ob.FsBackend(vault), bp.Policy(audit_path=None), vault)


class StubCli(ob.CliBackend):
    """The Obsidian CLI lists every file it indexes, attachments and CSVs included."""

    FILES: list[str] = []

    def _run(self, command, params=None, flags=None):
        if command == "files":
            return "\n".join(self.FILES) + "\n"
        return "CLI-SERVED-CONTENT"


class _FloorVault(unittest.TestCase):
    def setUp(self):
        self.vault = floor_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)


class TheFloorDecides(_FloorVault):
    def test_a_floor_hides_a_note_without_frontmatter(self):
        self.assertIs(kitlib.confidentiality(self.vault, NO_FM_REL), True)
        self.assertIs(kitlib.confidentiality(self.vault, NO_FM_REL.removesuffix(".md")), True, "`.md` is optional")
        self.assertIs(kitlib.own_confidentiality(self.vault, NO_FM_REL), False, "the note itself says nothing")

    def test_a_note_may_raise_itself_inside_an_open_folder(self):
        self.assertIs(kitlib.confidentiality(self.vault, RAISED_REL), True)
        self.assertIs(kitlib.confidentiality(self.vault, OPEN_REL), False)

    def test_a_note_marked_lower_than_its_floor_stays_confidential(self):
        self.assertIs(kitlib.own_confidentiality(self.vault, LABELLED_REL), False)
        self.assertIs(kitlib.confidentiality(self.vault, LABELLED_REL), True)
        with self.assertRaises(bp.PolicyError):
            guarded(self.vault).read_note(LABELLED_REL)

    def test_validate_warns_about_the_lower_label(self):
        r = run("validate", "--vault", str(self.vault))
        self.assertIn(f"warn   {LABELLED_REL}: `sensitivity: internal` inside a folder or path", r.stdout)
        self.assertNotIn(f"warn   {HARRIER_REL}:", r.stdout, "no label of its own, nothing to warn about")

    def test_matching_ignores_case_and_spelling(self):
        for rel in ("04-AREAS/board/kestrel-plan.md", "04-areas\\Board\\kestrel-plan", "./04-areas/board/x/../kestrel-plan.md",
                    "08-Resources/Exports/PAYROLL.CSV"):
            self.assertTrue(kitlib.confidentiality_policy(self.vault).covers(rel), rel)
        self.assertTrue(kitlib.is_confidential(self.vault, "04-AREAS/BOARD/kestrel-plan.md"))

    def test_a_folder_rule_does_not_match_a_sibling_with_the_same_prefix(self):
        write(self.vault, "04-areas/boardroom.md", "---\ntype: area\ntitle: Boardroom\n---\n# Boardroom\n")
        self.assertIs(kitlib.confidentiality(self.vault, "04-areas/boardroom.md"), False)

    def test_star_stays_in_its_segment_and_double_star_crosses(self):
        pol = kitlib.ConfidentialityPolicy.build(paths=["a/*.csv", "b/**/c-*.md", "d/**"])
        self.assertTrue(pol.covers("a/x.csv"))
        self.assertFalse(pol.covers("a/sub/x.csv"), "`*` must not cross a folder")
        self.assertTrue(pol.covers("b/c-1.md"))
        self.assertTrue(pol.covers("b/x/y/c-1.md"))
        self.assertTrue(pol.covers("b/x/c-1"), "a reference without `.md`")
        self.assertTrue(pol.covers("d/any/depth.md"))
        self.assertFalse(pol.covers("e/x.csv"))

    def test_a_path_rule_hides_a_csv_and_only_the_floor_judges_it(self):
        self.assertIs(kitlib.confidentiality(self.vault, CSV_REL), True)
        self.assertIs(kitlib.confidentiality(self.vault, CSV_OPEN_REL), False,
                      "a non-Markdown file no rule names is open: it was looked up as `public.csv.md` before")

    def test_a_non_markdown_reference_is_not_judged_by_a_note_named_like_it(self):
        write(self.vault, "08-resources/report.csv", "a,b\n")
        write(self.vault, "08-resources/report.csv.md", "---\ntype: note\nsensitivity: confidential\n---\n# x\n")
        self.assertIs(kitlib.confidentiality(self.vault, "08-resources/report.csv"), False, "the CSV, which no rule names")
        self.assertIs(kitlib.confidentiality(self.vault, "08-resources/report.csv.md"), True, "the note beside it")

    def test_the_cli_backend_hides_a_csv_from_list_files(self):
        StubCli.FILES = [CSV_REL, CSV_OPEN_REL, NO_FM_REL, OPEN_REL, "05-people/alex-example.md"]
        g = guarded(self.vault, StubCli(vault_name="v", binary="none", vault_path=str(self.vault)))
        listed = g.list_files()
        self.assertNotIn(CSV_REL, listed)
        self.assertNotIn(NO_FM_REL, listed)
        self.assertNotIn("05-people/alex-example.md", listed)
        self.assertIn(CSV_OPEN_REL, listed)
        self.assertIn(OPEN_REL, listed)
        self.assertEqual(g.list_files("08-resources"), [CSV_OPEN_REL])

    def test_the_bridge_refuses_to_read_a_csv_by_any_spelling(self):
        StubCli.FILES = [CSV_REL]
        g = guarded(self.vault, StubCli(vault_name="v", binary="none", vault_path=str(self.vault)))
        for ref in (CSV_REL, "payroll.csv", "08-Resources/Exports/payroll.csv"):
            with self.assertRaises(bp.PolicyError, msg=ref):
                g.read_note(ref)

    def test_a_forced_minutes_overwrite_writes_no_label_for_a_floor(self):
        set_config(self.vault, 'confidentiality:\n  confidential_folders: ["02-meetings"]\n')
        day = dt.date(2026, 9, 21)
        kitrecon.file_minutes(self.vault, "We met.", "Floor sync", date=day, mode="plain")
        m = kitrecon.file_minutes(self.vault, "We met again.", "Floor sync", date=day, mode="plain", force=True)
        note = kitlib.parse_note(self.vault / m.path, self.vault)
        self.assertNotEqual(str(note.frontmatter.get("sensitivity", "")).lower(), "confidential",
                            "the floor hides the meeting; it must not write a label into it")
        self.assertTrue(kitlib.is_confidential(self.vault, m.path))


BROKEN = {
    "yaml": "confidentiality: [unclosed\n",
    "top level not a mapping": "- confidentiality\n",
    "block not a mapping": "confidentiality: 05-people\n",
    "null block": "confidentiality:\nproviders:\n  search: naive\n",
    "unknown key": 'confidentiality:\n  confidential_folder: ["05-people"]\n',
    "not a list": "confidentiality:\n  confidential_folders: 05-people\n",
    "not strings": "confidentiality:\n  confidential_folders: [2024]\n",
    "absolute": 'confidentiality:\n  confidential_folders: ["/etc"]\n',
    "home": 'confidentiality:\n  confidential_paths: ["~/notes/*.md"]\n',
    "drive": 'confidentiality:\n  confidential_paths: ["C:/vault/x.md"]\n',
    "escapes": 'confidentiality:\n  confidential_folders: ["../elsewhere"]\n',
    "empty entry": 'confidentiality:\n  confidential_paths: ["  "]\n',
    "folder glob": 'confidentiality:\n  confidential_folders: ["05-people/*"]\n',
}


class ABrokenConfigHidesEverything(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)

    def test_every_broken_shape_is_an_error_not_an_empty_policy(self):
        for name, text in BROKEN.items():
            with self.subTest(name):
                set_config(self.vault, text + "#" * len(name))   # a new size each time: the cache keys on it
                with self.assertRaises(kitlib.PolicyConfigError):
                    kitlib.confidentiality_policy(self.vault)
                self.assertIsNone(kitlib.confidentiality(self.vault, "07-knowledge/hybrid-search.md"))
                self.assertIsNone(kitlib.confidentiality(self.vault, "no/such/note.md"),
                                  "a broken config answers before the no-such-file branch")

    def test_unreadable_configs_are_errors(self):
        cfg = self.vault / kitlib.CONFIG_REL
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_bytes("confidentiality:\n  confidential_folders: [\"caf\xe9\"]\n".encode("latin-1"))
        self.assertIsNone(kitlib.confidentiality(self.vault, "07-knowledge/hybrid-search.md"), "not UTF-8")
        cfg.unlink()
        cfg.mkdir()
        self.assertIsNone(kitlib.confidentiality(self.vault, "07-knowledge/hybrid-search.md"), "a directory")

    def test_missing_file_or_block_means_no_floors(self):
        self.assertIs(kitlib.confidentiality(self.vault, "07-knowledge/hybrid-search.md"), False)
        set_config(self.vault, "providers:\n  search: naive\n")
        self.assertIs(kitlib.confidentiality(self.vault, "07-knowledge/hybrid-search.md"), False)
        set_config(self.vault, "")
        self.assertIs(kitlib.confidentiality(self.vault, "07-knowledge/hybrid-search.md"), False)
        shutil.rmtree(self.vault / ".kit")
        (self.vault / ".kit").write_text("not a folder\n", encoding="utf-8")
        self.assertIs(kitlib.confidentiality(self.vault, "07-knowledge/hybrid-search.md"), False)

    def test_the_providers_reader_stays_lenient(self):
        set_config(self.vault, BROKEN["yaml"])
        self.assertEqual(kitlib.confidentiality(self.vault, "07-knowledge/hybrid-search.md"), None)
        self.assertEqual(kit.kitproviders.read_config(self.vault), {})

    def test_readers_hide_everything(self):
        set_config(self.vault, BROKEN["unknown key"])
        g = guarded(self.vault)
        self.assertEqual(g.list_files(), [])
        self.assertEqual(g.search("hybrid search"), [])
        with self.assertRaises(bp.PolicyError):
            g.read_note("07-knowledge/hybrid-search.md")
        self.assertEqual([n for n in g.graph().nodes.values() if n.kind == "note"], [])
        self.assertEqual(g.todos()["tasks"], [])
        index = kitlib.build_index(self.vault)
        self.assertNotIn("](07-knowledge/", index)
        self.assertNotRegex(index, r"(?m)^\* \[")
        self.assertTrue(kitlib.is_confidential(self.vault, "07-knowledge/hybrid-search.md"))

    def test_validate_reports_it(self):
        set_config(self.vault, BROKEN["escapes"])
        r = run("validate", "--vault", str(self.vault))
        self.assertRegex(r.stdout, r"(?m)^ERROR  \.kit/config\.yml: confidentiality\.confidential_folders entry '\.\./elsewhere' "
                                   r"must not use `\.\.` — until this is fixed every reader treats every note as confidential$")

    def test_the_template_vault_has_no_floor_section(self):
        r = run("validate", "--vault", str(self.vault))
        self.assertNotIn("== Confidentiality", r.stdout)


class TheSignatureFollowsTheConfig(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)

    def digest(self) -> str:
        return kitgraph.signature_digest(kitgraph.note_set_signature(self.vault))

    def test_declaring_changing_breaking_and_removing_a_floor_each_change_it(self):
        seen = [self.digest()]
        for text in ('confidentiality:\n  confidential_folders: ["03-projects"]\n',
                     'confidentiality:\n  confidential_folders: ["03-projects", "06-decisions"]\n',
                     BROKEN["yaml"]):
            set_config(self.vault, text)
            seen.append(self.digest())
        (self.vault / kitlib.CONFIG_REL).unlink()
        seen.append(self.digest())
        self.assertEqual(len(set(seen[:4])), 4, "each config state has its own signature")
        self.assertEqual(seen[0], seen[4], "no config is no config")
        self.assertEqual(kitgraph.note_set_signature(self.vault), ob.FsBackend(self.vault)._note_set_signature())

    def test_graph_query_rebuilds_after_a_floor_is_declared(self):
        rel = "03-projects/search-relaunch.md"
        self.assertEqual(run("graph", "build", "--vault", str(self.vault)).returncode, 0)

        def ids() -> set[str]:
            r = run("graph", "query", "select id from nodes", "--json", "--limit", "1000", "--vault", str(self.vault))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return {row["id"] for row in json.loads(r.stdout)}

        self.assertIn(rel, ids())
        set_config(self.vault, 'confidentiality:\n  confidential_folders: ["03-projects"]\n')
        self.assertNotIn(rel, ids(), "graph query answered from a database built before the floor")

    def test_the_bridge_graph_cache_follows_the_config(self):
        g = guarded(self.vault)
        rel = "03-projects/search-relaunch.md"
        self.assertIn(rel, g.graph().nodes)
        set_config(self.vault, 'confidentiality:\n  confidential_folders: ["03-projects"]\n')
        self.assertNotIn(rel, g.graph().nodes)


class Parity(_FloorVault):
    """Every path the predicate calls confidential is hidden by every reader, and by nothing of its own.

    Per reader, two checks: no hidden path is served, listed, cited or a node (the sample's visible
    notes link to its people notes, and that link is the visible note's content, so a bare path in
    the text is not a leak); and no field of the planted notes appears anywhere.
    """

    def setUp(self):
        super().setUp()
        files = [p for p in self.vault.rglob("*") if p.is_file() and kitlib._visible(p.relative_to(self.vault))]
        self.rels = sorted(p.relative_to(self.vault).as_posix() for p in files)
        self.hidden = {r for r in self.rels if kitlib.is_confidential(self.vault, r)}

    def assert_clean(self, text: str, where: str):
        for rel in (*HIDDEN_NOTES, CSV_REL):
            self.assertNotIn(rel, text, f"{rel} in {where}")
        for marker in MARKERS:
            self.assertNotIn(marker.lower(), text.lower(), f"{marker!r} in {where}")

    def test_the_fixture_has_what_the_test_needs(self):
        self.assertTrue({*HIDDEN_NOTES, CSV_REL} <= self.hidden, self.hidden)
        self.assertNotIn(OPEN_REL, self.hidden)
        self.assertNotIn(CSV_OPEN_REL, self.hidden)
        self.assertTrue({"05-people/alex-example.md", "05-people/sam-example.md"} <= self.hidden)

    def test_the_bridge_refuses_every_hidden_path(self):
        g = guarded(self.vault)
        for rel in sorted(self.hidden):
            with self.assertRaises(bp.PolicyError, msg=rel):
                g.read_note(rel)
        self.assertEqual(g.read_note(OPEN_REL), OPEN)
        self.assertFalse(self.hidden & set(g.list_files()))
        self.assertFalse(self.hidden & {h["file"] for h in g.search(QUESTION, 50)})
        self.assertIn(OPEN_REL, {h["file"] for h in g.search(QUESTION, 50)})
        todos = g.todos()
        self.assertFalse(self.hidden & {t["file"] for t in todos["tasks"]})
        self.assert_clean(json.dumps(todos), "the bridge's todos")

    def test_index_md(self):
        index = kitlib.build_index(self.vault)
        listed = set(re.findall(r"(?m)^\* \[[^\]]*\]\(([^)]+)\)", index))
        self.assertIn(OPEN_REL, listed)
        self.assertFalse(self.hidden & listed)
        self.assert_clean(index, "index.md")

    def test_the_redacted_graph(self):
        for g, where in ((kitgraph.redact_vault(kitgraph.build_graph(self.vault), self.vault), "kitgraph.redact_vault"),
                         (guarded(self.vault).graph(), "the bridge's graph")):
            paths = {n.path for n in g.nodes.values() if n.kind == "note"}
            self.assertFalse(self.hidden & paths, where)
            self.assertIn(OPEN_REL, paths)
            self.assert_clean(json.dumps(kitgraph.to_json(g), default=str), where)

    def test_the_todo_digest(self):
        rep = kitrecon.todo_report(self.vault)
        self.assertFalse(self.hidden & {t.file for t in rep.without_confidential().tasks})
        digest = kitrecon.render_digest(rep)
        self.assert_clean(digest, "the todo digest")
        self.assertIn("PLOVER-TASK", digest)

    def test_llm_ask(self):
        hits = {kit.kitproviders.hit_rel(h["file"]) for h in kit._search_hits(self.vault, QUESTION, 50, None, "naive")}
        self.assertTrue({NO_FM_REL, LABELLED_REL, HARRIER_REL, RAISED_REL} <= hits, "the search must find the hidden notes")
        Records.seen = []
        srv = HTTPServer(("127.0.0.1", 0), Records)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        r = run("llm", "ask", QUESTION, "--vault", str(self.vault), "--base-url", f"http://127.0.0.1:{srv.server_port}/v1",
                "--search-provider", "naive", "-n", "20", "--graph")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        prompt = "\n".join(m["content"] for m in Records.seen[-1]["messages"])
        cited = set(re.findall(r"(?m)^\[\d+\] (\S+) —", prompt))
        self.assertIn(OPEN_REL, cited)
        self.assertFalse(self.hidden & cited)
        self.assert_clean(prompt, "the llm ask prompt")
        self.assertIn("PLOVER-BODY", prompt)
        self.assert_clean(r.stdout, "the llm ask console")
        left_out = re.search(r"^(\d+) matching confidential note\(s\)", r.stdout, re.M)
        self.assertIsNotNone(left_out, r.stdout)
        self.assertGreaterEqual(int(left_out.group(1)), 4)


if __name__ == "__main__":
    unittest.main()
