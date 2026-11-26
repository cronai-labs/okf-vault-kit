"""The files the kit writes carry no field of a confidential note (#52).

The bridge hides a note one file at a time, from that file's own frontmatter. index.md, the hub
tables, the todo digest, log.md and the daily note are ordinary files it serves whole, so anything
a writer copies into them from a confidential note reaches the model. Each writer is checked
against the sample vault's confidential people notes, plus planted confidential projects and
decisions (the sample has none), and against a note whose frontmatter does not parse.
"""
import datetime as dt
import re
import shutil
import subprocess
import sys
import unittest

import kitrecon
from tests.helpers import ROOT, VAULT, kitlib, temp_vault

sys.path.insert(0, str(ROOT / "mcp"))
import bridge_policy as bp
import vault_bridge as ob

KIT = [sys.executable, str(ROOT / "kit.py")]

# Every field of the two sample people notes that a writer could copy.
ALEX = ("Alex Example", "alex-example", "tech lead for the search relaunch", "Send Alex the go/no-go template")
SAM = ("Sam Example", "sam-example", "owns the embedding-model evaluation", "Share the eval-set results")

SECRET_PROJECT = """---
type: project
title: Codename Kestrel
description: Kestrel acquisition groundwork, board only.
state: active
health: green
owner: me
sensitivity: confidential
---
# Codename Kestrel
"""
SECRET_DECISION = """---
type: decision
title: Kestrel go decision
description: Whether to proceed with Kestrel.
date: 2026-09-20
state: open
project: "[Search relaunch](../03-projects/search-relaunch.md)"
sensitivity: confidential
---
# Kestrel go decision
"""
# `title: a: b` is a YAML error: the note cannot say whether it is confidential, so it counts as such.
BROKEN = "---\ntype: person\ntitle: Brokenfront Person: oops\ndescription: Brokenfront description\n---\n# x\n\n- [ ] Brokenfront task — me, due 2026-09-15\n"


def _assert_none_of(case: unittest.TestCase, text: str, *fields_: str) -> None:
    for f in fields_:
        case.assertNotIn(f.lower(), text.lower(), f"{f!r} leaked")


class _TempVault(unittest.TestCase):
    def setUp(self):
        self.v = temp_vault()

    def tearDown(self):
        shutil.rmtree(self.v.parent, ignore_errors=True)

    def write(self, rel: str, text: str) -> None:
        p = self.v / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


class Index(_TempVault):
    COUNT = re.compile(r"^(\d+) note\(s\) marked `sensitivity: confidential` are not listed\.$", re.M)

    def _expected_hidden(self) -> int:
        return sum(1 for p in kitlib.iter_markdown(self.v)
                   if p.name not in kitlib.RESERVED_FILENAMES and kitlib.is_confidential(self.v, p.relative_to(self.v).as_posix()))

    def test_confidential_people_are_left_out_and_counted(self):
        index = kitlib.build_index(self.v)
        _assert_none_of(self, index, *ALEX[:3], *SAM[:3])
        self.assertIn("05-people/team-platform-engineering.md", index, "an internal note in the same folder stays listed")
        m = self.COUNT.search(index)
        self.assertIsNotNone(m, index[:600])
        self.assertEqual(int(m.group(1)), self._expected_hidden())
        self.assertGreaterEqual(int(m.group(1)), 2)

    def test_the_shipped_index_lists_no_confidential_note(self):
        shipped = (VAULT / "index.md").read_text(encoding="utf-8")
        _assert_none_of(self, shipped, *ALEX[:3], *SAM[:3])
        self.assertRegex(shipped, self.COUNT)

    def test_no_count_line_when_nothing_is_confidential(self):
        for p in kitlib.iter_markdown(self.v):
            if kitlib.is_confidential(self.v, p.relative_to(self.v).as_posix()):
                p.unlink()
        index = kitlib.build_index(self.v)
        self.assertNotIn("sensitivity: confidential", index)

    def test_unparsable_frontmatter_fails_closed(self):
        before = int(self.COUNT.search(kitlib.build_index(self.v)).group(1))
        self.write("05-people/brokenfront.md", BROKEN)
        self.assertIsNotNone(kitlib.parse_note(self.v / "05-people/brokenfront.md", self.v).fm_error, "the fixture must be a YAML error")
        index = kitlib.build_index(self.v)
        _assert_none_of(self, index, "brokenfront")
        self.assertEqual(int(self.COUNT.search(index).group(1)), before + 1)


class Hubs(_TempVault):
    def setUp(self):
        super().setUp()
        self.write("03-projects/kestrel.md", SECRET_PROJECT)
        self.write("06-decisions/2026-09-20-kestrel-go.md", SECRET_DECISION)
        self.write("05-people/brokenfront.md", BROKEN)

    def _seed(self, hub: str, *rows: str) -> None:
        """Rows an earlier reconcile (or a person) wrote, appended to the hub's first table."""
        p = self.v / hub
        lines = p.read_text(encoding="utf-8").splitlines()
        end = max(i for i, line in enumerate(lines) if line.startswith("|")) + 1
        lines[end:end] = list(rows)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def test_project_hub_never_adds_and_removes_confidential_rows(self):
        self._seed(kitrecon.PROJECT_HUB,
                   "| [Codename Kestrel](kestrel.md) | green | active |  | Kestrel acquisition groundwork, board only. |",
                   "| [Alex Example](../05-people/alex-example.md) | green | active |  | Senior engineer, tech lead for the search relaunch |",
                   "| [Brokenfront Person](../05-people/brokenfront.md) | green | active |  | Brokenfront description |")
        hub = self.v / kitrecon.PROJECT_HUB
        seeded = hub.read_text(encoding="utf-8")

        dry = kitrecon.reconcile(self.v)
        removals = [c for c in dry if c.code == "hub_confidential_row" and c.file == kitrecon.PROJECT_HUB]
        self.assertEqual(len(removals), 3, [c.render() for c in dry])
        self.assertFalse(any(c.applied for c in dry), "dry run by default")
        self.assertEqual(hub.read_text(encoding="utf-8"), seeded, "a dry run changes nothing")
        self.assertFalse(any(c.code == "hub_missing_row" and "kestrel" in c.message for c in dry), "no row is asked for")
        _assert_none_of(self, "\n".join(c.render() for c in removals), "Kestrel", *ALEX[:3], "brokenfront")

        applied = kitrecon.reconcile(self.v, apply=True, today=dt.date(2026, 9, 14))
        self.assertTrue(all(c.applied for c in applied if c.code == "hub_confidential_row"))
        text = hub.read_text(encoding="utf-8")
        _assert_none_of(self, text, "Kestrel", *ALEX[:3], "brokenfront")
        self.assertIn("[Search relaunch](search-relaunch.md)", text, "the ordinary row stays, free text and all")
        self.assertIn("Hybrid search for the docs portal", text)
        log = (self.v / "log.md").read_text(encoding="utf-8")
        self.assertIn("row(s) for confidential notes removed", log)
        _assert_none_of(self, log, "Kestrel", *ALEX[:2])
        self.assertFalse(any(c.code == "hub_confidential_row" for c in kitrecon.reconcile(self.v)), "idempotent")

    def test_decision_log_never_adds_and_removes_confidential_rows(self):
        self._seed(kitrecon.DECISION_HUB,
                   "| 2026-09-20 | Kestrel go decision | open | — | [ADR](2026-09-20-kestrel-go.md) |")
        hub = self.v / kitrecon.DECISION_HUB
        seeded = hub.read_text(encoding="utf-8")
        dry = kitrecon.reconcile(self.v)
        self.assertEqual([c.file for c in dry if c.code == "hub_confidential_row"], [kitrecon.DECISION_HUB])
        self.assertEqual(hub.read_text(encoding="utf-8"), seeded)

        # The row is gone after --apply, and none comes back on the next run.
        kitrecon.reconcile(self.v, apply=True, today=dt.date(2026, 9, 14))
        text = hub.read_text(encoding="utf-8")
        _assert_none_of(self, text, "Kestrel", "kestrel-go")
        self.assertIn("[ADR](2026-09-08-adopt-qmd-for-local-search.md)", text, "the ordinary rows stay")
        self.assertEqual(kitrecon.reconcile(self.v, apply=True), [])
        self.assertEqual(hub.read_text(encoding="utf-8"), text)

    def test_priorities_do_not_ask_for_a_confidential_open_decision(self):
        codes = [c for c in kitrecon.reconcile(self.v) if c.code == "priorities_missing_open_decision"]
        _assert_none_of(self, "\n".join(c.render() for c in codes), "kestrel")


class Digest(_TempVault):
    COUNT = re.compile(r"^(\d+) task\(s\) in notes marked `sensitivity: confidential` are not listed", re.M)
    TODAY = dt.date(2026, 9, 14)

    def setUp(self):
        super().setUp()
        self.write("05-people/brokenfront.md", BROKEN)
        # The same commitment open in an ordinary note and done in a confidential one: a done/open
        # conflict that involves a confidential note, which the bridge drops whole.
        with (self.v / "03-projects/search-relaunch.md").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n- [ ] Circulate the vendor shortlist memo — me, due 2026-09-16\n")
        with (self.v / "05-people/alex-example.md").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n- [x] Circulate the vendor shortlist memo — me, due 2026-09-16\n")

    def _check(self, text: str) -> None:
        _assert_none_of(self, text, *ALEX[1:], *SAM[1:], "brokenfront")
        self.assertNotIn("done in one note, open in another", text)
        self.assertNotIn("## Same task in several notes\n\n-", text, "the only duplicate involves a confidential note")
        self.assertIn("Circulate the vendor shortlist memo", text, "the ordinary copy is still listed as a task")
        m = self.COUNT.search(text)
        self.assertIsNotNone(m, text[:800])
        self.assertEqual(int(m.group(1)), 4, "Alex 2, Sam 1, the unparsable note 1")

    def test_write_digest_leaves_out_confidential_tasks_and_conflicts(self):
        rep = kitrecon.todo_report(self.v, today=self.TODAY)
        self.assertTrue(rep.done_conflicts, "the fixture must produce a conflict")
        self._check(kitrecon.write_digest(self.v, rep).read_text(encoding="utf-8"))

    def test_cli_digest(self):
        r = subprocess.run([*KIT, "todos", "--vault", str(self.v), "--digest"], capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self._check((self.v / kitrecon.DIGEST).read_text(encoding="utf-8"))

    def test_the_report_itself_and_the_bridge_are_unchanged(self):
        """todo_report keeps every task: --sync-done, the CLI listing and the bridge work on the whole
        vault, and the bridge decides per policy, which --show-confidential turns off."""
        rep = kitrecon.todo_report(self.v, today=self.TODAY)
        self.assertTrue(any(t.file == "05-people/alex-example.md" for t in rep.tasks))

        def guarded(**kw) -> bp.Guarded:
            return bp.Guarded(ob.FsBackend(self.v), bp.Policy(audit_path=self.v / ".kit" / "audit.jsonl", **kw), self.v)

        hidden, shown = guarded().todos(), guarded(hide_confidential=False).todos()
        self.assertFalse(any(t["file"].startswith("05-people/") for t in hidden["tasks"]))
        self.assertEqual(hidden["conflicts"], [])
        self.assertTrue(any(t["file"] == "05-people/alex-example.md" for t in shown["tasks"]))
        self.assertEqual(len(shown["conflicts"]), 1)


class Minutes(_TempVault):
    RECAP = "We met to go through the Bluebird numbers in detail.\nAction: draft the Bluebird summary — me, due 2026-09-18\n"

    def test_a_confidential_minutes_note_is_logged_without_title_or_path(self):
        tpl = self.v / "90-templates/meeting.md"
        tpl.write_text(tpl.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: confidential"),
                       encoding="utf-8", newline="\n")
        day = dt.date(2026, 9, 14)
        m = kitrecon.file_minutes(self.v, self.RECAP, "Bluebird review", day, daily=True)
        self.assertTrue(kitlib.is_confidential(self.v, m.path), "the template made the note confidential")
        log = (self.v / "log.md").read_text(encoding="utf-8")
        self.assertIn("a meeting note marked confidential", log)
        _assert_none_of(self, log, "Bluebird")
        daily = kitlib.daily_note_path(self.v, day).read_text(encoding="utf-8")
        self.assertIn("Meeting filed: a note marked confidential", daily)
        _assert_none_of(self, daily, "Bluebird")

    def test_an_ordinary_minutes_note_is_still_logged_by_title(self):
        m = kitrecon.file_minutes(self.v, self.RECAP, "Bluebird review", dt.date(2026, 9, 14), daily=True)
        self.assertIn(f"[Bluebird review]({m.path})", (self.v / "log.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
