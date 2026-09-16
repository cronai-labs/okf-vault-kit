"""End-to-end tests of the kit.py CLI (subprocess, real files)."""
import contextlib
import getpass
import http.server
import io
import json
import os
import shutil
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
            for p in kitlib.iter_markdown(target):
                self.assertNotIn("human:me", p.read_text(encoding="utf-8"), p)
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
        r = run("mcp-config", "--client", "lmstudio", "--vault", str(VAULT), env=dict(os.environ, KIT_NO_UV="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(r.stdout.split("\n# would be written")[0])
        qmd = cfg["mcpServers"]["qmd"]
        self.assertEqual(qmd["args"], ["mcp"])
        # resolved path when qmd is installed: the MCP client spawns this, and on Windows a
        # bare name does not find a .cmd shim
        self.assertEqual(Path(qmd["command"]).stem, "qmd")
        srv = cfg["mcpServers"]["obsidian-vault"]
        self.assertTrue(any(a.endswith("obsidian_bridge.py") for a in srv["args"]))
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
            srv = json.loads(r.stdout.split("\n# would be written")[0])["mcpServers"]["obsidian-vault"]
            self.assertTrue(srv["command"].startswith(str(tmp)), srv)
            self.assertEqual(srv["args"][0], "run")
            self.assertTrue(srv["args"][1].endswith("obsidian_bridge.py"))
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
            r = run("mcp-config", "--client", "lmstudio", "--vault", str(v), "--write", "--qmd-http", env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertIn("other", data["mcpServers"])
            self.assertEqual(data["mcpServers"]["qmd"], {"url": "http://localhost:8181/mcp"})
            self.assertTrue(cfg_path.with_suffix(".json.bak").exists())
            self.assertIn(f"vault served: {v.resolve()}", r.stdout, "the served vault must be visible on the write lane")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(v.parent, ignore_errors=True)

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
            r = run("validate", "--vault", str(v))
            self.assertEqual(r.returncode, 1); self.assertIn("enum_violation", r.stdout)
            r = run("validate", "--vault", str(v), "--no-ontology")
            self.assertEqual(r.returncode, 0, "ontology checks can be switched off")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_graph_commands(self):
        v = temp_vault()
        try:
            r = run("graph", "build", "--vault", str(v)); self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("0 finding(s)", r.stdout)
            self.assertTrue((v / ".kit/graph.sqlite").exists())
            r = run("graph", "query", "select count(*) n from nodes", "--vault", str(v), "--json"); self.assertEqual(json.loads(r.stdout)[0]["n"], 26)
            r = run("graph", "query", "drop table nodes", "--vault", str(v)); self.assertEqual(r.returncode, 1)
            r = run("graph", "export", "--format", "nt", "--vault", str(v)); self.assertIn("<http://purl.org/dc/terms/title>", r.stdout)
            r = run("graph", "neighbors", "alex-example", "--pred", "owns", "--vault", str(v)); self.assertIn("—owns→", r.stdout)
            r = run("graph", "path", "hybrid-search", "alex-example", "--vault", str(v)); self.assertEqual(r.returncode, 0)
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

    def test_llm_ask_graph_dry_run(self):
        r = run("llm", "ask", "who owns the search relaunch", "--vault", str(VAULT), "--graph", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Facts from the vault graph", r.stdout); self.assertIn("- owner: Alex Example", r.stdout)


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
            r = run("doctor", "--vault", str(v), "--report", str(out))
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
        self.assertIn("05-people/alex-example.md", r.stdout, "it may still be cited")
        self.assertNotIn("Delegated authority", r.stdout, "note body leaked into the prompt")
        self.assertNotIn("Rolling 1:1 agenda", r.stdout)
        self.assertIn("sensitivity: confidential", r.stdout, "say when something was withheld")

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
