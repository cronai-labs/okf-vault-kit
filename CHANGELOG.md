# Changelog

All dates are the day the work landed. Versions before 0.4.0 were developed in one sitting and
carry the same date; they are kept for the record rather than as a release history.

## 0.4.0-alpha.2 — 2026-09-15

First public release, and the one that made the alpha's own claims true. A fourteen-dimension
adversarial review plus real runs on macOS and a clean Ubuntu machine found 101 verified defects;
all of the blocking ones are fixed here.

**Security**

- The bridge's policy layer failed open in four ways: the graph tools ignored confidential hiding entirely, `create_note`'s template argument read any file on disk, the deny and allow lists matched the spelling the model sent rather than the file that would be written, and a confidential note could be declassified with `set_property` and then read. Propose mode re-checks at apply time, `graph_query` runs read-only, and a write can no longer land without an audit line.
- `doctor --report` promised no paths, no username and no endpoint credentials, and leaked all three. Rewritten and attacked twice; the two gaps that remain are stated in ALPHA.md rather than implied away.

**Fixed**

- The default model failed the kit's own smoke test: MiniCPM5-2B is a reasoning model and the token budget was sized as if it were not, so `llm smoke` reported failure while the model answered correctly.
- `doctor` reported the MCP SDK installed on exactly the machines that lack it — the repository's own `mcp/` directory satisfied a bare `import mcp`.
- A UTF-8 BOM defeated frontmatter parsing, which silently disabled confidential hiding; an undecodable note crashed five read tools.
- `mcp-config --write` bound the model to the kit's sample vault instead of the user's.
- Corporate proxy variables captured loopback traffic, so a running endpoint read as unreachable and prompt bodies went to the proxy.
- `reconcile --apply` appended duplicate hub rows without bound and corrupted escaped pipes; `todos --sync-done` ticked other people's checkboxes.
- Graph exports emitted invalid N-Triples; notes made from the shipped templates failed `validate`.

**Added**

- `testkit/` — one command that walks the documented path and writes a report a tester can send back.
- CI: conventional-commit titles, secret scanning, SAST, packaging on every run, behind one fail-closed required check.
- `docs/decisions/` — starting with why the MCP SDK is pinned, and what would change it.

## 0.4.0-alpha.1 — 2026-09-14

First build handed to alpha testers. Two of these were the difference between a product and a demo.

**Fixed**

- The MCP bridge did not start against any currently installable SDK: it targets the v1 API, `mcp>=1.2` resolves to 2.x (where `FastMCP` became `MCPServer`), and the resulting `ModuleNotFoundError` was reported as "the MCP python SDK is missing". Pinned to `mcp>=1.2,<2` in all four places that declare it, and the error now distinguishes *absent* from *unsupported*.
- Confidential notes were readable whenever the vault path was not canonical — a symlinked home, `--vault .`, macOS `/tmp`, `/mnt/c` under WSL. The policy layer now canonicalises the vault and fails closed on a reference it cannot resolve.
- Windows: the CLI could not print. Em dashes and arrows against a legacy console code page killed `--help` and `doctor` with `UnicodeEncodeError`. stdio is forced to UTF-8; `subprocess(text=True)` names its codec, so note text no longer comes back through a pipe with "—" turned into "�".
- Windows: qmd was spawned by bare name, which `CreateProcess` cannot resolve to the `qmd.cmd` shim that `npm install -g` produces. Resolved paths everywhere, including the ones written into `mcp.json`.
- Generated files are written with explicit LF at all 22 sites, instead of CRLF on Windows.
- `sqlite3.connect(f"file:{db}")` → `db.as_uri()`; drive letters and spaces are not URI-safe.
- The qmd end-to-end suite wrote a collection into the user's global qmd index and never removed it, so the second run always failed and testers gained a collection pointing at a deleted temp directory.

**Removed**

- Scheduling and unattended operation: `kit.py maintain`, `scripts/headless/`, the launchd and Task Scheduler templates, `docs/headless.md`. Scheduling a vault needs a workflow engine and a permission model per job, neither of which belongs in this CLI.
- The container sandbox (`docker/`). A sandbox cannot decide what a model may write to your notes; the policy layer can, and `docs/security.md` §3 now points at what the OS already provides.

**Added**

- `kit.py --version`, and the version in `doctor`'s first row, so a tester can say which build they are on. `VERSION` is the single source; `pyproject.toml` reads it and a test fails on drift.

## 0.3.0 — 2026-09-14

- Access control: `mcp/bridge_policy.py` — read-only mode (write tools unregistered), write deny/allow lists (people and system folders denied by default), confidential-note hiding (reads, search, listings, todos, context packs), per-call size and per-minute rate limits, propose mode with `kit.py proposals list|show|apply|reject`, JSONL audit log, bearer-token middleware for the HTTP transport (`--token`, `mcp-config --bridge-token`), non-loopback warning.
- Sandboxing: `docker/Dockerfile` (non-root, deps resolved at build) and `docker/compose.yml` (read-only root fs, `cap_drop ALL`, `no-new-privileges`, limits, loopback-only ports, vault read-only for search, token + allow-list for the bridge, optional Ollama). `docs/security.md`: threat model, profiles, Colima/Docker Desktop/Podman/WSL2/Apple `container`, lighter sandboxes, supply chain, checklist.
- Template: `00-home/priorities.md` is `sensitivity: internal` so the briefing pack stays visible to the local model.

## 0.2.1 — 2026-09-14

- Headless operation: `kit.py maintain` (qmd update/embed → reconcile → todo digest → index → validate → graph → git, daily report in `.kit/`), bridge `--http` transport and `mcp-config --bridge-http`, `scripts/headless/` with maintenance runners, launchd templates + installer (macOS) and Task Scheduler registration (Windows), `docs/headless.md` (llmster, Ollama, llama.cpp, NSSM, tunnels, troubleshooting).

## 0.2.0 — 2026-09-14

- Ontology: `vault/99-system/ontology.yml` (classes with inheritance, typed properties, ranges, enums, required, edge inverses/transitivity, property and value aliases, rule toggles) and `vault/context.jsonld` (Dublin Core, schema.org, PROV-O, SKOS). `kit.py validate` gains an ontology section.
- Graph (`kitgraph.py`): notes, actors and sources as nodes; ref-properties, body links, citations and provenance as edges; derived inverses, transitive closure, trust tier, staleness, at-risk propagation, superseded decisions; exports to JSON, N-Triples, JSON-LD, SQLite; `graph build|export|query|neighbors|path|pack`.
- Reconciliation (`kitrecon.py`): `reconcile [--apply]` (hub tables vs notes, derived states), `todos [--digest] [--sync-done]` (surfacing, duplicates, done/open conflicts), `minutes` (file a recap: entity resolution, action/decision extraction, template, log, daily link, optional LLM pass).
- Bridge tools: `graph_context`, `graph_neighbors`, `graph_path`, `graph_query`, `vault_todos`, `file_meeting_minutes`; `llm ask --graph --dry-run`.
- Template: Sam Example and the platform team notes; decision template gains `supersedes`, person template `aliases`.

## 0.1.1 — 2026-09-14

- Dependencies hardened: qmd via Bun (Homebrew/winget single binary) instead of npm; `kit.py` and the MCP bridge carry PEP 723 inline metadata and run through `uv run` — no pip, no venv; installers and CI rewritten accordingly; `mcp-config` registers the bridge as `uv run …` when uv is present (`--no-uv` / `KIT_NO_UV=1` to opt out).
- `state` is now a plain, unvalidated property with suggested values; the kit ships no formal workflow model.
- New `docs/knowledge-graph.md`: staged roadmap from ontology file and JSON-LD context to SQLite/N-Triples extraction, rules, and GraphRAG-lite bridge tools.

## 0.1.0 — 2026-09-14

- Template vault v4: kebab-case structure, markdown links, OKF v0.2 frontmatter (`generated`, `verified`, `status`, `stale_after`, `sources`), `state` for workflow, 13 templates, 3 Bases / 15 views, example notes, generated `index.md`, `log.md`.
- `kit.py`: doctor, init (actor stamping, qmd registration), validate (OKF + links), index, log, mcp-config (LM Studio / Claude Desktop / Cursor), llm smoke / ask, test.
- `mcp/obsidian_bridge.py`: MCP server with `fs` (headless) and `cli` (official Obsidian CLI) backends.
- Installers for macOS (Homebrew), Windows (winget), WSL2 (apt + nvm); Linux documented.
- Docs: quickstart, vault guide, tools, local LLM, models, plugins, OKF mapping, examples, testing, FAQ, platforms.
- Tests: structure, OKF conformance, links, CLI, bridge, docs (offline); qmd and LLM end-to-end (gated); CI matrix.
