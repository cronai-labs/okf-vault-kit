"""Structure of the template vault: folders, Obsidian config, templates, bookmarks, bases, root capture, private folders."""
import json
import re
import shutil
import sys
import unittest

import yaml

import kitgraph
from tests.helpers import ROOT, VAULT, kitlib, temp_vault

# The bridge lives in mcp/, which is on sys.path only once this module puts it there — relying on
# whichever test module imported it first is an ordering the runner is free to change.
sys.path.insert(0, str(ROOT / "mcp"))
import obsidian_bridge as ob

EXPECTED_DIRS = ["00-home", "00-home/bases", "01-journal", "01-journal/daily", "01-journal/weekly", "01-journal/quarterly",
                 "02-meetings", "03-projects", "04-areas", "05-people", "06-decisions", "07-knowledge",
                 "08-resources/attachments", "09-archive", "90-templates", "99-system", ".obsidian"]
EXPECTED_TEMPLATES = ["daily", "weekly-review", "quarterly-review", "meeting", "project", "decision", "person",
                      "team", "area", "concept", "runbook", "spec", "retro"]
DUE_DATE_RE = re.compile(r"(due|decide by) (\d{4}-\d{2}-\d{2})")
# Where a note made from each template is filed (conventions.md "Folders"). A retro has no folder of
# its own, so both plausible homes are exercised.
TEMPLATE_DESTINATIONS = {
    "daily": ["01-journal/daily/2026/09"], "weekly-review": ["01-journal/weekly"],
    "quarterly-review": ["01-journal/quarterly"], "meeting": ["02-meetings"],
    "project": ["03-projects"], "area": ["04-areas"], "person": ["05-people"], "team": ["05-people"],
    "decision": ["06-decisions"], "concept": ["07-knowledge"], "runbook": ["07-knowledge"],
    "spec": ["07-knowledge"], "retro": ["01-journal/quarterly", "03-projects"],
}


def _section(body: str, heading: str) -> str:
    """The lines under `## <heading>`, up to the next heading of the same level."""
    out, inside = [], False
    for line in body.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == heading
        elif inside:
            out.append(line)
    return "\n".join(out)


class VaultStructure(unittest.TestCase):
    def test_folders_exist(self):
        for d in EXPECTED_DIRS:
            self.assertTrue((VAULT / d).is_dir(), f"missing folder {d}")

    def test_obsidian_configs_are_valid_json(self):
        for name in ["app.json", "core-plugins.json", "daily-notes.json", "templates.json", "hotkeys.json", "bookmarks.json", "types.json", "community-plugins.json"]:
            data = json.loads((VAULT / ".obsidian" / name).read_text(encoding="utf-8"))
            self.assertIsNotNone(data, name)

    def test_daily_notes_config(self):
        cfg = kitlib.obsidian_config(VAULT, "daily-notes.json")
        self.assertEqual(cfg["format"], "[daily]/YYYY/MM/YYYY-MM-DD")
        self.assertEqual(cfg["folder"], "01-journal")
        self.assertTrue((VAULT / cfg["template"]).exists(), "daily template referenced by config must exist")

    def test_daily_path_computation_matches_example_note(self):
        import datetime as dt
        p = kitlib.daily_note_path(VAULT, dt.date(2026, 9, 14))
        self.assertEqual(p, VAULT / "01-journal/daily/2026/09/2026-09-14.md")
        self.assertTrue(p.exists())

    def test_templates_present_and_typed(self):
        for t in EXPECTED_TEMPLATES:
            path = VAULT / "90-templates" / f"{t}.md"
            self.assertTrue(path.exists(), f"missing template {t}")
            note = kitlib.parse_note(path, VAULT)
            self.assertIsNone(note.fm_error, f"{t}: {note.fm_error}")
            self.assertIn("type", note.frontmatter or {}, f"{t} needs a type")
            self.assertIn("{{title}}", path.read_text(encoding="utf-8"))

    def test_every_template_renders_into_a_vault_that_validates(self):
        """A note made from a shipped template must pass `kit.py validate` where it is actually filed.

        Folder-relative body links cannot satisfy this: a path that resolves from 90-templates/ does
        not resolve from the destination folder, which is why templates name paths instead of linking.
        """
        self.assertEqual(sorted(TEMPLATE_DESTINATIONS), sorted(EXPECTED_TEMPLATES), "every template needs a destination")
        v = temp_vault()
        try:
            for name, folders in TEMPLATE_DESTINATIONS.items():
                text = (VAULT / "90-templates" / f"{name}.md").read_text(encoding="utf-8")
                for folder in folders:
                    dest = v / folder / f"zz-{name}-{folder.replace('/', '-')}.md"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(kitlib.render_template(text, f"Rendered {name}"), encoding="utf-8", newline="\n")
            errors = (kitlib.validate_okf(v).errors + kitlib.check_links(v).errors
                      + kitgraph.ontology_report(kitgraph.build_graph(v)).errors)
            self.assertEqual(errors, [], "\n".join(errors))
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_templates_carry_no_relative_body_links(self):
        for name in EXPECTED_TEMPLATES:
            path = VAULT / "90-templates" / f"{name}.md"
            body = kitlib._strip_code(kitlib.parse_note(path, VAULT).body)
            targets = [m.group(1) for m in kitlib.MD_LINK_RE.finditer(body)]
            local = [x for x in targets if not x.startswith(("http://", "https://", "mailto:", "#"))]
            self.assertEqual(local, [], f"{name}.md: relative link(s) break once the note is filed elsewhere")

    def test_required_ontology_properties_are_prefilled_in_templates(self):
        onto = kitgraph.load_ontology(VAULT)
        for name in EXPECTED_TEMPLATES:
            note = kitlib.parse_note(VAULT / "90-templates" / f"{name}.md", VAULT)
            fm = note.frontmatter or {}
            for prop, spec in onto.props(str(fm.get("type"))).items():
                if spec.required:
                    self.assertNotIn(fm.get(prop), (None, "", []), f"{name}.md: `{prop}` is required and must ship filled")

    def test_templates_folder_configured(self):
        self.assertEqual(kitlib.obsidian_config(VAULT, "templates.json")["folder"], "90-templates")

    def test_markdown_links_and_relative_format_enabled(self):
        app = kitlib.obsidian_config(VAULT, "app.json")
        self.assertTrue(app["useMarkdownLinks"], "OKF consumers read markdown links, not wikilinks")
        self.assertEqual(app["newLinkFormat"], "relative")
        self.assertIn("09-archive/", app["userIgnoreFilters"])

    def test_core_plugins_needed_are_enabled(self):
        core = json.loads((VAULT / ".obsidian/core-plugins.json").read_text(encoding="utf-8"))
        for p in ["bases", "properties", "daily-notes", "templates", "bookmarks", "file-recovery"]:
            self.assertTrue(core.get(p), f"core plugin {p} must be enabled")

    def test_no_community_plugins_shipped(self):
        self.assertEqual(json.loads((VAULT / ".obsidian/community-plugins.json").read_text(encoding="utf-8")), [])

    def test_bookmarks_point_to_existing_files(self):
        bm = json.loads((VAULT / ".obsidian/bookmarks.json").read_text(encoding="utf-8"))
        for item in bm["items"]:
            self.assertTrue((VAULT / item["path"]).exists(), item["path"])

    def test_bases_parse_and_have_unique_named_views(self):
        for base in (VAULT / "00-home/bases").glob("*.base"):
            data = yaml.safe_load(base.read_text(encoding="utf-8"))
            names = [v["name"] for v in data["views"]]
            self.assertEqual(len(names), len(set(names)), f"duplicate view names in {base.name}")
            declared = set(data.get("properties", {}))
            for v in data["views"]:
                for col in v.get("order", []):
                    self.assertTrue(col in declared or col.startswith("file."), f"{base.name}/{v['name']}: column {col} lacks a displayName")
                for s in v.get("sort", []):
                    self.assertIn(s.get("direction"), ("ASC", "DESC"))

    def test_dashboard_embeds_every_overview_view(self):
        dash = (VAULT / "00-home/dashboard.md").read_text(encoding="utf-8")
        data = yaml.safe_load((VAULT / "00-home/bases/overview.base").read_text(encoding="utf-8"))
        missing = [v["name"] for v in data["views"] if f"#{v['name']}]]" not in dash and v["name"] not in ("Decisions",)]
        self.assertEqual(missing, [], "dashboard should surface every overview view (Decisions lives in the decision log)")

    def test_ontology_and_context_ship_with_the_vault(self):
        import yaml
        onto = yaml.safe_load((VAULT / "99-system/ontology.yml").read_text(encoding="utf-8"))
        self.assertIn("project", onto["classes"]); self.assertIn("supersedes", onto["edges"])
        ctx = json.loads((VAULT / "context.jsonld").read_text(encoding="utf-8"))["@context"]
        self.assertEqual(ctx["title"], "dcterms:title"); self.assertEqual(ctx["Person"], "schema:Person")
        for term in ("owner", "people", "project", "generated_by", "cites", "links_to"):
            self.assertIn(term, ctx, term)

    def test_sample_due_dates_fall_on_weekdays(self):
        """The examples teach the `- [ ] verb, owner, due YYYY-MM-DD` convention, so their own dates must hold up."""
        import datetime as dt
        bad = []
        for note in kitlib.load_vault(VAULT):
            for m in DUE_DATE_RE.finditer(note.body):
                day = dt.date.fromisoformat(m.group(2))
                if day.weekday() >= 5:
                    bad.append(f"{note.rel}: {m.group(0)} is a {day.strftime('%A')}")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_sample_open_decision_is_traceable_to_an_open_action(self):
        """The briefing pack must not list as open what the vault already recorded as decided."""
        section = _section(kitlib.parse_note(VAULT / "00-home/priorities.md", VAULT).body, "Open decisions")
        dates = DUE_DATE_RE.findall(section)
        self.assertTrue(dates, "the sample should show at least one dated open decision")
        open_tasks = "\n".join(l for n in kitlib.load_vault(VAULT) for l in n.body.splitlines()
                               if l.lstrip().startswith("- [ ]") and not n.rel.startswith("90-templates/"))
        for _, date in dates:
            self.assertIn(date, open_tasks, f"priorities.md waits on {date}, but no open action anywhere carries it")

    def test_shipped_ontology_matches_the_default_copy(self):
        """config/ontology.yml is the fallback kitgraph loads for a vault without one: same bytes, no drift."""
        self.assertEqual((ROOT / "config/ontology.yml").read_bytes(),
                         (VAULT / "99-system/ontology.yml").read_bytes())

    def test_ontology_never_tolerates_a_status_kitlib_rejects(self):
        """One verdict per value: the graph layer must not normalise what `validate` hard-errors on."""
        onto = kitgraph.load_ontology(VAULT)
        for value in (onto.value_aliases.get("status") or {}):
            self.assertIn(value, kitlib.LIFECYCLE, f"`status: {value}` is aliased but validate rejects it")
        self.assertEqual(set(onto.props("concept")["status"].values), kitlib.LIFECYCLE)

    def test_no_placeholder_actor_outside_templates(self):
        # examples carry human:me deliberately until `kit init --actor` runs; this asserts they are consistent
        for note in kitlib.load_vault(VAULT):
            fm = note.frontmatter or {}
            gen = fm.get("generated")
            if isinstance(gen, dict):
                self.assertTrue(str(gen.get("by", "")).startswith("human:"), note.rel)


class RootCaptureAndPrivateFolders(unittest.TestCase):
    """A raw `Inbox.md` at the root is allowed; `_`-prefixed folders are invisible to the kit."""

    def setUp(self):
        self.vault = temp_vault()

    def tearDown(self):
        shutil.rmtree(self.vault.parent, ignore_errors=True)

    def test_template_ships_a_raw_root_inbox(self):
        inbox = VAULT / "Inbox.md"
        self.assertTrue(inbox.exists(), "the template vault must carry a root Inbox.md")
        self.assertIsNone(kitlib.parse_note(inbox, VAULT).frontmatter, "the inbox is raw capture: no frontmatter")

    def test_raw_root_inbox_validates_and_stays_out_of_the_index(self):
        (self.vault / "Inbox.md").write_text("# Inbox\n\n- ring the dentist\n", encoding="utf-8", newline="\n")
        rep = kitlib.validate_okf(self.vault)
        self.assertEqual(rep.errors, [], rep.render())
        self.assertEqual(rep.warnings, [], rep.render())
        self.assertNotIn("Inbox.md", kitlib.build_index(self.vault))

    def test_an_inbox_below_the_root_is_an_ordinary_note(self):
        (self.vault / "07-knowledge/Inbox.md").write_text("# not the root inbox\n", encoding="utf-8", newline="\n")
        errors = kitlib.validate_okf(self.vault).errors
        self.assertTrue(any(e.startswith("07-knowledge/Inbox.md: missing YAML frontmatter") for e in errors), errors)

    def test_private_folders_are_invisible_to_validate_links_index_graph_and_bridge(self):
        for rel in ("_private/x.md", "03-projects/_scratch/y.md"):
            path = self.vault / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\ntype: concept\ndescription: kept private\n---\n[[nowhere]]\n",
                            encoding="utf-8", newline="\n")

        def private(names):
            return [n for n in names if any(part.startswith("_") for part in n.split("/")[:-1])]

        walked = [p.relative_to(self.vault).as_posix() for p in kitlib.iter_markdown(self.vault)]
        self.assertEqual(private(walked), [])
        self.assertEqual(kitlib.validate_okf(self.vault).errors, [])
        self.assertEqual(kitlib.check_links(self.vault).errors, [], "a private note's broken wikilink must not be read")
        index = kitlib.build_index(self.vault)
        self.assertNotIn("_private", index); self.assertNotIn("_scratch", index)
        self.assertEqual(private(kitgraph.build_graph(self.vault).nodes), [])
        bridge = ob.FsBackend(self.vault)
        self.assertEqual(private(bridge.list_files()), [])
        self.assertEqual(private([h["file"] for h in bridge.search("kept private")]), [])
