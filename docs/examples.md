# Examples — walkthroughs on the sample vault

The template vault doubles as the sample: every **(example)** note is a fixture the tests use. Run these against the template (`vault/`) or against your own copy.

## 1. Find a decision from the terminal (qmd)

```bash
qmd collection add ./vault --name kit-demo --mask "**/*.md"
qmd context add qmd://kit-demo "OKF vault kit sample notes"
qmd search "hosted vector database" -c kit-demo
```

Expected: `06-decisions/2026-09-08-adopt-qmd-for-local-search.md` in the top 3 (keyword hit on "hosted vector database" in the options table). Then:

```bash
qmd embed -c kit-demo        # first time: downloads the embedding model
qmd query "why did the team not buy a managed search service" -c kit-demo
```

Expected: the same decision note first — no keyword overlap this time; the vector leg and the re-ranker carry it. `qmd bench tests/fixtures/qmd-bench.json -c kit-demo` prints precision/recall for five such queries across bm25, vector, hybrid and full pipelines.

## 2. Ask the model with citations (endpoint)

```bash
lms server start
uv run kit.py llm ask "what is blocking the search relaunch pilot" --vault ./vault --collection kit-demo
```

Expected shape:

```
Latency on the CPU tier: p95 is 1.6 s against a 0.8 s target; the go/no-go on 2026-09-18 is conditional
on a benchmark under 1.0 s [1][3]. The DE/EN embedding model is also undecided (due 2026-09-17) [2].

Sources:
  [1] 03-projects/search-relaunch.md
  [2] 02-meetings/2026-09-11-search-relaunch-sync.md
  [3] 00-home/priorities.md
```

Without qmd installed the command falls back to a term-frequency search over the vault — worse ranking, same citations.

## 3. Distil a recap in the chat window (LM Studio + tools)

1. Paste a meeting recap into `02-meetings/2026-09-11-search-relaunch-sync.md` under *Recap*.
2. In LM Studio (tools on), prompt:
   > Read `02-meetings/2026-09-11-search-relaunch-sync.md` with obsidian_read_note. From the Recap section, extract Outcomes, Decisions and Actions in the note's format; actions as `- [ ] verb — owner, due YYYY-MM-DD`. Show me the text; do not write yet.
3. Review, then:
   > Append exactly that text under a heading `## Distilled` using obsidian_append_note.

LM Studio shows the append call with its arguments before executing. The bridge only appends; it never rewrites the note.

## 4. Let the model keep the daily log (bridge)

> Append "- 2026-09-14 — benchmark scheduled for Tuesday, see search relaunch" to today's daily note.

The bridge computes the path from `.obsidian/daily-notes.json` (`01-journal/daily/2026/09/2026-09-14.md`), creates it from the daily template if missing (placeholders rendered, frontmatter valid) and appends the line. `tests/test_bridge.py::test_daily_append_creates_todays_note_at_configured_path` is this exact scenario.

## 5. Engineer lane: an ADR and a runbook

- `Alt + T` → `decision` → name it `2026-09-20-cap-reranking-at-40-candidates`. Fill Context / Options / Decision / Consequences; set `state: decided`; add the row to `06-decisions/decision-log.md`.
- `Alt + T` → `runbook` → `benchmark-search-latency`. Fill steps; leave `status: draft` until it has been run once, then set `status: stable`, `verified: { by: human:you, at: … }`, `stale_after` six months out.
- `uv run kit.py validate` — both notes conformant; `uv run kit.py log "Added latency benchmark runbook" --kind Creation`.

## 6. PM lane: a spec with metrics

`Alt + T` → `spec`. The success-metrics table is the contract; link the spec from the project note's *Frame* row and link decisions from the spec's *Decisions taken*. `07-knowledge/search-relaunch-spec.md` is the filled example; note it stays `status: draft` with a `stale_after` so it appears in *Needs Review* until promoted.

## 7. Manager lane: the Monday briefing

Attach `00-home/priorities.md`, `03-projects/projects.md`, `06-decisions/decision-log.md` to any model (local or hosted) and use prompt 1 from `99-system/ai-workflows.md`. These three files are hand-maintained literal text precisely so that this works without tools, plugins or an index.

## 8. Check a vault without opening Obsidian

```bash
python mcp/obsidian_bridge.py --backend fs --vault ./vault --selftest
uv run kit.py index --vault ./vault && uv run kit.py validate --vault ./vault --strict
```

Neither needs the app running, so this works over SSH or in CI. The GitHub Actions workflow in `.github/workflows/ci.yml` does exactly this on macOS, Windows and Linux.

## 9. Reconcile after a busy week

```bash
uv run kit.py reconcile            # e.g. hub_drift: search-relaunch — health: hub 'yellow' vs note 'green'
uv run kit.py reconcile --apply    # hub rows follow the notes; log.md gets a line
uv run kit.py todos --digest       # 00-home/todo-digest.md: overdue, due soon, duplicates, by owner
uv run kit.py todos --sync-done    # a task ticked in the meeting note is ticked in the project note too
```

## 10. File a recap without opening Obsidian

```bash
pbpaste > /tmp/recap.txt    # or any text
uv run kit.py minutes /tmp/recap.txt --title "Search relaunch sync" --daily
```

Output names the note, the people and project it resolved, and the extracted actions; the recap stays verbatim under *Recap*. In LM Studio the same is one sentence: "File this recap as today's search relaunch sync" — the model calls `file_meeting_minutes`, you confirm the arguments.

## 11. Ask the graph

```bash
uv run kit.py graph pack search-relaunch                          # what a model sees
uv run kit.py llm ask "who owns the search relaunch" --graph      # facts + sources → answer with citations
uv run kit.py graph query "select id from v_at_risk"              # knowledge resting on stale knowledge
```
