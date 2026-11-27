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
import kitagent
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

    def test_a_rule_without_an_extension_covers_the_note_both_ways(self):
        # The note gets one verdict however it is referenced: the rule was only ever compared with
        # the reference plus `.md`, never with itself plus `.md`.
        pol = kitlib.ConfidentialityPolicy.build(paths=["07-knowledge/salary-review", "a/plan?"])
        for ref in ("07-knowledge/salary-review", "07-knowledge/salary-review.md", "a/plan1.md", "a/plan1"):
            self.assertTrue(pol.covers(ref), ref)
        self.assertFalse(pol.covers("07-knowledge/salary-review-2.md"))
        self.assertEqual(kitlib.path_rule_spellings("07-knowledge/salary-review"),
                         ("07-knowledge/salary-review", "07-knowledge/salary-review.md"))
        for rule in ("x/plan.md", "x/Plan.MD", "x/plan-*", "x/**"):
            self.assertEqual(kitlib.path_rule_spellings(rule), (rule,), "`.md` already, or a `*` that matches it already")
        for rule in ("x/*.csv", "daily/2024.11.26", "07-knowledge/Salary Review v1.2"):
            self.assertEqual(kitlib.path_rule_spellings(rule), (rule, rule + ".md"), "a dot is not `.md`")

    def test_a_rule_whose_last_name_holds_a_dot_covers_the_note(self):
        # `2024.11.26` read as a name with the extension `.26`: the rule got no `.md` spelling, hid
        # nothing, and validate called it a misspelling.
        notes = {"daily/2024.11.26.md": "# Day\n\nDOTDAY-BODY\n",
                 "07-knowledge/Salary Review v1.2.md": "---\ntype: concept\ntitle: Pay bands\n---\n# Pay\n\nDOTSAL-BODY\n"}
        for rel, text in notes.items():
            write(self.vault, rel, text)
        self.assertTrue(set(notes) <= {n.path for n in kitgraph.build_graph(self.vault).nodes.values()}, "the fixture")
        set_config(self.vault, 'confidentiality:\n  confidential_paths: ["daily/2024.11.26", "07-knowledge/Salary Review v1.2"]\n')
        index = kitlib.build_index(self.vault)
        graph = json.dumps(kitgraph.to_json(kitgraph.redact_vault(kitgraph.build_graph(self.vault), self.vault)), default=str)
        for rel in notes:
            self.assertIs(kitlib.confidentiality(self.vault, rel), True, rel)
            self.assertIs(kitlib.confidentiality(self.vault, rel.removesuffix(".md")), True, rel)
            self.assertNotIn(rel, index)
            self.assertNotIn(rel, graph)
        for marker in ("DOTDAY", "DOTSAL", "Pay bands"):
            self.assertNotIn(marker, index + graph)
        r = run("validate", "--vault", str(self.vault))
        self.assertNotIn("matches no file", r.stdout)

    def test_a_path_rule_ending_in_a_slash_points_to_the_folders_key(self):
        with self.assertRaisesRegex(kitlib.PolicyConfigError, "confidential_folders"):
            kitlib.parse_confidentiality({"confidentiality": {"confidential_paths": ["05-people/"]}})
        with self.assertRaisesRegex(kitlib.PolicyConfigError, r"only \* \? and \*\*"):
            kitlib.parse_confidentiality({"confidentiality": {"confidential_paths": ["exports/[0-9]*.csv"]}})

    def test_validate_warns_about_a_path_rule_that_matches_no_file(self):
        set_config(self.vault, CONFIG.replace('"07-knowledge/**/harrier-*.md"', '"07-knowledge/**/harrier-*.md", "nowhere/*.pdf"'))
        r = run("validate", "--vault", str(self.vault))
        self.assertIn("warn   .kit/config.yml: path rule 'nowhere/*.pdf' matches no file in the vault", r.stdout)
        self.assertNotIn("path rule '08-resources/exports/*.csv' matches no file", r.stdout, "it matches payroll.csv")

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

    def test_the_bridge_refuses_a_template_under_a_floor(self):
        # A labelled template carried its `sensitivity` line into the copy; a floor does not, so
        # create_note would copy the template's text into a note nothing hides.
        set_config(self.vault, 'confidentiality:\n  confidential_folders: ["90-templates/hr"]\n')
        write(self.vault, "90-templates/hr/letter.md", "# Letter\n\nLETTER-BODY salary bands\n")
        g = guarded(self.vault)
        for spelling in ("hr/letter", "hr/letter.md", "HR/Letter"):
            with self.assertRaisesRegex(bp.PolicyError, "template", msg=spelling):
                g.create_note("00-inbox/x", template=spelling)
        self.assertFalse((self.vault / "00-inbox/x.md").exists())
        g.create_note("00-inbox/y", template="meeting")          # an open template still works

    def test_the_bridge_copies_a_labelled_template_and_hides_the_copy(self):
        # The kit's own person template says `sensitivity: confidential`; the label travels into the
        # copy, so the copy is hidden as the template is. Refusing it broke the shipped templates.
        g = guarded(self.vault)
        self.assertIs(kitlib.own_confidentiality(self.vault, "90-templates/person.md"), True, "the fixture")
        g.create_note("00-inbox/new-person", template="person")
        self.assertIs(kitlib.confidentiality(self.vault, "00-inbox/new-person.md"), True)
        with self.assertRaises(bp.PolicyError):
            g.read_note("00-inbox/new-person")
        # A template whose confidentiality cannot be told is refused: its label may be what failed to parse.
        write(self.vault, "90-templates/broken.md", "---\ntitle: [unclosed\nsensitivity: confidential\n---\nBODY\n")
        with self.assertRaisesRegex(bp.PolicyError, "template"):
            g.create_note("00-inbox/from-broken", template="broken")
        self.assertFalse((self.vault / "00-inbox/from-broken.md").exists())

    def test_a_forced_minutes_overwrite_writes_no_label_for_a_floor(self):
        set_config(self.vault, 'confidentiality:\n  confidential_folders: ["02-meetings"]\n')
        day = dt.date(2026, 9, 21)
        kitrecon.file_minutes(self.vault, "We met.", "Floor sync", date=day, mode="plain")
        m = kitrecon.file_minutes(self.vault, "We met again.", "Floor sync", date=day, mode="plain", force=True)
        note = kitlib.parse_note(self.vault / m.path, self.vault)
        self.assertNotEqual(str(note.frontmatter.get("sensitivity", "")).lower(), "confidential",
                            "the floor hides the meeting; it must not write a label into it")
        self.assertTrue(kitlib.is_confidential(self.vault, m.path))


def symlink(test: unittest.TestCase, link: Path, target: Path | str) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=Path(link.parent, target).is_dir())
    except (OSError, NotImplementedError) as exc:     # Windows without the privilege
        test.skipTest(f"cannot create a symlink here ({exc.__class__.__name__})")


class LinksAndTrashKeepTheFloor(unittest.TestCase):
    """A floor travels with the file: a link to it and its copy in `.trash` are judged as it is."""

    PERSON = "# Alice\n\nALICE-BODY\n"         # no sensitivity line: only the floor hides it

    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)
        set_config(self.vault, 'confidentiality:\n  confidential_folders: ["05-people"]\n')
        write(self.vault, "05-people/alice.md", self.PERSON)

    def test_a_link_outside_the_floor_to_a_note_inside_it(self):
        symlink(self, self.vault / "03-projects/alice.md", Path("../05-people/alice.md"))
        for ref in ("03-projects/alice.md", "03-projects/alice"):
            self.assertIs(kitlib.confidentiality(self.vault, ref), True, ref)
        hits = guarded(self.vault).search("ALICE-BODY")
        self.assertFalse([h for h in hits if "alice" in h["file"] or "ALICE-BODY" in json.dumps(h)], hits)
        self.assertNotIn("03-projects/alice", kitlib.build_index(self.vault))

    def test_a_linked_folder_into_the_floor(self):
        symlink(self, self.vault / "03-projects/shared", Path("../05-people"))
        self.assertIs(kitlib.confidentiality(self.vault, "03-projects/shared/alice.md"), True)

    def test_a_link_out_of_the_vault_is_judged_by_its_own_path(self):
        outside = self.vault.parent / "elsewhere"
        write(outside, "05-people/bob.md", "# Bob\n")       # a folder of that name, but not the vault's
        symlink(self, self.vault / "03-projects/ext", outside)
        self.assertIs(kitlib.confidentiality(self.vault, "03-projects/ext/05-people/bob.md"), False)
        symlink(self, self.vault / "05-people/ext", outside)
        self.assertIs(kitlib.confidentiality(self.vault, "05-people/ext/05-people/bob.md"), True, "the lexical floor")

    def test_a_note_in_the_trash_keeps_its_floor(self):
        write(self.vault, ".trash/05-people/alice.md", self.PERSON)
        self.assertIs(kitlib.confidentiality(self.vault, ".trash/05-people/alice.md"), True)
        self.assertTrue(kitlib.ConfidentialityPolicy.build(paths=["exports/*.csv"]).covers(".trash/exports/x.csv"))
        with self.assertRaises(bp.PolicyError):
            guarded(self.vault).read_note(".trash/05-people/alice")


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
    # A path rule that would silently match nothing, or something else than it says (#120 review).
    "path rule ends in a slash": 'confidentiality:\n  confidential_paths: ["05-people/"]\n',
    "character class": 'confidentiality:\n  confidential_paths: ["exports/[0-9]*.csv"]\n',
    "backslash": 'confidentiality:\n  confidential_paths: ["exports\\\\x.csv"]\n',
    "bang": 'confidentiality:\n  confidential_paths: ["exports/!x.csv"]\n',
    "hash": 'confidentiality:\n  confidential_paths: ["exports/#x.csv"]\n',
    # A key spelt twice: yaml.safe_load keeps the last one, which erased the first.
    "duplicate key in the block": 'confidentiality:\n  confidential_folders: ["05-people"]\n  confidential_folders: []\n',
    "duplicate block": ('confidentiality:\n  confidential_folders: ["05-people"]\n'
                        'confidentiality:\n  confidential_paths: ["x.csv"]\n'),
    # Near misses of the block's name at the top level protect nothing unless they are refused.
    "capitalised block": 'Confidentiality:\n  confidential_folders: ["05-people"]\n',
    "misspelt block": 'confidentialty:\n  confidential_folders: ["05-people"]\n',
    "key at the top level": 'confidential_folders: ["05-people"]\n',
    # open_folders (#126): each of these would open more than it says, or nothing at all.
    "open folders empty": "confidentiality:\n  open_folders: []\n",
    "open folders null": "confidentiality:\n  open_folders:\n",
    "open folder dot": 'confidentiality:\n  open_folders: ["."]\n',
    "open folder dot slash": 'confidentiality:\n  open_folders: ["./"]\n',
    "open folder root": 'confidentiality:\n  open_folders: ["/"]\n',
    "open folder glob": 'confidentiality:\n  open_folders: ["04-areas/*"]\n',
    "open folder absolute": 'confidentiality:\n  open_folders: ["/etc"]\n',
    "open folder escapes": 'confidentiality:\n  open_folders: ["04-areas/../../x"]\n',
    "open folder not a list": "confidentiality:\n  open_folders: 04-areas\n",
    "open folder is a confidential folder": ('confidentiality:\n  confidential_folders: ["05-people"]\n'
                                             '  open_folders: ["05-People"]\n'),
    "open folder under a confidential folder": ('confidentiality:\n  confidential_folders: ["05-people"]\n'
                                                '  open_folders: ["04-areas", "05-people/team"]\n'),
    "open folder twice": 'confidentiality:\n  open_folders: ["04-areas", "04-Areas/"]\n',
    # Indented one level too little: read as "no policy", the whole vault stayed open (#126 review).
    "open folders at the top level": ('confidentiality:\n  confidential_paths: ["**/salary*"]\n'
                                      'open_folders: ["04-areas"]\n'),
    "open folders at the top level, capitalised": 'Open_Folders: ["04-areas"]\n',
    "open folders at the top level, hyphenated": 'open-folders: ["04-areas"]\n',
    "open folders at the top level, run together": 'openfolders: ["04-areas"]\n',
    "confidential folders at the top level, spaced": 'confidential folders: ["05-people"]\n',
}


class HubRowsFollowTheFloor(unittest.TestCase):
    """reconcile removes a hub row on a declared floor, and keeps every row while the config is broken."""

    ROW = "[Search relaunch](search-relaunch.md)"

    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)

    def codes(self, changes) -> list[str]:
        return [c.code for c in changes if c.file == kitrecon.PROJECT_HUB]

    def test_a_path_rule_removes_the_row(self):
        set_config(self.vault, 'confidentiality:\n  confidential_paths: ["03-projects/search-relaunch.md"]\n')
        self.assertIn("hub_confidential_row", self.codes(kitrecon.reconcile(self.vault)))
        kitrecon.reconcile(self.vault, apply=True, today=dt.date(2026, 9, 14))
        self.assertNotIn(self.ROW, (self.vault / kitrecon.PROJECT_HUB).read_text(encoding="utf-8"))

    def test_a_broken_config_removes_no_row(self):
        set_config(self.vault, BROKEN["yaml"])
        changes = kitrecon.reconcile(self.vault, apply=True, today=dt.date(2026, 9, 14))
        self.assertNotIn("hub_confidential_row", self.codes(changes))
        self.assertIn("hub_row_unverifiable", self.codes(changes))
        self.assertEqual([c.file for c in changes if c.code == "confidentiality_config"], [kitlib.CONFIG_REL],
                         "the cause is named once, where it is")
        self.assertIn(self.ROW, (self.vault / kitrecon.PROJECT_HUB).read_text(encoding="utf-8"))


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

    def test_minutes_in_raise_mode_refuse_to_file(self):
        # Every link reads as confidential while the config is broken, so "raise" would write a
        # `sensitivity: confidential` line into the new note that stays after the config is fixed.
        set_config(self.vault, BROKEN["unknown key"])
        recap = self.vault.parent / "recap.txt"
        recap.write_text("Alex and Sam met about the search relaunch.\n", encoding="utf-8")
        r = run("minutes", str(recap), "--title", "Broken sync", "--vault", str(self.vault), "--date", "2026-09-21")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(".kit/config.yml", r.stderr)
        self.assertFalse((self.vault / "02-meetings/2026-09-21-broken-sync.md").exists())
        with self.assertRaisesRegex(ob.BridgeError, r"\.kit/config\.yml"):
            ob.FsBackend(self.vault).file_minutes("We met.", "Broken sync", "2026-09-21", mode="raise")
        g = bp.Guarded(ob.FsBackend(self.vault), bp.Policy(audit_path=None, hide_confidential=False), self.vault)
        with self.assertRaisesRegex(ob.BridgeError, r"\.kit/config\.yml"):
            g.file_minutes("We met.", "Broken sync", "2026-09-21")
        self.assertFalse((self.vault / "02-meetings/2026-09-21-broken-sync.md").exists())
        # --keep-open writes no label, so it may still file; the predicate hides the note meanwhile.
        r = run("minutes", str(recap), "--title", "Broken sync", "--vault", str(self.vault), "--date", "2026-09-21",
                "--keep-open")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        note = kitlib.parse_note(self.vault / "02-meetings/2026-09-21-broken-sync.md", self.vault)
        self.assertNotEqual(str(note.frontmatter.get("sensitivity", "")).lower(), "confidential")

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
    the text is not a leak); and no field of the planted notes appears anywhere. A subclass runs
    the same checks on another vault (open folders, #126) by overriding the class attributes.
    """

    PLANTED = (*HIDDEN_NOTES, CSV_REL)        # planted files every reader must hide
    PLANTED_MARKERS = MARKERS                 # their fields, none of which may appear anywhere
    ASK_HITS = (NO_FM_REL, LABELLED_REL, HARRIER_REL, RAISED_REL)    # hidden notes the search finds

    def setUp(self):
        super().setUp()
        files = [p for p in self.vault.rglob("*") if p.is_file() and kitlib._visible(p.relative_to(self.vault))]
        self.rels = sorted(p.relative_to(self.vault).as_posix() for p in files)
        self.hidden = {r for r in self.rels if kitlib.is_confidential(self.vault, r)}

    def assert_clean(self, text: str, where: str):
        for rel in self.PLANTED:
            self.assertNotIn(rel, text, f"{rel} in {where}")
        for marker in self.PLANTED_MARKERS:
            self.assertNotIn(marker.lower(), text.lower(), f"{marker!r} in {where}")

    def test_the_fixture_has_what_the_test_needs(self):
        self.assertTrue(set(self.PLANTED) <= self.hidden, self.hidden)
        self.assertNotIn(OPEN_REL, self.hidden)
        self.assertNotIn(CSV_OPEN_REL, self.hidden)
        self.assertTrue({"05-people/alex-example.md", "05-people/sam-example.md"} <= self.hidden)

    def test_the_policy_export(self):
        export = kitagent.policy_export(self.vault)
        folders = kitlib.ConfidentialityPolicy.build(export["confidential_folders"])
        listed = set(export["confidential_files"])
        for rel in sorted(self.hidden):
            self.assertTrue(rel in listed or folders.in_folder(rel), f"{rel} missing from the export")
        for rel in (OPEN_REL, CSV_OPEN_REL):
            self.assertFalse(rel in listed or folders.in_folder(rel), f"{rel} exported")

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
        self.assertTrue(set(self.ASK_HITS) <= hits, "the search must find the hidden notes")
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


# ---------------------------------------------------------------- open folders (#126)

# The floor fixture again, turned around: three open folders (one spelt in another case than on
# disk), and inside them the same confidential folder, path rules and label as above. Everything
# outside them is confidential by default, like the planted note below, which has no label at all.
OPEN_CONFIG = CONFIG + '  open_folders: ["04-Areas", "07-knowledge", "08-RESOURCES"]\n'
CONDOR_REL = "03-projects/condor-brief.md"
CONDOR = "# Condor brief\n\nCONDOR-BODY kestrel board pack terms.\n\n- [ ] CONDOR-TASK send the kestrel brief — me, due 2026-09-20\n"
OPEN_MARKERS = (*MARKERS, "condor-brief", "Condor brief", "CONDOR-BODY", "CONDOR-TASK")


def open_vault() -> Path:
    v = floor_vault()
    set_config(v, OPEN_CONFIG)
    write(v, CONDOR_REL, CONDOR)
    return v


class _OpenVault(unittest.TestCase):
    def setUp(self):
        self.vault = open_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)


class OpenFoldersDecide(_OpenVault):
    def test_outside_every_open_folder_is_confidential(self):
        for rel in (CONDOR_REL, CONDOR_REL.removesuffix(".md"), "05-people/alex-example.md", "index.md", "log.md",
                    "Inbox.md", ".kit/config.yml", "00-home/no-such-note.md", "", "04-areas-old/x.md", "../x.md"):
            self.assertIs(kitlib.confidentiality(self.vault, rel), True, repr(rel))
        self.assertIs(kitlib.own_confidentiality(self.vault, CONDOR_REL), False, "the note itself says nothing")

    def test_inside_an_open_folder_the_note_decides(self):
        for rel in (OPEN_REL, CSV_OPEN_REL, "07-knowledge/hybrid-search.md", "04-AREAS/plover", "./04-areas/x/../plover.md"):
            self.assertIs(kitlib.confidentiality(self.vault, rel), False, rel)

    def test_a_label_inside_an_open_folder_still_hides(self):
        self.assertIs(kitlib.confidentiality(self.vault, RAISED_REL), True)
        with self.assertRaises(bp.PolicyError):
            guarded(self.vault).read_note(RAISED_REL)

    def test_a_confidential_folder_and_a_path_rule_inside_an_open_folder_still_hide(self):
        for rel in (NO_FM_REL, LABELLED_REL, HARRIER_REL, CSV_REL):
            self.assertIs(kitlib.confidentiality(self.vault, rel), True, rel)

    def test_the_trash_is_outside_every_open_folder(self):
        # The trash does not record where a file came from: `.trash/04-areas/` may as well be a
        # deleted `clients/x/04-areas/`. Judged over both forms, as floors are, it stays hidden,
        # and an agent's sandbox and hook refuse `.trash/` too.
        for rel in (".trash/04-areas/old.md", ".trash/03-projects/old.md", ".trash/04-areas/board/old.md"):
            write(self.vault, rel, "# Old\n")
            self.assertIs(kitlib.confidentiality(self.vault, rel), True, rel)
        pol = kitlib.confidentiality_policy(self.vault)
        self.assertTrue(pol.outside_open(".trash/04-areas/plover.md"))
        self.assertTrue(pol.outside_open(".Trash/07-knowledge/x"))
        trash_open = kitlib.ConfidentialityPolicy.build(open_folders=[".trash"])
        self.assertTrue(trash_open.outside_open(".trash/03-projects/x.md"), "the origin is judged too")

    def test_the_bridge_shows_everything_with_show_confidential(self):
        g = bp.Guarded(ob.FsBackend(self.vault), bp.Policy(audit_path=None, hide_confidential=False), self.vault)
        self.assertIn("CONDOR-BODY", g.read_note(CONDOR_REL))
        with self.assertRaises(bp.PolicyError):
            guarded(self.vault).read_note(CONDOR_REL)

    def test_the_bridge_writes_only_inside_open_folders(self):
        g = bp.Guarded(ob.FsBackend(self.vault), bp.Policy(audit_path=None), self.vault)
        with self.assertRaisesRegex(bp.PolicyError, "confidential"):
            g.create_note("00-inbox/new-idea", "x")
        g.create_note("04-areas/new-idea", "x")
        self.assertTrue((self.vault / "04-areas/new-idea.md").exists())

    def test_minutes_link_no_person_outside_the_open_folders(self):
        set_config(self.vault, OPEN_CONFIG.replace('["04-Areas"', '["02-meetings", "04-Areas"'))
        res = guarded(self.vault).file_minutes("Alex Example joined.\nDecision: ship it", "Open sync", "2026-09-21",
                                               people="Sam Example")
        self.assertNotIn("05-people", json.dumps(res))
        body = (self.vault / res["path"]).read_text(encoding="utf-8")
        self.assertNotIn("05-people", body)
        self.assertNotIn("alex-example", body)

    def test_hub_rows_outside_the_open_folders_are_removed(self):
        self.assertIn("hub_confidential_row",
                      [c.code for c in kitrecon.reconcile(self.vault) if c.file == kitrecon.PROJECT_HUB])

    def test_validate_warns_about_an_open_folder_that_does_not_exist(self):
        set_config(self.vault, OPEN_CONFIG.replace('"07-knowledge"', '"07-knowlege"'))
        r = run("validate", "--vault", str(self.vault))
        self.assertIn("warn   .kit/config.yml: open folder '07-knowlege' does not exist", r.stdout)
        self.assertNotIn("open folder '04-Areas'", r.stdout, "matching ignores case")
        self.assertNotIn("open folder '08-RESOURCES'", r.stdout)
        self.assertIn("== Confidentiality: floors, path rules and open folders", r.stdout)

    def test_validate_does_not_warn_about_labels_outside_the_open_folders(self):
        r = run("validate", "--vault", str(self.vault))
        self.assertIn(f"warn   {LABELLED_REL}: `sensitivity: internal` inside a folder or path", r.stdout)
        self.assertNotRegex(r.stdout, r"warn   0[0-35-9][^:]*: `sensitivity:", "the default is no declared floor")


class OpenFoldersAndLinks(unittest.TestCase):
    """A reference is cleared only when it is inside an open folder as written and wherever it lands."""

    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)
        set_config(self.vault, 'confidentiality:\n  open_folders: ["04-areas"]\n')
        write(self.vault, "05-people/alice.md", "# Alice\n\nALICE-BODY\n")

    def test_a_link_from_an_open_folder_to_a_note_outside_it(self):
        symlink(self, self.vault / "04-areas/alice.md", Path("../05-people/alice.md"))
        for ref in ("04-areas/alice.md", "04-areas/alice"):
            self.assertIs(kitlib.confidentiality(self.vault, ref), True, ref)
        with self.assertRaises(bp.PolicyError):
            guarded(self.vault).read_note("04-areas/alice")
        self.assertNotIn("ALICE-BODY", json.dumps(guarded(self.vault).search("ALICE-BODY")))

    def test_a_linked_folder_from_an_open_folder_to_outside_it(self):
        symlink(self, self.vault / "04-areas/people", Path("../05-people"))
        self.assertIs(kitlib.confidentiality(self.vault, "04-areas/people/alice.md"), True)

    def test_a_link_from_an_open_folder_out_of_the_vault(self):
        outside = self.vault.parent / "elsewhere"
        write(outside, "bob.md", "# Bob\n")
        symlink(self, self.vault / "04-areas/ext", outside)
        self.assertIs(kitlib.confidentiality(self.vault, "04-areas/ext/bob.md"), True, "not proven inside an open folder")
        symlink(self, self.vault / "04-areas/bob.md", outside / "bob.md")
        self.assertIs(kitlib.confidentiality(self.vault, "04-areas/bob"), True)

    def test_a_link_into_an_open_folder_from_outside_is_judged_by_its_own_path_too(self):
        symlink(self, self.vault / "03-projects/platform.md", Path("../04-areas/platform-engineering.md"))
        self.assertIs(kitlib.confidentiality(self.vault, "04-areas/platform-engineering.md"), False)
        self.assertIs(kitlib.confidentiality(self.vault, "03-projects/platform.md"), True)

    def test_a_link_inside_the_open_folder(self):
        symlink(self, self.vault / "04-areas/pe.md", Path("platform-engineering.md"))
        self.assertIs(kitlib.confidentiality(self.vault, "04-areas/pe.md"), False)


class OpenFolderConfigErrors(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)

    def error(self, text: str) -> str:
        set_config(self.vault, text)
        with self.assertRaises(kitlib.PolicyConfigError) as ctx:
            kitlib.confidentiality_policy(self.vault)
        return str(ctx.exception)

    def test_the_messages_say_what_is_wrong(self):
        self.assertIn("open_folders is empty", self.error(BROKEN["open folders empty"]))
        self.assertIn("open_folders is empty", self.error(BROKEN["open folders null"] + "#"))
        for name in ("open folder dot", "open folder dot slash", "open folder root"):
            self.assertIn("names the vault root", self.error(BROKEN[name] + "#" * len(name)), name)
        self.assertIn("is a pattern", self.error(BROKEN["open folder glob"]))
        self.assertIn("must be relative to the vault", self.error(BROKEN["open folder absolute"]))
        self.assertIn("must not use `..`", self.error(BROKEN["open folder escapes"]))
        self.assertIn("at or under the confidential folder '05-people'",
                      self.error(BROKEN["open folder is a confidential folder"]))
        self.assertIn("'05-people/team' lies at or under the confidential folder '05-people'",
                      self.error(BROKEN["open folder under a confidential folder"]))
        self.assertIn("lists '04-Areas' twice", self.error(BROKEN["open folder twice"]))
        for name in ("open folders at the top level", "open folders at the top level, capitalised"):
            self.assertIn("look like the confidentiality block", self.error(BROKEN[name]), name)

    def test_a_confidential_folder_inside_an_open_one_is_fine(self):
        set_config(self.vault, 'confidentiality:\n  confidential_folders: ["04-areas/hr"]\n  open_folders: ["04-areas"]\n')
        pol = kitlib.confidentiality_policy(self.vault)
        self.assertEqual(pol.open_folders, ("04-areas",))
        self.assertEqual(pol.declared_open_folders, ("04-areas",))
        self.assertIs(kitlib.confidentiality(self.vault, "04-areas/hr/x.md"), True)

    def test_the_declared_spelling_is_kept(self):
        set_config(self.vault, 'confidentiality:\n  open_folders: ["./04-Areas/", "07-knowledge\\\\notes"]\n')
        pol = kitlib.confidentiality_policy(self.vault)
        self.assertEqual(pol.declared_open_folders, ("04-Areas", "07-knowledge/notes"))
        self.assertEqual(pol.open_folders, ("04-areas", "07-knowledge/notes"))


class OpenFoldersChangeTheSignature(unittest.TestCase):
    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)

    def test_declaring_and_changing_open_folders_make_the_graph_stale(self):
        def digest() -> str:
            return kitgraph.signature_digest(kitgraph.note_set_signature(self.vault))

        seen = [digest()]
        for text in ('confidentiality:\n  open_folders: ["04-areas"]\n',
                     'confidentiality:\n  open_folders: ["04-areas", "07-knowledge"]\n'):
            set_config(self.vault, text)
            seen.append(digest())
        self.assertEqual(len(set(seen)), 3, "each config state has its own signature")

    def test_the_bridge_graph_cache_follows_open_folders(self):
        g = guarded(self.vault)
        rel = "03-projects/search-relaunch.md"
        self.assertIn(rel, g.graph().nodes)
        set_config(self.vault, 'confidentiality:\n  open_folders: ["04-areas"]\n')
        self.assertNotIn(rel, g.graph().nodes)


class OpenParity(Parity):
    """`Parity` on the open-folders vault: the default-confidential note joins the planted ones."""

    PLANTED = (*HIDDEN_NOTES, CSV_REL, CONDOR_REL)
    PLANTED_MARKERS = OPEN_MARKERS
    ASK_HITS = (NO_FM_REL, LABELLED_REL, HARRIER_REL, RAISED_REL, CONDOR_REL)

    def setUp(self):
        self.vault = open_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, True)
        files = [p for p in self.vault.rglob("*") if p.is_file() and kitlib._visible(p.relative_to(self.vault))]
        self.rels = sorted(p.relative_to(self.vault).as_posix() for p in files)
        self.hidden = {r for r in self.rels if kitlib.is_confidential(self.vault, r)}

    def test_everything_outside_the_open_folders_is_hidden(self):
        for rel in self.rels:
            if not rel.lower().startswith(("04-areas/", "07-knowledge/", "08-resources/")):
                self.assertIn(rel, self.hidden)
        self.assertIn("index.md", self.hidden)
        self.assertIn("07-knowledge/hybrid-search.md", set(self.rels) - self.hidden)


if __name__ == "__main__":
    unittest.main()
