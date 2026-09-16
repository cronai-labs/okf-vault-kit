"""Access control: policy layer (read-only, deny/allow, confidential hiding, limits, propose + audit), proposals CLI,
bearer-auth middleware, container files."""
import asyncio
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import ROOT, VAULT, kitlib, temp_vault

sys.path.insert(0, str(ROOT / "mcp"))
import bridge_policy as bp
import obsidian_bridge as ob

KIT = [sys.executable, str(ROOT / "kit.py")]


def guarded(vault: Path, **kw) -> bp.Guarded:
    pol = bp.Policy(audit_path=vault / ".kit" / "bridge-audit.jsonl", **kw)
    return bp.Guarded(ob.FsBackend(vault), pol, vault)


class PolicyLayer(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()

    def tearDown(self):
        shutil.rmtree(self.vault.parent, ignore_errors=True)

    def test_defaults_deny_people_and_system_writes(self):
        g = guarded(self.vault)
        with self.assertRaises(bp.PolicyError):
            g.append_note("05-people/alex-example.md", "- [ ] sneaky")
        with self.assertRaises(bp.PolicyError):
            g.set_property("99-system/conventions.md", "type", "x")
        with self.assertRaises(bp.PolicyError):
            g.create_note("index.md", "hijack")
        self.assertEqual(g.append_note("03-projects/search-relaunch.md", "- 2026-09-14 — ok"), "03-projects/search-relaunch.md")

    def test_confidential_hidden_by_default(self):
        g = guarded(self.vault)
        with self.assertRaises(bp.PolicyError):
            g.read_note("alex-example")
        self.assertFalse(any("alex-example" in f for f in g.list_files("05-people")))
        self.assertFalse(any("alex-example" in h["file"] for h in g.search("Alex Example", 20)))
        self.assertFalse(any("05-people/alex" in t["file"] for t in g.todos()["tasks"]))
        with self.assertRaises(bp.PolicyError):
            g.graph_context("alex-example")
        self.assertIn("Alex Example", g.read_note("03-projects/search-relaunch.md"), "internal notes stay readable")
        shown = guarded(self.vault, hide_confidential=False)
        self.assertIn("type: person", shown.read_note("alex-example"))

    def test_confidential_hiding_survives_a_non_canonical_vault_path(self):
        """The bypass that made the test above red on macOS but green on Linux.

        FsBackend resolves its vault path; Guarded used to keep whatever it was handed. When the
        two disagreed, relative_to() raised, a blanket `except: pass` swallowed it, and the
        confidential check ran against a path that does not exist — which reads as "not
        confidential". macOS /tmp is a symlink so it showed up there; on Linux it did not.
        """
        link = self.vault.parent / "vault-via-symlink"
        try:
            link.symlink_to(self.vault, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available (Windows without developer mode)")
        g = guarded(link)
        with self.assertRaises(bp.PolicyError):
            g.read_note("alex-example")
        self.assertFalse(any("alex-example" in f for f in g.list_files("05-people")))

    def test_unresolvable_reference_fails_closed(self):
        """If we cannot say what a reference points at, it must not default to 'safe to read'."""
        g = guarded(self.vault)
        self.assertTrue(g._confidential(bp._UNRESOLVED))

    def test_read_only_and_allow_list(self):
        ro = guarded(self.vault, read_only=True)
        with self.assertRaises(bp.PolicyError):
            ro.daily_append("- x")
        self.assertTrue(ro.search("qmd", 3))
        narrow = guarded(self.vault, allow_write=("02-meetings",))
        with self.assertRaises(bp.PolicyError):
            narrow.append_note("03-projects/search-relaunch.md", "x")
        self.assertTrue(narrow.create_note("02-meetings/2026-09-30-x", "# x").startswith("02-meetings/"))

    def test_size_and_rate_limits(self):
        g = guarded(self.vault, max_write_bytes=50, max_writes_per_minute=2)
        with self.assertRaises(bp.PolicyError):
            g.append_note("03-projects/search-relaunch.md", "x" * 51)
        g.append_note("03-projects/search-relaunch.md", "- one"); g.append_note("03-projects/search-relaunch.md", "- two")
        with self.assertRaises(bp.PolicyError):
            g.append_note("03-projects/search-relaunch.md", "- three")

    def test_propose_mode_and_audit(self):
        g = guarded(self.vault, propose=True)
        before = (self.vault / "03-projects/search-relaunch.md").read_text(encoding="utf-8")
        out = g.append_note("03-projects/search-relaunch.md", "- proposed line")
        self.assertTrue(out.startswith("proposed:"))
        self.assertEqual((self.vault / "03-projects/search-relaunch.md").read_text(encoding="utf-8"), before, "nothing written")
        pending = g.proposals.pending(); self.assertEqual(len(pending), 1); self.assertEqual(pending[0]["tool"], "append_note")
        audit = [json.loads(l) for l in (self.vault / ".kit/bridge-audit.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(audit[-1]["tool"], "append_note"); self.assertTrue(audit[-1]["ok"])
        with self.assertRaises(bp.PolicyError):
            g.append_note("05-people/alex-example.md", "x")
        audit = [json.loads(l) for l in (self.vault / ".kit/bridge-audit.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertFalse(audit[-1]["ok"]); self.assertIn("denied", audit[-1]["detail"])

    def test_bridge_flags_selftest(self):
        r = subprocess.run([sys.executable, str(ROOT / "mcp/obsidian_bridge.py"), "--backend", "fs", "--vault", str(self.vault),
                            "--read-only", "--propose", "--allow-write", "02-meetings", "--no-audit", "--selftest"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("policy: read-only; propose; confidential hidden", r.stdout)

    def test_cli_backend_without_a_vault_refuses_to_start(self):
        """`confidential hidden` in the banner has to mean the check can run."""
        r = subprocess.run([sys.executable, str(ROOT / "mcp/obsidian_bridge.py"), "--backend", "cli", "--vault-name", "v", "--selftest"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("confidential hiding needs the vault", r.stderr)

    # -------------------------------------------------------- write-path bypasses
    def test_deny_list_sees_the_path_the_write_lands_on(self):
        """Every spelling of a denied note: short name, `..`, and a different case."""
        g = guarded(self.vault)
        before = (self.vault / "05-people/alex-example.md").read_text(encoding="utf-8")
        for name in ("alex-example", "03-projects/../05-people/alex-example", "05-People/alex-example", "05-PEOPLE/injected"):
            with self.assertRaises(bp.PolicyError, msg=name):
                g.create_note(name, "OVERWRITTEN BY MODEL", overwrite=True)
        with self.assertRaises(bp.PolicyError):
            g.append_note("05-People/alex-example", "- sneaky")
        with self.assertRaises(bp.PolicyError):
            g.set_property("05-People/alex-example", "state", "done")
        with self.assertRaises(bp.PolicyError):
            g.append_note("Log", "* **Creation**: forged entry")
        self.assertEqual((self.vault / "05-people/alex-example.md").read_text(encoding="utf-8"), before)
        narrow = guarded(self.vault, allow_write=("00-home",))
        with self.assertRaises(bp.PolicyError):
            narrow.create_note("00-home/../05-people/injected", "x")

    def test_confidential_notes_are_write_protected(self):
        g = guarded(self.vault)
        for prop, value in (("sensitivity", "internal"), ("state", "done")):
            with self.assertRaises(bp.PolicyError):
                g.set_property("05-people/alex-example.md", prop, value)
        with self.assertRaises(bp.PolicyError):
            g.create_note("05-people/alex-example", "DESTROYED", overwrite=True)
        self.assertIn("sensitivity: confidential", (self.vault / "05-people/alex-example.md").read_text(encoding="utf-8"))
        shown = guarded(self.vault, hide_confidential=False, deny_write=())
        self.assertTrue(shown.set_property("05-people/alex-example.md", "state", "open"))

    def test_template_cannot_escape_the_templates_folder(self):
        g = guarded(self.vault)
        outside = self.vault.parent / "outside-secret.md"
        outside.write_text("OUTSIDE-VAULT-SECRET\n", encoding="utf-8", newline="\n")
        for tpl in ("../05-people/alex-example.md", str(outside), "../../etc/hosts.md"):
            with self.assertRaises((bp.PolicyError, ob.BridgeError), msg=tpl):
                g.create_note("03-projects/exfil", "", template=tpl)
        self.assertFalse((self.vault / "03-projects/exfil.md").exists())
        self.assertTrue(g.create_note("02-meetings/2026-09-30-x", "", template="meeting").endswith(".md"))

    def test_a_write_without_an_audit_line_does_not_happen(self):
        if sys.platform.startswith("win") or getattr(os, "geteuid", lambda: 1)() == 0:
            self.skipTest("needs a directory mode the process actually honours")
        g = guarded(self.vault)
        g.policy.audit_path = self.vault.parent / "locked" / "sub" / "audit.jsonl"
        g.policy.audit_path.parent.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(g.policy.audit_path.parent.parent, stat.S_IRUSR | stat.S_IXUSR)
        try:
            with self.assertRaises(bp.PolicyError):
                g.create_note("03-projects/audit-gap", "x")
            self.assertFalse((self.vault / "03-projects/audit-gap.md").exists(), "written with no audit record")
        finally:
            os.chmod(g.policy.audit_path.parent.parent, stat.S_IRWXU)

    def test_minutes_skips_a_denied_daily_note_and_keeps_the_log_entry_on_one_line(self):
        g = guarded(self.vault, allow_write=("02-meetings",))
        res = g.file_minutes("Decision: ship it", "Injected\nforged: line", "2026-09-22")
        self.assertTrue(res["skipped"], "the denied daily note must be reported, not written")
        self.assertFalse((self.vault / "01-journal/daily/2026/09/2026-09-22.md").exists())
        log = (self.vault / "log.md").read_text(encoding="utf-8")
        self.assertIn("Injected forged: line", log, "the title is collapsed to one line")
        self.assertNotIn("\nforged: line", log, "a newline in the title would forge a log entry")

    # -------------------------------------------------------- read-path bypasses
    def test_graph_tools_hide_confidential_notes(self):
        """What read_note refuses, graph_query used to hand over in full: title, description, frontmatter."""
        (self.vault / "03-projects/nda-secret.md").write_text(
            "---\ntype: project\ntitle: Codename Falcon\ndescription: SECRET-DESC merger\nsensitivity: confidential\n"
            "state: active\nowner: \"[Alex Example](../05-people/alex-example.md)\"\n---\n# Falcon\nSECRET-BODY\n",
            encoding="utf-8", newline="\n")
        g = guarded(self.vault)
        ids = [r[0] for r in g.graph_query("select id from nodes")["rows"]]
        self.assertNotIn("03-projects/nda-secret.md", ids)
        self.assertNotIn("05-people/alex-example.md", ids, "people notes are confidential by default")
        self.assertIn("03-projects/search-relaunch.md", ids, "the graph still answers about visible notes")
        blob = json.dumps(g.graph_query("select id, title, description, props from nodes"))
        self.assertNotIn("SECRET-DESC", blob); self.assertNotIn("Codename Falcon", blob)
        self.assertNotIn("SECRET-DESC", json.dumps(g.graph_query("select node, key, value from props")))
        edges = json.dumps(g.graph_query("select src, pred, dst from edges"))
        self.assertNotIn("nda-secret", edges); self.assertNotIn("alex-example", edges)
        with self.assertRaises(bp.PolicyError):
            g.graph_neighbors("03-projects/nda-secret.md")
        self.assertEqual(g.graph_path("07-knowledge/hybrid-search.md", "05-people/alex-example.md"), [])
        self.assertNotIn("Alex Example", g.graph_context("03-projects/search-relaunch"), "a hidden neighbour must not be labelled")
        self.assertTrue(g.graph_neighbors("03-projects/search-relaunch.md"))
        shown = guarded(self.vault, hide_confidential=False)
        self.assertIn("Codename Falcon", json.dumps(shown.graph_query("select title from nodes")))

    def test_graph_query_is_read_only(self):
        g = guarded(self.vault)
        before = g.graph_query("select count(*) as n from nodes")["rows"][0][0]
        for sql in ("with x as (select 1) delete from nodes", "with x as (select 1) update nodes set title='x'"):
            with self.assertRaises((ValueError, sqlite3.Error), msg=sql):
                g.graph_query(sql)
        self.assertEqual(g.graph_query("select count(*) as n from nodes")["rows"][0][0], before)

    def test_a_bom_does_not_declassify_a_note(self):
        """PowerShell 5.1 and legacy Notepad write one; parse_note then sees no frontmatter."""
        p = self.vault / "07-knowledge/bom-secret.md"
        p.write_bytes("\ufeff---\ntype: concept\ntitle: BOM Secret\nsensitivity: confidential\n---\n# BOM\nBOM-BODY\n".encode("utf-8"))
        g = guarded(self.vault)
        with self.assertRaises(bp.PolicyError):
            g.read_note("07-knowledge/bom-secret.md")
        self.assertFalse(any("bom-secret" in f for f in g.list_files("07-knowledge")))
        self.assertFalse(any("bom-secret" in h["file"] for h in g.search("BOM-BODY", 20)))

    def test_an_undecodable_note_is_invisible_and_harmless(self):
        (self.vault / "07-knowledge/utf16.md").write_bytes("---\ntype: concept\n---\nUTF16\n".encode("utf-16"))
        g = guarded(self.vault)
        self.assertTrue(g.search("hybrid search", 5), "one bad file must not take out search")
        self.assertTrue(g.backlinks("hybrid-search.md"))
        self.assertFalse(any("utf16" in f for f in g.list_files()))
        with self.assertRaises(bp.PolicyError):
            g.read_note("07-knowledge/utf16.md")

    def test_show_confidential_reports_an_undecodable_note_instead_of_raising(self):
        """Hiding is what caught this file by default; with hiding off nothing else did, and the
        tool answered with a UnicodeDecodeError traceback."""
        (self.vault / "07-knowledge/utf16.md").write_bytes("---\ntype: concept\n---\nUTF16\n".encode("utf-16"))
        g = guarded(self.vault, hide_confidential=False)
        with self.assertRaises(ob.BridgeError) as caught:
            g.read_note("07-knowledge/utf16.md")
        self.assertIn("not valid UTF-8", str(caught.exception))

    def test_cli_backend_resolves_short_names_before_hiding(self):
        """The CLI resolves short names against its index; the policy layer has to resolve them too."""
        class StubCli(ob.CliBackend):
            def _run(self, command, params=None, flags=None):
                return "CLI-SERVED-CONTENT"

        g = bp.Guarded(StubCli(vault_name="v", binary="none", vault_path=str(self.vault)), bp.Policy(), self.vault)
        with self.assertRaises(bp.PolicyError):
            g.read_note("alex-example")
        with self.assertRaises(bp.PolicyError):
            g.read_note("05-people/alex-example.md")
        self.assertEqual(g.read_note("07-knowledge/hybrid-search"), "CLI-SERVED-CONTENT")


class ProposalsCli(unittest.TestCase):
    def test_list_apply_reject(self):
        v = temp_vault()
        try:
            g = guarded(v, propose=True)
            pid1 = g.append_note("03-projects/search-relaunch.md", "- applied via proposal").split(":")[1].split(" ")[0]
            pid2 = g.daily_append("- rejected via proposal").split(":")[1].split(" ")[0]
            r = subprocess.run(KIT + ["proposals", "list", "--vault", str(v)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            self.assertIn(pid1, r.stdout); self.assertIn(pid2, r.stdout)
            r = subprocess.run(KIT + ["proposals", "apply", pid1, "--vault", str(v)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("- applied via proposal", (v / "03-projects/search-relaunch.md").read_text(encoding="utf-8"))
            self.assertIn("Applied proposal", (v / "log.md").read_text(encoding="utf-8"))
            r = subprocess.run(KIT + ["proposals", "reject", pid2, "--vault", str(v), "--reason", "no"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            self.assertEqual(r.returncode, 0)
            r = subprocess.run(KIT + ["proposals", "list", "--vault", str(v)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            self.assertIn("no pending proposals", r.stdout)
            self.assertTrue(list((v / ".kit/proposals").glob("*.applied.json")) and list((v / ".kit/proposals").glob("*.rejected.json")))
            self.assertEqual(kitlib.validate_okf(v).errors, [])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_apply_re_checks_the_policy(self):
        """A proposal is a request, not a licence: the deny list decides again at apply time."""
        v = temp_vault()
        try:
            store = bp.ProposalStore(v / ".kit" / "proposals")
            pid = store.add("create_note", {"name": "alex-example", "content": "STEALTH OVERWRITE", "overwrite": True})
            with self.assertRaises(bp.PolicyError):
                store.apply(pid, ob.FsBackend(v))
            self.assertNotIn("STEALTH", (v / "05-people/alex-example.md").read_text(encoding="utf-8"))
            self.assertTrue((v / ".kit/proposals" / f"{pid}.json").exists(), "a refused proposal stays pending")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_proposals_record_the_resolved_target(self):
        """The human approves what the listing shows, so it must show the file that gets written."""
        v = temp_vault()
        try:
            g = guarded(v, propose=True)
            g.create_note("2026-09-30-x", "# x")
            self.assertEqual(g.proposals.pending()[0]["args"]["name"], "2026-09-30-x.md")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class BearerAuth(unittest.TestCase):
    def test_token_check(self):
        self.assertTrue(bp.token_ok("Bearer abc", "abc")); self.assertTrue(bp.token_ok(b"Bearer abc", "abc"))
        self.assertFalse(bp.token_ok("Bearer abd", "abc")); self.assertFalse(bp.token_ok("abc", "abc")); self.assertFalse(bp.token_ok(None, "abc")); self.assertFalse(bp.token_ok("Bearer ", ""))

    def test_middleware(self):
        calls = []

        async def app(scope, receive, send):
            calls.append("app"); await send({"type": "http.response.start", "status": 200, "headers": []}); await send({"type": "http.response.body", "body": b"ok"})

        mw = bp.BearerAuth(app, "s3cret")

        async def run(headers):
            sent = []
            async def send(msg): sent.append(msg)
            await mw({"type": "http", "headers": headers}, None, send)
            return sent
        sent = asyncio.run(run([]))
        self.assertEqual(sent[0]["status"], 401); self.assertEqual(calls, [])
        sent = asyncio.run(run([(b"authorization", b"Bearer s3cret")]))
        self.assertEqual(sent[0]["status"], 200); self.assertEqual(calls, ["app"])
        asyncio.run(run([(b"Authorization", b"Bearer wrong")]))
        self.assertEqual(calls, ["app"], "wrong token never reaches the app")

    def test_mcp_config_with_token(self):
        r = subprocess.run(KIT + ["mcp-config", "--vault", str(VAULT), "--bridge-http", "--bridge-token", "tok"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        cfg = json.loads(r.stdout.split("\n# would be written")[0])
        self.assertEqual(cfg["mcpServers"]["obsidian-vault"]["headers"], {"Authorization": "Bearer tok"})
        r = subprocess.run(KIT + ["mcp-config", "--vault", str(VAULT), "--bridge-flags", "--read-only --propose"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(__import__("os").environ, KIT_NO_UV="1"), check=False)
        self.assertIn("--read-only", json.loads(r.stdout.split("\n# would be written")[0])["mcpServers"]["obsidian-vault"]["args"])
