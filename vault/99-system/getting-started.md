---
type: system
title: Getting Started
description: Ten minutes from empty folder to working loop — setup, first notes, the weekly rhythm, and where the deeper docs are.
tags: [system]
sensitivity: internal
---

# Getting Started

## 1. Open (3 minutes)

- Obsidian → **Open folder as vault** → this folder → trust it.
- Check Settings → Core plugins: *Daily notes, Templates, Properties, Bases, Bookmarks* are on (they ship enabled).
- `Ctrl/Cmd + D` — today's note lands in `01-journal/daily/2026/09/`. If not: Settings → Daily notes → folder `01-journal`, format `[daily]/YYYY/MM/YYYY-MM-DD`.
- Open [Home](../00-home/home.md), pin the tab; open the [Dashboard](../00-home/dashboard.md) — the example notes should show up in the views.

## 2. Make it yours (week one)

- Replace `human:me` with your own actor id (`human:<your-handle>`) in `90-templates/*` — the kit's `uv run kit.py init --actor human:<handle>` does it for you.
- Create one real project (`Alt + T` → project), one real decision, one person note. Then remove the demonstration notes with `uv run kit.py examples remove --vault .` — it deletes them *and* repairs every hub, list and index entry that referred to them. Add `--dry-run` first to see exactly what it would change.
- Keep the daily note going. Five minutes, not fifty.

## 3. The rhythm

Daily capture · a meeting note when it matters · a decision note when something is decided · Friday review, 25 minutes · quarterly review. The [Home](../00-home/home.md) page has the table.

## 4. Add the machines (when the rhythm holds)

`docs/quickstart.md` in the kit repository installs qmd (local search), the Obsidian CLI and a local LLM with tool access in about fifteen minutes. [AI Workflows](ai-workflows.md) explains what to do with them.

## If something looks broken

- **Dashboard tables empty** → Bases core plugin off, or the note is missing its `type` property.
- **Daily note in the wrong place** → Daily notes settings, see above.
- **Template inserted with `{{date}}` left in** → insert via `Alt + T` or the command palette; placeholders resolve only on insert.
- **Links show `%20`** → the note has spaces in its name; the conventions use kebab-case for a reason.
