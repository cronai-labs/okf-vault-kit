---
type: decision
title: Embedding model for German and English queries (example)
description: Which embedding model the docs-portal pilot uses for a mixed DE/EN corpus — the last open question before the go/no-go.
date: 2026-09-15
state: open
project: "[Search relaunch](../03-projects/search-relaunch.md)"
owner: "[Sam Example](../05-people/sam-example.md)"
people: ["Sam Example", "Alex Example"]
due: 2026-09-17
tags: [decision, adr, search, embeddings]
sensitivity: internal
generated: { by: human:me, at: "2026-09-15T09:20:00+02:00" }
example: true
---

# Embedding model for German and English queries (example)

## Context

The corpus is roughly two thirds German, one third English, and users query in both — often mixing
them in one sentence. The pilot currently runs an English-first model, which is why recall on
German queries sits below target while English is already above it.

The retrieval stack itself is settled: [Adopt qmd for local search](2026-09-08-adopt-qmd-for-local-search.md)
decided that. This decision is only about which embedding model qmd is pointed at.

## Options

| Option | For | Against |
|---|---|---|
| Keep the English-first model | No re-index, no new download; English recall already above target | German recall stays below target, which is two thirds of the corpus |
| A multilingual model | One index, both languages, no routing logic | Larger download, slower on the CPU tier — and latency is already the open risk |
| One index per language | Best recall per language | Query-language detection becomes a new failure mode nobody owns |

## Decision

Open. Blocked on the evaluation in
[Search relaunch — pilot spec](../07-knowledge/search-relaunch-spec.md), which is running now.

## Consequences

Whatever is chosen, the index has to be rebuilt once before the pilot, so the decision must land
before the go/no-go or the pilot ships on the model nobody chose.

## Re-check triggers

- The latency measurement on the CPU tier comes back — a multilingual model is only viable if it
  leaves headroom.
- The corpus balance shifts materially away from two-thirds German.
