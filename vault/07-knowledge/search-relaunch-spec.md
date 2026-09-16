---
type: spec
title: Search relaunch — pilot spec (example)
description: Problem, goals, proposal and success metrics for the docs-portal search pilot with the support team.
project: "[Search relaunch](../03-projects/search-relaunch.md)"
owner: "[Alex Example](../05-people/alex-example.md)"
status: draft
stale_after: 2026-10-15T00:00:00Z
tags: [spec, search]
sensitivity: internal
generated: { by: human:me, at: "2026-09-02T13:00:00+02:00" }
example: true
---

# Search relaunch — pilot spec (example)

## Problem

Support engineers spend a median 6 minutes per ticket finding the right runbook; keyword search misses paraphrased questions and German-language queries.

## Goals & non-goals

- Goals: halve time-to-runbook; work fully on-premise; keep zero new infrastructure.
- Non-goals: ticket-history search; a chat interface (phase 2).

## Proposal

Hybrid retrieval over the docs portal and runbooks, exposed through the existing search box and through an MCP server for agents. See [Hybrid search](hybrid-search.md).

## Success metrics

| Metric | Now | Target | By |
|---|---|---|---|
| MRR on eval set | 0.61 | 0.80 | pilot start |
| p95 latency (CPU tier) | 1.6 s | 0.8 s | pilot start |
| Median time-to-runbook | 6 min | 3 min | pilot + 4 weeks |

## Open questions

- Embedding model for DE/EN — decision due 2026-09-17.

## Decisions taken

- [Adopt qmd for local search](../06-decisions/2026-09-08-adopt-qmd-for-local-search.md)
