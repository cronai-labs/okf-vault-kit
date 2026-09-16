---
type: system
title: Home
description: Entry point — the operating loop, the map of the vault, and the three files that brief an AI.
tags: [system]
sensitivity: internal
---

# Home

## The loop

| When | Do | Where |
|---|---|---|
| Every day | `Ctrl/Cmd + D`, capture what happened, what you decided, what you owe | today's daily note |
| After a meeting that matters | one meeting note: outcomes, decisions, actions | [meeting template](../90-templates/meeting.md) |
| When something is decided | one decision note, one line in the log | [decision template](../90-templates/decision.md) |
| Friday, 25 min | weekly review: empty the inbox, sweep actions, update priorities | [weekly template](../90-templates/weekly-review.md) |
| Quarterly | portfolio, people, strategy | [quarterly template](../90-templates/quarterly-review.md) |

Everything else — the [Dashboard](dashboard.md), search, AI briefings — is a by-product of this loop.

## Pick your lane

- **Manager:** projects, people, decisions, weekly review. Your dashboard sections: *Attention*, *Waiting*, *People*.
- **Project / product manager:** projects with milestones, [specs](../90-templates/spec.md), decisions, [retros](../90-templates/retro.md). Sections: *Projects*, *Open Decisions*, *Knowledge*.
- **Engineer:** [concepts](../90-templates/concept.md), [runbooks](../90-templates/runbook.md), ADR-style decisions, daily log. Sections: *Knowledge*, *Needs Review*, *Recently Touched*.

Same vault, same conventions — the lanes only change which templates you reach for first.

## Map

| I want to… | Go to |
|---|---|
| Capture something quickly | [Inbox](inbox.md) |
| See what needs attention | [Dashboard](dashboard.md) |
| See every open task in one place | [Open Actions](open-actions.md) |
| Brief myself or an AI on what matters now | [Priorities](priorities.md) |
| Look up or record a decision | [Decision Log](../06-decisions/decision-log.md) |
| Understand the conventions | [Conventions](../99-system/conventions.md) |
| Use the local LLM and search tools | [AI Workflows](../99-system/ai-workflows.md) |
| Set up tooling, plugins, tests | `docs/` in the kit repository |

## The briefing pack

Three files that are always current and always literal text — attach them to any AI (local model, Copilot, Claude) and most briefing and drafting prompts work immediately:

1. [Priorities](priorities.md) — top themes, key dates, open decisions, standing context
2. [Projects](../03-projects/projects.md) — one line per project
3. [Decision Log](../06-decisions/decision-log.md) — one line per decision

> First time here? Read [Getting Started](../99-system/getting-started.md) (10 minutes). Notes marked **(example)** show the patterns — replace them in week one, then remove them with `kit.py examples remove`.
