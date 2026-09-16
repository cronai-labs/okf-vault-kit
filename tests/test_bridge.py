"""The Obsidian MCP bridge: fs backend on a temp vault, cli backend against a stub `obsidian` binary."""
import datetime as dt
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT, kitlib, temp_vault

sys.path.insert(0, str(ROOT / "mcp"))
import obsidian_bridge as ob  # noqa: E402

IS_WIN = sys.platform.startswith("win")


class FsBackend(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()
        self.b = ob.FsBackend(self.vault)

    def tearDown(self):
        shutil.rmtree(self.vault.parent, ignore_errors=True)

    def test_search_read_backlinks(self):
        hits = self.b.search("hybrid search", 5)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["file"], "07-knowledge/hybrid-search.md")
        text = self.b.read_note("07-knowledge/hybrid-search")
        self.assertIn("type: concept", text)
        self.assertIn("03-projects/search-relaunch.md", self.b.backlinks("hybrid-search.md"))

    def test_read_by_unique_short_name(self):
        self.assertIn("Alex Example", self.b.read_note("alex-example"))
        with self.assertRaises(ob.BridgeError):
            self.b.read_note("does-not-exist")

    def test_path_escape_is_blocked(self):
        with self.assertRaises(ob.BridgeError):
            self.b.read_note("../../etc/passwd")

    def test_create_from_template_and_append(self):
        rel = self.b.create_note("02-meetings/2026-09-14-test-sync", template="meeting", content="- extra line")
        path = self.vault / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn("type: meeting", text)
        self.assertNotIn("{{", text, "template placeholders must be rendered")
        self.assertIn("- extra line", text)
        note = kitlib.parse_note(path, self.vault)
        self.assertIsNone(note.fm_error)
        self.assertEqual(note.frontmatter["title"], "2026-09-14-test-sync")
        self.assertTrue(kitlib.ISO_DATETIME.match(note.frontmatter["generated"]["at"]), note.frontmatter["generated"])
        with self.assertRaises(ob.BridgeError):
            self.b.create_note("02-meetings/2026-09-14-test-sync")
        self.b.append_note(rel, "- [ ] follow up — me, due 2026-09-20")
        self.assertTrue(path.read_text(encoding="utf-8").endswith("- [ ] follow up — me, due 2026-09-20\n"))

    def test_daily_append_creates_todays_note_at_configured_path(self):
        day = dt.date(2026, 10, 3)
        rel = self.b.daily_append("- [ ] call legal", day=day)
        self.assertEqual(rel, "01-journal/daily/2026/10/2026-10-03.md")
        text = (self.vault / rel).read_text(encoding="utf-8")
        self.assertIn("type: daily", text); self.assertIn("- [ ] call legal", text); self.assertNotIn("{{", text)
        self.assertEqual(kitlib.validate_okf(self.vault).errors, [])

    def test_set_property_keeps_body_and_validates(self):
        rel = self.b.set_property("03-projects/search-relaunch.md", "health", "green")
        note = kitlib.parse_note(self.vault / rel, self.vault)
        self.assertEqual(note.frontmatter["health"], "green")
        self.assertIn("## Executive summary", note.body)
        self.b.set_property(rel, "state", "done")
        self.assertEqual(kitlib.validate_okf(self.vault).errors, [])

    def test_list_files_with_prefix(self):
        files = self.b.list_files("07-knowledge")
        self.assertTrue(files and all(f.startswith("07-knowledge/") for f in files))

    def test_note_names_a_windows_vault_cannot_hold_are_refused(self):
        """A model invents `Plan: Q4`; on Windows that is an alternate data stream, not a note."""
        for name in ("03-projects/Plan: Q4", "03-projects/what?", '03-projects/say "hi"', "03-projects/a|b",
                     "03-projects/CON", "03-projects/nul", "03-projects/a. /plan"):
            with self.assertRaises(ob.BridgeError, msg=name) as ctx:
                self.b.create_note(name, "x")
            self.assertTrue(str(ctx.exception).startswith("note name"), str(ctx.exception))
        self.assertTrue(self.b.create_note("03-projects/plan-q4", "x").endswith("plan-q4.md"))

    def test_template_must_be_a_name_inside_the_templates_folder(self):
        outside = self.vault.parent / "outside-secret.md"
        outside.write_text("OUTSIDE-VAULT-SECRET\n", encoding="utf-8", newline="\n")
        for tpl in ("../05-people/alex-example.md", "../../etc/hosts.md", str(outside)):
            with self.assertRaises(ob.BridgeError, msg=tpl):
                self.b.create_note("03-projects/exfil", "", template=tpl)
        self.assertFalse((self.vault / "03-projects/exfil.md").exists())

    def test_one_undecodable_note_does_not_break_the_read_tools(self):
        """PowerShell 5.1's `Out-File` writes UTF-16 by default, and a vault gets one by accident.

        All nine read tools, not just the two that read note-by-note: `todos` and every `graph_*`
        go through kitlib.load_vault, which used to raise UnicodeDecodeError out of the bridge.
        """
        rel = "07-knowledge/utf16.md"
        (self.vault / rel).write_bytes("---\ntype: concept\n---\nUTF16\n".encode("utf-16"))
        self.assertTrue(self.b.search("hybrid search", 5))
        self.assertIn("03-projects/search-relaunch.md", self.b.backlinks("hybrid-search.md"))
        self.assertIn("07-knowledge/hybrid-search.md", self.b.list_files("07-knowledge"))
        self.assertIn("type: concept", self.b.read_note("07-knowledge/hybrid-search"))

        self.assertIsInstance(self.b.todos()["open"], int)
        self.assertTrue(self.b.graph_query("SELECT id FROM nodes")["rows"])
        self.assertTrue(self.b.graph_neighbors("03-projects/search-relaunch.md", 1))
        self.assertIsInstance(self.b.graph_path("03-projects/search-relaunch.md", "07-knowledge/hybrid-search.md"), list)
        self.assertIn("search-relaunch", self.b.graph_context("03-projects/search-relaunch.md"))

        # skipped, and named: nothing is allowed to go missing quietly
        self.assertEqual(kitlib.undecodable(self.vault), [rel])
        self.assertEqual(kitlib.validate_okf(self.vault).errors,
                         [f"{rel}: not valid UTF-8 — every tool skips it, so the note is invisible to "
                          "search, the graph and todos; re-save it as UTF-8"])

    def test_a_note_we_cannot_decode_is_a_bridge_error_on_every_tool_that_touches_it(self):
        """The tools that name one file, rather than scanning: with --show-confidential nothing
        stops them earlier, and a UnicodeDecodeError traceback is not an answer a model can act on."""
        rel = "07-knowledge/utf16.md"
        raw = "---\ntype: concept\n---\nUTF16\n".encode("utf-16")
        (self.vault / rel).write_bytes(raw)
        for name, call in (("read_note", lambda: self.b.read_note(rel)),
                           ("append_note", lambda: self.b.append_note(rel, "- appended")),
                           ("set_property", lambda: self.b.set_property(rel, "state", "done"))):
            with self.assertRaises(ob.BridgeError, msg=name) as caught:
                call()
            self.assertIn("not valid UTF-8", str(caught.exception))
        self.assertEqual((self.vault / rel).read_bytes(), raw, "a note we cannot read is not one we rewrite")

    def test_an_undecodable_template_fails_the_create_without_writing_the_note(self):
        (self.vault / "90-templates/meeting.md").write_bytes("# {{title}}\n".encode("utf-16"))
        with self.assertRaises(ob.BridgeError):
            self.b.create_note("02-meetings/2026-09-15-sync", template="meeting")
        self.assertFalse((self.vault / "02-meetings/2026-09-15-sync.md").exists())

    def test_an_undecodable_daily_template_still_gets_the_line_into_the_daily_note(self):
        day = dt.date(2026, 9, 15)
        cfg = kitlib.obsidian_config(self.vault, "daily-notes.json")
        (self.vault / cfg.get("template", "90-templates/daily.md")).write_bytes("# {{date}}\n".encode("utf-16"))
        rel = self.b.daily_append("- filed by the bridge", day)
        self.assertIn("- filed by the bridge", (self.vault / rel).read_text(encoding="utf-8"))

    def test_the_whole_vault_tools_tell_the_model_the_result_is_incomplete(self):
        """A model has no terminal to read the CLI's warning in, and infers "no such note" from a
        listing that looks complete. The count is announced, never the path: the bridge hides a
        note whose frontmatter it cannot read, and that includes its name."""
        self.assertEqual(ob._announced(self.b, "payload"), "payload", "a clean vault says nothing")
        (self.vault / "07-knowledge/utf16.md").write_bytes("---\ntype: concept\n---\nUTF16\n".encode("utf-16"))
        out = ob._announced(self.b, "payload")
        self.assertTrue(out.startswith("payload\n"))
        self.assertIn("1 file(s)", out)
        self.assertNotIn("utf16", out)


class CliBackend(unittest.TestCase):
    """A stub `obsidian` executable records the argv it was called with."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="okf-cli-"))
        self.log = self.tmp / "calls.log"
        if IS_WIN:
            stub = self.tmp / "obsidian.cmd"
            stub.write_text(f'@echo off\r\necho %* >> "{self.log}"\r\necho stub-output\r\n')
        else:
            stub = self.tmp / "obsidian"
            stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.log}"\necho stub-output\n')
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.b = ob.CliBackend(vault_name="my vault", binary=str(stub))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def calls(self):
        return [l.replace('"', '').strip() for l in self.log.read_text(encoding="utf-8").strip().splitlines()]

    def test_argument_grammar(self):
        self.b.read_note("03-projects/search-relaunch.md")
        self.b.create_note("02-meetings/new", content="# hi", template="meeting")
        self.b.daily_append("- [ ] x")
        self.b.set_property("03-projects/search-relaunch", "health", "green")
        self.b.search("latency", 3)
        c = self.calls()
        self.assertEqual(c[0], "vault=my vault read file=03-projects/search-relaunch")
        self.assertEqual(c[1], "vault=my vault create name=02-meetings/new content=# hi template=meeting silent")
        self.assertEqual(c[2], "vault=my vault daily:append content=- [ ] x")
        self.assertEqual(c[3], "vault=my vault property:set name=health value=green file=03-projects/search-relaunch")
        self.assertEqual(c[4], "vault=my vault search query=latency limit=3")

    def test_missing_binary_is_actionable(self):
        b = ob.CliBackend(binary=str(self.tmp / "nope"))
        with self.assertRaises(ob.BridgeError) as ctx:
            b.read_note("x")
        self.assertIn("Obsidian 1.12+", str(ctx.exception))


class BridgeProcess(unittest.TestCase):
    def test_selftest_runs(self):
        vault = temp_vault()
        try:
            r = subprocess.run([sys.executable, str(ROOT / "mcp/obsidian_bridge.py"), "--backend", "fs", "--vault", str(vault), "--selftest"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("selftest OK", r.stdout)
        finally:
            shutil.rmtree(vault.parent, ignore_errors=True)

    def test_mcp_server_lists_tools(self):
        try:
            import mcp  # noqa: F401
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("mcp python sdk not installed (pip install 'mcp<2')")
        import importlib.metadata
        try:
            version = importlib.metadata.version("mcp")
        except importlib.metadata.PackageNotFoundError:
            version = ""
        major = version.split(".")[0]
        if major.isdigit() and int(major) >= 2:
            self.skipTest(f"mcp {version} installed; the bridge targets the v1 API (mcp>=1.2,<2, issue #13)")
        import asyncio
        vault = temp_vault()

        async def go():
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp/obsidian_bridge.py"), "--backend", "fs", "--vault", str(vault)])
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    tools = {t.name for t in (await s.list_tools()).tools}
                    res = await s.call_tool("obsidian_read_note", {"file": "07-knowledge/hybrid-search"})
                    return tools, res.content[0].text
        try:
            tools, text = asyncio.run(go())
            self.assertTrue({"obsidian_search", "obsidian_read_note", "obsidian_daily_append", "obsidian_set_property", "graph_context", "graph_query", "vault_todos", "file_meeting_minutes"} <= tools)
            self.assertIn("Hybrid search", text)
        finally:
            shutil.rmtree(vault.parent, ignore_errors=True)


class GraphTools(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()
        self.b = ob.FsBackend(self.vault)

    def tearDown(self):
        shutil.rmtree(self.vault.parent, ignore_errors=True)

    def test_graph_tools(self):
        rows = self.b.graph_neighbors("search-relaunch", 1, ["owner"])
        self.assertEqual(rows[0]["dst"], "05-people/alex-example.md")
        path = self.b.graph_path("hybrid-search", "alex-example")
        self.assertTrue(path and path[-1]["dst"] == "05-people/alex-example.md")
        res = self.b.graph_query("select count(*) as n from edges where pred='owns'")
        self.assertEqual(res["columns"], ["n"]); self.assertGreater(res["rows"][0][0], 0)
        self.assertIn("- owner: Alex Example", self.b.graph_context("search-relaunch"))
        with self.assertRaises(ob.BridgeError):
            self.b.graph_neighbors("no-such-note")

    def test_graph_cache_invalidates_on_write(self):
        self.b.graph()
        self.b.set_property("03-projects/search-relaunch.md", "health", "red")
        self.assertIn("health: red", self.b.graph_context("search-relaunch"))

    def test_todos_and_minutes_tools(self):
        t = self.b.todos()
        self.assertGreater(t["open"], 5); self.assertTrue(all(not x["done"] for x in t["tasks"]))
        res = self.b.file_minutes("Decision: ship it\nAction: write release notes — Alex, due 2026-09-30", "Release sync", "2026-09-20")
        self.assertEqual(res["path"], "02-meetings/2026-09-20-release-sync.md")
        self.assertEqual(res["people"], ["05-people/alex-example.md"]); self.assertEqual(res["decisions"], ["ship it"])
        self.assertIn("write release notes", [x["text"] for x in self.b.todos()["tasks"]])
        with self.assertRaises(ob.BridgeError):
            self.b.file_minutes("x", "Release sync", "2026-09-20")


class CliBackendGraph(unittest.TestCase):
    def test_cli_backend_needs_vault_path_for_graph(self):
        b = ob.CliBackend(vault_name="v", binary="obsidian-not-there")
        with self.assertRaises(ob.BridgeError):
            b.graph_context("x")
        vault = temp_vault()
        try:
            b = ob.CliBackend(vault_name="v", binary="obsidian-not-there", vault_path=str(vault))
            self.assertIn("- type: project", b.graph_context("search-relaunch"))
        finally:
            shutil.rmtree(vault.parent, ignore_errors=True)
