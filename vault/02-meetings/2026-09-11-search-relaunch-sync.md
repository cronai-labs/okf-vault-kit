---
type: meeting
title: Search relaunch sync 2026-09-11 (example)
description: Eval results reviewed; latency on the CPU tier is the open risk for the go/no-go; embedding model for DE/EN to be decided by Thursday.
date: 2026-09-11
meeting_type: project
project: "[Search relaunch](../03-projects/search-relaunch.md)"
people: ["Alex Example", "Sam Example"]
state: waiting
tags: [meeting, search]
sensitivity: internal
generated: { by: human:me, at: "2026-09-11T14:40:00+02:00" }
---

# Search relaunch sync 2026-09-11 (example)

**Purpose:** review eval results ahead of the pilot go/no-go on 2026-09-18.
**Attendees:** me, Alex, Sam.

## Outcomes

- Ranking quality is above target (MRR 0.83 vs. 0.80); we stop tuning ranking.
- Latency p95 is 1.6 s on the CPU tier; target is 0.8 s. Reranking is the main cost.
- German queries under-retrieve with the default embedding model.

## Decisions

- Keep the pilot date; go/no-go is conditional on latency < 1.0 s in Tuesday's benchmark.

## Actions

- [ ] Benchmark with reranking capped at 40 candidates and with `--no-rerank` — Alex, due 2026-09-15
- [ ] Evaluate a multilingual embedding model and re-embed — Sam, due 2026-09-17

## Recap (pasted from Teams / transcript / recorder)

> (paste here)
