"""End-to-end tests of the kit.py CLI (subprocess, real files)."""
import contextlib
import getpass
import http.server
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT, VAULT, kitlib, temp_vault

KIT = [sys.executable, str(ROOT / "kit.py")]


def run(*args, **kw):
    return subprocess.run(KIT + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, **kw)


def model_stub(ids):
    """A loopback `/models` endpoint answering like LM Studio. Caller shuts it down."""
    body = json.dumps({"data": [{"id": i} for i in ids]}).encode("utf-8")

    class Models(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Models)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def report_for(model_ids, base_url=None, vault_name=None):
    """Run `doctor --report` against a stub endpoint and return (report text, vault dir name)."""
    srv = model_stub(model_ids) if model_ids is not None else None
    tmp = Path(tempfile.mkdtemp(prefix="okf-doctor-"))
    try:
        v = tmp / (vault_name or "vault")
        shutil.copytree(VAULT, v)
        out = tmp / "doctor.txt"
        url = base_url or f"http://127.0.0.1:{srv.server_address[1]}/v1"
        r = run("doctor", "--vault", str(v), "--report", str(out), "--base-url", url)
        assert r.returncode == 0, r.stderr[-400:]
        return out.read_text(encoding="utf-8")
    finally:
        if srv:
            srv.shutdown(); srv.server_close()      # shutdown alone leaves the listening socket open
        shutil.rmtree(tmp, ignore_errors=True)


class KitCli(unittest.TestCase):
    def test_help(self):
        r = run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("doctor", r.stdout)

    def test_validate_template_vault_passes(self):
        r = run("validate", "--vault", str(VAULT), "--strict")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)

    def test_validate_fails_on_broken_vault(self):
        v = temp_vault()
        try:
            (v / "07-knowledge/broken.md").write_text("no frontmatter\n", encoding="utf-8")
            r = run("validate", "--vault", str(v))
            self.assertEqual(r.returncode, 1)
            self.assertIn("missing YAML frontmatter", r.stdout)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_an_undecodable_note_is_a_finding_not_a_traceback(self):
        """PowerShell 5.1's `Out-File` writes UTF-16 by default; one such note used to kill the CLI."""
        v = temp_vault()
        try:
            (v / "07-knowledge/utf16.md").write_bytes("---\ntype: concept\n---\nUTF16\n".encode("utf-16"))
            r = run("validate", "--vault", str(v))
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(r.returncode, 1)
            self.assertIn("not valid UTF-8", r.stdout, "validate has to report it as a finding")
            self.assertIn("warning: skipping 1 file(s)", r.stderr)
            self.assertIn("07-knowledge/utf16.md", r.stderr, "the warning has to name the file")
            for argv in (("todos",), ("graph", "build"), ("index",), ("reconcile",)):
                c = run(*argv, "--vault", str(v))
                self.assertNotIn("Traceback", c.stderr, f"{argv} crashed on an undecodable note")
                self.assertIn("warning: skipping 1 file(s)", c.stderr, f"{argv} skipped it silently")
                self.assertIn(c.returncode, (0, 2), f"{argv}: {c.stderr[-400:]}")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_an_undecodable_file_outside_the_note_set_is_not_fatal_to_any_command(self):
        """The vault holds four files no `load_vault` ever sees — the ontology, the JSON-LD
        context, the hubs and the templates — and each was read without a guard."""
        for rel in ("99-system/ontology.yml", "context.jsonld", "03-projects/projects.md", "90-templates/meeting.md"):
            v = temp_vault()
            try:
                (v / rel).write_bytes("---\ntype: concept\n---\nUTF16\n".encode("utf-16"))
                for argv in (("validate",), ("graph", "build"), ("reconcile",), ("todos",), ("index",)):
                    c = run(*argv, "--vault", str(v))
                    self.assertNotIn("Traceback", c.stderr, f"{argv} crashed on an undecodable {rel}")
                    self.assertIn(c.returncode, (0, 1, 2), f"{argv} on {rel}: {c.stderr[-400:]}")
            finally:
                shutil.rmtree(v.parent, ignore_errors=True)

    def test_logging_into_an_undecodable_log_reports_it_rather_than_crashing(self):
        v = temp_vault()
        try:
            raw = "# Vault Update Log\n".encode("utf-16")
            (v / "log.md").write_bytes(raw)
            r = run("log", "a line nobody will read", "--vault", str(v))
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(r.returncode, 1)
            self.assertIn("log.md is not valid UTF-8", r.stdout)
            self.assertEqual((v / "log.md").read_bytes(), raw, "nothing written, and the log not rewritten")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_an_undecodable_template_does_not_stop_the_graph_build(self):
        """The graph reads 90-templates directly to name the vault owner; one UTF-16 file killed it."""
        v = temp_vault()
        try:
            (v / "90-templates/utf16.md").write_bytes("---\ntype: concept\n---\nby: human:someone\n".encode("utf-16"))
            r = run("graph", "build", "--vault", str(v))
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            self.assertIn("finding(s)", r.stdout, "the graph still has to be built")
            self.assertIn("90-templates/utf16.md", r.stderr, "the skipped file has to be named")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_init_force_leaves_an_undecodable_file_alone(self):
        """`--force` personalises a directory that already holds files, and one of them can be UTF-16."""
        tmp = Path(tempfile.mkdtemp(prefix="okf-init-"))
        try:
            target = tmp / "my-vault"
            (target / "07-knowledge").mkdir(parents=True)
            (target / "07-knowledge/utf16.md").write_bytes("---\ntype: concept\n---\nby: human:me\n".encode("utf-16"))
            r = run("init", "--target", str(target), "--actor", "human:tester", "--no-qmd", "--force")
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("1 file(s) left untouched", r.stdout, "a file we skipped must be announced")
            self.assertTrue((target / "index.md").exists(), "the rest of init still has to run")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_index_is_deterministic(self):
        v = temp_vault()
        try:
            r1 = run("index", "--vault", str(v)); first = (v / "index.md").read_text(encoding="utf-8")
            r2 = run("index", "--vault", str(v)); second = (v / "index.md").read_text(encoding="utf-8")
            self.assertEqual(r1.returncode, 0); self.assertEqual(r2.returncode, 0)
            self.assertEqual(first, second)
            self.assertIn("* [Hybrid search](07-knowledge/hybrid-search.md) - Retrieval that fuses", first)
            self.assertIn('okf_version: "0.2"', first)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_init_personalises_and_indexes(self):
        tmp = Path(tempfile.mkdtemp(prefix="okf-init-"))
        try:
            target = tmp / "my-vault"
            r = run("init", "--target", str(target), "--actor", "human:tester", "--no-qmd")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((target / "index.md").exists())
            text = (target / "90-templates/meeting.md").read_text(encoding="utf-8")
            self.assertIn("human:tester", text); self.assertNotIn("human:me", text)
            # The ACTOR is `by: human:me` in frontmatter. The token also appears in the prose of
            # 99-system/getting-started.md, whose job is to tell the reader to replace it — a blind
            # replace turned that instruction into nonsense, and would do the same to the user's own
            # notes on a later --force. So: no actor left anywhere, and the instruction intact. (#10)
            for p in kitlib.iter_markdown(target):
                self.assertNotIn("by: human:me", p.read_text(encoding="utf-8"), p)
            guide = (target / "99-system/getting-started.md").read_text(encoding="utf-8")
            self.assertIn("Replace `human:me` with your own actor id", guide,
                          "init rewrote the instruction that tells the reader what to replace")
            self.assertEqual(kitlib.validate_okf(target).errors, [])
            r = run("init", "--target", str(target), "--no-qmd")
            self.assertNotEqual(r.returncode, 0, "refuses to overwrite a non-empty target without --force")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_init_rejects_bad_actor(self):
        tmp = Path(tempfile.mkdtemp(prefix="okf-init-"))
        try:
            r = run("init", "--target", str(tmp / "v"), "--actor", "tester", "--no-qmd")
            self.assertNotEqual(r.returncode, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_log_appends_under_today(self):
        import datetime as dt
        v = temp_vault()
        try:
            r = run("log", "First entry", "--vault", str(v), "--kind", "Creation")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = run("log", "Second entry", "--vault", str(v))
            text = (v / "log.md").read_text(encoding="utf-8")
            today = dt.date.today().isoformat()
            self.assertIn(f"## {today}\n* **Update**: Second entry\n* **Creation**: First entry", text)
            self.assertTrue(text.index(f"## {today}") < text.index("## 2026-09-11"), "newest first")
            rep = kitlib.Report(); kitlib.check_reserved(kitlib.parse_note(v / "log.md", v), rep)
            self.assertEqual(rep.errors, [])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_mcp_config_prints_valid_json(self):
        r = run("mcp-config", "--client", "lmstudio", "--vault", str(VAULT), "--with-qmd-mcp", env=dict(os.environ, KIT_NO_UV="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(r.stdout.split("\n# would be written")[0])
        qmd = cfg["mcpServers"]["qmd"]
        self.assertEqual(qmd["args"], ["mcp"])
        # resolved path when qmd is installed: the MCP client spawns this, and on Windows a
        # bare name does not find a .cmd shim
        self.assertEqual(Path(qmd["command"]).stem, "qmd")
        srv = cfg["mcpServers"]["okf-vault"]
        self.assertTrue(any(a.endswith("vault_bridge.py") for a in srv["args"]))
        self.assertIn("--backend", srv["args"])
        self.assertEqual(srv["command"], sys.executable, "KIT_NO_UV should force the plain-Python launcher")

    def test_mcp_config_prefers_uv_when_present(self):
        import stat
        tmp = Path(tempfile.mkdtemp(prefix="okf-uv-"))
        try:
            if sys.platform.startswith("win"):
                (tmp / "uv.cmd").write_text("@echo off\r\n")
            else:
                stub = tmp / "uv"; stub.write_text("#!/bin/sh\n"); stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
            env = {k: v for k, v in os.environ.items() if k != "KIT_NO_UV"}
            env["PATH"] = str(tmp) + os.pathsep + env.get("PATH", "")
            r = run("mcp-config", "--client", "lmstudio", "--vault", str(VAULT), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            srv = json.loads(r.stdout.split("\n# would be written")[0])["mcpServers"]["okf-vault"]
            self.assertTrue(srv["command"].startswith(str(tmp)), srv)
            self.assertEqual(srv["args"][0], "run")
            self.assertTrue(srv["args"][1].endswith("vault_bridge.py"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mcp_config_write_merges_existing(self):
        tmp = Path(tempfile.mkdtemp(prefix="okf-mcp-"))
        v = temp_vault()
        try:
            env = dict(os.environ, HOME=str(tmp), USERPROFILE=str(tmp))
            cfg_path = tmp / ".lmstudio" / "mcp.json"
            cfg_path.parent.mkdir(parents=True)
            cfg_path.write_text(json.dumps({"mcpServers": {"other": {"url": "http://x"}}}), encoding="utf-8")
            r = run("mcp-config", "--client", "lmstudio", "--vault", str(v), "--write", "--with-qmd-mcp", "--qmd-http", env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertIn("other", data["mcpServers"])
            self.assertEqual(data["mcpServers"]["qmd"], {"url": "http://localhost:8181/mcp"})
            self.assertTrue(cfg_path.with_suffix(".json.bak").exists())
            self.assertIn(f"vault served: {v.resolve()}", r.stdout, "the served vault must be visible on the write lane")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(v.parent, ignore_errors=True)

    LEGACY = {"command": "uv", "args": ["run", "/old/kit/mcp/obsidian_bridge.py", "--backend", "fs", "--vault", "/old/vault"]}
    FOREIGN = {"command": "npx", "args": ["-y", "some-obsidian-mcp"]}

    def test_mcp_config_write_replaces_the_kits_old_obsidian_vault_entry(self):
        """The server was renamed okf-vault; an old entry left beside it spawns a script that no
        longer exists on every client start. One that is not ours keeps its name and stays."""
        for old, kept in ((self.LEGACY, False), (self.FOREIGN, True)):
            with self.subTest(kept=kept):
                tmp = Path(tempfile.mkdtemp(prefix="okf-mcp-"))
                v = temp_vault()
                try:
                    env = dict(os.environ, HOME=str(tmp), USERPROFILE=str(tmp))
                    cfg_path = tmp / ".lmstudio" / "mcp.json"
                    cfg_path.parent.mkdir(parents=True)
                    cfg_path.write_text(json.dumps({"mcpServers": {"obsidian-vault": old}}), encoding="utf-8")
                    r = run("mcp-config", "--client", "lmstudio", "--vault", str(v), "--write", env=env)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    servers = json.loads(cfg_path.read_text(encoding="utf-8"))["mcpServers"]
                    self.assertIn("okf-vault", servers)
                    self.assertEqual("obsidian-vault" in servers, kept)
                    self.assertEqual("replaced the old `obsidian-vault` entry" in r.stdout, not kept)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                    shutil.rmtree(v.parent, ignore_errors=True)

    def test_mcp_config_writes_each_clients_own_file(self):
        """Claude Desktop and Cursor keep their config in different places per OS; the entry is the same."""
        import platform
        for client in ("claude-desktop", "cursor"):
            with self.subTest(client=client):
                tmp = Path(tempfile.mkdtemp(prefix="okf-mcp-"))
                v = temp_vault()
                try:
                    env = dict(os.environ, HOME=str(tmp), USERPROFILE=str(tmp), APPDATA=str(tmp / "AppData"), KIT_NO_UV="1")
                    if client == "cursor":
                        want = tmp / ".cursor" / "mcp.json"
                    elif platform.system() == "Darwin":
                        want = tmp / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
                    elif sys.platform.startswith("win"):
                        want = tmp / "AppData" / "Claude" / "claude_desktop_config.json"
                    else:
                        want = tmp / ".config" / "Claude" / "claude_desktop_config.json"
                    r = run("mcp-config", "--client", client, "--vault", str(v), "--write", env=env)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    srv = json.loads(want.read_text(encoding="utf-8"))["mcpServers"]["okf-vault"]
                    self.assertTrue(srv["args"][0].endswith("vault_bridge.py"), srv)
                    self.assertEqual(srv["args"][srv["args"].index("--vault") + 1], str(v.resolve()))
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                    shutil.rmtree(v.parent, ignore_errors=True)

    def test_doctor_reports_a_client_config_still_naming_the_old_bridge(self):
        tmp = Path(tempfile.mkdtemp(prefix="okf-mcp-"))
        try:
            env = dict(os.environ, HOME=str(tmp), USERPROFILE=str(tmp))
            cfg_path = tmp / ".cursor" / "mcp.json"
            cfg_path.parent.mkdir(parents=True)
            cfg_path.write_text(json.dumps({"mcpServers": {"obsidian-vault": self.LEGACY}}), encoding="utf-8")
            r = run("doctor", "--vault", str(VAULT), "--base-url", "http://127.0.0.1:9", env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("mcp client", r.stdout)
            self.assertIn("kit.py mcp-config --client cursor", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_doctor_runs(self):
        r = run("doctor", "--vault", str(VAULT), "--base-url", "http://127.0.0.1:9")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("qmd", r.stdout); self.assertIn("llm endpoint", r.stdout)

    def test_llm_ask_falls_back_to_local_search_when_no_endpoint(self):
        r = run("llm", "ask", "why did we choose qmd", "--vault", str(VAULT), "--base-url", "http://127.0.0.1:9/v1")
        # search works, the model call fails with a connection error -> non-zero exit, but sources were found
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("no matching notes found", r.stdout)


class KitCliGraphAndRecon(unittest.TestCase):
    def test_validate_includes_ontology(self):
        r = run("validate", "--vault", str(VAULT), "--strict")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== Ontology", r.stdout)
        v = temp_vault()
        try:
            (v / "03-projects/bad.md").write_text("---\ntype: project\ndescription: x\nhealth: purple\nowner: me\n---\n", encoding="utf-8")
            # An ontology enum violation is this kit's rule, not the spec's, so it is reported
            # but does not set the exit code — §11 forbids rejecting a bundle over it. --strict
            # is what gates the repo. (#22)
            r = run("validate", "--vault", str(v))
            self.assertIn("enum_violation", r.stdout)
            self.assertEqual(r.returncode, 0, "our own rule must not read as non-conformance")
            r = run("validate", "--vault", str(v), "--strict")
            self.assertEqual(r.returncode, 1, "--strict still fails on it")
            r = run("validate", "--vault", str(v), "--no-ontology")
            self.assertEqual(r.returncode, 0, "ontology checks can be switched off")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_graph_commands(self):
        v = temp_vault()
        try:
            r = run("graph", "build", "--vault", str(v)); self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("0 finding(s)", r.stdout)
            self.assertTrue((v / ".kit/graph.sqlite").exists())
            r = run("graph", "query", "select count(*) n from nodes", "--vault", str(v), "--json"); self.assertEqual(json.loads(r.stdout)[0]["n"], 26)   # 27: an open sample decision was added for the Open Decisions view (#10); 28: the sample month note; 26: the two confidential people notes are left out (#52)
            r = run("graph", "query", "drop table nodes", "--vault", str(v)); self.assertEqual(r.returncode, 1)
            r = run("graph", "export", "--format", "nt", "--vault", str(v)); self.assertIn("<http://purl.org/dc/terms/title>", r.stdout)
            r = run("graph", "neighbors", "alex-example", "--pred", "owns", "--vault", str(v), "--show-confidential"); self.assertIn("—owns→", r.stdout)
            r = run("graph", "path", "hybrid-search", "alex-example", "--vault", str(v), "--show-confidential"); self.assertEqual(r.returncode, 0)
            r = run("graph", "pack", "search-relaunch", "--vault", str(v)); self.assertIn("## Relations", r.stdout)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_reconcile_todos_minutes_commands(self):
        v = temp_vault()
        try:
            r = run("reconcile", "--vault", str(v)); self.assertEqual(r.returncode, 0); self.assertIn("nothing to reconcile", r.stdout)
            kitlib.set_frontmatter(v / "03-projects/search-relaunch.md", {"health": "red"})
            r = run("reconcile", "--vault", str(v)); self.assertEqual(r.returncode, 2); self.assertIn("hub_drift", r.stdout)
            r = run("reconcile", "--vault", str(v), "--apply"); self.assertEqual(r.returncode, 0); self.assertIn("applied", r.stdout)
            r = run("todos", "--vault", str(v), "--json"); tasks = json.loads(r.stdout); self.assertTrue(tasks and all(not t["done"] for t in tasks))
            r = run("todos", "--vault", str(v), "--digest"); self.assertTrue((v / "00-home/todo-digest.md").exists())
            recap = v.parent / "recap.txt"; recap.write_text("Decision: ship\nAction: tell Sam — Alex, due 2026-09-30\n", encoding="utf-8")
            r = run("minutes", str(recap), "--title", "Ship sync", "--date", "2026-09-21", "--vault", str(v), "--dry-run"); self.assertEqual(r.returncode, 0); self.assertIn("would write", r.stdout)
            self.assertFalse((v / "02-meetings/2026-09-21-ship-sync.md").exists())
            r = run("minutes", str(recap), "--title", "Ship sync", "--date", "2026-09-21", "--vault", str(v), "--daily"); self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((v / "02-meetings/2026-09-21-ship-sync.md").exists())
            r = run("minutes", str(recap), "--title", "Ship sync", "--date", "2026-09-21", "--vault", str(v)); self.assertEqual(r.returncode, 1)
            r = run("validate", "--vault", str(v), "--strict"); self.assertEqual(r.returncode, 0, r.stdout)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_minutes_refuses_a_date_that_does_not_exist_as_an_argument_error(self):
        """`--date 2026-02-30` ended in a ValueError traceback from date.fromisoformat (#114)."""
        v = temp_vault()
        try:
            recap = v.parent / "recap.txt"; recap.write_text("Decision: ship\n", encoding="utf-8")
            for bad in ("2026-02-30", "tomorrow"):
                with self.subTest(bad):
                    r = run("minutes", str(recap), "--title", "Ship sync", "--date", bad, "--vault", str(v),
                            stdin=subprocess.DEVNULL)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn("--date", r.stderr)
                    self.assertIn(bad, r.stderr)
                    self.assertIn("YYYY-MM-DD", r.stderr)
            self.assertFalse(list((v / "02-meetings").glob("*ship-sync.md")), "nothing is filed on a bad date")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_llm_ask_graph_dry_run(self):
        r = run("llm", "ask", "who owns the search relaunch", "--vault", str(VAULT), "--graph", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Facts from the vault graph", r.stdout); self.assertIn("- milestone: Pilot go/no-go", r.stdout)
        self.assertNotIn("(05-people/alex-example.md)", r.stdout, "the owner is a confidential note: no relation names it (#52)")


class CrossPlatform(unittest.TestCase):
    """Windows-shaped defects that used to pass on macOS and fail in the field."""

    def test_output_survives_a_legacy_console(self):
        """`--help` and `doctor` print em dashes and arrows; a cp1252 console must not kill them."""
        for argv in (["--help"], ["doctor", "--vault", str(VAULT)]):
            env = dict(os.environ, PYTHONIOENCODING="cp1252")
            r = run(*argv, env=env)
            self.assertEqual(r.returncode, 0, f"{argv} exited {r.returncode}: {r.stderr[-400:]}")
            self.assertTrue(r.stdout.strip(), f"{argv} printed nothing")

    def test_generated_files_are_lf(self):
        """The vault is LF by contract; text mode on Windows would silently make it CRLF."""
        v = temp_vault()
        try:
            self.assertEqual(run("index", "--vault", str(v)).returncode, 0)
            kitlib.append_log(v, "line-ending check", "Update")
            for rel in ("index.md", "log.md"):
                self.assertNotIn(b"\r\n", (v / rel).read_bytes(), f"{rel} was written with CRLF")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_non_ascii_survives_a_cli_round_trip(self):
        """Note text goes out through a subprocess pipe and must come back unchanged."""
        v = temp_vault()
        try:
            kitlib.append_log(v, "em dash \u2014 arrow \u2192 umlaut \u00fc", "Update")
            r = run("log", "another entry", "--vault", str(v))
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            self.assertIn("em dash \u2014 arrow \u2192 umlaut \u00fc", (v / "log.md").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_a_byte_order_mark_does_not_hide_frontmatter(self):
        """PowerShell 5.1 and legacy Notepad prefix a BOM; the frontmatter behind it must still be read."""
        v = temp_vault()
        try:
            note = v / "07-knowledge/bom-note.md"
            note.write_bytes(
                "---\ntype: concept\ntitle: BOM note\ndescription: A note written by a tool that emits a BOM.\n"
                "sensitivity: confidential\n---\n\n# BOM note\n\nNothing links out of here.\n".encode("utf-8-sig"))
            parsed = kitlib.parse_note(note, v)
            self.assertIsNotNone(parsed.frontmatter, "a leading BOM hid the frontmatter")
            self.assertEqual(parsed.frontmatter["sensitivity"], "confidential")
            r = run("validate", "--vault", str(v), "--strict")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("missing YAML frontmatter", r.stdout)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_piped_recap_is_decoded_as_utf8(self):
        """`minutes -` reads UTF-8 bytes; a legacy code page on stdin would write mojibake into the vault."""
        v = temp_vault()
        try:
            recap = "Entscheidung: Gr\u00f6\u00dfe pr\u00fcfen \u2014 B\u00fcro K\u00f6ln\n"
            r = run("minutes", "-", "--title", "Umlaut sync", "--date", "2026-09-22", "--vault", str(v),
                    input=recap, env=dict(os.environ, PYTHONIOENCODING="cp1252"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            note = (v / "02-meetings/2026-09-22-umlaut-sync.md").read_text(encoding="utf-8")
            self.assertIn("Gr\u00f6\u00dfe pr\u00fcfen \u2014 B\u00fcro K\u00f6ln", note)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_recap_files_survive_the_encodings_windows_tools_write(self):
        """UTF-16 and BOM'd recaps file cleanly; anything else fails with a sentence, not a traceback."""
        v = temp_vault()
        try:
            recap = "Entscheidung: Gr\u00f6\u00dfe pr\u00fcfen\n"
            utf16 = v.parent / "recap-utf16.txt"; utf16.write_bytes(recap.encode("utf-16"))
            r = run("minutes", str(utf16), "--title", "UTF-16 sync", "--date", "2026-09-22", "--vault", str(v))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Gr\u00f6\u00dfe pr\u00fcfen", (v / "02-meetings/2026-09-22-utf-16-sync.md").read_text(encoding="utf-8"))
            bom = v.parent / "recap-bom.txt"; bom.write_bytes(recap.encode("utf-8-sig"))
            r = run("minutes", str(bom), "--title", "BOM sync", "--date", "2026-09-22", "--vault", str(v))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            filed = (v / "02-meetings/2026-09-22-bom-sync.md").read_text(encoding="utf-8")
            self.assertIn("Gr\u00f6\u00dfe pr\u00fcfen", filed); self.assertNotIn("\ufeff", filed)
            legacy = v.parent / "recap-cp1252.txt"; legacy.write_bytes(recap.encode("cp1252"))
            r = run("minutes", str(legacy), "--title", "Legacy sync", "--date", "2026-09-22", "--vault", str(v))
            self.assertEqual(r.returncode, 1)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("re-save it as UTF-8", r.stderr)
            self.assertFalse((v / "02-meetings/2026-09-22-legacy-sync.md").exists())
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class DoctorReport(unittest.TestCase):
    """A tester mails this back. It must diagnose without disclosing."""

    def test_relative_paths_and_scheme_less_hosts_are_redacted_too(self):
        """A consultant's folder is named after the client, so an explicitly relative path
        discloses just as much as an absolute one. A BARE `dir/file` stays unmatched on purpose:
        the report is full of install hints that look like that, and mangling them costs more."""
        import kit
        for raw in ("../../Kunde-Example/models/m.gguf", "./Kunde-Example/m.gguf",
                    "/Users/someone/Kunde-Example/m.gguf"):
            self.assertNotIn("Kunde-Example", kit._scrub(raw), raw)
        # a host:port typed without a scheme is an endpoint, not a path
        self.assertNotIn("corp-lab-07.internal", kit._scrub("corp-lab-07.internal:1234/v1"))
        # and none of this may eat ordinary text or a plain model id
        self.assertEqual(kit._scrub("minicpm5-2b"), "minicpm5-2b")
        for hint in ("bun install -g @tobilu/qmd", "brew tap yakitrak/yakitrak", "see docs/quickstart.md"):
            self.assertEqual(kit._scrub(hint), hint, "an install hint is not a path")
        self.assertEqual(kit._scrub("a sentence; with punctuation, and no path"),
                         "a sentence; with punctuation, and no path")

    def test_a_relative_glob_survives_and_an_absolute_path_is_still_reduced(self):
        """`/` after a glob character is inside a relative pattern, not the start of an absolute
        path: `.obsidian/plugins/*/data.json` came out as `.obsidian/plugins/*data.json` (#114)."""
        import kit
        for kept in ("'.obsidian/plugins/*/data.json'", "Read(.obsidian/plugins/*/data.json)",
                     "09-archive/**/*.md", "notes/?/x.md"):
            self.assertEqual(kit._scrub(kept), kept, "a relative glob is not a path to redact")
        # every place a real absolute path stands in the report is still reduced to its basename
        for raw in ("/Users/someone/Kunde-Example/m.gguf", "at /Users/someone/Kunde-Example/m.gguf",
                    "'/Users/someone/Kunde-Example/m.gguf'", '"/Users/someone/Kunde-Example/m.gguf"',
                    "(/Users/someone/Kunde-Example/m.gguf)", "[/Users/someone/Kunde-Example/m.gguf]",
                    "--vault=/Users/someone/Kunde-Example/m.gguf", "vault:/Users/someone/Kunde-Example/m.gguf",
                    "/Users/someone/Kunde-Example/.obsidian/plugins/*/m.gguf",
                    r"C:\Users\someone\Kunde-Example\m.gguf", "~/Kunde-Example/m.gguf"):
            out = kit._scrub(raw)
            self.assertNotIn("Kunde-Example", out, raw)
            self.assertIn("m.gguf", out, raw)

    def test_the_sdk_row_is_not_fooled_by_the_repo_s_own_mcp_directory(self):
        """This repository has a directory named `mcp/`, so from the repo root a bare
        `import mcp` succeeds as a namespace package. Detection must ask the package database,
        or doctor reports the SDK present on exactly the machines that lack it."""
        src = (ROOT / "kit.py").read_text(encoding="utf-8")
        self.assertNotIn("import mcp  # noqa", src, "doctor must not detect the SDK by importing it")
        self.assertIn("importlib.metadata.version(\"mcp\")", src)

    def test_a_unc_path_is_reduced_like_any_other(self):
        """On a managed Windows machine the model or the vault often lives on a file share, and
        the server name is exactly the internal detail this report promises not to carry."""
        import kit
        for raw in (r"\\CORPFS01\models\tenant-4711\m.gguf",
                    r"\\corpfs01.corp.example\share\home\someone\vault"):
            out = kit._scrub(raw)
            self.assertNotIn("CORPFS01", out, out)
            self.assertNotIn("corpfs01", out, out)
            self.assertNotIn("tenant-4711", out, out)
            self.assertNotIn("\\\\", out, out)

    def test_report_carries_no_paths_no_username_no_note_content(self):
        v = temp_vault()
        try:
            out = Path(v.parent) / "doctor.txt"
            r = run("doctor", "--vault", str(v), "--report", str(out))
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            text = out.read_text(encoding="utf-8")

            self.assertIn(kitlib.KIT_VERSION, text, "we need to know which build produced it")
            self.assertIn("platform", text)

            self.assertNotIn(str(Path.home()), text, "home directory leaked")
            self.assertNotIn(getpass.getuser(), text, "username leaked")
            self.assertNotIn(str(v), text, "the vault path leaked")
            # note titles from the template vault must not appear
            for phrase in ("Search relaunch", "Alex Example", "Prepare go/no-go"):
                self.assertNotIn(phrase, text, f"note content leaked: {phrase!r}")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_report_says_whether_a_proxy_is_set_without_naming_it(self):
        """At a corporate site the proxy URL is the one variable that carries credentials."""
        v = temp_vault()
        try:
            out = Path(v.parent) / "doctor.txt"
            run("doctor", "--vault", str(v), "--report", str(out),
                env=dict(os.environ, HTTPS_PROXY="http://svc-user:hunter2@proxy.example.com:8080"))
            text = out.read_text(encoding="utf-8")
            self.assertIn("proxy     set", text)
            for secret in ("proxy.example.com", "hunter2", "svc-user"):
                self.assertNotIn(secret, text, f"proxy detail leaked: {secret}")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_report_redacts_a_credentialed_endpoint_and_a_user_named_vault(self):
        """The three leaks a default-environment run can never reach: base URL, vault name, model ids."""
        user = getpass.getuser()
        tmp = Path(tempfile.mkdtemp(prefix="okf-doctor-"))
        try:
            v = tmp / user                        # a vault folder named after its owner
            shutil.copytree(VAULT, v)
            out = tmp / "doctor.txt"
            r = run("doctor", "--vault", str(v), "--report", str(out),
                    "--base-url", f"http://svc-account:S3cretPass@{user.capitalize()}-MacBook-Pro.invalid:1234/v1")
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            text = out.read_text(encoding="utf-8")
            for secret in ("S3cretPass", "svc-account", "MacBook-Pro", str(tmp)):
                self.assertNotIn(secret, text, f"leaked: {secret}")
            self.assertNotIn(user.lower(), text.lower(), "the vault folder name carried the username")
            self.assertIn(":1234", text, "the port is diagnostic and should survive")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_report_reduces_a_path_shaped_model_id_to_its_basename(self):
        """llama-server reports the loaded .gguf file, so a model id is often a home path."""
        user = getpass.getuser()
        text = report_for([f"/Users/{user.capitalize()}/Models/private-project/qwen2.5-7b.gguf", "acme-internal/codename-x"])
        self.assertIn("qwen2.5-7b.gguf", text, "the model still has to be identifiable")
        self.assertNotIn("private-project", text); self.assertNotIn("/Models/", text)
        self.assertNotIn(user.lower(), text.lower())
        self.assertIn("codename-x", text, "a plain model id is not a path and stays")

    def test_report_drops_an_api_key_carried_in_the_base_url_query(self):
        """Half the hosted OpenAI-compatible endpoints authenticate with `?api_key=`, not a header."""
        text = report_for(None, base_url="http://endpoint.corp-site.invalid:1234/v1?api_key=hunter2")
        for secret in ("hunter2", "api_key", "corp-site", "endpoint.corp-site.invalid"):
            self.assertNotIn(secret, text, f"leaked: {secret}")
        self.assertIn(":1234", text, "the port is diagnostic and should survive")
        self.assertIn("llm endpoint", text, "the report still has to say the endpoint was tried")

    def test_report_drops_a_tenant_id_carried_in_the_base_url_path(self):
        """A corporate gateway puts the site in the path: keeping "the useful part" discloses it."""
        text = report_for(None, base_url="http://gw.example.invalid:1234/corp-tenant-4711/v1")
        for secret in ("corp-tenant-4711", "gw.example.invalid"):
            self.assertNotIn(secret, text, f"leaked: {secret}")
        self.assertIn(":1234", text)

    def test_report_drops_a_base_url_fragment(self):
        """A fragment is pasted along with whatever the vendor's console copied into it."""
        text = report_for(None, base_url="http://gw.example.invalid:1234/v1#hunter2")
        for secret in ("hunter2", "gw.example.invalid"):
            self.assertNotIn(secret, text, f"leaked: {secret}")
        self.assertIn(":1234", text)

    def test_report_reduces_a_model_path_with_spaces_to_its_basename(self):
        """A space in a model path is the common case -- `LM Studio` is the vendor's own default
        folder. The token this asserts on must not be one the report can produce for another
        reason: `LM Studio` also appears in the `lms` install hint, which is printed only when lms
        is absent, so it passes on a developer laptop and fails on every CI runner."""
        user = getpass.getuser()
        text = report_for([f"/Users/{user.capitalize()}/Vendor Suite/models/qwen2.5-7b.gguf",
                           f"C:\\Users\\{user.capitalize()}\\Corp Models\\secret.gguf"])
        for secret in ("Vendor Suite", "Corp Models", "C:\\", "/Users/"):
            self.assertNotIn(secret, text, f"leaked: {secret}")
        self.assertNotIn(user.lower(), text.lower())
        self.assertIn("qwen2.5-7b.gguf", text, "the model still has to be identifiable")
        self.assertIn("secret.gguf", text)

    def test_report_reduces_a_file_url_model_id_to_its_basename(self):
        """A `file://` id is a path wearing a scheme; the URL rule must not claim it."""
        user = getpass.getuser()
        text = report_for([f"file:///Users/{user.capitalize()}/Models/qwen2.5-7b.gguf",
                           "file:///srv/corp-nfs/models/secret.gguf"])
        for secret in ("corp-nfs", "/Models/", "/srv/", "file://"):
            self.assertNotIn(secret, text, f"leaked: {secret}")
        self.assertNotIn(user.lower(), text.lower())
        self.assertIn("qwen2.5-7b.gguf", text); self.assertIn("secret.gguf", text)

    def test_report_reduces_a_model_path_with_punctuation_to_its_basename(self):
        """A `,` `;` or `)` inside a path must not truncate it into a disclosing fragment."""
        text = report_for(["/srv/corp-nfs/models/a,b/first.gguf", "/opt/corp-team/(private)/second.gguf",
                           "/opt/corp-team/p;q/third.gguf"])
        for secret in ("corp-nfs", "corp-team", "(private)", "p;q", "a,b"):
            self.assertNotIn(secret, text, f"leaked: {secret}")
        for kept in ("first.gguf", "second.gguf", "third.gguf"):
            self.assertIn(kept, text, "the model still has to be identifiable")

    def test_report_redacts_a_vault_named_after_the_person_not_the_account(self):
        """People name the folder for themselves, not their login: `<user>-Notizen` is still them."""
        user = getpass.getuser()
        text = report_for(None, base_url="http://127.0.0.1:1/v1", vault_name=f"{user.capitalize()}-Notizen")
        self.assertNotIn("Notizen", text, "the rest of the folder name names the owner too")
        self.assertNotIn(user.lower(), text.lower())
        self.assertIn("vault", text, "the report still has to say a vault was found")

    def test_report_drops_a_query_string_behind_a_bracketed_ipv6_host(self):
        """`[::1]` is on the loopback allow-list, so the token has to survive its brackets whole."""
        text = report_for(None, base_url="http://[::1]:9/v1?api_key=hunter2#hunter2")
        for secret in ("hunter2", "api_key"):
            self.assertNotIn(secret, text, f"leaked: {secret}")
        self.assertIn("[::1]:9", text, "a loopback endpoint is the diagnosis, not a disclosure")
        self.assertIn("openai-compat", text, "the report still has to name the active provider")

    def test_doctor_still_reports_when_a_provider_is_misconfigured(self):
        """The command that diagnoses a broken config must not die on one."""
        v = temp_vault()
        try:
            (v / ".kit").mkdir(exist_ok=True)
            (v / ".kit/config.yml").write_text(f"providers:\n  search: /Users/{getpass.getuser()}/secret-project/mysearch\n",
                                               encoding="utf-8", newline="\n")
            out = Path(v.parent) / "doctor.txt"
            # without the offline suite's pin: the vault config is under test, and the variable outranks it
            env = {k: val for k, val in os.environ.items() if k != "KIT_SEARCH_PROVIDER"}
            r = run("doctor", "--vault", str(v), "--report", str(out), env=env)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            text = out.read_text(encoding="utf-8")
            # Assert the behaviour, not the phrasing. The layer below (#26) made doctor survive a
            # broken config and worded the row one way; this layer moved the resilience into
            # kitproviders.status, which words it another. What must hold either way is that the
            # report is still written and names the provider the config asked for.
            self.assertIn("mysearch", text)
            self.assertIn("search", text)
            self.assertNotIn("secret-project", text)
            self.assertNotIn(getpass.getuser().lower(), text.lower())
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class TheSampleVaultIsNotYours(unittest.TestCase):
    """`mcp-config --write` with no --vault used to bind the client to the kit's own sample notes."""

    def test_write_refuses_the_template_vault(self):
        tmp = Path(tempfile.mkdtemp(prefix="okf-tmpl-"))
        try:
            env = dict(os.environ, HOME=str(tmp), USERPROFILE=str(tmp))
            env.pop("KIT_VAULT", None)
            r = run("mcp-config", "--client", "lmstudio", "--write", env=env)
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("--vault", r.stdout + r.stderr)
            self.assertFalse((tmp / ".lmstudio" / "mcp.json").exists(), "nothing may be written")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_printing_it_carries_a_warning(self):
        env = dict(os.environ, KIT_NO_UV="1")
        env.pop("KIT_VAULT", None)
        r = run("mcp-config", "--client", "lmstudio", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        json.loads(r.stdout.split("\n# would be written")[0])          # still valid JSON to paste
        self.assertIn("WARNING", r.stdout)
        self.assertIn("--vault ~/Notes/vault", r.stdout)

    def test_a_real_vault_is_written_without_complaint(self):
        tmp = Path(tempfile.mkdtemp(prefix="okf-tmpl-"))
        v = temp_vault()
        try:
            env = dict(os.environ, HOME=str(tmp), USERPROFILE=str(tmp), KIT_NO_UV="1")
            r = run("mcp-config", "--client", "lmstudio", "--vault", str(v), "--write", env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("WARNING", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(v.parent, ignore_errors=True)


class NoVaultGivenIsNotTheTemplate(unittest.TestCase):
    """Without --vault or KIT_VAULT every command fell back to the kit's own sample vault (#114).

    A writer then changed the template: `examples remove` deleted the sample set with no prompt,
    and every later `init` copied the damage. These run a *copy* of the kit, so a writer that
    still acts cannot touch this checkout's vault/ while the test watches it fail.
    """

    def setUp(self):
        self.kit = Path(tempfile.mkdtemp(prefix="okf-kitcopy-"))
        self.addCleanup(shutil.rmtree, self.kit, ignore_errors=True)
        for p in ROOT.glob("*.py"):
            shutil.copy2(p, self.kit / p.name)
        shutil.copy2(ROOT / "VERSION", self.kit / "VERSION")
        for d in ("mcp", "config", "vault"):
            shutil.copytree(ROOT / d, self.kit / d, ignore=shutil.ignore_patterns("__pycache__", ".kit"))
        self.env = dict(os.environ)
        self.env.pop("KIT_VAULT", None)
        self.recap = self.kit / "recap.txt"
        self.recap.write_text("We agreed to ship.\n", encoding="utf-8")

    def kit_run(self, *args, env=None):
        return subprocess.run([sys.executable, str(self.kit / "kit.py"), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False, cwd=self.kit, env=env or self.env)

    def snapshot(self) -> dict[str, bytes]:
        v = self.kit / "vault"
        return {p.relative_to(v).as_posix(): p.read_bytes() for p in v.rglob("*") if p.is_file()}

    def test_every_writer_refuses_the_fallback_and_writes_nothing(self):
        writers = [
            ("log", "hello"),
            ("index",),
            ("examples", "remove"),
            ("minutes", str(self.recap), "--title", "Sync"),
            ("reconcile", "--apply"),
            ("todos", "--digest"),
            ("todos", "--sync-done"),
            ("proposals", "apply", "p-1"),
            ("proposals", "reject", "p-1"),
            ("graph", "build"),
            ("graph", "query", "SELECT 1", "--rebuild"),
            ("validate", "--fix-aliases", "--apply"),
        ]
        before = self.snapshot()
        for argv in writers:
            with self.subTest(argv=" ".join(argv)):
                r = self.kit_run(*argv)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("refused:", r.stderr)
                self.assertIn("--vault", r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(self.snapshot(), before, f"{' '.join(argv)} wrote into the template vault")

    def test_naming_the_template_explicitly_is_allowed(self):
        """`make lint` validates vault/ by name, and a maintainer regenerates its index the same way."""
        r = self.kit_run("index", "--vault", str(self.kit / "vault"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.kit_run("log", "hello", env=dict(self.env, KIT_VAULT=str(self.kit / "vault")))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_reader_says_which_vault_it_read_on_stderr(self):
        for argv in (("validate",), ("examples", "remove", "--dry-run"), ("reconcile",), ("todos", "--json")):
            with self.subTest(argv=" ".join(argv)):
                r = self.kit_run(*argv)
                self.assertNotIn("refused:", r.stderr)
                lines = [l for l in r.stderr.splitlines() if "sample vault" in l]
                self.assertEqual(len(lines), 1, r.stderr)
                self.assertIn("--vault", lines[0])
                self.assertNotIn("sample vault", r.stdout, "stdout stays the command's own output")

    def test_a_named_vault_gets_no_notice(self):
        r = self.kit_run("validate", "--vault", str(self.kit / "vault"))
        self.assertNotIn("sample vault", r.stderr)

    def test_a_reader_writes_nothing_into_the_fallback(self):
        """A read must not leave `.kit/` in the template either: `init` copies the template whole,
        and a release archive has no .gitignore to keep a graph cache or a proposals folder out."""
        readers = [
            ("proposals", "list"),
            ("proposals", "show", "p-1"),
            ("graph", "query", "SELECT count(*) AS n FROM nodes"),
        ]
        for argv in readers:
            with self.subTest(argv=" ".join(argv)):
                shutil.rmtree(self.kit / "vault" / ".kit", ignore_errors=True)   # each reader on its own
                r = self.kit_run(*argv)
                self.assertNotIn("no vault given", r.stderr, "a reader is not refused")
                self.assertNotIn("Traceback", r.stderr)
                self.assertFalse((self.kit / "vault" / ".kit").exists(),
                                 f"{' '.join(argv)} created .kit/ in the template vault")
        r = self.kit_run("graph", "query", "SELECT count(*) AS n FROM nodes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertGreater(int(r.stdout.splitlines()[1]), 0, "the query is still answered, from memory")

    def test_a_stale_cache_in_the_fallback_is_answered_from_memory_not_rewritten(self):
        v = self.kit / "vault"
        r = self.kit_run("graph", "build", "--vault", str(v))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        built = self.snapshot()
        extra = v / "zz-added-after-the-build.md"
        extra.write_text("---\ntype: note\ntitle: Added after the build\n---\n\nBody.\n", encoding="utf-8")
        r = self.kit_run("graph", "query", "SELECT count(*) AS n FROM nodes WHERE title = 'Added after the build'")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[1], "1", "the answer reflects the notes on disk, not the stale file")
        after = self.snapshot(); after.pop(extra.relative_to(v).as_posix())
        self.assertEqual(after, built, "a read rewrote the template's graph cache")


class McpConfigAcrossTheWslBoundary(unittest.TestCase):
    """In WSL the file lands in the Linux home, which LM Studio on Windows never reads."""

    def run_mcp_config(self, *argv, wsl: bool) -> str:
        sys.path.insert(0, str(ROOT))
        import kit
        out = io.StringIO()
        with mock.patch.object(kit, "_is_wsl", return_value=wsl), contextlib.redirect_stdout(out):
            self.assertEqual(kit.main(["mcp-config", *argv]), 0)
        return out.getvalue()

    def test_it_says_where_the_windows_client_reads_instead(self):
        tmp = Path(tempfile.mkdtemp(prefix="okf-wsl-"))
        v = temp_vault()
        try:
            with mock.patch.dict(os.environ, {"HOME": str(tmp), "USERPROFILE": str(tmp), "KIT_NO_UV": "1"}):
                text = self.run_mcp_config("--client", "lmstudio", "--vault", str(v), "--write", wsl=True)
            self.assertIn("WSL detected", text)
            self.assertIn(".lmstudio", text)
            self.assertIn('"mcpServers"', text.split("WSL detected")[1], "print the JSON to paste")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_nothing_is_said_off_wsl(self):
        tmp = Path(tempfile.mkdtemp(prefix="okf-wsl-"))
        v = temp_vault()
        try:
            with mock.patch.dict(os.environ, {"HOME": str(tmp), "USERPROFILE": str(tmp), "KIT_NO_UV": "1"}):
                text = self.run_mcp_config("--client", "lmstudio", "--vault", str(v), "--write", wsl=False)
            self.assertNotIn("WSL detected", text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(v.parent, ignore_errors=True)


class InitRecordsItsChoices(unittest.TestCase):
    def test_the_collection_name_lands_in_the_vault_config(self):
        tmp = Path(tempfile.mkdtemp(prefix="okf-init-"))
        try:
            target = tmp / "my-vault"
            r = run("init", "--target", str(target), "--no-qmd", "--collection", "work-notes")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            import kitproviders
            self.assertEqual(kitproviders.collection_name(target), "work-notes",
                             "llm ask must scope qmd to the collection init registered")
            self.assertIn("qmd collection add", r.stdout, "the manual command must name the vault")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# What a vault kept in git must leave out (#54). The lines are the contract with the docs.
VAULT_IGNORES = (".kit/*", "!.kit/config.yml", ".obsidian/workspace*.json", ".obsidian/cache", ".obsidian/plugins/*/data.json")


def git_env(**extra) -> dict:
    """An environment where neither the developer's global or system git config nor git's default
    excludes file (`$XDG_CONFIG_HOME/git/ignore`, read even with no config) can change what
    `git check-ignore` answers: one that ignores `.kit/` would hide the bug."""
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_COUNT="1",
               GIT_CONFIG_KEY_0="core.excludesFile", GIT_CONFIG_VALUE_0=os.devnull, **extra)
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(var, None)
    return env


def git_in(where: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=where, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False, env=git_env())


class InitKeepsMachineStateOutOfGit(unittest.TestCase):
    """`init` gave new vaults no .gitignore, so `git add .` committed `.kit/` and plugin credentials."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="okf-init-git-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_init_writes_a_gitignore_with_one_comment_per_line(self):
        target = self.tmp / "v"
        r = run("init", "--target", str(target), "--no-qmd")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        gi = target / ".gitignore"
        self.assertTrue(gi.is_file(), "init must write <vault>/.gitignore")
        lines = gi.read_text(encoding="utf-8").splitlines()
        patterns = [ln for ln in lines if ln and not ln.startswith("#")]
        self.assertEqual(tuple(patterns), VAULT_IGNORES)
        for ln in patterns:
            above = lines[lines.index(ln) - 1]
            self.assertTrue(above.startswith("# "), f"{ln!r} needs its own comment saying why")
        guide = (ROOT / "docs" / "vault-guide.md").read_text(encoding="utf-8")
        for ln in lines:
            self.assertIn(ln, guide, "docs/vault-guide.md shows the file init writes; keep them the same")

    @unittest.skipUnless(shutil.which("git"), "git not on PATH")
    def test_the_written_gitignore_ignores_what_it_says(self):
        target = self.tmp / "v"
        self.assertEqual(run("init", "--target", str(target), "--no-qmd").returncode, 0)
        self.assertEqual(git_in(target, "init", "-q").returncode, 0)
        for path in (".kit/graph.json", ".kit/bridge-audit.jsonl", ".kit/proposals/x.json",
                     ".kit/agent-settings/claude-code.json", ".obsidian/workspace.json", ".obsidian/workspace-mobile.json", ".obsidian/cache",
                     ".obsidian/plugins/obsidian-local-rest-api/data.json"):
            with self.subTest(path=path):
                self.assertEqual(git_in(target, "check-ignore", "-q", "--no-index", path).returncode, 0,
                                 f"{path} would be committed")
        for path in ("index.md", ".kit/config.yml", ".obsidian/app.json",
                     ".obsidian/plugins/obsidian-linter/main.js"):
            with self.subTest(path=path):
                self.assertEqual(git_in(target, "check-ignore", "-q", "--no-index", path).returncode, 1,
                                 f"{path} belongs in the vault's history")

    @unittest.skipUnless(shutil.which("git"), "git not on PATH")
    def test_git_add_keeps_the_kit_config_and_leaves_the_rest_of_kit_out(self):
        """The config holds the confidentiality floors and open folders: a clone needs it (#54).
        git cannot re-include a file below an excluded folder, so the rule must be `.kit/*`."""
        target = self.tmp / "v"
        self.assertEqual(run("init", "--target", str(target), "--no-qmd").returncode, 0)
        for rel in (".kit/graph.json", ".kit/bridge-audit.jsonl", ".kit/proposals/hidden-draft.json",
                    ".kit/agent-settings/claude-code.json"):
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
        self.assertEqual(git_in(target, "init", "-q").returncode, 0)
        self.assertEqual(git_in(target, "add", ".").returncode, 0)
        self.assertEqual(git_in(target, "ls-files", ".kit").stdout.split(), [".kit/config.yml"])

    def test_force_leaves_an_existing_gitignore_byte_identical(self):
        target = self.tmp / "v"
        target.mkdir()
        mine = b"# my own rules\r\n*.pdf\r\nprivate/"          # CRLF, no trailing newline
        (target / ".gitignore").write_bytes(mine)
        r = run("init", "--target", str(target), "--no-qmd", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((target / ".gitignore").read_bytes(), mine)

    def test_a_populated_checkout_does_not_seed_the_new_vault(self):
        """After a plain `graph build` in the checkout, `init` copied the sample's `.kit/` state."""
        import kit
        template = self.tmp / "template"
        shutil.copytree(VAULT, template, ignore=shutil.ignore_patterns(".kit"))
        for rel in (".kit/graph.json", ".kit/bridge-audit.jsonl", ".kit/proposals/hidden-draft.json",
                    ".obsidian/workspace.json", ".obsidian/workspace-mobile.json", ".obsidian/cache/state"):
            p = template / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
        target = self.tmp / "v"
        with mock.patch.object(kit, "TEMPLATE_VAULT", template), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(kit.main(["init", "--target", str(target), "--no-qmd"]), 0)
        self.assertEqual(sorted(p.name for p in (target / ".kit").iterdir()), ["config.yml"])
        for rel in (".obsidian/workspace.json", ".obsidian/workspace-mobile.json", ".obsidian/cache"):
            self.assertFalse((target / rel).exists(), f"{rel} is per-machine state of the checkout")
        self.assertTrue((target / ".obsidian" / "app.json").is_file(), "the settings themselves still travel")

    def init_from(self, template: Path, target: Path, *extra: str) -> None:
        import kit
        with mock.patch.object(kit, "TEMPLATE_VAULT", template), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(kit.main(["init", "--target", str(target), "--no-qmd", *extra]), 0)

    def template_with(self, files: dict[str, str]) -> Path:
        template = self.tmp / "template"
        shutil.copytree(VAULT, template, ignore=shutil.ignore_patterns(".kit"))
        for rel, text in files.items():
            p = template / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return template

    def test_only_the_template_root_s_state_is_left_out(self):
        """The patterns matched at every depth: a notes folder named `cache`, or a note's
        `workspace-plan.json`, was dropped from the copy."""
        notes = ("07-knowledge/cache/hidden-note.md", "03-projects/.kit/hidden.md",
                 "03-projects/workspace-plan.json", "03-projects/cache")
        template = self.template_with({rel: "kept" for rel in notes})
        target = self.tmp / "v"
        self.init_from(template, target)
        for rel in notes:
            with self.subTest(rel=rel):
                self.assertTrue((target / rel).is_file(), f"{rel} is the user's content, not machine state")

    def test_the_template_s_kit_config_is_copied_and_its_state_is_not(self):
        """The config holds the floors and open folders; the rest of `.kit/` is per-machine."""
        config = "# the template's floors\nconfidential_folders: [05-people]\ncollection: \"tpl\"\n"
        template = self.template_with({".kit/config.yml": config, ".kit/graph.json": "{}",
                                       ".kit/proposals/hidden-draft.json": "{}"})
        target = self.tmp / "v"
        self.init_from(template, target)
        self.assertEqual(sorted(p.name for p in (target / ".kit").iterdir()), ["config.yml"])
        self.assertEqual((target / ".kit" / "config.yml").read_text(encoding="utf-8"), config)

    def test_force_keeps_the_vault_s_own_kit_config(self):
        """`copytree` overwrites: under `--force` the template's config would replace the
        vault's own floors, and its confidential folders would go open without a word."""
        template = self.template_with({".kit/config.yml": "collection: \"tpl\"\n"})
        target = self.tmp / "v"
        mine = b"confidential_folders: [hidden-folder]\r\ncollection: \"mine\"\r\n"
        (target / ".kit").mkdir(parents=True)
        (target / ".kit" / "config.yml").write_bytes(mine)
        self.init_from(template, target, "--force")
        self.assertEqual((target / ".kit" / "config.yml").read_bytes(), mine)


@unittest.skipUnless(shutil.which("git"), "git not on PATH")
class DoctorVaultGitRow(unittest.TestCase):
    """Doctor warns before the first commit that `.kit/` or plugin settings would go into git."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="okf-doctor-git-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = self.tmp / "vault"
        shutil.copytree(VAULT, self.vault, ignore=shutil.ignore_patterns(".kit"))

    def doctor(self, *extra, env=None):
        r = run("doctor", "--vault", str(self.vault), "--base-url", "http://127.0.0.1:9", *extra,
                env=env or git_env(GIT_CEILING_DIRECTORIES=str(self.tmp)))
        self.assertEqual(r.returncode, 0, "the row is informational; doctor's exit code must not change\n"
                         + r.stdout + r.stderr)
        return r.stdout

    @staticmethod
    def row(out: str) -> str:
        return next((ln for ln in out.splitlines() if ln.startswith("vault git")), "")

    def test_an_unignored_kit_folder_is_a_warning_before_the_first_commit(self):
        self.assertEqual(git_in(self.vault, "init", "-q").returncode, 0)
        row = self.row(self.doctor())
        self.assertIn("warning", row)
        self.assertIn(".kit/", row)
        self.assertIn("plugin", row)

    def test_a_tracked_kit_file_is_counted_with_the_fix(self):
        self.assertEqual(git_in(self.vault, "init", "-q").returncode, 0)
        for rel in (".kit/hidden-state.json", ".obsidian/plugins/hidden-plugin/data.json"):
            p = self.vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
        self.assertEqual(git_in(self.vault, "add", "-f", ".kit", ".obsidian").returncode, 0)
        report = self.tmp / "doctor.txt"
        row = self.row(self.doctor("--report", str(report)))
        self.assertIn("2 file(s)", row)
        self.assertIn("git rm --cached", row)
        self.assertIn("git ls-files -ci --exclude-standard", row, "the fix picks files by the rules, not by folder")
        self.assertIn("rotate", row, "a pushed key is in the remote's history; removing it is not enough")
        text = report.read_text(encoding="utf-8")
        import kit
        self.assertIn(kit._GIT_FIX, text, "the report must carry the fix unmangled")
        for leak in ("hidden-state", "hidden-plugin", str(self.vault)):
            self.assertNotIn(leak, text, "the report carries no path from inside the vault")

    def test_a_vault_made_by_init_is_ok(self):
        shutil.rmtree(self.vault)
        self.assertEqual(run("init", "--target", str(self.vault), "--no-qmd").returncode, 0)
        self.assertEqual(git_in(self.vault, "init", "-q").returncode, 0)
        row = self.row(self.doctor())
        self.assertTrue(row, "inside a work tree the row is there")
        self.assertNotIn("warning", row.lower())

    def test_no_row_outside_a_work_tree(self):
        out = self.doctor()
        self.assertEqual(self.row(out), "")
        self.assertIn("vault", out)

    def test_no_row_for_the_kit_s_own_sample(self):
        """The sample sits in the kit's checkout: its git rules are the kit's, not a vault's."""
        r = run("doctor", "--vault", str(VAULT), "--base-url", "http://127.0.0.1:9", env=git_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.row(r.stdout), "")

    def test_no_row_and_no_failure_without_git(self):
        self.assertEqual(git_in(self.vault, "init", "-q").returncode, 0)
        empty = self.tmp / "empty-path"
        empty.mkdir()
        out = self.doctor(env=git_env(PATH=str(empty)))
        self.assertEqual(self.row(out), "")

    # A vault made by `init`, under git, with `lines` added to its .gitignore and `files` written.
    def init_vault(self, lines: tuple[str, ...] = (), files: tuple[str, ...] = ()):
        # Git writes its object files read-only; on Windows that makes rmtree fail with
        # "Access is denied" when a test builds a second vault in the same place.
        for d, _, names in os.walk(self.vault):
            for n in names:
                os.chmod(os.path.join(d, n), stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(self.vault)
        self.assertEqual(run("init", "--target", str(self.vault), "--no-qmd").returncode, 0)
        self.assertEqual(git_in(self.vault, "init", "-q").returncode, 0)
        if lines:
            with (self.vault / ".gitignore").open("a", encoding="utf-8", newline="\n") as fh:
                fh.write("".join(f"{ln}\n" for ln in lines))
        for rel in files:
            p = self.vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")

    LINTER = ".obsidian/plugins/obsidian-linter/data.json"

    def test_a_re_included_plugin_is_not_a_warning(self):
        """docs/vault-guide.md recommends `!.obsidian/plugins/<id>/data.json`; doctor warned about it."""
        self.init_vault(lines=(f"!{self.LINTER}",), files=(self.LINTER,))
        row = self.row(self.doctor())
        self.assertIn("ok", row)
        self.assertNotIn("warning", row.lower())

    def test_a_committed_re_included_plugin_is_not_counted(self):
        self.init_vault(lines=(f"!{self.LINTER}",), files=(self.LINTER,))
        self.assertEqual(git_in(self.vault, "add", self.LINTER).returncode, 0)
        row = self.row(self.doctor())
        self.assertIn("ok", row)
        self.assertNotIn("warning", row.lower())

    def test_kit_config_kept_in_git_is_not_counted(self):
        """The ignore file init writes keeps `.kit/config.yml`, the floors and open folders, in
        git. Doctor must not then tell the user to untrack it."""
        self.init_vault()
        self.assertEqual(git_in(self.vault, "add", ".").returncode, 0)
        self.assertIn(".kit/config.yml", git_in(self.vault, "ls-files", ".kit").stdout.split())
        row = self.row(self.doctor())
        self.assertIn("ok", row)
        self.assertNotIn("warning", row.lower())

    def test_a_tracked_kit_config_is_never_counted(self):
        """Whatever rule covers it, a tracked `.kit/config.yml` is where it belongs (#54). A vault
        with no rule for `.kit`, or one from before the `.kit/*` rule that force-added it, gets the
        count of the other files only."""
        for lines in ((), (".kit/",)):
            with self.subTest(lines=lines):
                self.init_vault(files=(".kit/graph.json",))
                gi = self.vault / ".gitignore"
                kept = [ln for ln in gi.read_text(encoding="utf-8").splitlines() if not ln.startswith((".kit", "!.kit"))]
                gi.write_text("".join(f"{ln}\n" for ln in (*kept, *lines)), encoding="utf-8", newline="\n")
                self.assertEqual(git_in(self.vault, "add", "-f", ".kit/config.yml", ".kit/graph.json").returncode, 0)
                row = self.row(self.doctor())
                self.assertIn("WARNING: 1 file(s)", row)

    def test_only_files_the_rules_cover_are_counted_and_the_fix_lists_just_those(self):
        hidden = ".obsidian/plugins/hidden-plugin/data.json"
        self.init_vault(lines=(f"!{self.LINTER}",), files=(self.LINTER, hidden))
        self.assertEqual(git_in(self.vault, "add", "-f", self.LINTER, hidden).returncode, 0)
        row = self.row(self.doctor())
        self.assertIn("WARNING: 1 file(s)", row)
        self.assertNotIn("not ignored", row)
        import kit
        self.assertIn(kit._GIT_FIX, row)
        # The fix names its files by the rules, so it leaves the re-included plugin tracked.
        listed = git_in(self.vault, "ls-files", "-ci", "--exclude-standard")
        self.assertEqual(listed.stdout.split(), [hidden])

    def test_a_tracked_file_under_an_ignore_rule_is_counted_not_reported_unignored(self):
        """`git check-ignore` without --no-index answers "not ignored" for any tracked path."""
        self.init_vault(files=(".kit/graph.json", ".obsidian/plugins/hidden-plugin/data.json"))
        self.assertEqual(git_in(self.vault, "add", "-f", ".kit/graph.json",
                                ".obsidian/plugins/hidden-plugin/data.json").returncode, 0)
        row = self.row(self.doctor())
        self.assertIn("WARNING: 2 file(s)", row)
        self.assertNotIn("not ignored", row)

    def test_git_that_does_not_answer_is_said_so(self):
        import kit
        real = kit._git
        fatal = subprocess.CompletedProcess([], 128, "", "fatal")
        # Each git call fails on its own, the others answer for real; then git that prints nothing.
        for verb, broken in (("ls-files", fatal), ("ls-files", None), ("check-ignore", fatal),
                             ("check-ignore", None),
                             ("check-ignore", subprocess.CompletedProcess([], 0, "", ""))):
            def git(g, v, *args, stdin=None, verb=verb, broken=broken):
                return broken if args[0] == verb else real(g, v, *args, stdin=stdin)

            with self.subTest(verb=verb, broken=broken), mock.patch.object(kit, "_git", side_effect=git), \
                    mock.patch.dict(os.environ, git_env(GIT_CEILING_DIRECTORIES=str(self.tmp))):
                self.assertEqual(git_in(self.vault, "init", "-q").returncode, 0)
                self.assertEqual(kit._vault_git_row(self.vault), "cannot check: git did not answer")


class DoctorKeepsDiagnosing(unittest.TestCase):
    def test_an_unknown_search_provider_is_a_row_not_the_end(self):
        env = dict(os.environ, KIT_SEARCH_PROVIDER="qdm")
        r = run("doctor", "--vault", str(VAULT), "--base-url", "http://127.0.0.1:9/v1", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("unknown search provider", r.stdout)
        self.assertIn("llm endpoint", r.stdout, "the rest of the diagnosis must still print")

    def test_a_proxy_variable_is_reported(self):
        env = dict(os.environ, HTTP_PROXY="http://127.0.0.1:1")
        r = run("doctor", "--vault", str(VAULT), "--base-url", "http://127.0.0.1:9/v1", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("proxy", r.stdout)
        self.assertIn("HTTP_PROXY", r.stdout)


class NoModelLoaded(BaseHTTPRequestHandler):
    """LM Studio right after the server is switched on: /models answers, completions do not."""

    def _send(self, code: int, body: dict):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        self._send(200, {"data": []})

    def do_POST(self):
        self._send(400, {"error": "No model loaded. Please load a model first."})

    def log_message(self, *args):
        pass


class LlmSmokeDiagnoses(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), NoModelLoaded)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_port}/v1"

    def test_no_model_loaded_is_a_sentence_not_a_traceback(self):
        r = run("llm", "smoke", "--base-url", self.base)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("no model loaded", r.stdout)
        self.assertIn("No model loaded. Please load a model first.", r.stdout,
                      "the server's own diagnosis is the useful part")


class ConfidentialStaysOutOfThePrompt(unittest.TestCase):
    """People notes ship as `sensitivity: confidential`; the bridge hides them and so must `llm ask`."""

    def test_the_body_of_a_person_note_is_not_sent(self):
        # naive, explicitly: with `auto` the hits depend on whatever the machine's qmd index holds
        r = run("llm", "ask", "what does Alex Example need from me", "--vault", str(VAULT),
                "--search-provider", "naive", "--graph", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("05-people/alex-example.md", r.stdout, "a confidential hit is dropped whole, path and title too (#52)")
        self.assertNotIn("Delegated authority", r.stdout, "note body leaked into the prompt")
        self.assertNotIn("Rolling 1:1 agenda", r.stdout)
        self.assertIn("confidential note(s) were left out", r.stdout, "say when something was withheld")

    def test_a_qmd_hit_id_names_the_same_note(self):
        """The qmd lane reaches the check through `hit_rel`; a collection-prefixed id would skip it."""
        sys.path.insert(0, str(ROOT))
        import kit
        import kitproviders
        self.assertTrue(kit._confidential(VAULT, kitproviders.hit_rel("qmd://vault/05-people/alex-example.md")))
        self.assertFalse(kit._confidential(VAULT, kitproviders.hit_rel("qmd://vault/00-home/priorities.md")))


class TodosOwnerFilter(unittest.TestCase):
    def test_the_owner_filter_reaches_the_text_output(self):
        v = temp_vault()
        try:
            everything = run("todos", "--vault", str(v))
            self.assertEqual(everything.returncode, 0, everything.stderr)
            filtered = run("todos", "--vault", str(v), "--owner", "nobody-at-all")
            self.assertEqual(filtered.returncode, 0, filtered.stderr)
            self.assertNotEqual(everything.stdout, filtered.stdout,
                                "--owner used to change nothing without --json")
            self.assertIn("0 open", filtered.stdout)
            self.assertIn("for owner nobody-at-all", filtered.stdout)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class Thinks(BaseHTTPRequestHandler):
    """The kit's documented default class of model: it reasons, then answers, out of one budget.

    `content` is the answer, `reasoning_content` the chain of thought. Below 200 tokens the budget
    is gone before the answer starts and `finish_reason` is `length` with nothing in `content`.
    """

    THINKING = "The user wants the literal token. I should not explain myself."

    def _send(self, code: int, body: dict):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        self._send(200, {"data": [{"id": "thinker"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        enough = body.get("max_tokens", 0) >= 200
        self._send(200, {"choices": [{"finish_reason": "stop" if enough else "length",
                                      "message": {"role": "assistant",
                                                  "content": "kit ok!" if enough else "",
                                                  "reasoning_content": self.THINKING}}]})

    def log_message(self, *args):
        pass


class StarvedThinker(Thinks):
    """Whatever the budget, this one only ever thinks — the failure the hint has to name."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._send(200, {"choices": [{"finish_reason": "length",
                                      "message": {"role": "assistant", "content": "",
                                                  "reasoning_content": self.THINKING}}]})


class ReasoningModelsPassTheSmokeTest(unittest.TestCase):
    def serve(self, handler) -> str:
        srv = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}/v1"

    def test_smoke_passes_on_the_answer_not_its_punctuation(self):
        r = run("llm", "smoke", "--base-url", self.serve(Thinks))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("kit ok!", r.stdout)
        self.assertNotIn(Thinks.THINKING, r.stdout, "chain of thought is not an answer")

    def test_a_budget_spent_thinking_is_one_diagnosed_line(self):
        r = run("llm", "smoke", "--base-url", self.serve(StarvedThinker))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("budget thinking", r.stdout)
        self.assertIn("thinking toggle", r.stdout)
        self.assertRegex(r.stdout, r"whole \d+-token budget", "the hint must name the budget it used")
        self.assertNotIn(Thinks.THINKING, r.stdout)

    def test_ask_says_why_it_has_no_answer_instead_of_printing_an_empty_one(self):
        r = run("llm", "ask", "why did we choose qmd", "--vault", str(VAULT),
                "--base-url", self.serve(StarvedThinker))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("budget thinking", r.stdout)
        self.assertIn("Sources found:", r.stdout, "the retrieval half worked and must still be shown")
        self.assertNotIn(Thinks.THINKING, r.stdout)

class Answers(Thinks):
    """A model that answers: grounded prose for `llm ask`, JSON (or not) for `minutes --llm`.

    Records every request body, so a test can read the prompt the kit actually sent.
    """

    seen: list = []
    minutes_reply = ""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        type(self).seen.append(body)
        system = body["messages"][0]["content"]
        content = self.minutes_reply if "structured minutes" in system else "We chose qmd because it searches on-device [1]."
        self._send(200, {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]})


class LlmHappyPaths(unittest.TestCase):
    """What a working endpoint gives back: the answer and its sources, the model's structure in
    the minutes — and only what the policy allows in the prompt."""

    def setUp(self):
        Answers.seen = []
        srv = HTTPServer(("127.0.0.1", 0), Answers)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.base = f"http://127.0.0.1:{srv.server_port}/v1"
        self.env = dict(os.environ, KIT_SEARCH_PROVIDER="naive")

    def prompt(self) -> str:
        return "\n".join(m["content"] for m in Answers.seen[-1]["messages"])

    def test_ask_prints_the_answer_and_its_sources(self):
        r = run("llm", "ask", "why did we choose qmd", "--vault", str(VAULT), "--base-url", self.base, env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("We chose qmd because it searches on-device [1].", r.stdout)
        sources = re.findall(r"^  \[\d+\] (\S+)$", r.stdout.split("Sources:", 1)[1], re.M)
        self.assertTrue(sources, r.stdout)
        self.assertIn(sources[0], self.prompt(), "the top source is what the model was given")

    def test_ask_with_graph_keeps_a_confidential_body_out_of_the_prompt(self):
        r = run("llm", "ask", "who leads the search relaunch and what may they decide", "--graph",
                "--vault", str(VAULT), "--base-url", self.base, env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Delegated authority", self.prompt(), "05-people/alex-example.md is confidential")

    def minutes(self, reply: str):
        Answers.minutes_reply = reply
        v = temp_vault()
        self.addCleanup(shutil.rmtree, v.parent, True)
        recap = v.parent / "recap.txt"
        recap.write_text("We talked through the pilot and agreed to ship on Friday.", encoding="utf-8")
        r = run("minutes", str(recap), "--title", "Pilot sync", "--date", "2026-09-22", "--vault", str(v),
                "--llm", "--base-url", self.base, env=self.env)
        return r, v / "02-meetings/2026-09-22-pilot-sync.md"

    def test_minutes_llm_adds_the_models_actions_and_decisions(self):
        r, note = self.minutes(json.dumps({"outcomes": ["pilot is on track"], "decisions": ["ship on Friday"],
                                           "actions": [{"text": "Write the release notes", "owner": "Sam", "due": "2026-09-25"}]}))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = note.read_text(encoding="utf-8")
        self.assertIn("- [ ] Write the release notes — Sam, due 2026-09-25", text)
        self.assertIn("ship on Friday", text)

    def test_minutes_llm_files_the_recap_when_the_model_answers_prose(self):
        r, note = self.minutes("Sure! Here are the minutes: the pilot ships Friday.")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("LLM structuring skipped", r.stdout)
        self.assertTrue(note.exists(), "the recap is filed without the model's structure")
        self.assertNotIn("Traceback", r.stderr)


class KitCliRootInbox(unittest.TestCase):
    """Quick capture: `init` lays down a raw root `Inbox.md` that the vault stays valid with."""

    def test_init_produces_a_raw_root_inbox_outside_the_index(self):
        tmp = Path(tempfile.mkdtemp(prefix="okf-inbox-"))
        try:
            target = tmp / "my-vault"
            r = run("init", "--target", str(target), "--no-qmd")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            inbox = target / "Inbox.md"
            self.assertTrue(inbox.exists(), "init must produce a root Inbox.md")
            self.assertFalse(inbox.read_text(encoding="utf-8").startswith("---"), "the inbox is raw: no frontmatter")
            self.assertNotIn("Inbox.md", (target / "index.md").read_text(encoding="utf-8"))
            r = run("validate", "--vault", str(target), "--strict")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_both_qmd_sites_keep_private_folders_out(self):
        import kit
        self.assertIn("!(_*)", kit.QMD_MASK, "the registered mask must exclude `_`-prefixed folders")
        self.assertIn("_*/**", (ROOT / "config/qmd/index.example.yml").read_text(encoding="utf-8"))
