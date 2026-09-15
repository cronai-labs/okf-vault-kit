---
type: system
title: AI Workflows
description: How the vault, the local search index and a local LLM work together — the five workflows, the prompts, and the rules.
tags: [system, ai]
sensitivity: internal
---

# AI Workflows

Three moving parts, all on your machine:

1. **This vault** — Markdown with frontmatter; the source of truth.
2. **qmd** — indexes the vault; answers `search` / `vsearch` / `query`; exposes the same as MCP tools.
3. **A local LLM** in LM Studio (or Unsloth Desktop / Ollama) — chats with you, calls qmd through MCP, and serves an OpenAI-compatible endpoint for scripts.

Setup lives in the kit's `docs/local-llm.md`. This page is the *how to use it*.

## Rule zero

A model only knows what it is given. Attach the briefing pack (Priorities, Projects, Decision Log) or let it search through qmd. Small models (2B) are good at retrieval-grounded summarising and drafting; they are **not** reliable for arithmetic, dates, or anything they have to know from memory. Treat every output as a draft and every claim as unverified until you see the source note.

## The five workflows

**1. Morning briefing.** *Attach* `00-home/priorities.md`, `03-projects/projects.md`, `06-decisions/decision-log.md`.
> Using only the attached files, give me a five-bullet briefing for today: what is due, what is at risk, what I am waiting on, and one thing I should decide this week. Quote the file each bullet comes from.

**2. Find and summarise.** *With qmd tools enabled.*
> Search the vault for everything about `<topic>`; list the notes you found with their paths, then summarise the current state in five sentences and flag contradictions between notes.

**3. Meeting prep.**
> Read the project note and the last two meeting notes about `<project>` (search for them). Draft an agenda with three items, each with the decision I need and the open action it depends on.

**4. Distill a recap.** Paste the Teams / transcript recap into the meeting note, then:
> From the pasted recap, extract Outcomes, Decisions and Actions in the note's format. Actions as `- [ ] verb — owner, due YYYY-MM-DD`. Mark anything you are unsure about with `(?)`.

**5. Knowledge upkeep.**
> List every knowledge note whose `stale_after` is in the past or whose `status` is `draft`. For each, propose in one line what to check before it can be marked `stable` and `verified`.

## Working with the tools from a terminal

```bash
uv run kit.py graph pack search-relaunch     # structured facts about a note and its relations
uv run kit.py todos                          # overdue, due soon, duplicated tasks
uv run kit.py minutes recap.txt --title "Sync"   # file a recap as a meeting note
qmd query "why did we choose qmd"            # hybrid search, best quality
qmd search "latency" -c vault --format json   # keyword, scriptable
qmd get "07-knowledge/hybrid-search.md"       # read a note by path
obsidian daily:append content="- [ ] call legal — me, due 2026-09-15"   # official Obsidian CLI (app running)
uv run kit.py llm ask "what is blocking the pilot"   # qmd → local LLM, with citations
```

## Rules

- Nothing leaves the machine unless you explicitly attach a file to a cloud model.
- People notes are `sensitivity: confidential`: do not feed them to anything that logs prompts.
- The model writes drafts; you keep the `generated.by: human:<you>` line honest — set `generated.by` to the agent's name (for example `lmstudio/minicpm5-2b`) on notes an AI produced, and verify them (`verified`) before relying on them.
