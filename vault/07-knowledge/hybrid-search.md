---
type: concept
title: Hybrid search
description: Retrieval that fuses keyword (BM25) and vector results with reciprocal rank fusion, optionally re-ranked by a small LLM — why it beats either method alone.
tags: [concept, search, retrieval]
status: stable
stale_after: 2027-03-01T00:00:00Z
sources:
  - id: qmd-readme
    resource: https://github.com/tobi/qmd
    title: QMD — Query Markup Documents (README)
    last_modified: 2026-08-15T00:00:00Z
sensitivity: internal
generated: { by: human:me, at: "2026-09-05T11:00:00+02:00" }
verified: { by: human:me, at: "2026-09-11T15:00:00+02:00" }
---

# Hybrid search

## Summary

Keyword search (BM25) is precise for exact terms and identifiers; vector search finds passages that mean the same thing in different words. Fusing both ranked lists — typically with reciprocal rank fusion — gets the strengths of each, and an LLM re-ranker on the top candidates adds a final precision boost.[^qmd-readme]

## Details

- **BM25** scores term overlap with length normalization; it fails on synonyms and paraphrase.
- **Vector search** embeds chunks and queries into the same space; it fails on rare identifiers and exact phrases.
- **RRF** merges lists by rank, not by incomparable scores: `score = Σ 1/(k + rank)`, typically with k = 60.
- **Re-ranking** runs a cross-encoder or small LLM over the top 20–40 fused candidates. It is the most expensive step; on CPU-only hosts it is the first thing to cap or skip.

## Examples

In the qmd tool the three modes map to `qmd search` (BM25), `qmd vsearch` (vector) and `qmd query` (hybrid + re-rank); the benchmark fixture that ships with the kit shows why hybrid wins on a mixed corpus.

## Related

- [Search relaunch](../03-projects/search-relaunch.md) (example)
- [Reindex qmd](reindex-qmd.md)

[^qmd-readme]: QMD — Query Markup Documents (README)
