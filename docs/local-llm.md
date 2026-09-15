# Local LLM — install a GUI, run a small model everywhere, get an endpoint, give the chat window tools

The goal is one model that runs on any laptop (2B class, ~1.6 GB quantised), reachable two ways: as an **OpenAI-compatible endpoint** for scripts and as a **chat window with MCP tools** that can search and edit the vault.

## Choose the app

| App | Best for | Endpoint | MCP in chat | Notes |
|---|---|---|---|---|
| **LM Studio** (default in this kit) | polished chat, model discovery, `lms` CLI, headless `llmster` | OpenAI + Anthropic compatible on `:1234` | yes, `mcp.json` (Cursor notation) | proprietary app, free for personal and work use |
| **Unsloth Desktop** | open-source, also fine-tunes, self-healing tool calls, sandboxed code exec | OpenAI compatible | yes | beta since Aug 2026; Tauri app; `unsloth start claude\|codex` connects agents |
| **Ollama** | servers, homelabs, k3s | OpenAI compatible on `:11434` | no chat UI worth the name | import any GGUF with a `Modelfile` |
| **llama.cpp** `llama-server` | minimal, scriptable | OpenAI compatible | no | `llama-server -hf openbmb/MiniCPM5-2B-GGUF` |

Everything below works with any of them; the kit reads `KIT_LLM_BASE_URL` (default `http://localhost:1234/v1`).

## LM Studio workflow

1. **Install** — [platform pages](platforms/macos.md) (`brew install --cask lm-studio`, `winget install ElementLabs.LMStudio`, AppImage on Linux). Launch it once so `lms` gets registered.
2. **Get the model**

   ```bash
   lms get openbmb/MiniCPM5-2B-GGUF          # choose Q4_K_M (≈1.6 GB); Q8_0 if you have RAM to spare
   lms ls                                     # confirm
   ```

   Any GGUF or MLX repo works the same way (`lms get <user>/<repo>[@quant]`, or paste a Hugging Face URL). Alternatives are compared in [models.md](models.md).
3. **Load and chat** — pick the model in the chat window. In the model settings, set context length to 8–16k (128k is supported, but a 2B model on CPU gets slow past 16k). Leave the **thinking toggle** off unless you want reasoning: MiniCPM5-2B spends its chain of thought out of the same token budget as the answer, so with thinking on a small budget returns nothing at all ([models.md](models.md)). The kit budgets for it — `KIT_LLM_REASONING_TOKENS`, 512 by default — but the toggle is the cheaper fix when the answer does not need reasoning.
4. **Start the server**

   ```bash
   lms server start --port 1234              # or Developer tab → Start server
   curl http://localhost:1234/v1/models
   uv run kit.py llm smoke
   ```

   Endpoints: `POST /v1/chat/completions`, `/v1/embeddings`, `/v1/responses`, `GET /v1/models` (OpenAI); `POST /v1/messages` (Anthropic-compatible); `POST /api/v1/chat` (LM Studio's REST API, which can call MCP servers server-side when *Allow calling servers from mcp.json* is enabled in Server Settings). Models load on first request (JIT); the first call is slow.
5. **Give the chat window tools**

   ```bash
   uv run kit.py mcp-config --client lmstudio --write     # writes ~/.lmstudio/mcp.json (backup kept)
   ```

   Restart LM Studio → open a chat → *Program* tab in the right sidebar → toggle **qmd** and **obsidian-vault** on. LM Studio shows a confirmation dialog per tool call; choose *always allow* for `query`, `get`, `obsidian_search`, `obsidian_read_note`, and keep asking for writes. The bridge adds its own guard rails (confidential notes hidden, people/system folders read-only, size and rate limits; `--propose` for a human-applied queue) — [security.md](security.md). Paste the system prompt from [`config/lmstudio/system-prompt.md`](../config/lmstudio/system-prompt.md) (`Cmd/Ctrl+Shift+E` opens the system-prompt editor).
6. **Prompts that work with a 2B model** — retrieval-grounded, one task at a time, explicit output shape:
   > Use the qmd query tool to find notes about the search relaunch. Then answer in five bullets: what is due, what is at risk, who owns what. Cite note paths.

   > Read `03-projects/search-relaunch.md` with obsidian_read_note. Draft the next update-log line for today from the latest actions. Do not write anything yet; show me the line.

   > Append this to today's daily note: "- benchmark scheduled for Tuesday — see search relaunch". Use obsidian_daily_append.

   > Use graph_context on the search relaunch, then tell me who owns it, which decisions it depends on, and whether anything it relies on is stale.

   > Here is the recap of today's sync: … File it as "Search relaunch sync" for today with Alex and Sam, then show me the actions you extracted.
7. **Use the same tools from scripts**

   ```bash
   uv run kit.py llm ask "what is blocking the pilot" --collection vault   # qmd → model, with citations
   ```

8. **Headless / remote** — `llmster` is LM Studio's engine as a standalone daemon for macOS, Windows and Linux (`lms daemon up`, `lms server start`); the desktop app can also run its server on login without a window. For a homelab, Ollama on k3s with the OpenAI-compatible API is the boring, reliable choice; point `KIT_LLM_BASE_URL` at it.

## Unsloth Desktop workflow

1. Download the native app (macOS `.dmg`, Windows `.exe`, Linux `.deb`/AppImage) from `unsloth.ai` or GitHub Releases; or `curl -fsSL https://unsloth.ai/install.sh | sh` (Windows: `irm https://unsloth.ai/install.ps1 | iex`) for the web UI variant, then `unsloth studio`.
2. *Model hub* → search `MiniCPM5-2B` → choose the GGUF quantisation that fits the device → download → chat.
3. Tools: Unsloth supports MCP servers and tool calling with permission prompts; register the same two servers as above (the kit's `mcp-config` output is plain Cursor-style JSON you can paste).
4. Endpoint: Unsloth exposes an OpenAI-compatible API; set `KIT_LLM_BASE_URL` accordingly. `unsloth start claude` / `codex` connects coding agents to the loaded model.
5. Caution: the *Remote access* feature publishes the app through a Cloudflare HTTPS tunnel. Convenient, but it is a US-operated relay in front of your notes — keep it off if data residency matters to you.

## Ollama (homelab / servers)

Check `ollama search minicpm5` first; if the library has no build yet, import the GGUF you downloaded from Hugging Face:

```bash
cat > Modelfile <<'M'
FROM ./MiniCPM5-2B-Q4_K_M.gguf
PARAMETER num_ctx 16384
M
ollama create minicpm5-2b -f Modelfile && ollama run minicpm5-2b
export KIT_LLM_BASE_URL=http://<host>:11434/v1
```

Ollama has no MCP-capable chat window; use it as the endpoint behind `kit.py llm ask`, behind Obsidian plugins, or behind an agent that hosts MCP itself (Claude Code, Codex, Hermes, OpenCode).

## Windows + WSL2

Run the GUI on Windows; run `qmd` and the kit wherever the vault lives. The Windows server is reachable from WSL2 at `localhost:1234` when `.wslconfig` has `networkingMode=mirrored` (Windows 11 22H2+); otherwise use the Windows host IP from `ip route | grep default`. Keep the vault on the side that runs Obsidian — cross-filesystem I/O is slow and file watching is unreliable.

## Sanity limits for a 2B model

Good at: summarising retrieved notes, extracting actions from a pasted recap, drafting in a given format, deciding which tool to call. Weak at: arithmetic, dates, anything from memory, long multi-step plans. The kit's `test_e2e_llm.py` encodes the bar: follow a one-line instruction, answer a grounded question from a note, and (optionally) emit a well-formed tool call.
