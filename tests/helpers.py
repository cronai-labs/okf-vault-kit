"""Shared test helpers: paths, temp vault copies, environment gates."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"
DOCS = ROOT / "docs"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp"))

import kitlib  # noqa: E402
import kitproviders  # noqa: E402


def temp_vault() -> Path:
    """Copy the template vault into a fresh temp dir (caller removes it)."""
    tmp = Path(tempfile.mkdtemp(prefix="okf-vault-"))
    dst = tmp / "vault"
    shutil.copytree(VAULT, dst)
    return dst


def has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def llm_base_url() -> str:
    return os.environ.get("KIT_LLM_BASE_URL", "http://localhost:1234/v1")


def llm_reachable(timeout: float = 2.0) -> list[str]:
    """Return model ids if an OpenAI-compatible endpoint answers, else [].

    Through the kit's own opener: on a machine with HTTP_PROXY set, a bare urlopen sends the
    loopback probe to the proxy and the whole LLM suite skips itself while LM Studio is running.
    """
    try:
        with kitproviders.urlopen(f"{llm_base_url()}/models", timeout=timeout) as r:
            return [m.get("id", "") for m in json.load(r).get("data", [])]
    except Exception:  # noqa: BLE001
        return []


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")
