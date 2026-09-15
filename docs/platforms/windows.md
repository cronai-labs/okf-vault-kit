# Windows, natively

**Try [WSL2](wsl2.md) first if the machine has it.** uv and Bun install more predictably on the
Linux side, and behind a TLS-inspecting proxy the difference is measured in hours. This page is the
fallback lane, and it works — it just has more edges.

Windows 10 22H2 / Windows 11, PowerShell 7 recommended.

## Install

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install-windows.ps1
```

winget installs **Bun** (`Oven-sh.Bun`), **uv** (`astral-sh.uv`), **Obsidian**, **LM Studio**,
`ripgrep`, `fd`, `jq`; then `bun install -g @tobilu/qmd`. No Node, no npm, no pip.

**Use winget rather than the vendors' `curl | iex` installers.** winget goes through the Microsoft
CDN and the system proxy, which managed networks generally permit; astral.sh and GitHub release
downloads are frequently blocked. If uv fails to install natively on a work machine, that is almost
always what happened — [proxy.md](proxy.md) has the fix, and `UV_SYSTEM_CERTS=1` is usually the
whole of it.

If winget is missing from the image: install *App Installer* from the Microsoft Store, ask IT for
the MSIs, or get uv from an internal PyPI mirror with `pip install uv`.

Then:

1. Launch LM Studio once; `lms get openbmb/MiniCPM5-2B-GGUF`; `lms server start`. Or skip it and run
   `llama-server -m model.gguf --port 1234`, or Ollama — the kit only wants an OpenAI-compatible URL.
2. Obsidian → Settings → General → *Command line interface* → **Register CLI**. This places
   `Obsidian.com` next to `Obsidian.exe`; add that folder to `PATH` if registration did not.
   Test with `obsidian version`. Optional — the kit's `fs` backend does not need it.
3. `uv run kit.py doctor`.

## Paths

`mcp-config` writes absolute paths, so `mcp.json` works regardless of where Python lives.
`~/.lmstudio/mcp.json` is `%USERPROFILE%\.lmstudio\mcp.json`. qmd lands in `%USERPROFILE%\.bun\bin`;
the installer puts it on the session PATH — make it permanent via System Properties if a new
terminal cannot find it.

## GPU

- qmd with CUDA: if embedding crashes (`ggml-cuda.cu`), set `QMD_EMBED_PARALLELISM=1`. Vulkan
  (`QMD_LLAMA_GPU=vulkan`) is the stable fallback; `QMD_FORCE_CPU=1` always works.
- LM Studio picks CUDA/Vulkan itself; check *Runtime* in the app if a model loads on CPU unexpectedly.

## Windows containers

Ruled out, and worth saying why so nobody re-litigates it: the container images this kind of stack
needs are Linux-based, Windows base images cannot run Obsidian or a model host, and there is no
practical GPU path for llama.cpp inside a Windows container. Docker Desktop's normal mode on Windows
runs *Linux* containers on WSL2 — that is the WSL2 lane, not a Windows-container one. If you want
isolation, use [WSL2](wsl2.md) or a separate Windows user account.
