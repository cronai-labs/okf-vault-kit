# Vault guide — structure, conventions, and the three lanes

The vault is a directory of Markdown files with YAML frontmatter. Obsidian renders it; `qmd` indexes it; a local model reads and writes it through MCP; any OKF consumer can parse it. Nothing in it depends on a plugin.

## Design decisions (and why)

| Decision | Why |
|---|---|
| Numbered, **kebab-case** folders and file names, no spaces | deterministic sort order in every tool; links without `%20`; shell- and agent-friendly. The `title` property carries the display name |
| **Markdown links, relative paths** (`useMarkdownLinks: true`, `newLinkFormat: relative`) | OKF §6, GitHub, qmd, and agents all understand them; wikilinks are Obsidian-only. Base embeds still use `![[file.base#View]]` |
| Only `type` is required; `description` is expected on everything | OKF conformance is cheap; one good sentence per note powers the dashboard and every AI prompt |
| `state` for workflow, `status` for OKF lifecycle | they answer different questions ("what am I doing with this" vs. "can an agent trust this"); both stay valid. `state` is a plain property with suggested words — the kit deliberately ships no formal workflow/state machine |
| Daily notes in `01-journal/daily/YYYY/MM/` | a year of notes stays browsable; the path is computed, never typed |
| Templates via the core Templates plugin, `Alt + T` | zero plugins; `{{date:…}}` formats give ISO-8601 timestamps with offset for `generated.at` |
| Example notes marked **(example)** | show the patterns; the tests use them as fixtures; remove them in week one with `kit.py examples remove` (they cross-link on purpose, so deleting by hand breaks the vault) |

## Folders

```text
00-home/       home · dashboard · inbox · priorities · open-actions · bases/*.base
01-journal/    daily/YYYY/MM/ · weekly/ · quarterly/
02-meetings/   YYYY-MM-DD-topic.md
03-projects/   projects.md (one-liners) · one note per project
04-areas/      standing responsibilities
05-people/     one note per person, 1:1 inside
06-decisions/  decision-log.md (register) · YYYY-MM-DD-what-was-decided.md
07-knowledge/  concepts · specs · runbooks — the OKF concept heartland
08-resources/  attachments/ · clippings/
09-archive/    inactive; ignored by views and (configure it) by qmd
90-templates/  13 templates
99-system/     conventions · ai-workflows · getting-started
index.md       generated OKF index (kit.py index)
log.md         OKF update log (kit.py log)
```

## Properties

The complete table is in the vault: `99-system/conventions.md`. The short version:

- **every note**: `type`, `title`, `description`, `tags`, `sensitivity`
- **workflow**: `state` ∈ open · active · waiting · on-hold · done · decided · superseded; `health` ∈ green · yellow · red; `owner`, `milestone`, `due`
- **OKF trust & lifecycle** (mostly on knowledge and decisions): `generated {by, at}`, `verified {by, at}` (list allowed), `status` ∈ draft · stable · deprecated, `stale_after`, `sources [{id, resource, title, last_modified}]` with `[^id]` footnotes in the body
- **people**: `role`, `team`, `next_1_1`; **meetings**: `date`, `meeting_type`, `project`, `people`

`kit.py validate` enforces the OKF rules and warns about missing descriptions; the Obsidian property panel shows nested `generated`/`verified` as raw text — that is expected.

## The dashboard

`00-home/dashboard.md` embeds views from `00-home/bases/overview.base`, `people.base`, `journal.base`: Attention, Projects, Open decisions, Waiting, Recent meetings, Knowledge, Needs review (drafts + past `stale_after`), Reviews, Recent dailies, Recently touched, People, Teams. Views are filters over properties — no maintenance. Add a view by copying a block in the `.base` file; the tests check that every view name embedded in a page exists.

## Three lanes, one vault

**Manager.** Daily note → meeting notes → person notes with the rolling 1:1 → weekly review that rewrites `priorities.md`. Dashboard sections: Attention, Waiting, People. Briefing pack for any AI: `priorities.md`, `projects.md`, `decision-log.md`.

**Project / product manager.** Project notes with `milestone`/`due`/`health`, a `spec` per initiative, decisions as they happen, a `retro` per milestone. Sections: Projects, Open decisions, Knowledge. The `projects.md` one-liners are the status report.

**Engineer.** `concept` notes for things worth explaining once, `runbook` notes for things that page you, ADR-shaped `decision` notes (context → options → decision → consequences), the daily note as a lab log. Sections: Knowledge, Needs review, Recently touched. `verified` + `stale_after` keep the wiki honest; `kit.py log` keeps the OKF history.

The lanes are documented on the vault's Home page; nothing structural differs between them.

## Rhythm

Daily (5 min) · after key meetings (5 min) · Friday weekly review (25 min, the template walks you through inbox, actions, projects, decisions, knowledge upkeep, priorities) · quarterly. The `weekly-review` template is the maintenance contract; if it is kept, the vault stays trustworthy for both humans and agents.

## Sync and safety

- One machine at a time on synced folders (OneDrive, iCloud, Nextcloud); conflict copies appear as `… (conflicted copy).md`.
- Git works well for engineers (`.obsidian/workspace.json` in `.gitignore`); OKF explicitly recommends git as the bundle distribution form.
- `sensitivity: confidential` notes (people) should never be attached to a cloud model; the local model only sees what you let its tools read.
- Obsidian's *File recovery* core plugin keeps snapshots; `09-archive` is excluded from views, and the qmd example config ignores it.
