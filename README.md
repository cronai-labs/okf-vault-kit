# OKF Vault Kit

An Obsidian vault template that is also a Google **Open Knowledge Format** bundle, wired to **qmd** (Tobi Lütke's on-device hybrid search), the **Obsidian CLI**, and a **local 2B-class LLM** that can search and edit the vault through MCP — on macOS, Windows (native or WSL2) and Linux — with end-to-end tests.

For managers, project/product managers and engineers: same structure, three lanes.

> **This is an alpha.** Read [ALPHA.md](ALPHA.md) for what we are asking of you and what is not
> finished yet, and [FEEDBACK.md](FEEDBACK.md) for where to send what you find. The version is in
> [`VERSION`](VERSION) and printed by `kit.py --version`.

```bash
git clone https://github.com/cronai-labs/okf-vault-kit && cd okf-vault-kit
uv run kit.py doctor                                   # what is installed, what is missing
uv run kit.py init --target ~/Notes/vault --actor human:you
export KIT_VAULT=~/Notes/vault                         # so the commands below mean YOUR vault

bun install -g @tobilu/qmd && qmd collection add ~/Notes/vault --name vault && qmd embed
lms get openbmb/MiniCPM5-2B-GGUF && lms server start   # or Unsloth Desktop / Ollama
uv run kit.py mcp-config --client lmstudio --write     # the chat window gets the tools
uv run kit.py llm ask "what is blocking the pilot" --graph   # search + graph facts → model, with citations
uv run kit.py graph build && uv run kit.py reconcile   # the vault as a graph; hubs vs notes
uv run --with 'mcp>=2,<3' kit.py test                  # prove it
```

`uv run` provisions each script's declared dependencies on the fly; plain `python kit.py …` works too once PyYAML is installed.

## What is in the box

| Path | What |
|---|---|
| `vault/` | The template vault (and sample): OKF-conformant frontmatter, 13 templates, 3 Bases with 15 views, example notes, conventions, AI workflows, `99-system/ontology.yml`, `context.jsonld` |
| `kitproviders.py` | Swappable slots — `search` (qmd · naive), `llm` (any OpenAI-compatible endpoint), `embed`, `graph`; chosen per vault in `.kit/config.yml` |
| `kit.py` · `kitlib.py` | Cross-platform CLI: `doctor`, `init`, `validate` (OKF + links + ontology), `index`, `log`, `mcp-config`, `llm smoke\|ask [--graph]`, `graph build\|export\|query\|neighbors\|path\|pack`, `reconcile`, `todos`, `minutes`, `proposals`, `test` |
| `kitgraph.py` · `kitrecon.py` | The vault as a graph (ontology, derived facts, exports, SQLite) · reconciliation (hubs, todos, minutes) |
| `mcp/obsidian_bridge.py` · `mcp/bridge_policy.py` | MCP server exposing the vault to a local model — read/write tools plus `graph_context`, `graph_neighbors`, `graph_path`, `graph_query`, `vault_todos`, `file_meeting_minutes`; policy layer: read-only, allow/deny lists, confidential hiding, limits, propose mode, audit, bearer token |
| `scripts/` | `install-macos.sh`, `install-windows.ps1`, `install-wsl.sh` |
| `config/` | qmd `index.yml` starter, LM Studio `mcp.json` example and system prompt |
| `tests/` | `unittest`/pytest suite: structure, OKF, links, CLI, bridge, docs; qmd and LLM e2e tests that skip cleanly when the tool or endpoint is absent |
| `.github/workflows/ci.yml` | ubuntu / macos / windows matrix |

## Documentation

| Read | When |
|---|---|
| [docs/quickstart.md](docs/quickstart.md) | first 15 minutes |
| [docs/vault-guide.md](docs/vault-guide.md) | how the vault is organised, properties, lanes, rhythm |
| [docs/tools.md](docs/tools.md) | qmd, official Obsidian CLI, yakitrak obsidian-cli, kit.py, the bridge |
| [docs/local-llm.md](docs/local-llm.md) | LM Studio / Unsloth Desktop / Ollama: endpoint + chat with tools |
| [docs/models.md](docs/models.md) | the 2026 2B class, quantisation, qmd's models, German notes |
| [docs/plugins.md](docs/plugins.md) | plugin recommendations and the approval process |
| [docs/okf.md](docs/okf.md) | how the vault maps to OKF v0.2 |
| [docs/examples.md](docs/examples.md) | eleven walkthroughs on the sample notes |
| [docs/testing.md](docs/testing.md) | what is tested, switches, CI |
| [docs/knowledge-graph.md](docs/knowledge-graph.md) | the vault as a graph: ontology, exports (JSON / N-Triples / JSON-LD / SQLite), derived facts, queries |
| [docs/reconciliation.md](docs/reconciliation.md) | hubs vs notes, todo surfacing and sync, filing meeting minutes, graph-assisted chat, conflict policy |
| [docs/security.md](docs/security.md) | threat model, the bridge policy layer (read-only, allow/deny, confidential hiding, propose mode, audit, bearer token), and why OS-level isolation instead of containers |
| [docs/faq.md](docs/faq.md) | the questions that come up |
| [docs/platforms/wsl2.md](docs/platforms/wsl2.md) · [macos.md](docs/platforms/macos.md) · [windows.md](docs/platforms/windows.md) · [linux.md](docs/platforms/linux.md) | per-OS install; WSL2 is the recommended lane on Windows |
| [docs/platforms/proxy.md](docs/platforms/proxy.md) | managed machines: corporate proxy, company CA, blocked registries |
| [docs/it-review.md](docs/it-review.md) | the page to forward to your security team |

## Requirements

Two runtimes, both single binaries from Homebrew/winget: **uv** (Python 3.11+, PyYAML and the MCP SDK are provisioned automatically from the scripts' inline metadata) and **Bun** (for qmd). Obsidian 1.12+ for Bases and the official CLI. An 8 GB machine runs the whole stack. `requirements.txt` exists for people who prefer pip.

## Status

Everything offline is executed by the test suite. qmd and LLM end-to-end tests run wherever those tools exist; they are gated, not mocked. Bases views were validated structurally and need one visual check in Obsidian after edits.

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). MIT — see [LICENSE](LICENSE); what else you end up installing, and under which licence, is in [THIRD-PARTY.md](THIRD-PARTY.md) (Obsidian itself is free for commercial use since its 2025-02-20 licence change). Changes in [CHANGELOG.md](CHANGELOG.md).
