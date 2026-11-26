"""The files the kit writes carry no field of a confidential note (#52).

The bridge hides a note one file at a time, from that file's own frontmatter. index.md, the hub
tables, the todo digest, log.md and the daily note are ordinary files it serves whole, so anything
a writer copies into them from a confidential note reaches the model. Each writer is checked
against the sample vault's confidential people notes, plus planted confidential projects and
decisions (the sample has none), and against a note whose frontmatter does not parse.
"""
import datetime as dt
import os
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

HIDDEN_PROJECT = """---
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
HIDDEN_DECISION = """---
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


def _skip_unless_modes_bite(case: unittest.TestCase) -> None:
    """chmod 000 keeps nothing out on Windows, nor from root: the test would pass for nothing."""
    if sys.platform.startswith("win") or getattr(os, "geteuid", lambda: 1)() == 0:
        case.skipTest("needs a file mode the process actually honours")


def _lock(case: unittest.TestCase, path, restore: int) -> None:
    """chmod 000 `path` and put it back after the test, before tearDown's rmtree needs to get in."""
    os.chmod(path, 0)
    case.addCleanup(os.chmod, path, restore)


def _assert_none_of(case: unittest.TestCase, text: str, *fields_: str) -> None:
    for f in fields_:
        case.assertNotIn(f.lower(), text.lower(), f"{f!r} leaked")


class _TempVault(unittest.TestCase):
    def setUp(self):
        self.v = temp_vault()
        # A cleanup, not tearDown: cleanups run last-in first-out after tearDown, so _lock's
        # chmod back runs before this, and rmtree can get into a folder a test locked.
        self.addCleanup(shutil.rmtree, self.v.parent, ignore_errors=True)

    def write(self, rel: str, text: str) -> None:
        p = self.v / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


class Index(_TempVault):
    COUNT = re.compile(r"^(\d+) file\(s\) not listed: confidential, or unreadable\.$", re.M)

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

    def test_an_undecodable_file_is_counted(self):
        """The count says the index is incomplete; a file it skipped without counting made it lie."""
        before = int(self.COUNT.search(kitlib.build_index(self.v)).group(1))
        (self.v / "07-knowledge/utf16-note.md").write_bytes("---\ntitle: Widechar\n---\n# Widechar\n".encode("utf-16"))
        index = kitlib.build_index(self.v)
        _assert_none_of(self, index, "widechar", "utf16-note")
        self.assertEqual(int(self.COUNT.search(index).group(1)), before + 1)

    def test_an_unreadable_file_is_counted(self):
        _skip_unless_modes_bite(self)
        before = int(self.COUNT.search(kitlib.build_index(self.v)).group(1))
        _lock(self, self.v / "07-knowledge/hybrid-search.md", 0o644)
        index = kitlib.build_index(self.v)
        self.assertNotIn("07-knowledge/hybrid-search.md", index)
        self.assertEqual(int(self.COUNT.search(index).group(1)), before + 1)

    def test_a_template_marked_confidential_stays_out(self):
        """The bridge hides a confidential template like any note; the index must not list it either."""
        tpl = self.v / "90-templates/meeting.md"
        self.assertIn("90-templates/meeting.md", kitlib.build_index(self.v), "templates are listed as such")
        tpl.write_text(tpl.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: confidential"),
                       encoding="utf-8", newline="\n")
        self.assertNotIn("90-templates/meeting.md", kitlib.build_index(self.v))


class Hubs(_TempVault):
    def setUp(self):
        super().setUp()
        self.write("03-projects/kestrel.md", HIDDEN_PROJECT)
        self.write("06-decisions/2026-09-20-kestrel-go.md", HIDDEN_DECISION)
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
        self.assertEqual(len(removals), 2, [c.render() for c in dry])
        unverifiable = [c for c in dry if c.code == "hub_row_unverifiable" and c.file == kitrecon.PROJECT_HUB]
        self.assertEqual(len(unverifiable), 1, "the unparsable target is reported, not judged confidential")
        self.assertFalse(any(c.applied for c in dry), "dry run by default")
        self.assertEqual(hub.read_text(encoding="utf-8"), seeded, "a dry run changes nothing")
        self.assertFalse(any(c.code == "hub_missing_row" and "kestrel" in c.message for c in dry), "no row is asked for")
        _assert_none_of(self, "\n".join(c.render() for c in removals), "Kestrel", *ALEX[:3], "brokenfront")

        applied = kitrecon.reconcile(self.v, apply=True, today=dt.date(2026, 9, 14))
        self.assertTrue(all(c.applied for c in applied if c.code == "hub_confidential_row"))
        text = hub.read_text(encoding="utf-8")
        _assert_none_of(self, text, "Kestrel", *ALEX[:3])
        self.assertIn("| [Brokenfront Person](../05-people/brokenfront.md) | green | active |  | Brokenfront description |", text,
                      "a row the kit cannot judge is the user's to decide, and kept as written")
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


class UnverifiableRows(_TempVault):
    """--apply deletes a hub row only when its target says `sensitivity: confidential`.

    A row is hand-written: its free-text cells exist nowhere else, and a regenerated row does not
    bring them back. So "the kit could not read or parse the target" is reported and the row kept.
    Every read path (index, digest, graph, bridge) still hides such a note: hiding is undone by
    the next run, a deletion is not.
    """
    ROW = "| 2026-09-15 | Embedding model for German and English queries (example) | open | — | [ADR]({target}) |"
    NOTE = "06-decisions/2026-09-15-embedding-model-for-de-en.md"

    def _row_kept(self, target: str) -> None:
        hub = self.v / kitrecon.DECISION_HUB
        changes = kitrecon.reconcile(self.v, apply=True, today=dt.date(2026, 9, 14))
        found = [c for c in changes if c.code == "hub_row_unverifiable"]
        self.assertEqual([c.file for c in found], [kitrecon.DECISION_HUB], [c.render() for c in changes])
        self.assertFalse(found[0].applied)
        self.assertRegex(found[0].message, r"^line \d+ links to a note the kit cannot read or parse")
        _assert_none_of(self, found[0].message, "embedding", "ADR")
        self.assertFalse(any(c.code == "hub_confidential_row" for c in changes), [c.render() for c in changes])
        self.assertIn(self.ROW.format(target=target), hub.read_text(encoding="utf-8"), "the row, free text and all, is kept")

    def test_a_note_the_kit_cannot_read_keeps_its_row(self):
        _skip_unless_modes_bite(self)
        _lock(self, self.v / self.NOTE, 0o644)
        self._row_kept("2026-09-15-embedding-model-for-de-en.md")

    def test_a_note_whose_frontmatter_does_not_parse_keeps_its_row(self):
        note = self.v / self.NOTE
        note.write_text(note.read_text(encoding="utf-8").replace("type: decision", "type: decision\nbroken: a: b", 1),
                        encoding="utf-8", newline="\n")
        self.assertIsNotNone(kitlib.parse_note(note, self.v).fm_error, "the fixture must be a YAML error")
        self._row_kept("2026-09-15-embedding-model-for-de-en.md")

    def test_an_unreadable_folder_is_a_finding_not_a_traceback(self):
        """`Path.is_file` raises PermissionError on Python 3.11 for a file under a chmod-000 folder."""
        _skip_unless_modes_bite(self)
        sub = self.v / "06-decisions/sub"
        sub.mkdir()
        shutil.move(str(self.v / self.NOTE), str(sub))
        hub = self.v / kitrecon.DECISION_HUB
        target = "sub/2026-09-15-embedding-model-for-de-en.md"
        hub.write_text(hub.read_text(encoding="utf-8").replace("(2026-09-15-embedding-model-for-de-en.md)", f"({target})"),
                       encoding="utf-8", newline="\n")
        _lock(self, sub, 0o755)
        self.assertTrue(kitlib.is_confidential(self.v, "06-decisions/" + target), "fails closed for every reader")
        self._row_kept(target)

    def test_a_confidential_target_is_still_removed(self):
        note = self.v / self.NOTE
        note.write_text(note.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: confidential"),
                        encoding="utf-8", newline="\n")
        changes = kitrecon.reconcile(self.v, apply=True, today=dt.date(2026, 9, 14))
        self.assertEqual([c.code for c in changes if c.file == kitrecon.DECISION_HUB], ["hub_confidential_row"])
        self.assertNotIn("embedding-model-for-de-en", (self.v / kitrecon.DECISION_HUB).read_text(encoding="utf-8"))


class Predicate(_TempVault):
    def test_a_missing_reference_is_not_confidential(self):
        self.assertFalse(kitlib.is_confidential(self.v, "03-projects/no-such-note"))
        self.assertFalse(kitlib.is_confidential(self.v, "03-projects/search-relaunch.md/below-a-file"), "NotADirectoryError")

    def test_a_file_under_an_unreadable_folder_is_confidential(self):
        _skip_unless_modes_bite(self)
        sub = self.v / "03-projects/locked"
        sub.mkdir()
        (sub / "inside.md").write_text("---\ntype: note\nsensitivity: internal\n---\n", encoding="utf-8", newline="\n")
        _lock(self, sub, 0o755)
        self.assertTrue(kitlib.is_confidential(self.v, "03-projects/locked/inside.md"))
        self.assertIsNone(kitlib.confidentiality(self.v, "03-projects/locked/inside"), "could not tell, which is not yes")


class Digest(_TempVault):
    COUNT = re.compile(r"^(\d+) task\(s\) in confidential notes are not listed", re.M)
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


VISIBLE_PERSON = """---
type: person
title: Robin Visible
description: Runs the docs portal.
aliases: [Robin]
sensitivity: internal
---
# Robin Visible
"""


class Minutes(_TempVault):
    """A filed meeting may raise its confidentiality, never lower it (#52).

    "raise", the default, links people and projects as the whole vault knows them and marks the
    meeting confidential when it links a confidential note. "plain" (`--keep-open`, the bridge's
    default) links only notes that are not confidential, keeps every other name as written, and
    leaves the template's sensitivity alone.
    """
    RECAP = "We met to go through the Bluebird numbers in detail.\nAction: draft the Bluebird summary — me, due 2026-09-18\n"
    DAY = dt.date(2026, 9, 14)

    def fm(self, m) -> dict:
        return kitlib.parse_note(self.v / m.path, self.v).frontmatter

    def confidential_template(self) -> None:
        tpl = self.v / "90-templates/meeting.md"
        tpl.write_text(tpl.read_text(encoding="utf-8").replace("sensitivity: internal", "sensitivity: confidential"),
                       encoding="utf-8", newline="\n")

    def test_a_confidential_minutes_note_is_logged_without_title_or_path(self):
        self.confidential_template()
        m = kitrecon.file_minutes(self.v, self.RECAP, "Bluebird review", self.DAY, daily=True)
        self.assertTrue(kitlib.is_confidential(self.v, m.path), "the template made the note confidential")
        log = (self.v / "log.md").read_text(encoding="utf-8")
        self.assertIn("a meeting note marked confidential", log)
        _assert_none_of(self, log, "Bluebird")
        daily = kitlib.daily_note_path(self.v, self.DAY).read_text(encoding="utf-8")
        self.assertIn("Meeting filed: a note marked confidential", daily)
        _assert_none_of(self, daily, "Bluebird")

    def test_raise_links_the_people_and_marks_the_note_confidential(self):
        recap = "Alex and Sam walked through the Bluebird numbers.\nAction: draft the Bluebird summary — me, due 2026-09-18\n"
        m = kitrecon.file_minutes(self.v, recap, "Bluebird review", self.DAY, daily=True)
        self.assertEqual(set(m.people), {"05-people/alex-example.md", "05-people/sam-example.md"})
        self.assertEqual(sorted(m.confidential_links), ["05-people/alex-example.md", "05-people/sam-example.md"])
        fm = self.fm(m)
        self.assertEqual(sorted(fm["people"]), ["Alex Example", "Sam Example"])
        self.assertEqual(fm["sensitivity"], "confidential")
        self.assertTrue(kitlib.is_confidential(self.v, m.path))
        # Every file the bridge serves whole: nothing of the confidential meeting in any of them.
        kitlib.write_index(self.v)
        kitrecon.write_digest(self.v, kitrecon.todo_report(self.v, today=self.DAY))
        served = {
            "log.md": (self.v / "log.md").read_text(encoding="utf-8"),
            "daily": kitlib.daily_note_path(self.v, self.DAY).read_text(encoding="utf-8"),
            "index.md": (self.v / "index.md").read_text(encoding="utf-8"),
            "digest": (self.v / kitrecon.DIGEST).read_text(encoding="utf-8"),
        }
        for name, text in served.items():
            with self.subTest(file=name):
                _assert_none_of(self, text, "Bluebird", m.path, "bluebird-review")
        self.assertIn("a meeting note marked confidential", served["log.md"])
        self.assertIn("Meeting filed: a note marked confidential", served["daily"])

    def test_raise_counts_the_links_on_a_dry_run_too(self):
        m = kitrecon.file_minutes(self.v, "Alex joined.\n", "Dry", self.DAY, dry_run=True)
        self.assertEqual(m.confidential_links, ["05-people/alex-example.md"])
        self.assertFalse((self.v / m.path).exists())

    def test_raise_links_a_confidential_project_and_marks_the_note(self):
        self.write("03-projects/kestrel.md", HIDDEN_PROJECT)
        m = kitrecon.file_minutes(self.v, "x", "Kestrel two", self.DAY, project="Codename Kestrel")
        self.assertEqual(m.project, "03-projects/kestrel.md")
        self.assertEqual(self.fm(m)["sensitivity"], "confidential")

    def test_raise_keeps_the_template_level_when_nothing_linked_is_confidential(self):
        m = kitrecon.file_minutes(self.v, "The Search relaunch is on track.\n", "Track", self.DAY)
        self.assertEqual((m.project, m.confidential_links), ("03-projects/search-relaunch.md", []))
        self.assertEqual(self.fm(m)["sensitivity"], "internal")

    def test_a_template_already_confidential_stays_confidential(self):
        self.confidential_template()
        for mode, title in (("raise", "Kept one"), ("plain", "Kept two")):
            with self.subTest(mode=mode):
                m = kitrecon.file_minutes(self.v, "Nothing linked here.\n", title, self.DAY, mode=mode)
                self.assertEqual(m.confidential_links, [])
                self.assertEqual(self.fm(m)["sensitivity"], "confidential", "a template's level is never lowered")

    def test_force_over_a_raised_note_keeps_it_confidential(self):
        """`--force` replaces a meeting note; it must not lower one an earlier run raised."""
        first = kitrecon.file_minutes(self.v, "Alex Example joined.\nDecision: ship it\n", "Forced", self.DAY)
        self.assertTrue(kitlib.is_confidential(self.v, first.path))
        again = kitrecon.file_minutes(self.v, "Nobody named.\nDecision: wait\n", "Forced", self.DAY, mode="plain", force=True)
        self.assertEqual(again.path, first.path)
        self.assertTrue(kitlib.is_confidential(self.v, again.path))

    def test_an_unknown_attendee_is_plain_text_and_leaves_no_graph_warning(self):
        import kitgraph
        m = kitrecon.file_minutes(self.v, "Decision: ship it\n", "Attendees", self.DAY, people=["Nobody Known"], mode="plain")
        fm = self.fm(m)
        self.assertEqual((fm["people"], fm["attendees"]), ([], ["Nobody Known"]))
        g = kitgraph.build_graph(self.v)
        self.assertEqual([f.render() for f in g.findings if f.node == m.path and f.code == "unresolved_ref"], [])

    def test_raise_keeps_an_unknown_name_as_written(self):
        m = kitrecon.file_minutes(self.v, "x", "Explicit", self.DAY, people=["Sam", "Unknown Person"], dry_run=True)
        self.assertEqual(m.people, ["05-people/sam-example.md"])
        self.assertEqual(m.names, ["Sam Example", "Unknown Person"])
        self.assertEqual(m.unresolved, ["Unknown Person"])

    def test_plain_lists_only_visible_people_from_the_recap(self):
        """Plain mode lists a person the recap mentions only when it links to a visible note: listing a
        confidential one while an unknown name is never found would tell them apart. The recap stays
        verbatim in the body. The confidential note's title, description and path never reach the
        frontmatter."""
        self.write("05-people/robin-visible.md", VISIBLE_PERSON)
        recap = "Today alex example, Sam and Robin Visible walked through the numbers.\nDecision: ship it\n"
        m = kitrecon.file_minutes(self.v, recap, "Numbers sync", self.DAY, mode="plain")
        self.assertEqual(m.people, ["05-people/robin-visible.md"])
        self.assertEqual(m.names, ["Robin Visible"])
        self.assertEqual(m.unresolved, [])
        self.assertEqual(m.confidential_links, [])
        fm = self.fm(m)
        self.assertEqual(fm["people"], ["Robin Visible"])
        self.assertNotIn("attendees", fm)
        self.assertEqual(fm["sensitivity"], "internal", "plain mode keeps the template's level")
        raw = (self.v / m.path).read_text(encoding="utf-8").split("\n---\n", 1)[0]
        for leak in ("Alex Example", "Sam Example", "05-people/alex", "05-people/sam", ALEX[2], SAM[2]):
            self.assertNotIn(leak, raw)
        self.assertIn("[Numbers sync]", (self.v / "log.md").read_text(encoding="utf-8"), "an open note is logged by title")

    def test_plain_treats_a_passed_confidential_name_like_an_unknown_one(self):
        self.write("05-people/robin-visible.md", VISIBLE_PERSON)
        m = kitrecon.file_minutes(self.v, "x", "Explicit", self.DAY, people=["Sam Example", "Nobody Known", "Robin"],
                                  mode="plain", dry_run=True)
        self.assertEqual(m.people, ["05-people/robin-visible.md"])
        self.assertEqual(m.names, ["Sam Example", "Nobody Known", "Robin Visible"])
        self.assertEqual(m.unresolved, ["Sam Example", "Nobody Known"], "a confidential name reads like an unknown one")

    def test_plain_neither_links_nor_stores_a_confidential_project(self):
        self.write("03-projects/kestrel.md", HIDDEN_PROJECT)
        m = kitrecon.file_minutes(self.v, "Codename Kestrel status.\n", "Kestrel", self.DAY, mode="plain")
        self.assertEqual((m.project, m.unresolved), (None, []))
        self.assertEqual(self.fm(m)["project"], "")
        m = kitrecon.file_minutes(self.v, "x", "Kestrel two", self.DAY, project="Codename Kestrel", mode="plain", dry_run=True)
        self.assertIsNone(m.project)
        self.assertEqual(m.unresolved, ["Codename Kestrel"])

    def test_plain_links_the_visible_project_beside_a_confidential_one(self):
        """Only one project mentioned is linked; a hidden one must not make the visible one ambiguous."""
        self.write("03-projects/kestrel.md", HIDDEN_PROJECT)
        m = kitrecon.file_minutes(self.v, "Codename Kestrel and the Search relaunch.\n", "Both", self.DAY, mode="plain", dry_run=True)
        self.assertEqual(m.project, "03-projects/search-relaunch.md")

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            kitrecon.file_minutes(self.v, "x", "Bad", self.DAY, mode="hide", dry_run=True)

    def test_an_ordinary_minutes_note_is_still_logged_by_title(self):
        m = kitrecon.file_minutes(self.v, self.RECAP, "Bluebird review", self.DAY, daily=True)
        self.assertIn(f"[Bluebird review]({m.path})", (self.v / "log.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class SupersededByAConfidentialNote(_TempVault):
    """`reconcile` (and `--json`, which agents read) names no confidential note in a state conflict."""
    HIDDEN = "06-decisions/2026-09-15-embedding-model-for-de-en.md"
    ADOPT = "06-decisions/2026-09-08-adopt-qmd-for-local-search.md"

    def test_the_conflict_is_reported_without_the_path(self):
        p = self.v / self.HIDDEN
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("sensitivity: internal", "sensitivity: confidential\nsupersedes: \"[Adopt qmd](2026-09-08-adopt-qmd-for-local-search.md)\"", 1),
                     encoding="utf-8", newline="\n")
        changes = kitrecon.reconcile(self.v, apply=False, today=dt.date(2026, 9, 14))
        conflicts = [c for c in changes if c.code == "state_conflict"]
        self.assertEqual([c.file for c in conflicts], [self.ADOPT], [c.render() for c in changes])
        self.assertIn("a confidential note", conflicts[0].message)
        for c in changes:
            self.assertNotIn("embedding-model", c.render())


@unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "POSIX permissions, not root")
class ValidateAnUnreadableLinkTarget(_TempVault):
    """A link into a folder the kit may not read is a warning, not a crash (Path.exists() raised before 3.14)."""

    def test_validate_reports_and_finishes(self):
        self.write("07-knowledge/locked/hidden.md", "---\ntype: note\ntitle: Hidden\ndescription: x\n---\nbody\n")
        self.write("07-knowledge/points-in.md", "---\ntype: note\ntitle: Points in\ndescription: x\n---\n[hidden](locked/hidden.md)\n")
        locked = self.v / "07-knowledge" / "locked"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        rep = kitlib.check_links(self.v)
        self.assertTrue(any("cannot be checked" in w and "locked/hidden.md" in w for w in rep.warnings), rep.warnings)
        self.assertFalse(any("locked/hidden.md" in e for e in rep.errors), rep.errors)
