# Linux (documentation only — no install script)

Any modern distribution. The pieces map one-to-one onto macOS/Windows; only the package sources differ.

| Piece | Debian/Ubuntu | Fedora | Arch |
|---|---|---|---|
| uv (runs kit.py, tests, bridge) | `curl -LsSf https://astral.sh/uv/install.sh \| sh`, or Homebrew on Linux `brew install uv` | same | `pacman -S uv` |
| Bun (runs qmd) | `curl -fsSL https://bun.sh/install \| bash`, or `brew install oven-sh/bun/bun` | same | AUR `bun-bin` |
| ripgrep, fd, jq | `apt install ripgrep fd-find jq` (`fd` is `fdfind`) | `dnf install ripgrep fd-find jq` | `pacman -S ripgrep fd jq` |
| qmd | `bun install -g @tobilu/qmd` (system SQLite is fine on Linux; `npm install -g @tobilu/qmd` also works) | same | same |
| Obsidian | AppImage from obsidian.md, Flatpak `md.obsidian.Obsidian`, or `.deb` | AppImage / Flatpak | `obsidian` (AUR/community) |
| Obsidian CLI | Settings → General → Command line interface → Register (copies to `~/.local/bin/obsidian`) | same | same |
| LM Studio | AppImage; `llmster` for a headless service (`lms server start` without a desktop) | same | same |
| Unsloth Desktop | `.deb` or AppImage; `curl -fsSL https://unsloth.ai/install.sh \| sh` for the web UI | same | same |
| Ollama | `curl -fsSL https://ollama.com/install.sh \| sh` | same | same |

```bash
uv run kit.py doctor                         # no pip, no venv — deps come from the script's inline metadata
uv run kit.py init --target ~/notes/vault --actor human:you
```

Notes:

- GPU: qmd auto-detects CUDA/Vulkan; force with `QMD_LLAMA_GPU=cuda|vulkan|false`. LM Studio and Ollama pick up NVIDIA drivers automatically; AMD via ROCm/Vulkan.
- Flatpak Obsidian runs in a sandbox: the CLI registration and the MCP bridge's `cli` backend may not see it; use the AppImage/deb, or the bridge's `fs` backend.
- Servers/homelab: run Ollama or `llmster` as a systemd service, `qmd mcp --http --daemon --host 0.0.0.0` behind your own auth (the endpoints are unauthenticated), and point the kit at them with `KIT_LLM_BASE_URL`.
