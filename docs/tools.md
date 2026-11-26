# Tools — what each one is, how to install it, how the kit uses it

| Task | Tool | Needs Obsidian running? |
|---|---|---|
| Search notes by keyword or meaning, from a terminal or an agent | **qmd** | no |
| Read/create/append notes, apply templates, set properties, from a script or an agent | **Obsidian CLI** (official) | yes |
| Same, headless (CI, WSL, servers) | **yakitrak/obsidian-cli** or the kit's **MCP bridge** (`fs` backend) | no |
| Validate, index, log, wire up MCP, talk to the model | **`kit.py`** | no |
| Fast grep / find / JSON in shell pipelines | ripgrep, fd, jq (optional) | no |

## Swapping a tool out

Nothing here is load-bearing by name. The pieces that touch the outside world sit in slots, and
`kit.py doctor` prints which provider is active in each:

| Slot | Providers | What it does |
|---|---|---|
| `search` | `qmd` · `naive` | find notes for a question |
| `llm` | `openai-compat` | answer from any `/v1/chat/completions` endpoint |
| `embed` | `qmd` | vectors for semantic search |
| `graph` | `builtin` | the vault as nodes and edges |

Selection order, first hit wins: the command-line flag (`--search-provider`), then
`KIT_SEARCH_PROVIDER` in the environment, then `.kit/config.yml` in the vault, then `auto`.

```yaml
# <vault>/.kit/config.yml
providers:
  search: naive        # or qmd, or auto
```

The same file declares which folders and files are confidential as a whole, whatever their notes say (`confidentiality:`, with `confidential_folders` and `confidential_paths`). That block is read strictly, and a broken one hides every note from every reader until it is fixed: [security.md](security.md#scoped-hiding-confidential-folders-and-path-rules).

`auto` takes the first provider that reports itself usable. That is what makes a locked-down
machine survivable: if the package registry is blocked and qmd never installs, the search slot
falls back to `naive` — term frequency over the files, no index, no dependencies — and everything
else carries on. Slower and less clever, but the vault, the model and the tool calls all still work.
The same fallback catches a qmd that is installed but has not indexed anything yet.

The `llm` slot has one provider because it does not need more: LM Studio, llama.cpp's
`llama-server`, Ollama and vLLM all speak the same API. Point `KIT_LLM_BASE_URL` at whichever you
run. Adding a provider means adding a class to `kitproviders.py` and registering it; it does not
mean touching `kit.py`.

## qmd — Query Markup Documents

Tobi Lütke's on-device search engine for Markdown (MIT). BM25 full-text search (SQLite FTS5) + vector search + LLM re-ranking, all local via node-llama-cpp and GGUF models. It ships an MCP server, which is what makes a small local model useful on a large vault.

**Install** — the kit standardises on **Bun** as the runtime: one Homebrew/winget package, one global install, no Node version management, and none of npm's global-bin wrapper problems (there is an open qmd issue about exactly that on Linuxbrew). qmd's own README lists the bun path first.

```bash
brew install oven-sh/bun/bun sqlite   # Windows: winget install Oven-sh.Bun; Linux: curl -fsSL https://bun.sh/install | bash
bun install -g @tobilu/qmd            # global bin in ~/.bun/bin — add it to PATH once
qmd doctor                            # runtime, sqlite-vec, GPU probe
```

`npm install -g @tobilu/qmd` (Node ≥ 22) remains a valid alternative. Windows works natively either way (see [platforms/windows.md](platforms/windows.md) for the CUDA note). There is no Homebrew formula for qmd itself; a Rust port, `rqmd`, exists for people who prefer a compiled binary — check its README for current feature parity before relying on it.

### The commands you will actually use

```bash
qmd collection add ~/Notes/vault --name vault --mask "**/*.md"
qmd context add qmd://vault "My working notes …"     # context is returned with hits — do not skip it
qmd update                                           # re-scan after edits (fast)
qmd embed                                            # vectors for new/changed chunks; -f after a model change
qmd search "latency"                                 # BM25, instant, no models
qmd vsearch "how do we combine keyword and semantic results"   # vector
qmd query "why did we pick qmd"                      # hybrid + expansion + re-rank; --no-rerank is faster on CPU
qmd get "07-knowledge/hybrid-search.md"              # read; supports :line:count and #docid
qmd search "pilot" --format json -n 10               # for scripts and agents; also csv|md|xml|files
qmd status · qmd cleanup · qmd bench <fixture.json>  # health · cache · retrieval quality
```

**Configuration** lives in `~/.config/qmd/index.yml` (a commented starter is in [`config/qmd/index.example.yml`](../config/qmd/index.example.yml)). Notable keys: `ignore:` globs per collection (keep `09-archive/**` and `.obsidian/**` out), `models:` overrides, per-collection `update:` hooks (e.g. `git pull`). `qmd init` creates a **project-local** index in `.qmd/`, which qmd finds by walking up from the working directory. The e2e tests take a different route — they redirect `XDG_CONFIG_HOME`/`XDG_CACHE_HOME` into a temp directory, so your real index is never touched ([testing.md](testing.md)). Checked-in `.qmd` configs are trust-gated; `QMD_TRUST_LOCAL_CONFIG=1` allows them in CI.

**Metadata filters**: notes may carry a `qmd: { metadata: {...} }` frontmatter block and every search surface accepts a JSON filter (`--filter '{"key":"status","operator":"eq","value":"stable"}'`). The vault's OKF frontmatter is *not* automatically the qmd metadata namespace; add the block to notes where you want typed filtering.

**MCP**: `qmd mcp` (stdio) or `qmd mcp --http [--daemon]` on `localhost:8181` (`POST /mcp`, `POST /query`, `GET /health`). Tools: `query`, `get`, `multi_get`, `status`. HTTP keeps the models loaded between calls, which matters on CPU-only machines.

## The official Obsidian CLI

Shipped with Obsidian 1.12 (February 2026), in the standard desktop release since 1.12.4, native binary since 1.12.7. It is a **remote control for the running app** — index-aware, template-aware, and able to do anything the UI can — not a headless tool. If Obsidian is not running, the first command launches it.

**Enable**: Settings → General → *Command line interface* → **Register CLI**. macOS symlinks `/usr/local/bin/obsidian`; Windows adds `Obsidian.com` next to `Obsidian.exe` (put that folder on `PATH`); Linux copies to `~/.local/bin/obsidian`. Test with `obsidian version`.

**Grammar**: `obsidian <command> key=value flag`. Flags are bare words; the only dashed flag is `--copy`. With several vaults, `vault="Name"` first.

```bash
obsidian files                                          # list notes
obsidian read file="03-projects/search-relaunch"
obsidian create name="02-meetings/2026-09-14-sync" template="meeting" silent
obsidian append file="03-projects/search-relaunch" content="- 2026-09-14 — benchmark scheduled"
obsidian daily:append content="- [ ] call legal — me, due 2026-09-15"
obsidian property:set name="health" value="green" file="03-projects/search-relaunch"
obsidian search query="latency" limit=10
obsidian backlinks file="07-knowledge/hybrid-search"
obsidian tasks daily todo
```

Limits: desktop only, app must be running, sequential execution (slow for thousands of files), syntax still evolving. The kit's MCP bridge wraps exactly these commands (`--backend cli`) so a local model can use them with confirmation prompts.

## yakitrak/obsidian-cli (Go)

A third-party, file-based CLI that predates the official one. Works without the app, which makes it the right tool for scripts on servers or in WSL.

```bash
brew tap yakitrak/yakitrak && brew install yakitrak/yakitrak/obsidian-cli      # macOS / Linux
scoop bucket add scoop-yakitrak https://github.com/yakitrak/scoop-yakitrak.git && scoop install obsidian-cli   # Windows
obsidian-cli set-default "vault"
obsidian-cli create "02-meetings/2026-09-14-sync" --content "# Sync"
obsidian-cli search "latency"
obsidian-cli move "old-name" "new-name"      # rewrites links
```

It also edits frontmatter (`obsidian-cli frontmatter ...`); check `obsidian-cli --help` for the current verbs — releases are frequent.

## kit.py and the MCP bridge

Both scripts carry inline dependency metadata (PEP 723), so **uv** runs them without any setup — `uv run kit.py …` — and that is what the installers and docs use. Plain `python kit.py …` works too once PyYAML (and `mcp>=2,<3` for the bridge) is installed.

```bash
uv run kit.py doctor | init | validate | index | log | mcp-config | test
uv run kit.py llm smoke | llm ask [--graph]
uv run kit.py graph build | export | query | neighbors | path | pack [--show-confidential]
uv run kit.py reconcile | proposals list|show|apply|reject
uv run kit.py todos [--show-confidential]
uv run kit.py minutes recap.txt --title "Sync" [--keep-open]
uv run kit.py examples remove [--dry-run]                                # delete the sample notes and repair what referred to them
uv run mcp/vault_bridge.py --backend fs --vault ~/Notes/vault          # MCP server, headless
uv run mcp/vault_bridge.py --backend cli --vault-name "vault"          # MCP server via official CLI
```

Notes marked `sensitivity: confidential`, and the files a confidential folder or path rule in `.kit/config.yml` covers ([security.md](security.md#scoped-hiding-confidential-folders-and-path-rules)), stay out of what `kit.py` hands a model or writes to disk, as they stay out of the bridge's tools:

- `llm ask` leaves a confidential search hit out of the prompt whole (no path, no title, no snippet) and only prints how many it left out: the confidential hits that ranked ahead of the last source it sends. It asks the search for more hits than it uses, so the hits it leaves out do not shrink the list of sources the model gets. With `--graph`, the context packs come from the redacted graph, so a visible note's pack does not name a confidential neighbour either. A confidential note's title counts as confidential, on the bridge and in the CLI alike.
- `graph build`, `export`, `query`, `neighbors`, `path` and `pack` use the same redacted graph as the bridge's graph tools by default: confidential notes, their edges, their findings, and every derived value or finding of another note that refers to one are left out of `.kit/graph.json`, `graph.jsonld`, `graph.nt`, `graph.sqlite` and the printed output. `graph build` prints how many notes it left out; the other graph commands do not. `--show-confidential` includes them, with the same meaning as the bridge's flag; use it only for output that stays with you.
- `graph query` reads `.kit/graph.sqlite`, which records the mode it was built in. A default query rewrites the file without confidential notes first when it was built with `--show-confidential`, by a kit version that did not redact yet, or when any note was added, removed, moved or edited since the build (the file records each note's path and modification time). A file that is only out of date is rewritten in the mode it was built in, except that a default query never answers from one built with `--show-confidential`. `graph query --show-confidential` over a redacted file answers from memory and leaves the file as it is. `--rebuild` forces a rebuild, for example after an edit that kept a note's modification time.
- `todos` leaves tasks from confidential notes out of its summary and its `--json`, and prints how many it left out (with `--json` that line goes to stderr, so stdout stays JSON); `--show-confidential` includes them. The digest file `todos --digest` writes leaves them out even with `--show-confidential`, since it is a file others read. `--sync-done` still ticks matching copies inside confidential notes and names none of them. Details in [reconciliation.md](reconciliation.md#kitpy-todos).
- `minutes` follows one rule: a note may raise its confidentiality, never lower it. By default it links people and projects as the whole vault knows them, and a meeting note that links a confidential note is marked `sensitivity: confidential` itself; the command says so in one line, and log.md and the daily note then name the meeting without its title or path. `--keep-open` links only notes that are not confidential and keeps the template's sensitivity: a name passed with `--people` that links to no such note, confidential or unknown, goes to `attendees:` as written (plain text; `people:` holds only links), a name the recap merely mentions is listed only when it links, and a confidential project is left out. `--force` over a meeting note that is confidential keeps it confidential. Details in [reconciliation.md](reconciliation.md#kitpy-minutes--filing-meeting-notes).
- Rebuild graph files from an older version (`kit.py graph build`) to take confidential nodes off the disk. A visible note's own fields and body can still name a confidential note (an `owner` link, for example); that is the visible note's content, and the bridge serves it the same way.

The bridge exposes `vault_search`, `vault_read_note`, `vault_list_files`, `vault_backlinks`, `graph_context`, `graph_neighbors`, `graph_path`, `graph_query`, `vault_todos` and — unless it is started with `--read-only` — `vault_create_note`, `vault_append_note`, `vault_daily_append`, `vault_set_property`, `file_meeting_minutes`. The `fs` backend refuses paths outside the vault, and overwriting an existing note is denied by the policy layer unless the bridge is started with `--allow-overwrite`; the `cli` backend delegates to the official CLI. `kit.py mcp-config` registers it as `uv run …` when uv is on PATH (override with `--no-uv`). With `--http --port 8765` the bridge serves MCP over streamable HTTP at `http://127.0.0.1:8765/mcp`, which is what a client that cannot spawn a local process needs — an MCP client on Windows talking to a bridge inside WSL, for instance; `mcp-config --bridge-http` points clients at it.

## Supporting tools

`ripgrep` (`rg`) and `fd` are the fastest way to grep/find inside a vault from scripts; `jq` turns `qmd --format json` into pipelines. All three are one `brew`/`winget`/`apt` away and entirely optional.
