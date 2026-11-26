# Testing — what is automated, how to run it, what each switch does

The suite is plain `unittest` (runs with `pytest` too). Offline tests always run; anything needing a tool or an endpoint **skips itself** with a message saying what to install or set.

```bash
uv run --with 'mcp>=2,<3' kit.py test           # everything, verbose (the SDK enables the real MCP session test)
uv run kit.py test -p "test_bridge.py"          # one module
python -m pytest tests -q                       # if you prefer pytest (needs pyyaml installed)
```

## Layers

| Module | Covers | Needs |
|---|---|---|
| `test_vault_structure.py` | folders, `.obsidian/*.json`, daily-note and month-note path computation, 14 templates, bookmarks, Bases files parse and every embedded view exists | nothing |
| `test_okf_conformance.py` | OKF v0.2 rules on the template vault; validator catches missing `type`, wrong `status`, bad timestamps/actors; `index.md` and `log.md` shape; index is up to date | nothing |
| `test_links.py` | every markdown link, wikilink and base embed resolves; code blocks ignored | nothing |
| `test_kit_cli.py` | `kit.py` end-to-end via subprocess: validate, index determinism, init + personalisation, log, mcp-config (print and merge-write for every client, the old `obsidian-vault` entry replaced, the token from `KIT_BRIDGE_TOKEN` and its 0600 file mode), doctor, llm ask fallback; `llm ask` and `minutes --llm` against a stub endpoint (answer and sources, a confidential note's path, title and body kept out of the prompt, JSON and prose replies) | nothing |
| `test_confidential_writers.py` | no field of a confidential note (title, description, path, tasks) in `index.md`, the project and decision hubs, the todo digest or the log; count lines; hub rows for confidential notes removed only with `--apply`, and kept (reported as `hub_row_unverifiable`) when the note cannot be read or parsed; an unreadable folder fails closed instead of crashing; a note with broken frontmatter counts as confidential; minutes: the default marks a meeting that links a confidential note confidential (and log.md, the daily note, `index.md` and the digest then carry no title or path of it), `--keep-open` keeps a confidential name as written and still links a visible person, a confidential template stays confidential, and the bridge's default result is the same for a confidential and an unknown name it was passed | nothing |
| `test_confidential_floor.py` | folder floors and path rules from `.kit/config.yml`: a floor hides a note without frontmatter and one marked `internal` (which `validate` warns about), a note raises itself in an open folder, globs and case-insensitive matching, a path rule hides a CSV from the `cli` backend's listing, every broken config shape hides everything (bridge, index, graph, todos) and `validate` reports it, the graph signature follows the config, a forced `minutes` overwrite writes no label for a floor, `reconcile` removes a hub row a path rule covers and keeps every row (naming the config) while the config is broken; a parity test that every path the predicate hides is refused by `read_note` and absent from `index.md`, the redacted graph, the todo digest and the `llm ask` prompt | nothing |
| `test_confidential_cli.py` | `llm ask` prompts (plain and `--graph`) carry no field of a confidential note, and the console line is count-only; graph build files and `graph export/query/neighbors/path/pack` hide confidential nodes unless `--show-confidential`, including derived values and findings that name one; `graph query` rebuilds a database built from other notes; `kit.py todos` counts confidential tasks instead of listing them; `llm ask` keeps `-n` sources when confidential notes rank first; the bridge and the CLI share one redaction | nothing |
| `test_bridge.py` | MCP bridge `fs` backend on a temp vault (search, read, create-from-template, append, daily append, set property, path-escape guard); `cli` backend against a stub `obsidian` binary (argument grammar); `--selftest`; real MCP stdio sessions: every one of the 14 tools called over the wire, a `--read-only` server serving 9, a write turned into a proposal, three policy refusals; the `--http` transport over a real socket (401 without the token, a session with it) | `uv run --with 'mcp>=2,<3'` (or `pip install 'mcp>=2,<3'`) for the last one; the v1 SDK skips it, the bridge targets the 2.x API |
| `test_graph.py` | ontology loading/inheritance/aliases; template graph is clean; injected faults produce the right findings; resolver methods; derived facts (trust, stale → at-risk propagation, transitive supersedes, state conflicts); JSON determinism; N-Triples well-formedness and vocabulary mapping; JSON-LD shape; SQLite views and recursive CTEs; artifacts on disk | nothing |
| `test_reconcile.py` | hub drift/missing rows detected and applied (free text and escaped pipes preserved, newest-first register, rows with spaces in the filename round-trip); superseded state written back without reformatting the rest of the frontmatter; back-dated log entries stay newest-first; task parsing; duplicates, done/open conflicts, `--sync-done` owner and recurring guards; digest validity; entity detection; minutes extraction and end-to-end filing (frontmatter, sections, log, daily link, no overwrite) | nothing |
| `test_security.py` | policy layer (deny/allow lists, read-only tool surface, confidential hiding across read/search/list/todos/graph, size and rate limits, propose mode, audit log); `proposals` CLI list/apply/reject; bearer-token check and ASGI middleware | nothing |
| `test_docs.py` | docs exist, relative links resolve, H1 present, README indexes docs, CLI commands mentioned exist, every declared and documented `mcp` pin matches `pyproject.toml`, `tools.md` lists every tool the bridge registers | nothing |
| `test_e2e_qmd.py` | qmd indexes a temp copy of the vault with `XDG_CONFIG_HOME`/`XDG_CACHE_HOME` redirected into a temp directory, so your own collections and index database are never touched (the GGUF models are symlinked from the real cache rather than downloaded again); keyword search finds the concept note, `get` works; the bridge searching through qmd and still hiding a confidential note qmd returns (needs qmd's models in the cache, so CI skips it); vector/hybrid query and `bench` behind flags | `qmd` on PATH |
| `test_e2e_llm.py` | endpoint lists models, follows a one-line instruction, answers a grounded question from a decision note, `kit.py llm smoke`; behind `KIT_E2E_TOOLS=1`: tool-call emission, and the model driving the real bridge (its own `list_tools` schemas over stdio, the calls it emits run against the bridge, then a text answer) | a running OpenAI-compatible endpoint |

That isolation rests on qmd honouring `XDG_CONFIG_HOME`/`XDG_CACHE_HOME`, which was checked on macOS and is unverified on native Windows — and qmd also picks up a project-local `.qmd/` by walking up from the working directory. The canary that asserts your real index was left alone only watches the XDG location, so on Windows treat a green e2e run as weaker evidence and check `qmd collection list` afterwards.

Last real-model run, 2026-11-25, `minicpm5-2b` in LM Studio on macOS with `KIT_E2E_TOOLS=1`: all six tests of `test_e2e_llm.py`, the bridge-driving one included, passed in each of three consecutive runs. The bridge-driving test checks that the model called a bridge tool, got a note back and answered — not whether the answer is right; that needs an eval, not a unit test.

## Switches

| Variable | Effect |
|---|---|
| `KIT_LLM_BASE_URL` | endpoint (default `http://localhost:1234/v1`); Ollama: `http://localhost:11434/v1` |
| `KIT_LLM_MODEL` | model id to use (default: first listed) |
| `KIT_LLM_API_KEY` | bearer token if the server requires one |
| `KIT_E2E_EMBED=1` | run `qmd embed` + vector + hybrid queries (downloads models once, minutes on CPU) |
| `KIT_E2E_BENCH=1` | run `qmd bench` with `tests/fixtures/qmd-bench.json` |
| `KIT_E2E_TOOLS=1` | assert the model emits an OpenAI-style tool call, and that it can drive the bridge's real tools to an answer |
| `KIT_VAULT` | default vault path for `kit.py` commands |

## What is deliberately not automated

- The official Obsidian CLI needs a running desktop app with a registered vault; the `cli` backend is tested against a stub that records the exact argv. Run `obsidian version` manually after registering.
- Bases views are validated structurally (YAML, named views, columns declared), not rendered — Obsidian is the only renderer. Open the dashboard once after editing a `.base`.
- Model quality: `test_e2e_llm.py` sets a floor (instruction following, grounded answer, tool call), not a benchmark. Use `qmd bench` for retrieval quality and compare models by hand.
- Your coding agent's own settings: the suite tests the bridge, and a coding agent's own file and shell tools never go through it. What they can read depends on the agent's settings, its version and your OS — check it with the canary run below.

## Canary: check your own agent settings

A manual run, a few minutes long, that shows whether your coding agent can read a folder you meant to deny it ([security.md §5](security.md#5-coding-agents-and-other-direct-readers) has the settings and what was tested). Run it after changing the agent's settings and after upgrading the agent.

1. **Use a throwaway copy**, never your real vault: copy the template `vault/` to a scratch folder and work there. Keep the run output in a second folder outside it.
2. **Put the settings you want to check in place**, as you use them, with the folder that holds confidential notes (here `05-people/`) denied. Your deny entries name your real vault, and the run works on a copy elsewhere, so they would protect nothing in it: either point the deny paths at the scratch copy and keep every other setting as it is, or put the settings in the copy's own `.claude/settings.json` with relative paths (`Read(./05-people/**)`, `./05-people`) and start the agent in the copy, where they resolve to its folders ([security.md §5](security.md#5-coding-agents-and-other-direct-readers) has the path forms). Otherwise the canary shows up because the copy was never denied, and the run says nothing about your settings.
3. **Plant two unique strings**: the canary in the denied folder, and a control in a folder you do not deny. Each file is named after its own string, so a run that only lists files shows it too. The control is what proves a run tried: a canary that does not appear means nothing if the control does not appear either.

   ```bash
   OUT="$(mktemp -d)"      # run output, outside the scratch vault
   CANARY="canary-$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
   CONTROL="control-$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
   printf 'canary: %s\n' "$CANARY" > "05-people/$CANARY.md"
   printf 'control: %s\n' "$CONTROL" > "07-knowledge/$CONTROL.md"
   ```

4. **Ask the agent, one route per run**, for "the value after `canary:` and the value after `control:` in this folder", and name the route: its file-read tool, its search tool, its file-pattern tool, `cat`, `ls -R` and `grep -r` in its shell, a short script that opens every file, and a subagent. If your agent has a mode that skips permission prompts, add a run in it. Never put either value into a prompt — the check below looks for them. With Claude Code, save each run as `stream-json`, which carries the tool calls and their results ([headless](https://code.claude.com/docs/en/headless.md)): `claude -p --output-format stream-json --verbose "…" > "$OUT/run-grep-r.jsonl"`.
5. **Check**: `grep -l "$CANARY" "$OUT"/run-*` prints nothing, and `grep -L "$CONTROL" "$OUT"/run-*` prints nothing either (every run found the control). Then search the agent's session transcripts for the canary too — Claude Code keeps them as JSONL files under `~/.claude/projects/` (seen on 2.1.330, macOS).

The run passes when the canary appears in no run output and no transcript, and the control appears in every run. Delete the scratch folder afterwards.

## CI

`.github/workflows/ci.yml` runs the offline suite on ubuntu, macos and windows with Python 3.11 and 3.14, and on ubuntu also with 3.12 and 3.13, through `astral-sh/setup-uv`. A `qmd-e2e` job installs qmd via `oven-sh/setup-bun` and runs `test_e2e_qmd.py` (keyword tier only — no model downloads); only its install step is advisory, and the job is one of the checks `ci-required` waits for. Endpoint tests never run in CI.
