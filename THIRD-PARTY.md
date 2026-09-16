# Third-party components

The kit itself is MIT (see [LICENSE](LICENSE)). It installs nothing silently: everything below is
something you choose to install, and the licence is the publisher's, not ours. Listed because the
first question from a corporate reviewer is always "what else comes with it".

## Tools the kit drives

| Component | Licence | How it arrives | Notes |
|---|---|---|---|
| [qmd](https://www.npmjs.com/package/@tobilu/qmd) | MIT | `bun install -g @tobilu/qmd` (or npm) | On-device hybrid search. Optional — the kit falls back to its own search provider when qmd is absent. |
| [Bun](https://bun.sh) | MIT | Homebrew / winget | Runtime for qmd only. |
| [uv](https://github.com/astral-sh/uv) | MIT or Apache-2.0 | Homebrew / winget | Runs `kit.py` and the bridge with their declared dependencies. |
| [MCP Python SDK](https://pypi.org/project/mcp/) | MIT | `uv run` / `pip`, pinned `>=2,<3` | Only needed for the MCP bridge. |
| [PyYAML](https://pypi.org/project/PyYAML/) | MIT | `uv run` / `pip` | Frontmatter parsing. |
| [Obsidian](https://obsidian.md) | Proprietary, free for personal use | obsidian.md / winget | **Commercial use needs a paid licence.** Check this before rolling the kit out at work — it is the one component with a per-seat cost. |
| [LM Studio](https://lmstudio.ai) | Proprietary, free for personal and work use | lmstudio.ai / winget | Optional. Any OpenAI-compatible endpoint works instead: llama.cpp's `llama-server`, Ollama, vLLM. |

## Command-line tools the installers add

`scripts/install-macos.sh`, `install-windows.ps1` and `install-wsl.sh` install these as part of the
toolchain rather than prompting for each one, so a reviewer reading the scripts should see them
listed here. They all come from your platform's own package manager, the installers are optional,
and `kit.py doctor` only reports what is present — it installs nothing. The WSL script also pulls
`curl`, `git` and `unzip` from Ubuntu's archive to bootstrap bun and uv.

| Component | Licence | How it arrives | Notes |
|---|---|---|---|
| [ripgrep](https://github.com/BurntSushi/ripgrep) | MIT or Unlicense (dual) | Homebrew `ripgrep` / winget `BurntSushi.ripgrep.MSVC` / apt `ripgrep` | Fast search; qmd and the kit's own fallback search use it when present. |
| [fd](https://github.com/sharkdp/fd) | MIT or Apache-2.0 (dual) | Homebrew `fd` / winget `sharkdp.fd` / apt `fd-find` | File finder. The Debian package installs the binary as `fdfind`. |
| [jq](https://jqlang.github.io/jq/) | MIT | Homebrew `jq` / winget `jqlang.jq` / apt `jq` | JSON on the command line; used by the shell examples in the docs. |
| [SQLite](https://sqlite.org) | Public domain | Homebrew `sqlite` (macOS only) | qmd needs a `sqlite3` with loadable extensions; macOS's own build does not have them. |
| [yakitrak/obsidian-cli](https://github.com/Yakitrak/obsidian-cli) | check licence at the repository before rolling it out | Homebrew tap `yakitrak/yakitrak` (macOS) / scoop bucket `scoop-yakitrak` (Windows, optional) | Headless vault CLI. Both the tap and the bucket are the author's own, not Homebrew's or scoop's official ones — a third party you are choosing to trust. |

## Models

Model weights are **not** redistributed by this kit; you download them from the publisher. Licences
differ and some are not OSI-style open source:

| Model | Licence | Note |
|---|---|---|
| MiniCPM5-2B (default) | Apache-2.0 | The kit's recommended default. |
| Qwen3.5-2B | Apache-2.0 | Stronger in German. |
| Gemma 4 E2B | Google's **Gemma Terms of Use** | Not an OSI licence. It carries use restrictions and a redistribution clause — read it before deploying in a company. |
| Granite 4.2 3B | Apache-2.0 | IBM; enterprise documentation. |
| Ministral 3 | check the current release | Mistral has shipped both Apache-2.0 and research-only licences; verify per release. |

qmd downloads its own embedding, reranking and query-expansion models (EmbeddingGemma, Qwen3
Reranker, a query-expansion model) into `~/.cache/qmd/models`. EmbeddingGemma is under the **Gemma
Terms of Use** as well.

GGUF is a plain tensor container with no code execution path, unlike pickle-based formats. Download
from the publisher's own Hugging Face organisation.

## Specifications

[Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog) v0.2 — Apache-2.0,
Google Cloud. The vault is an OKF bundle; the spec is a document, not a dependency.
