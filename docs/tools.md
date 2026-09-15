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

Both scripts carry inline dependency metadata (PEP 723), so **uv** runs them without any setup — `uv run kit.py …` — and that is what the installers and docs use. Plain `python kit.py …` works too once PyYAML (and `mcp<2` for the bridge) is installed.

```bash
uv run kit.py doctor | init | validate | index | log | mcp-config | llm smoke | llm ask | test
uv run mcp/obsidian_bridge.py --backend fs --vault ~/Notes/vault          # MCP server, headless
uv run mcp/obsidian_bridge.py --backend cli --vault-name "vault"          # MCP server via official CLI
```

The bridge exposes `obsidian_search`, `obsidian_read_note`, `obsidian_list_files`, `obsidian_backlinks`, `graph_context`, `graph_neighbors`, `graph_path`, `graph_query`, `vault_todos` and — unless it is started with `--read-only` — `obsidian_create_note`, `obsidian_append_note`, `obsidian_daily_append`, `obsidian_set_property`, `file_meeting_minutes`. The `fs` backend refuses paths outside the vault and never overwrites unless told to; the `cli` backend delegates to the official CLI. `kit.py mcp-config` registers it as `uv run …` when uv is on PATH (override with `--no-uv`). With `--http --port 8765` the bridge serves MCP over streamable HTTP at `http://127.0.0.1:8765/mcp`, which is what a client that cannot spawn a local process needs — an MCP client on Windows talking to a bridge inside WSL, for instance; `mcp-config --bridge-http` points clients at it.

## Supporting tools

`ripgrep` (`rg`) and `fd` are the fastest way to grep/find inside a vault from scripts; `jq` turns `qmd --format json` into pipelines. All three are one `brew`/`winget`/`apt` away and entirely optional.
