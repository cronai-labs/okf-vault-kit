# Quickstart — 15 minutes to a vault your local model can use

You will end up with: an Obsidian vault (OKF-conformant), `qmd` indexing it, a local LLM in LM Studio that can search and write the vault through MCP tools, and a test suite that proves the pieces work together.

## 0. Prerequisites

| Need | Why | Check |
|---|---|---|
| **uv** | runs `kit.py`, the tests and the MCP bridge with their dependencies auto-provisioned (no pip, no venv; fetches a Python if none is present) | `uv --version` |
| **Bun** | runs `qmd` (single binary; no Node/npm) | `bun --version` |
| Obsidian 1.12+ | vault UI, Bases, official CLI | Settings → About |
| 8 GB RAM, ~4 GB disk | a 2B model + qmd's three GGUF models | — |

Platform installers do this for you: [macOS](platforms/macos.md) · [Windows via WSL2](platforms/wsl2.md) · [Windows natively](platforms/windows.md) · [Linux](platforms/linux.md).

**On a work machine?** Read [proxy.md](platforms/proxy.md) before you start — a corporate proxy with TLS inspection is the single most common reason step 1 fails. [it-review.md](it-review.md) is the page to forward if someone needs to approve this.

## 1. Get the kit and check the machine

```bash
git clone <your-fork>/okf-vault-kit && cd okf-vault-kit     # or unzip the release
uv run kit.py doctor
```

`uv run` reads the dependency list embedded at the top of `kit.py` and provisions it in a cached, isolated environment — nothing is installed globally. (Plain `python kit.py` works too after `pip install pyyaml`.) `doctor` prints one line per tool with the install hint when something is missing.

## 2. Create your vault

```bash
uv run kit.py init --target ~/Notes/vault --actor human:yourhandle
```

Copies the template, stamps your actor id into every `generated.by`, writes the OKF `index.md`, and — if `qmd` is already installed — registers the vault as a qmd collection.

Open the folder in Obsidian (**Open folder as vault**), press `Ctrl/Cmd + D`, and read `99-system/getting-started.md` inside the vault. Ten minutes.

## 3. Local search with qmd

```bash
brew install oven-sh/bun/bun sqlite         # Windows: winget install Oven-sh.Bun
bun install -g @tobilu/qmd                  # ~/.bun/bin on PATH; npm install -g @tobilu/qmd also works
qmd collection add ~/Notes/vault --name vault --mask "**/*.md"
qmd context add qmd://vault "My working notes: journal, meetings, projects, people, decisions, knowledge"
qmd embed                                   # downloads the embedding model once (~300 MB)
qmd query "what is blocking the pilot"      # hybrid search with re-ranking
```

German or mixed-language notes? Switch the embedding model before the first `embed` — see [models.md](models.md#embedding-models-for-qmd).

## 4. A local model with an endpoint

```bash
# LM Studio: install, launch once, then:
lms get openbmb/MiniCPM5-2B-GGUF            # pick the Q4_K_M quant when asked (~1.6 GB)
lms server start --port 1234                # OpenAI-compatible API at http://localhost:1234/v1
uv run kit.py llm smoke                     # lists models, runs a one-line completion
uv run kit.py llm ask "why did we choose qmd" --collection vault
```

Prefer Unsloth Desktop or Ollama? Same endpoint idea, different app — [local-llm.md](local-llm.md).

## 5. Give the chat window tools (MCP)

```bash
uv run kit.py mcp-config --client lmstudio --vault ~/Notes/vault --write
```

Pass your own vault. The bridge serves whatever `--vault` says, and its default is the sample vault inside the checkout — so without it the client answers from the sample notes and writes into the checkout instead of your vault. `--write` refuses that on purpose.

This adds **obsidian-vault** to `~/.lmstudio/mcp.json` — the kit's bridge: read, search, backlinks, create, append, daily note, set property, plus the graph, todos and minutes tools. qmd's own MCP server is deliberately *not* registered: it has no policy layer, so it would answer from an index that includes the `sensitivity: confidential` notes the bridge hides. The bridge's own search uses the same qmd index and applies the policy. Pass `--with-qmd-mcp` if you want qmd's tools anyway and accept that. The bridge is registered as `uv run …/obsidian_bridge.py`, so LM Studio can start it on any machine that has uv — the MCP SDK is provisioned on first launch. Restart LM Studio, open a chat, enable both tool sets in the *Program* panel, then try:

> Search my vault for the search relaunch and tell me what is blocking the pilot. Then append a one-line summary to today's daily note.

LM Studio asks you to confirm each tool call the first time. Say yes to reads; read the arguments before you allow writes. The bridge itself hides confidential notes and refuses writes to people and system folders by default — [security.md](security.md) has the knobs (`--read-only`, `--allow-write`, `--propose`).

## 6. Prove it

```bash
uv run --with 'mcp>=2,<3' kit.py test       # vault, OKF, links, CLI, bridge, docs — offline
KIT_E2E_EMBED=1 uv run kit.py test -p "test_e2e_*.py"   # + qmd vector/hybrid + live endpoint
```

Everything that needs a tool or an endpoint skips itself cleanly when that piece is missing. [testing.md](testing.md) lists every switch.

## Where next

- How the vault is organised and why: [vault-guide.md](vault-guide.md)
- Every tool, every command: [tools.md](tools.md)
- The Obsidian plugin question: [plugins.md](plugins.md)
- Worked examples on the sample notes: [examples.md](examples.md)
- The vault as a graph — ontology, exports, SQL: [knowledge-graph.md](knowledge-graph.md)
- Hubs vs notes, todos, filing minutes, conflict policy: [reconciliation.md](reconciliation.md)
- What the model may touch, and how to sandbox the tools: [security.md](security.md)
- Something odd? [faq.md](faq.md)
