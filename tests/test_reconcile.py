"""Reconciliation: hubs vs notes, derived states, todo surfacing/sync, minutes filing."""
import datetime as dt
import shutil
import unittest
from pathlib import Path

from tests.helpers import VAULT, kitlib, temp_vault

import kitgraph
import kitrecon


class Hubs(unittest.TestCase):
    def test_template_is_consistent(self):
        self.assertEqual([c.render() for c in kitrecon.reconcile(VAULT)], [])

    def test_drift_detected_and_applied(self):
        v = temp_vault()
        try:
            kitlib.set_frontmatter(v / "03-projects/search-relaunch.md", {"health": "green", "milestone": "Pilot live", "due": "2026-10-01"})
            (v / "03-projects/new-thing.md").write_text("---\ntype: project\ntitle: New thing\ndescription: A brand new project.\nstate: active\nhealth: green\nowner: me\n---\n# New thing\n", encoding="utf-8")
            kitlib.set_frontmatter(v / "06-decisions/2026-09-08-adopt-qmd-for-local-search.md", {"state": "open"})
            (v / "06-decisions/2026-09-12-second.md").write_text("---\ntype: decision\ntitle: Second decision\ndescription: d\ndate: 2026-09-12\nstate: decided\nproject: \"[Search relaunch](../03-projects/search-relaunch.md)\"\n---\n# Second\n", encoding="utf-8")
            changes = kitrecon.reconcile(v)
            codes = [(c.code, c.file) for c in changes]
            self.assertIn(("hub_drift", kitrecon.PROJECT_HUB), codes)
            self.assertIn(("hub_missing_row", kitrecon.PROJECT_HUB), codes)
            self.assertIn(("hub_drift", kitrecon.DECISION_HUB), codes)
            self.assertIn(("hub_missing_row", kitrecon.DECISION_HUB), codes)
            self.assertIn(("priorities_missing_open_decision", kitrecon.PRIORITIES), codes)
            self.assertFalse(any(c.applied for c in changes), "dry run by default")
            self.assertEqual((v / kitrecon.PROJECT_HUB).read_text(encoding="utf-8").count("yellow"), 1)
            applied = kitrecon.reconcile(v, apply=True, today=dt.date(2026, 9, 14))
            self.assertTrue(all(c.applied for c in applied if c.code in ("hub_drift", "hub_missing_row")))
            hub = (v / kitrecon.PROJECT_HUB).read_text(encoding="utf-8")
            self.assertIn("| green | active | Pilot live 2026-10-01 | Hybrid search for the docs portal", hub, "cells updated, free text kept")
            self.assertIn("[New thing](new-thing.md) | green | active |  | A brand new project.", hub)
            log = (v / kitrecon.DECISION_HUB).read_text(encoding="utf-8")
            self.assertIn("| 2026-09-12 | Second decision | decided |  | [note](2026-09-12-second.md) |", log)
            self.assertLess(log.index("2026-09-12 | Second"), log.index("2026-09-08 | Adopt"), "newest first")
            self.assertIn("| 2026-09-08 | Adopt qmd as the local search stack for the docs portal pilot (example) | open |", log)
            self.assertEqual(kitrecon.reconcile(v)[0].code, "priorities_missing_open_decision")
            self.assertEqual(len(kitrecon.reconcile(v)), 1, "only the non-applicable finding remains")
            self.assertEqual(kitlib.validate_okf(v).errors, []); self.assertEqual(kitlib.check_links(v).errors, [])
            self.assertIn("Reconciled hubs", (v / "log.md").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_superseded_state_applied(self):
        v = temp_vault()
        try:
            (v / "06-decisions/2026-09-20-newer.md").write_text("---\ntype: decision\ntitle: Newer\ndescription: d\ndate: 2026-09-20\nstate: decided\nproject: \"[Search relaunch](../03-projects/search-relaunch.md)\"\nsupersedes: \"[old](2026-09-08-adopt-qmd-for-local-search.md)\"\n---\n# Newer\n", encoding="utf-8")
            changes = kitrecon.reconcile(v, apply=True)
            self.assertIn("state_conflict", [c.code for c in changes])
            note = kitlib.parse_note(v / "06-decisions/2026-09-08-adopt-qmd-for-local-search.md", v)
            self.assertEqual(note.frontmatter["state"], "superseded")
            self.assertEqual(kitrecon.reconcile(v), [])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class Todos(unittest.TestCase):
    def test_parse_task_line(self):
        self.assertEqual(kitrecon.parse_task_line("- [ ] Draft slide — me, due 2026-07-15"), (False, "Draft slide", "me", "2026-07-15"))
        self.assertEqual(kitrecon.parse_task_line("- [x] Done thing — Alex"), (True, "Done thing", "Alex", ""))
        self.assertEqual(kitrecon.parse_task_line("- [ ] Fix it 📅 2026-09-20"), (False, "Fix it", "", "2026-09-20"))
        self.assertEqual(kitrecon.parse_task_line("- [ ] a - b - c")[1], "a - b - c", "plain hyphens are not owner separators")
        self.assertIsNone(kitrecon.parse_task_line("- not a task"))

    def test_report_on_template(self):
        rep = kitrecon.todo_report(VAULT, today=dt.date(2026, 9, 14))
        self.assertGreater(len(rep.open), 5)
        self.assertTrue(all(t.due < "2026-09-14" for t in rep.overdue))
        self.assertIn("Send Alex the go/no-go template", [t.text for t in rep.overdue])
        self.assertEqual(rep.done_conflicts, [])

    def test_duplicates_conflicts_and_sync(self):
        v = temp_vault()
        try:
            (v / "02-meetings/x.md").write_text("---\ntype: meeting\ndescription: x\ndate: 2026-09-10\n---\n- [x] Prepare go/no-go one-pager — me, due 2026-09-17\n", encoding="utf-8")
            rep = kitrecon.todo_report(v, today=dt.date(2026, 9, 14))
            self.assertEqual(len(rep.done_conflicts), 1)
            rep = kitrecon.todo_report(v, today=dt.date(2026, 9, 14), sync_done=True)
            self.assertEqual(len(rep.synced), 1)
            self.assertIn("- [x] Prepare go/no-go one-pager — me, due 2026-09-17", (v / "03-projects/search-relaunch.md").read_text(encoding="utf-8"))
            self.assertEqual(kitrecon.todo_report(v, today=dt.date(2026, 9, 14)).done_conflicts, [])
            out = kitrecon.write_digest(v, kitrecon.todo_report(v, today=dt.date(2026, 9, 14)))
            text = out.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\ntype: system"))
            self.assertIn("## Overdue", text)
            self.assertEqual(kitlib.validate_okf(v).errors, []); self.assertEqual(kitlib.check_links(v).errors, [], "digest links resolve")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


RECAP = """Search relaunch sync with Alex and Sam.
Outcome: ranking quality above target.
Decision: keep the pilot date; go/no-go conditional on latency under 1.0 s.
Action: rerun the latency benchmark — Alex, due 2026-09-16
- [ ] Evaluate multilingual embedding model — Sam, due 2026-09-17
TODO: send the go/no-go template to Alex
"""


class Minutes(unittest.TestCase):
    def test_extract(self):
        actions, decisions, outcomes = kitrecon.extract_minutes(RECAP)
        self.assertEqual(actions[0], "- [ ] rerun the latency benchmark — Alex, due 2026-09-16")
        self.assertEqual(actions[1], "- [ ] Evaluate multilingual embedding model — Sam, due 2026-09-17")
        self.assertEqual(actions[2], "- [ ] send the go/no-go template to Alex")
        self.assertEqual(len(decisions), 1); self.assertEqual(len(outcomes), 1)

    def test_file_minutes_end_to_end(self):
        v = temp_vault()
        try:
            m = kitrecon.file_minutes(v, RECAP, "Latency check-in", dt.date(2026, 9, 15), daily=True)
            self.assertEqual(m.path, "02-meetings/2026-09-15-latency-check-in.md")
            self.assertEqual(set(m.people), {"05-people/alex-example.md", "05-people/sam-example.md"})
            self.assertEqual(m.project, "03-projects/search-relaunch.md")
            note = kitlib.parse_note(v / m.path, v)
            self.assertIsNone(note.fm_error); self.assertEqual(note.frontmatter["type"], "meeting")
            self.assertEqual(note.frontmatter["people"], ["Alex Example", "Sam Example"])
            self.assertEqual(note.frontmatter["generated"]["by"], "process:kit-minutes")
            self.assertIn("## Actions\n\n- [ ] rerun the latency benchmark — Alex, due 2026-09-16", note.body)
            self.assertIn("> Search relaunch sync with Alex and Sam.", note.body)
            self.assertNotIn("{{", note.body)
            self.assertEqual(kitlib.validate_okf(v).errors, []); self.assertEqual(kitlib.check_links(v).errors, [])
            g = kitgraph.build_graph(v)
            self.assertEqual([f.render() for f in g.findings], [])
            self.assertIn((m.path, "project", "03-projects/search-relaunch.md", "frontmatter"), g.edges)
            self.assertIn("Filed meeting note [Latency check-in]", (v / "log.md").read_text(encoding="utf-8"))
            self.assertIn("Meeting filed: [Latency check-in]", kitlib.daily_note_path(v, dt.date(2026, 9, 15)).read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                kitrecon.file_minutes(v, RECAP, "Latency check-in", dt.date(2026, 9, 15))
            self.assertIn("rerun the latency benchmark", [t.text for t in kitrecon.scan_todos(v)])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_dry_run_and_explicit_people(self):
        v = temp_vault()
        try:
            m = kitrecon.file_minutes(v, "nothing structured here", "Quick chat", dt.date(2026, 9, 16), people=["Sam", "Unknown Person"], dry_run=True)
            self.assertEqual(m.people, ["05-people/sam-example.md"]); self.assertEqual(m.unresolved, ["Unknown Person"])
            self.assertFalse((v / m.path).exists())
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_daily_link_starts_its_own_line_and_stays_lf(self):
        """A hand-edited daily note need not end in a newline; the bullet must not glue onto its last line."""
        v = temp_vault()
        try:
            day = dt.date(2026, 9, 17)
            dpath = kitlib.daily_note_path(v, day)
            dpath.parent.mkdir(parents=True, exist_ok=True)
            dpath.write_text("---\ntype: daily\ntitle: Day\ndescription: d\ndate: 2026-09-17\n---\n\n## Log\n\n- health back to green.",
                             encoding="utf-8", newline="\n")
            kitrecon.file_minutes(v, RECAP, "Glue check", day, daily=True)
            raw = dpath.read_bytes()
            self.assertNotIn(b"\r\n", raw, "the daily note was appended with CRLF")
            lines = raw.decode("utf-8").splitlines()
            self.assertEqual(lines[-2], "- health back to green.")
            self.assertTrue(lines[-1].startswith("- Meeting filed: [Glue check]"), lines[-1])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

PROJECT_NOTE = ("---\ntype: project\ntitle: {title}\ndescription: {title} work.\nstate: active\nhealth: green\n"
                "tags: [project]\nsensitivity: internal\n---\n# {title}\n")


class HubRows(unittest.TestCase):
    """A row the reconciler writes has to be a row it can read back, or --apply appends it forever."""

    def test_space_named_project_row_round_trips(self):
        v = temp_vault()
        try:
            (v / "03-projects/Customer Portal.md").write_text(PROJECT_NOTE.format(title="Customer Portal"), encoding="utf-8", newline="\n")
            first = kitrecon.reconcile(v, apply=True, today=dt.date(2026, 9, 14))
            self.assertEqual([c.code for c in first if c.applied], ["hub_missing_row"])
            hub = (v / kitrecon.PROJECT_HUB).read_text(encoding="utf-8")
            self.assertIn("| [Customer Portal](Customer%20Portal.md) | green | active |", hub, "space encoded so the link parses again")
            again = kitrecon.reconcile(v, apply=True, today=dt.date(2026, 9, 14))
            self.assertEqual([c.render() for c in again], [], "nothing left to do on the second run")
            self.assertEqual((v / kitrecon.PROJECT_HUB).read_text(encoding="utf-8"), hub, "no duplicate row")
            self.assertEqual(kitlib.check_links(v).errors, [])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_hand_written_percent_encoded_row_is_recognised(self):
        v = temp_vault()
        try:
            (v / "03-projects/Customer Portal.md").write_text(PROJECT_NOTE.format(title="Customer Portal"), encoding="utf-8", newline="\n")
            hub = v / kitrecon.PROJECT_HUB
            text = hub.read_text(encoding="utf-8")
            row = [l for l in text.splitlines() if "Search relaunch" in l][0]
            hub.write_text(text.replace(row, row + "\n| [Customer Portal](Customer%20Portal.md) | green | active |  | Customer Portal work. |"),
                           encoding="utf-8", newline="\n")
            self.assertEqual([c.render() for c in kitrecon.reconcile(v)], [], "the row Obsidian writes is not broken")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_escaped_pipe_survives_a_rewrite(self):
        v = temp_vault()
        try:
            hub = v / kitrecon.PROJECT_HUB
            text = hub.read_text(encoding="utf-8")
            row = [l for l in text.splitlines() if "Search relaunch" in l][0]
            cells = row.split(" | ")
            cells[-1] = "CI \\| CD pipeline for docs portal |"
            hub.write_text(text.replace(row, " | ".join(cells)), encoding="utf-8", newline="\n")
            kitlib.set_frontmatter(v / "03-projects/search-relaunch.md", {"health": "green"})
            applied = kitrecon.reconcile(v, apply=True, today=dt.date(2026, 9, 14))
            self.assertEqual([c.code for c in applied if c.applied], ["hub_drift"])
            out = [l for l in hub.read_text(encoding="utf-8").splitlines() if "Search relaunch" in l][0]
            self.assertIn("| green | active |", out)
            self.assertIn("CI \\| CD pipeline for docs portal", out, "the escape is free text and stays byte for byte")
            self.assertEqual(len(kitrecon._split_row(out)), 5, "no phantom column")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class TodoSyncSafety(unittest.TestCase):
    """--sync-done may only close a checkbox the same person left open for the same round."""

    def test_another_owner_is_never_ticked(self):
        v = temp_vault()
        try:
            (v / "02-meetings/a.md").write_text("---\ntype: meeting\ndescription: a\ndate: 2026-09-10\n---\n- [x] Prepare the quarterly review deck — alice, due 2026-09-10\n", encoding="utf-8", newline="\n")
            (v / "02-meetings/b.md").write_text("---\ntype: meeting\ndescription: b\ndate: 2026-09-11\n---\n- [ ] Prepare the quarterly review deck — bob, due 2026-10-01\n", encoding="utf-8", newline="\n")
            rep = kitrecon.todo_report(v, today=dt.date(2026, 9, 14), sync_done=True)
            self.assertEqual([t.file for t in rep.synced], [], "different owner and due date, different commitment")
            self.assertIn("- [ ] Prepare the quarterly review deck — bob", (v / "02-meetings/b.md").read_text(encoding="utf-8"))
            self.assertTrue(any("Prepare the quarterly review deck" in g[0].text for g in rep.done_conflicts), "still surfaced as a conflict")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_recurring_daily_task_is_not_ticked(self):
        v = temp_vault()
        try:
            for day, mark in ((dt.date(2026, 9, 13), "x"), (dt.date(2026, 9, 14), " ")):
                p = kitlib.daily_note_path(v, day)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"---\ntype: daily\ntitle: '{day.isoformat()}'\ndescription: d\ndate: {day.isoformat()}\n---\n- [{mark}] Water the office plants\n", encoding="utf-8", newline="\n")
            rep = kitrecon.todo_report(v, today=dt.date(2026, 9, 14), sync_done=True)
            self.assertEqual([t.text for t in rep.synced], [], "yesterday's copy does not close today's")
            self.assertIn("- [ ] Water the office plants", kitlib.daily_note_path(v, dt.date(2026, 9, 14)).read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class EntityDetection(unittest.TestCase):
    def test_bare_first_name_needs_its_capital(self):
        v = temp_vault()
        try:
            for slug, title in (("will-turner", "Will Turner"), ("mark-weber", "Mark Weber"), ("max-grant", "Max Grant")):
                (v / f"05-people/{slug}.md").write_text(f"---\ntype: person\ntitle: {title}\ndescription: A person.\ntags: [person]\nsensitivity: personal\n---\n# {title}\n", encoding="utf-8", newline="\n")
            g = kitgraph.build_graph(v)
            people, _ = kitrecon.detect_entities("We will ship on Friday, mark the release as final and grant max access.", g)
            self.assertEqual(people, [], "ordinary words are not attendees")
            people, _ = kitrecon.detect_entities("Will and Mark Weber joined; Max took the notes.", g)
            self.assertEqual(sorted(people), ["05-people/mark-weber.md", "05-people/max-grant.md", "05-people/will-turner.md"])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class FrontmatterAndLogFidelity(unittest.TestCase):
    """What reconcile writes into a user's note: one key changed, everything else as authored."""

    def test_untouched_frontmatter_survives_a_single_key_edit(self):
        v = temp_vault()
        try:
            note = v / "06-decisions/2026-09-08-adopt-qmd-for-local-search.md"
            text = note.read_text(encoding="utf-8")
            note.write_text(text.replace("state: decided", "state: decided   # revisit after the pilot")
                                .replace("description: Use qmd", "description: >-\n  Use qmd"), encoding="utf-8", newline="\n")
            (v / "06-decisions/2026-09-20-newer.md").write_text("---\ntype: decision\ntitle: Newer\ndescription: d\ndate: 2026-09-20\nstate: decided\nproject: \"[Search relaunch](../03-projects/search-relaunch.md)\"\nsupersedes: \"[old](2026-09-08-adopt-qmd-for-local-search.md)\"\n---\n# Newer\n", encoding="utf-8", newline="\n")
            kitrecon.reconcile(v, apply=True, today=dt.date(2026, 9, 20))
            out = note.read_text(encoding="utf-8")
            self.assertIn("state: superseded", out)
            self.assertIn("description: >-\n  Use qmd", out, "folded scalar kept")
            self.assertIn('generated: { by: human:me, at: "2026-09-08T17:05:00+02:00" }', out, "quoting and flow style kept")
            self.assertIn("people: [\"Alex Example\", \"Sam Example\"]", out)
            self.assertNotIn("{type:", out, "no collapse into one flow line")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_empty_frontmatter_block_and_new_key(self):
        v = temp_vault()
        try:
            note = v / "03-projects/bare.md"
            note.write_text("---\n---\nbody\n", encoding="utf-8", newline="\n")
            kitlib.set_frontmatter(note, {"state": "done"})
            self.assertEqual(note.read_text(encoding="utf-8"), "---\nstate: done\n---\nbody\n")
            plain = v / "03-projects/plain.md"
            plain.write_text("---\ntype: project\ntitle: Alpha rollout\ndescription: Rollout plan\n---\nBody\n", encoding="utf-8", newline="\n")
            kitlib.set_frontmatter(plain, {"state": "done"})
            self.assertEqual(plain.read_text(encoding="utf-8"), "---\ntype: project\ntitle: Alpha rollout\ndescription: Rollout plan\nstate: done\n---\nBody\n")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_back_dated_log_entry_keeps_newest_first(self):
        v = temp_vault()
        try:
            kitrecon.file_minutes(v, "Outcome: fine.", "Back dated", dt.date(2026, 9, 5))
            kitrecon.file_minutes(v, "Outcome: fine.", "Older still", dt.date(2025, 12, 31))
            headings = [l for l in (v / "log.md").read_text(encoding="utf-8").splitlines() if l.startswith("## ")]
            self.assertEqual(headings, sorted(headings, reverse=True), "the log stays newest first")
            self.assertIn("## 2026-09-05", headings)
            self.assertEqual(kitlib.validate_okf(v).errors, [])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class MinutesSections(unittest.TestCase):
    def test_decision_hint_is_kept_and_retargeted(self):
        v = temp_vault()
        try:
            m = kitrecon.file_minutes(v, "Decision: keep the pilot date.", "Hint check", dt.date(2026, 9, 15))
            body = (v / m.path).read_text(encoding="utf-8")
            # The hint must survive filing; its exact wording is the template's business. This
            # layer drops the folder-relative link the older assertion pinned, because that link
            # was broken from every destination a note is actually filed to.
            self.assertIn("- keep the pilot date.", body)
            hint = [l for l in body.splitlines() if "one line each" in l]
            self.assertEqual(len(hint), 1, f"the decision hint did not survive filing: {body}")
            self.assertIn("decision", hint[0].lower())
            self.assertEqual(kitlib.check_links(v).errors, [])
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_project_ref_to_a_space_named_note_resolves(self):
        v = temp_vault()
        try:
            (v / "03-projects/Customer Portal.md").write_text(PROJECT_NOTE.format(title="Customer Portal"), encoding="utf-8", newline="\n")
            m = kitrecon.file_minutes(v, "Outcome: fine.", "Portal sync", dt.date(2026, 9, 15), project="Customer Portal")
            self.assertIn("Customer%20Portal.md", (v / m.path).read_text(encoding="utf-8"))
            g = kitgraph.build_graph(v)
            self.assertIn((m.path, "project", "03-projects/Customer Portal.md", "frontmatter"), g.edges, "the written ref resolves back")
            self.assertEqual([f.render() for f in g.findings if f.node == m.path], [], "no unresolved reference")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class UndecodableFiles(unittest.TestCase):
    """PowerShell 5.1's `Out-File` defaults to UTF-16, and a vault ends up with one such file.

    Reconcile and minutes read three hubs, two templates and log.md outside `kitlib.load_vault`,
    so each of those reads was its own way to lose the whole command to one file.
    """

    UTF16 = "---\ntype: project\n---\nnot utf-8\n".encode("utf-16")

    def setUp(self):
        self.v = temp_vault()

    def tearDown(self):
        shutil.rmtree(self.v.parent, ignore_errors=True)

    def test_an_unreadable_hub_is_a_finding_and_the_other_passes_still_run(self):
        (self.v / kitrecon.PROJECT_HUB).write_bytes(self.UTF16)
        kitlib.set_frontmatter(self.v / "06-decisions/2026-09-08-adopt-qmd-for-local-search.md", {"state": "open"})
        codes = [(c.code, c.file) for c in kitrecon.reconcile(self.v)]
        self.assertIn(("hub_unreadable", kitrecon.PROJECT_HUB), codes)
        self.assertIn(("priorities_missing_open_decision", kitrecon.PRIORITIES), codes,
                      "the passes after the unreadable hub still run")

    def test_filing_minutes_with_an_unreadable_log_keeps_the_note_and_names_the_skip(self):
        (self.v / "log.md").write_bytes(self.UTF16)
        m = kitrecon.file_minutes(self.v, "Decision: ship it.", "Repro sync", dt.date(2026, 9, 15))
        self.assertTrue((self.v / m.path).exists(), "the note the caller asked for")
        self.assertEqual((self.v / "log.md").read_bytes(), self.UTF16, "a log we cannot read is not one we rewrite")
        self.assertTrue(any(s.startswith("log.md") for s in m.skipped), m.skipped)

    def test_an_unreadable_meeting_template_still_files_a_conforming_note(self):
        (self.v / "90-templates/meeting.md").write_bytes(self.UTF16)
        m = kitrecon.file_minutes(self.v, "Decision: ship it.", "Repro sync", dt.date(2026, 9, 15))
        note = kitlib.parse_note(self.v / m.path, self.v)
        self.assertEqual(note.frontmatter["type"], "meeting")
        self.assertTrue(any(s.startswith("90-templates/meeting.md") for s in m.skipped), m.skipped)

    def test_an_unreadable_daily_note_is_named_rather_than_appended_to(self):
        day = dt.date(2026, 9, 15)
        daily = kitlib.daily_note_path(self.v, day)
        daily.parent.mkdir(parents=True, exist_ok=True)
        daily.write_bytes(self.UTF16)
        m = kitrecon.file_minutes(self.v, "Decision: ship it.", "Repro sync", day, daily=True)
        self.assertTrue((self.v / m.path).exists())
        self.assertEqual(daily.read_bytes(), self.UTF16, "UTF-8 appended to a UTF-16 note is a second fault")
        rel = daily.relative_to(self.v).as_posix()
        self.assertTrue(any(s.startswith(rel) for s in m.skipped), m.skipped)
