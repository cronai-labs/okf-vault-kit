---
type: project
title: Search relaunch (example)
description: Replace the docs-portal keyword search with hybrid BM25 + vector search; pilot with the support team first.
state: active
health: yellow
owner: "[Alex Example](../05-people/alex-example.md)"
area: "[Platform engineering](../04-areas/platform-engineering.md)"
people: ["Alex Example", "Sam Example"]
milestone: Pilot go/no-go
due: 2026-09-18
tags: [project, search]
sensitivity: internal
generated: { by: human:me, at: "2026-09-11T15:20:00+02:00" }
---

# Search relaunch (example)

## Executive summary

We are replacing the docs portal's keyword search with hybrid retrieval (see [Hybrid search](../07-knowledge/hybrid-search.md)). Ranking quality on the eval set is above target; p95 latency on the CPU-only tier is still twice the target, which is the open risk before the pilot decision on 2026-09-18.

## Frame

| | |
|---|---|
| Goal / success criteria | MRR ≥ 0.80 on the 120-query eval set; p95 < 800 ms on the CPU tier; support team adopts it for 4 weeks |
| Scope (in / out) | In: docs portal, internal runbooks. Out: ticket history (next phase) |
| Budget / capacity | 1.5 FTE through Q4, no new infrastructure |
| Next milestone | `Pilot go/no-go` on `2026-09-18` |
| Links | repo · eval dashboard · [spec](../07-knowledge/search-relaunch-spec.md) |

## Risks & open questions

| Risk / question | Impact | Owner | Mitigation |
|---|---|---|---|
| CPU-tier latency 2× target | pilot slips | Alex | Skip reranking below 40 candidates; measure again Tue |
| Multilingual queries (DE/EN) under-retrieve | support team frustration | Sam | Switch embedding model, re-embed, re-run eval |

## Actions

- [ ] Re-run latency benchmark with `--no-rerank` — Alex, due 2026-09-15
- [ ] Decide embedding model for DE/EN — Sam, due 2026-09-17
- [ ] Prepare go/no-go one-pager — me, due 2026-09-17

## Update log

- 2026-09-11 — eval MRR 0.83 (target 0.80). Latency p95 1.6 s on CPU tier (target 0.8 s). Health → yellow.
- 2026-09-04 — pilot scope agreed with support lead; eval set frozen at 120 queries.
- 2026-08-28 — created
