---
type: system
title: Vault README
description: What this vault is, how to open it, and where to start.
tags: [system]
sensitivity: internal
---

# OKF Vault — an Obsidian second brain that machines can read

A working memory for people who run things: managers, project and product managers, engineers. Plain Markdown files with YAML frontmatter, organized so that **you** can navigate them in Obsidian, **your local LLM** can search and reason over them, and **any agent** that speaks the [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) can consume them without a custom SDK.

## Open it (3 minutes)

1. Copy this folder wherever your notes live (a synced folder is fine; keep it on one machine at a time).
2. Obsidian → **Open folder as vault** → select the folder → trust it.
3. Open [Home](00-home/home.md), press `Ctrl/Cmd + D` — today's daily note appears under `01-journal/daily/2026/09/`.

The kit that ships this vault — search tooling (qmd), the Obsidian CLI, a local LLM with tool access, tests — is documented in the repository's `docs/` folder. Start with `docs/quickstart.md`.

## What's inside

| Folder | Purpose |
|---|---|
| `00-home` | Home, live dashboard, inbox, priorities, open actions |
| `01-journal` | Daily notes (auto-filed by year/month), weekly and quarterly reviews |
| `02-meetings` | One note per meeting worth remembering |
| `03-projects` | One note per project or initiative |
| `04-areas` | Standing responsibilities (a team, a product, a platform, a budget) |
| `05-people` | One note per person you work with regularly, with the rolling 1:1 |
| `06-decisions` | Decision records — ADR-shaped, usable by managers and engineers alike |
| `07-knowledge` | Evergreen concepts, specs, runbooks — the wiki layer agents love |
| `08-resources` | Attachments, clippings, reading notes |
| `09-archive` | Everything inactive (excluded from views and search) |
| `90-templates` · `99-system` | Note templates · conventions, AI workflows, getting started |

Notes marked **(example)** show the intended patterns. Replace them in week one, then delete them.
