---
type: runbook
title: Reindex qmd
description: Rebuild the local qmd index and embeddings for the vault after a large import, a model change, or search results that look stale.
tags: [runbook, search, qmd]
status: stable
stale_after: 2027-01-15T00:00:00Z
sources:
  - id: qmd-readme
    resource: https://github.com/tobi/qmd
    title: QMD — Query Markup Documents (README)
sensitivity: internal
generated: { by: human:me, at: "2026-09-06T09:30:00+02:00" }
verified: { by: human:me, at: "2026-09-11T15:00:00+02:00" }
---

# Reindex qmd

## When to use

Search results miss notes you know exist, `qmd status` shows pending embeddings after a big import, or you changed the embedding model (vectors are not compatible across models).

## Preconditions

- qmd installed (`qmd --version`) and the vault registered as a collection (`qmd collection list`).
- Enough disk for the models (~2 GB) in `~/.cache/qmd/models/`.

## Steps

1. `qmd update` — re-scan the collection; runs any configured update command first.[^qmd-readme]
2. `qmd embed` — embed new or changed chunks. After a model change: `qmd embed -f` (force).
3. `qmd status` — confirm 0 pending and the expected document count.
4. Spot-check: `qmd search "hybrid search"` and `qmd query "how do we re-rank results"`.

## Verify

The spot-check returns the concept note in the top 3 with a score above 0.5.

## Rollback / escalation

- `qmd cleanup` removes orphaned cache data; `qmd doctor` diagnoses runtime, sqlite-vec and GPU issues.
- If the process crashes on Windows with CUDA, set `QMD_EMBED_PARALLELISM=1` or force CPU with `QMD_FORCE_CPU=1`.

[^qmd-readme]: QMD — Query Markup Documents (README)
