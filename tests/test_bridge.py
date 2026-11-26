"""The vault MCP bridge (okf-vault): fs backend on a temp vault, cli backend against a stub `obsidian` binary."""
import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT, kitlib, kitproviders, temp_vault

sys.path.insert(0, str(ROOT / "mcp"))
import vault_bridge as ob

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

    def test_search_goes_through_the_configured_search_provider(self):
        """#90: the bridge answered with its own term counting while the docs promised qmd."""
        class Fake:
            name = "fake"
            def __init__(self, hits): self.hits, self.asked = hits, []
            def available(self): return None
            def query(self, vault, question, n, collection):
                self.asked.append((question, n, collection)); return self.hits
        H = kitproviders.Hit
        fake = Fake([H("qmd://vault/07-knowledge/reindex-qmd.md", "Reindex qmd", "how to  reindex\n qmd", 0.9),
                     H("03-projects/only-in-another-vault.md", "Elsewhere", "x", 0.8)])
        b = ob.FsBackend(self.vault, search_provider=fake)
        hits = b.search("reindex", 5)
        self.assertEqual(fake.asked, [("reindex", 5, "vault")])
        self.assertEqual([h["file"] for h in hits], ["07-knowledge/reindex-qmd.md"],
                         "qmd:// paths are mapped, and a hit for a file this vault does not have is dropped")
        self.assertEqual(hits[0]["snippet"], "how to reindex qmd")

    def test_search_hides_what_every_other_reader_hides(self):
        """qmd indexes whatever its mask matches. A note in a nested `_`-folder or a dot folder is
        invisible to list, read and the graph, and search must not be the one tool that shows it."""
        for rel in ("03-projects/_drafts/secret-plan.md", ".obsidian/plugin-notes.md", "07-knowledge/visible.md"):
            (self.vault / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.vault / rel).write_text("---\ntype: concept\n---\n# x\n", encoding="utf-8")

        class Indexed:
            name = "qmd"
            def available(self): return None
            def query(self, vault, question, n, collection):
                return [kitproviders.Hit(f"qmd://vault/{r}", r, "x", 1.0) for r in
                        ("03-projects/_drafts/secret-plan.md", ".obsidian/plugin-notes.md", "07-knowledge/visible.md")]
        hits = ob.FsBackend(self.vault, search_provider=Indexed()).search("x", 10)
        self.assertEqual([h["file"] for h in hits], ["07-knowledge/visible.md"])

    def test_search_falls_back_to_keyword_search_when_the_provider_finds_nothing(self):
        class Empty:
            name = "qmd"
            def available(self): return None
            def query(self, *a): return []
        hits = ob.FsBackend(self.vault, search_provider=Empty()).search("hybrid search", 5)
        self.assertEqual(hits[0]["file"], "07-knowledge/hybrid-search.md")

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

    def test_list_files_accepts_the_folder_spellings_a_model_writes(self):
        want = self.b.list_files("05-people")
        self.assertTrue(want)
        for spelling in ("./05-people", "05-people/", "05-people\\", "/05-people", " 05-people "):
            with self.subTest(spelling=spelling):
                self.assertEqual(self.b.list_files(spelling), want)

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
                         [(f"{rel}: not valid UTF-8 — every tool skips it, so the note is invisible to "
                           "search, the graph and todos; re-save it as UTF-8")])

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
            r = subprocess.run([sys.executable, str(ROOT / "mcp/vault_bridge.py"), "--backend", "fs", "--vault", str(vault), "--selftest"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("selftest OK", r.stdout)
        finally:
            shutil.rmtree(vault.parent, ignore_errors=True)

    # The tool surface is the contract: client allow-lists and system prompts name these, so a
    # rename or a dropped tool breaks a machine we cannot reach. The obsidian_* → vault_* rename
    # in 0.3.0 was deliberate and rides the same breaking release as the okf-vault server name.
    TOOLS = {"vault_search", "vault_read_note", "vault_list_files", "vault_backlinks",
             "graph_context", "graph_neighbors", "graph_path", "graph_query", "vault_todos",
             "vault_create_note", "vault_append_note", "vault_daily_append",
             "vault_set_property", "file_meeting_minutes"}

    def test_a_real_stdio_session_serves_every_tool_and_still_refuses(self):
        """The whole bridge over the wire: 14 tools, a read, a write, and three refusals.

        The refusals are the half that a port can quietly lose. The SDK decides which exceptions
        reach the model, so "marked confidential" can become a bare "Error executing tool" while
        the tool list, the reads and the writes all still look right.
        """
        import importlib.metadata
        try:
            # The package database, not `import mcp`: this repository has a directory named
            # `mcp/`, which imports as a namespace package on exactly the machines with no SDK.
            version = importlib.metadata.version("mcp")
        except importlib.metadata.PackageNotFoundError:
            self.skipTest("mcp python sdk not installed (pip install 'mcp>=2,<3')")
        if version.split(".")[0] != "2":
            self.skipTest(f"mcp {version} installed; the bridge targets the 2.x API — pip install 'mcp>=2,<3'")
        import asyncio

        from mcp.client.stdio import stdio_client

        from mcp import ClientSession, StdioServerParameters
        vault = temp_vault()

        async def go():
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp/vault_bridge.py"), "--backend", "fs", "--vault", str(vault)],
                                           env=dict(os.environ))
            async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                out = {}
                for label, name, args in (
                        ("read", "vault_read_note", {"file": "07-knowledge/hybrid-search"}),
                        ("write", "vault_create_note", {"name": "03-projects/over-the-wire", "content": "written by the session"}),
                        ("confidential", "vault_read_note", {"file": "05-people/alex-example"}),
                        ("denied", "vault_create_note", {"name": "05-people/nope", "content": "x"}),
                        ("write_sql", "graph_query", {"sql": "delete from nodes"})):
                    res = await s.call_tool(name, args)
                    out[label] = (bool(res.is_error), res.content[0].text)
                return tools, out
        try:
            tools, out = asyncio.run(go())
            self.assertEqual(tools, self.TOOLS, f"the tool surface moved: {sorted(tools)}")

            self.assertFalse(out["read"][0], out["read"][1])
            self.assertIn("Hybrid search", out["read"][1])
            self.assertFalse(out["write"][0], out["write"][1])
            self.assertEqual(out["write"][1], "03-projects/over-the-wire.md")
            self.assertIn("written by the session",
                          (vault / "03-projects/over-the-wire.md").read_text(encoding="utf-8"))

            for label, why in (("confidential", "is marked confidential"),
                               ("denied", "denied by policy"),
                               ("write_sql", "only SELECT / WITH queries are allowed")):
                self.assertTrue(out[label][0], f"{label} must be refused, got {out[label][1]!r}")
                self.assertIn(why, out[label][1],
                              f"{label}: a refusal the model cannot read is one it will retry")
        finally:
            shutil.rmtree(vault.parent, ignore_errors=True)


def _need_sdk(case: unittest.TestCase) -> None:
    import importlib.metadata
    try:
        version = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        case.skipTest("mcp python sdk not installed (pip install 'mcp>=2,<3')")
    if version.split(".")[0] != "2":
        case.skipTest(f"mcp {version} installed; the bridge targets the 2.x API")


def _stdio_session(vault: Path, *flags: str, calls=()):
    """Start the bridge over stdio, list its tools, run `calls`; return (tool names, {label: (is_error, text)})."""
    import asyncio

    from mcp.client.stdio import stdio_client

    from mcp import ClientSession, StdioServerParameters

    async def go():
        # env: stdio_client passes only a whitelist, and the offline suite's provider pin must reach the child
        params = StdioServerParameters(command=sys.executable, env=dict(os.environ),
                                       args=[str(ROOT / "mcp/vault_bridge.py"), "--backend", "fs", "--vault", str(vault), *flags])
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            out = {}
            for label, name, args in calls:
                res = await s.call_tool(name, args)
                out[label] = (bool(res.is_error), "".join(getattr(c, "text", "") for c in res.content))
            return tools, out
    return asyncio.run(go())


class EveryToolOverTheWire(unittest.TestCase):
    """Each of the 14 tools called through a real stdio session, not only listed.

    The direct-call tests prove the logic; this proves the wiring a client actually crosses —
    argument names, types and JSON encoding as the SDK derives them from the signatures.
    """
    READ_TOOLS = {"vault_search", "vault_read_note", "vault_list_files", "vault_backlinks",
                  "graph_context", "graph_neighbors", "graph_path", "graph_query", "vault_todos"}

    def setUp(self):
        _need_sdk(self)
        self.vault = temp_vault()

    def tearDown(self):
        shutil.rmtree(self.vault.parent, ignore_errors=True)

    def test_every_tool_answers(self):
        calls = (
            ("search", "vault_search", {"query": "hybrid search", "limit": 3}),
            ("read", "vault_read_note", {"file": "07-knowledge/hybrid-search"}),
            ("list", "vault_list_files", {"folder": "./07-knowledge"}),
            ("backlinks", "vault_backlinks", {"file": "07-knowledge/hybrid-search"}),
            ("context", "graph_context", {"note": "search-relaunch"}),
            ("neighbors", "graph_neighbors", {"note": "search-relaunch", "predicates": "links_to"}),
            ("path", "graph_path", {"a": "hybrid-search", "b": "search-relaunch"}),
            ("query", "graph_query", {"sql": "select count(*) as n from nodes"}),
            ("todos", "vault_todos", {}),
            ("create", "vault_create_note", {"name": "03-projects/wire-note", "content": "first line"}),
            ("append", "vault_append_note", {"file": "03-projects/wire-note", "content": "second line"}),
            ("property", "vault_set_property", {"file": "03-projects/wire-note", "name": "state", "value": "active"}),
            ("daily", "vault_daily_append", {"content": "- logged over the wire"}),
            ("minutes", "file_meeting_minutes", {"text": "Decision: ship it", "title": 'Wire "sync": done', "date": "2026-09-21"}),
        )
        tools, out = _stdio_session(self.vault, calls=calls)
        self.assertEqual(len(tools), 14)
        self.assertEqual({name for _, name, _ in calls}, tools, "every registered tool is called once")
        for label, (is_error, text) in out.items():
            self.assertFalse(is_error, f"{label}: {text}")
        self.assertEqual(json.loads(out["search"][1])[0]["file"], "07-knowledge/hybrid-search.md")
        self.assertIn("07-knowledge/hybrid-search.md", out["list"][1])
        self.assertIn("health", out["context"][1])
        self.assertGreater(json.loads(out["query"][1])["rows"][0][0], 10)
        self.assertGreater(json.loads(out["todos"][1])["open"], 0)
        note = (self.vault / "03-projects/wire-note.md").read_text(encoding="utf-8")
        self.assertIn("first line", note); self.assertIn("second line", note); self.assertIn("state: active", note)
        self.assertEqual(json.loads(out["minutes"][1])["path"], "02-meetings/2026-09-21-wire-sync-done.md")

    def test_read_only_serves_nine_tools_and_no_write(self):
        tools, _ = _stdio_session(self.vault, "--read-only")
        self.assertEqual(tools, self.READ_TOOLS)

    def test_propose_mode_records_a_write_over_the_wire_instead_of_making_it(self):
        _, out = _stdio_session(self.vault, "--propose", calls=(
            ("create", "vault_create_note", {"name": "03-projects/proposed", "content": "x"}),))
        is_error, text = out["create"]
        self.assertFalse(is_error, text)
        self.assertTrue(text.startswith("proposed:"), text)
        self.assertFalse((self.vault / "03-projects/proposed.md").exists())
        self.assertTrue(list((self.vault / ".kit/proposals").glob("*")), "the proposal is on disk for a human")


class HttpTransport(unittest.TestCase):
    """`--http --token`: refused without the token, served with it — over a real socket."""

    def setUp(self):
        _need_sdk(self)
        # loopback must never go to a proxy: on a managed machine with HTTP_PROXY set, both the
        # probe and the SDK's client would send 127.0.0.1 to it (tests/helpers.py, llm_reachable)
        loopback = "127.0.0.1,localhost"
        patcher = mock.patch.dict(os.environ, {"NO_PROXY": loopback, "no_proxy": loopback})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vault = temp_vault()
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]
        env = dict(os.environ, KIT_BRIDGE_TOKEN="wire-token")
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "mcp/vault_bridge.py"), "--backend", "fs", "--vault", str(self.vault),
                                      "--http", "--port", str(self.port), "--no-audit"],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        import time
        import urllib.error
        import urllib.request
        for _ in range(100):
            try:
                urllib.request.urlopen(self.url, timeout=0.5)
            except urllib.error.HTTPError:
                break                                  # it answers: any HTTP status means it is up
            except OSError:
                if self.proc.poll() is not None:
                    self.fail(f"bridge exited: {self.proc.stderr.read().decode(errors='replace')}")
                time.sleep(0.1)
        else:
            self.fail("bridge did not start listening")

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stderr.close()
        shutil.rmtree(self.vault.parent, ignore_errors=True)

    def test_no_token_is_401_and_the_token_gets_a_session(self):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

        import asyncio

        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        from mcp import ClientSession

        async def go():
            async with create_mcp_http_client(headers={"Authorization": "Bearer wire-token"}) as http, \
                    streamable_http_client(self.url, http_client=http) as streams, \
                    ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                res = await s.call_tool("vault_read_note", {"file": "07-knowledge/hybrid-search"})
                return bool(res.is_error), res.content[0].text
        is_error, text = asyncio.run(go())
        self.assertFalse(is_error, text)
        self.assertIn("Hybrid search", text)


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

    def test_graph_cache_forgets_a_note_that_was_moved_away_or_deleted(self):
        """A move or a delete raises no mtime, so an mtime-only cache kept serving the old note.

        Moving a note into a `_`-folder is how a user hides it; the graph tools must stop
        answering about it at once, not after some other note happens to change.
        """
        sql = "select count(*) from nodes where path = '07-knowledge/reindex-qmd.md'"
        self.assertEqual(self.b.graph_query(sql)["rows"], [[1]])
        (self.vault / "_private").mkdir()
        shutil.move(str(self.vault / "07-knowledge/reindex-qmd.md"), str(self.vault / "_private/reindex-qmd.md"))
        self.assertEqual(self.b.graph_query(sql)["rows"], [[0]], "moved into a `_`-folder, still in the graph")
        sql2 = "select count(*) from nodes where path = '07-knowledge/hybrid-search.md'"
        self.assertEqual(self.b.graph_query(sql2)["rows"], [[1]])
        (self.vault / "07-knowledge/hybrid-search.md").unlink()
        self.assertEqual(self.b.graph_query(sql2)["rows"], [[0]], "deleted, still in the graph")

    def test_minutes_title_with_yaml_specials_is_filed(self):
        """The model chooses the title; quotes, a backslash or a colon must not break the YAML."""
        for i, title in enumerate(('Review "beta" launch', "Path C:\\qmd fix", "Plan: Q4 and beyond")):
            with self.subTest(title=title):
                res = self.b.file_minutes("Decision: ship it", title, f"2026-09-2{i}")
                note = kitlib.parse_note(self.vault / res["path"], self.vault)
                self.assertIsNotNone(note.frontmatter, note.fm_error)
                self.assertEqual(note.frontmatter["title"], title)
                self.assertIn(f"# {title}", note.body)

    def test_todos_and_minutes_tools(self):
        t = self.b.todos()
        self.assertGreater(t["open"], 5); self.assertTrue(all(not x["done"] for x in t["tasks"]))
        res = self.b.file_minutes("Decision: ship it\nAction: write release notes — Alex, due 2026-09-30", "Release sync", "2026-09-20")
        self.assertEqual(res["path"], "02-meetings/2026-09-20-release-sync.md")
        self.assertEqual(res["people"], ["05-people/alex-example.md"]); self.assertEqual(res["decisions"], ["ship it"])
        self.assertIn("write release notes", [x["text"] for x in self.b.todos()["tasks"]])
        with self.assertRaises(ob.BridgeError):
            self.b.file_minutes("x", "Release sync", "2026-09-20")

    def test_minutes_mode_reaches_the_entity_match(self):
        """The backend has no policy; Guarded passes it the mode, and it must arrive."""
        res = self.b.file_minutes("Alex Example joined.\nDecision: ship it", "Plain sync", "2026-09-21", people="Sam Example",
                                  mode="plain")
        self.assertEqual((res["people"], res["unresolved"]), (["Sam Example"], ["Sam Example"]))
        cli = ob.CliBackend(vault_name="v", binary="obsidian-not-there", vault_path=str(self.vault))
        res = cli.file_minutes("Alex Example joined.", "Plain sync two", "2026-09-22", mode="plain")
        self.assertEqual(res["people"], ["Alex Example"])
        self.assertNotIn("05-people", json.dumps(res))


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
