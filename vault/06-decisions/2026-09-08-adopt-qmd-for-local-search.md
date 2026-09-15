---
type: decision
title: Adopt qmd for local search (example)
description: Use qmd (BM25 + vector + reranking, fully local) as the retrieval layer for the docs-portal pilot instead of a hosted vector database.
date: 2026-09-08
state: decided
project: "[Search relaunch](../03-projects/search-relaunch.md)"
owner: "[Alex Example](../05-people/alex-example.md)"
people: ["Alex Example", "Sam Example"]
tags: [decision, adr, search]
sensitivity: internal
generated: { by: human:me, at: "2026-09-08T17:05:00+02:00" }
verified: { by: human:me, at: "2026-09-11T15:00:00+02:00" }
---

# Adopt qmd for local search (example)

## Context

The pilot needs retrieval that runs on the existing CPU tier, keeps documents on-premise, and is good enough on a mixed German/English corpus. Two candidates were evaluated for two weeks against the frozen 120-query eval set.

## Options

| Option | Pros | Cons | Cost / risk |
|---|---|---|---|
| A — qmd (local hybrid search) | No data leaves the host; MCP server built in; MRR 0.83 on eval | CPU latency needs tuning; single-node | zero licence cost; ops effort on us |
| B — hosted vector DB | Managed scaling; fast | Documents leave the tenant; DPA needed; recurring cost | 4-week procurement; data-residency review |

## Decision

Option A. Decided in the search relaunch sync on 2026-09-08 by the project team; owner Alex.

## Consequences

Easier: privacy story, agent integration via MCP. Harder: we own latency tuning and the embedding-model choice for DE/EN. Given up: elastic scaling beyond one node for now. Revisit trigger: corpus > 200k documents or p95 latency target missed after tuning.

## Follow-up

- [x] add a row to the [Decision Log](decision-log.md)
- [ ] inform: support lead, platform team — Alex, due 2026-09-11
