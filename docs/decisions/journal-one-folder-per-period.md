---
type: decision
title: The journal keeps one folder per period type, and month notes link rather than copy
description: Day notes nest by year and month, month notes live in `01-journal/monthly/YYYY-MM.md` beside `weekly/` and `quarterly/`, and a month note shows its tasks through live queries instead of copies; revisit if a period-notes plugin becomes core, or if month notes need their own year folders.
tags: [vault, journal, conventions]
sources:
  - { name: "the owner's decision of 2026-10-19: a journal must never flood its folder; kit logic, one folder per period type", credibility: verified-firsthand }
  - { name: "`.obsidian/daily-notes.json` format `[daily]/YYYY/MM/YYYY-MM-DD` and `kitlib.daily_note_path`, at 53043c0", credibility: verified-firsthand }
  - { name: "tests: `test_vault_structure.py` (month path, template queries, views), `test_okf_conformance.py` and `test_validate_exit_code.py` (placement warning)", credibility: verified-firsthand }
verified: true
verified_on: 2026-10-19
stale_after: 2027-04-19
status: active
---

# The journal keeps one folder per period type

## Decision

The journal in `01-journal/` has one folder per period type: `daily/`, `weekly/`, `monthly/`, `quarterly/`. Day notes nest further by year and month (`daily/YYYY/MM/YYYY-MM-DD.md`), as the shipped daily-notes format already files them. Month notes are `monthly/YYYY-MM.md`, made from `90-templates/monthly-review.md`.

A month note wraps a month up. It lists the month's day notes and shows the month's open and done tasks through core-search queries over `daily/YYYY/MM/`. It never copies a task.

`kit.py validate` warns about a day note outside the folder the daily-notes config files it in, and `kitlib.month_note_path` computes a month note's path from the same config as `daily_note_path`.

## Why

**No folder may flood.** A flat daily folder holds 365 files after a year and is no longer browsable, in Obsidian or in a shell. Nesting by year and month keeps every folder small, and a month note gives each month folder one entry point.

**One folder per period type, not month notes inside the month folders.** The kit already keeps weekly and quarterly reviews in folders of their own; month notes follow the same rule rather than starting a second one. A month note inside `daily/YYYY/MM/` would also be listed by its own Days query and swept by every view that means "day notes".

**Link, never copy.** One task lives in one place. A copied task goes stale the moment the original is ticked, and the vault then says two different things about the same work. The live query shows each task where it lives, so ticking it there is enough.

**No plugin.** Bases cannot list task lines; core search can, with `task-todo:` and `task-done:`, which is what Open Actions already uses. The kit ships with no community plugins, and this convention does not change that.

## Consequences

- The template pins its queries to the month in which it is inserted (`{{date:YYYY}}/{{date:MM}}`). A review written after the month has ended needs the month changed in its three paths; the template says so.
- The placement warning is a kit rule, not an OKF one, so it fails only `validate --strict`, like a missing description. Day notes made with `Ctrl/Cmd + D` cannot trigger it; notes filed by hand, by a sync tool or by an agent can.
- When the daily folder is the vault root, the warning is skipped: there a bare `YYYY-MM-DD.md` cannot be told apart from any other dated note.
- A sample month note (`monthly/2026-09.md`, `example: true`) ships with the vault and goes with `kit.py examples remove`.

## Re-check triggers

- Obsidian makes periodic notes (weekly, monthly, quarterly) a core feature with its own folder settings; the kit's folders should then follow its configuration, as day notes follow `daily-notes.json`.
- Month notes accumulate enough that `monthly/` itself needs year folders (after roughly a decade at one note a month).
- Bases gains task-level queries, which would let the Days and task sections share one mechanism.
