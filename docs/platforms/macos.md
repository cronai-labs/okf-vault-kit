# macOS

Apple silicon or Intel, macOS 13+. Everything installs through Homebrew; the script is `scripts/install-macos.sh`.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # if needed
bash scripts/install-macos.sh
```

What it installs — all from Homebrew: `oven-sh/bun/bun` (qmd's runtime), `uv` (runs `kit.py` and the bridge), `sqlite` (Homebrew build — required by qmd for extensions), `ripgrep`, `fd`, `jq`; then `bun install -g @tobilu/qmd`; the **Obsidian** and **LM Studio** casks; yakitrak's `obsidian-cli` from its tap. No Node, no npm, no pip. It skips anything already present and never touches your Obsidian settings.

Afterwards:

1. Launch LM Studio once (registers `lms`), then `lms get openbmb/MiniCPM5-2B-GGUF`.
2. Obsidian → Settings → General → *Command line interface* → **Register CLI** (creates `/usr/local/bin/obsidian`, asks for admin rights).
3. `uv run kit.py doctor`.

Notes:
- Metal acceleration is automatic for qmd (`QMD_LLAMA_GPU=metal`) and LM Studio; prefer MLX builds of chat models when offered.
- No system Python needed: uv downloads a managed CPython on first run if none is suitable (`uv python install 3.12` to do it explicitly).
- `~/.bun/bin` must be on `PATH` for `qmd`; the script appends it to `~/.zshrc`.
- Gatekeeper may quarantine the first launch of LM Studio/Obsidian from casks: right-click → Open.
- iCloud Drive vaults: keep *Optimise Mac Storage* off for the vault folder, or Obsidian sees placeholder files.
