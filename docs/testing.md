# Testing — what is automated, how to run it, what each switch does

The suite is plain `unittest` (runs with `pytest` too). Offline tests always run; anything needing a tool or an endpoint **skips itself** with a message saying what to install or set.

```bash
uv run --with 'mcp<2' kit.py test               # everything, verbose (the SDK enables the real MCP session test)
uv run kit.py test -p "test_bridge.py"          # one module
python -m pytest tests -q                       # if you prefer pytest (needs pyyaml installed)
```

## Layers

| Module | Covers | Needs |
|---|---|---|
| `test_vault_structure.py` | folders, `.obsidian/*.json`, daily-note path computation, 13 templates, bookmarks, Bases files parse and every embedded view exists | nothing |
| `test_okf_conformance.py` | OKF v0.2 rules on the template vault; validator catches missing `type`, wrong `status`, bad timestamps/actors; `index.md` and `log.md` shape; index is up to date | nothing |
| `test_links.py` | every markdown link, wikilink and base embed resolves; code blocks ignored | nothing |
| `test_kit_cli.py` | `kit.py` end-to-end via subprocess: validate, index determinism, init + personalisation, log, mcp-config (print and merge-write), doctor, llm ask fallback | nothing |
| `test_bridge.py` | MCP bridge `fs` backend on a temp vault (search, read, create-from-template, append, daily append, set property, path-escape guard); `cli` backend against a stub `obsidian` binary (argument grammar); `--selftest`; real MCP stdio session | `uv run --with 'mcp<2'` (or `pip install 'mcp<2'`) for the last one; mcp 2.x skips it, the bridge targets the v1 API |
| `test_graph.py` | ontology loading/inheritance/aliases; template graph is clean; injected faults produce the right findings; resolver methods; derived facts (trust, stale → at-risk propagation, transitive supersedes, state conflicts); JSON determinism; N-Triples well-formedness and vocabulary mapping; JSON-LD shape; SQLite views and recursive CTEs; artifacts on disk | nothing |
| `test_reconcile.py` | hub drift/missing rows detected and applied (free text and escaped pipes preserved, newest-first register, rows with spaces in the filename round-trip); superseded state written back without reformatting the rest of the frontmatter; back-dated log entries stay newest-first; task parsing; duplicates, done/open conflicts, `--sync-done` owner and recurring guards; digest validity; entity detection; minutes extraction and end-to-end filing (frontmatter, sections, log, daily link, no overwrite) | nothing |
| `test_security.py` | policy layer (deny/allow lists, read-only tool surface, confidential hiding across read/search/list/todos/graph, size and rate limits, propose mode, audit log); `proposals` CLI list/apply/reject; bearer-token check and ASGI middleware | nothing |
| `test_docs.py` | docs exist, relative links resolve, H1 present, README indexes docs, CLI commands mentioned exist, documented `mcp` installs carry the same pin as `pyproject.toml`, `tools.md` lists every tool the bridge registers | nothing |
| `test_e2e_qmd.py` | qmd indexes a temp copy of the vault with `XDG_CONFIG_HOME`/`XDG_CACHE_HOME` redirected into a temp directory, so your own collections and index database are never touched (the GGUF models are symlinked from the real cache rather than downloaded again); keyword search finds the concept note, `get` works; vector/hybrid query and `bench` behind flags | `qmd` on PATH |
| `test_e2e_llm.py` | endpoint lists models, follows a one-line instruction, answers a grounded question from a decision note, `kit.py llm smoke`; tool-call emission behind a flag | a running OpenAI-compatible endpoint |

That isolation rests on qmd honouring `XDG_CONFIG_HOME`/`XDG_CACHE_HOME`, which was checked on macOS and is unverified on native Windows — and qmd also picks up a project-local `.qmd/` by walking up from the working directory. The canary that asserts your real index was left alone only watches the XDG location, so on Windows treat a green e2e run as weaker evidence and check `qmd collection list` afterwards.

## Switches

| Variable | Effect |
|---|---|
| `KIT_LLM_BASE_URL` | endpoint (default `http://localhost:1234/v1`); Ollama: `http://localhost:11434/v1` |
| `KIT_LLM_MODEL` | model id to use (default: first listed) |
| `KIT_LLM_API_KEY` | bearer token if the server requires one |
| `KIT_E2E_EMBED=1` | run `qmd embed` + vector + hybrid queries (downloads models once, minutes on CPU) |
| `KIT_E2E_BENCH=1` | run `qmd bench` with `tests/fixtures/qmd-bench.json` |
| `KIT_E2E_TOOLS=1` | assert the model emits an OpenAI-style tool call |
| `KIT_VAULT` | default vault path for `kit.py` commands |

## What is deliberately not automated

- The official Obsidian CLI needs a running desktop app with a registered vault; the `cli` backend is tested against a stub that records the exact argv. Run `obsidian version` manually after registering.
- Bases views are validated structurally (YAML, named views, columns declared), not rendered — Obsidian is the only renderer. Open the dashboard once after editing a `.base`.
- Model quality: `test_e2e_llm.py` sets a floor (instruction following, grounded answer, tool call), not a benchmark. Use `qmd bench` for retrieval quality and compare models by hand.

## CI

`.github/workflows/ci.yml` runs the offline suite on ubuntu, macos and windows with Python 3.11 and 3.12 through `astral-sh/setup-uv`, plus an optional job that installs qmd via `oven-sh/setup-bun` and runs `test_e2e_qmd.py` (keyword tier only — no model downloads). Endpoint tests never run in CI.
