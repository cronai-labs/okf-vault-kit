#!/usr/bin/env bash
# Test kit bootstrap for macOS, Linux and WSL2. Run from the repository root:
#   ./testkit/run.sh            full run
#   ./testkit/run.sh --quick    skip the qmd index and model legs
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  cat >&2 <<'MSG'
uv is not installed, and the kit needs it.

  macOS/Linux/WSL:  curl -LsSf https://astral.sh/uv/install.sh | sh
  behind a proxy:   see docs/platforms/proxy.md first -- astral.sh is often blocked

Then open a new shell and run this script again.
MSG
  exit 1
fi

# --no-project keeps uv from building a .venv inside the tester's checkout.
exec uv run --no-project "$here/testkit/testkit.py" "$@"
