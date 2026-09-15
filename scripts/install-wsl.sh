#!/usr/bin/env bash
# OKF Vault Kit — WSL2 (Ubuntu/Debian) installer for the CLI side. GUI apps stay on Windows.
# Uses the official single-binary installers for bun and uv (Homebrew on Linux works too: brew install oven-sh/bun/bun uv).
set -euo pipefail
have() { command -v "$1" >/dev/null 2>&1; }
# A managed network is the usual reason a fetch dies here, and the script must name its own fix.
blocked() {
  echo "could not fetch $1 — on a managed machine a proxy or TLS inspection is the usual reason." >&2
  echo "read docs/platforms/proxy.md: proxy variables, the company CA, and the alternatives" >&2
  echo "(Homebrew on Linux, an internal npm mirror, 'pip install uv')." >&2
  exit 1
}

sudo apt-get update -qq || blocked "the apt archives"
sudo apt-get install -y -qq ripgrep fd-find jq curl git unzip || blocked "the apt packages"
have fd || sudo ln -sf "$(command -v fdfind)" /usr/local/bin/fd

have bun || { curl -fsSL https://bun.sh/install | bash || blocked "bun (bun.sh)"; export PATH="$HOME/.bun/bin:$PATH"; }
have uv  || { curl -LsSf https://astral.sh/uv/install.sh | sh || blocked "uv (astral.sh)"; export PATH="$HOME/.local/bin:$PATH"; }
have qmd || bun install -g @tobilu/qmd || blocked "qmd (the npm registry)"

cat <<'NEXT'

Next (WSL side; open a new shell so PATH picks up ~/.bun/bin and ~/.local/bin):
  uv run kit.py doctor --base-url http://localhost:1234/v1     # needs networkingMode=mirrored in %USERPROFILE%\.wslconfig
  qmd collection add /mnt/c/Users/<you>/Notes/vault --name vault --mask "**/*.md"
Windows side: install Obsidian + LM Studio (scripts/install-windows.ps1 or manually), start the LM Studio server.
NEXT
