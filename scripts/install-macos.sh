#!/usr/bin/env bash
# OKF Vault Kit — macOS installer. Everything comes from Homebrew except qmd itself, which Bun installs
# as a single-binary-managed package (no Node/npm on the machine). Idempotent.
set -euo pipefail

command -v brew >/dev/null 2>&1 || { echo "Homebrew missing: https://brew.sh"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
formula() { brew list --formula "$1" >/dev/null 2>&1 || brew install "$1"; }
cask()    { brew list --cask "$1" >/dev/null 2>&1 || brew install --cask "$1"; }
# A managed machine has apps IT deployed outside Homebrew: brew has no record of them, and
# `brew install --cask` refuses to overwrite an occupied /Applications entry. Check the app itself.
app()     { [ -d "/Applications/$2" ] || cask "$1"; }

echo "== runtimes: bun (qmd), uv (kit.py + MCP bridge), Homebrew sqlite (qmd extensions)"
formula oven-sh/bun/bun
formula uv
formula sqlite

echo "== shell tools: ripgrep fd jq"
for f in ripgrep fd jq; do formula "$f"; done

echo "== qmd via bun (global bin: ~/.bun/bin)"
export PATH="$HOME/.bun/bin:$PATH"
have qmd || bun install -g @tobilu/qmd
# shellcheck disable=SC2016  # the line is written literally; $HOME expands when zsh reads it, not now
grep -q '.bun/bin' "$HOME/.zshrc" 2>/dev/null || echo 'export PATH="$HOME/.bun/bin:$PATH"' >> "$HOME/.zshrc"

echo "== apps: Obsidian, LM Studio"
app obsidian "Obsidian.app"
app lm-studio "LM Studio.app"

echo "== yakitrak/obsidian-cli (headless vault CLI)"
have obsidian-cli || { brew tap yakitrak/yakitrak >/dev/null; brew install yakitrak/yakitrak/obsidian-cli; }

cat <<'NEXT'

Next:
  1. Launch LM Studio once (registers `lms`), then:  lms get openbmb/MiniCPM5-2B-GGUF && lms server start
  2. Obsidian -> Settings -> General -> Command line interface -> Register CLI
  3. uv run kit.py doctor
  4. uv run kit.py init --target ~/Notes/vault --actor human:<you>
No pip, no venv: `uv run` reads the dependency list embedded in kit.py and the bridge.
Optional: Unsloth Desktop (open-source alternative GUI) — https://unsloth.ai
NEXT
