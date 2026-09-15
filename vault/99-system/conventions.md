---
type: system
title: Conventions
description: The single rulebook — folders, properties (OKF-aligned), states, naming, tasks, links.
tags: [system]
sensitivity: internal
---

# Conventions

One page. If a rule is not here, it is not a rule.

## Folders

| Folder | What goes there |
|---|---|
| `00-home` | Home, dashboard, inbox, priorities, open actions. Maintained, not filed into. |
| `01-journal` | `daily/YYYY/MM/` (auto, `Ctrl/Cmd + D`), `weekly/`, `quarterly/` |
| `02-meetings` | One note per meeting worth remembering. `YYYY-MM-DD-topic.md` |
| `03-projects` | One note per project; `projects.md` holds the one-liners |
| `04-areas` | Standing responsibilities without an end date |
| `05-people` | One note per person you work with regularly; the 1:1 lives inside |
| `06-decisions` | ADR-shaped decision records; `decision-log.md` holds the register |
| `07-knowledge` | Concepts, specs, runbooks — evergreen, reviewed, sourced |
| `08-resources` | `attachments/` (drag-and-drop target), `clippings/`, reading notes |
| `09-archive` | Anything inactive. Excluded from dashboard views and search |
| `90-templates` | Templates (`Alt + T` inserts) |
| `99-system` | This rulebook, AI workflows, getting started |

## Properties

Frontmatter drives the dashboard and makes the vault an [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) bundle that agents can read without a custom SDK. `type` is required on every note and `owner` on projects — nothing else. Fill `description` on everything.

| Property | On | Values / format |
|---|---|---|
| `type` | every note | `daily` `weekly` `quarterly` `meeting` `project` `decision` `person` `team` `area` `concept` `runbook` `spec` `retro` `resource` `system` |
| `title` | every note | display name (templates fill it) |
| `description` | every note | **one sentence.** Powers every view, snippet and AI prompt |
| `tags` | every note | list, lower-case |
| `date` | dated notes | `YYYY-MM-DD` |
| `state` | meetings, decisions, projects, areas | workflow, free text; suggested: `open` `active` `waiting` `on-hold` `done` `decided` `superseded`. The dashboard views use `open`, `waiting`, `on-hold` |
| `status` | knowledge notes | OKF lifecycle: `draft` `stable` `deprecated` (absent = stable) |
| `health` | projects | `green` `yellow` `red` |
| `owner` | projects (**required**), areas, decisions, specs | link to a person note, or `me` for yourself |
| `milestone`, `due` | projects | text · `YYYY-MM-DD` |
| `project`, `area`, `people` | where relevant | markdown links or names |
| `meeting_type` | meetings | `project` `leadership` `1-1` `review` `external` |
| `role`, `team`, `next_1_1` | people | text · text · `YYYY-MM-DD` |
| `sensitivity` | every note | `personal` `internal` `confidential` |
| `generated` | any | `{ by: human:<you>, at: <ISO-8601 with offset> }` — who wrote it, when |
| `verified` | knowledge, decisions | `{ by: human:<you>, at: <ISO-8601> }` — who confirmed it (list allowed) |
| `stale_after` | knowledge | ISO-8601 instant after which the note needs re-checking |
| `sources` | knowledge | list of `{ id, resource, title, last_modified }`; cite in the body as `[^id]` |

Why two status-like fields: `state` is *your workflow*; `status` is the OKF *lifecycle* every consumer understands. They never conflict because they answer different questions. `state` is not a state machine — no transitions are enforced anywhere; a formal workflow layer is deliberately out of scope for the template.

## Naming

- Files: **kebab-case, no spaces** (`2026-09-11-search-relaunch-sync.md`). Links stay clean, shells stay happy, agents stay accurate. The `title` property carries the pretty name.
- Dated notes start with `YYYY-MM-DD`. People: `firstname-lastname.md`. Decisions: `YYYY-MM-DD-what-was-decided.md`.
- Daily notes are created by Obsidian — never by hand.

## Tasks

`- [ ] verb — owner, due YYYY-MM-DD`, written in the note where the task arose. [Open Actions](../00-home/open-actions.md) aggregates them live; the weekly review sweeps them. `state: waiting` on a note marks *the note* as something you chase.

## Links

Markdown links, relative paths: `[Hybrid search](../07-knowledge/hybrid-search.md)`. Obsidian writes them this way for you (Settings → Files & links) and every other tool — qmd, agents, GitHub, OKF consumers — understands them. Link generously: backlinks build the dossiers.

Templates are the one exception: their bodies carry no relative links. A path written in `90-templates/` cannot also be correct from the folder the new note lands in, so every note made from such a template would ship a broken link. Templates name destinations as vault-relative paths in backticks instead.

## Examples

Notes marked **(example)** are fictional. Replace them in week one, then delete them; the dashboard views need nothing else to work.
