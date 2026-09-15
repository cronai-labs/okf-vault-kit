# Windows with WSL2 — the recommended lane

If your machine already runs a WSL2 distro, install the kit there rather than on Windows natively.
The Linux side installs cleanly, apt already knows about your proxy, and the two runtimes the kit
needs — uv and Bun — behave predictably. Native Windows is the fallback, not the default:
[windows.md](windows.md).

The split: **GUI stays on Windows** (Obsidian, and the model host if you use LM Studio).
**Everything else runs in WSL** (qmd, Python, the kit, the MCP bridge).

## Install

```bash
bash scripts/install-wsl.sh        # apt basics, bun + uv, qmd, ripgrep/fd/jq
```

Behind a proxy, read [proxy.md](proxy.md) first — WSL does not inherit Windows' proxy settings
automatically.

## Where the vault lives

This is the decision that determines whether the setup is pleasant or annoying.

**Vault on Windows, opened from WSL through `/mnt/c`** — Obsidian is fast and native.
Cross-filesystem access is slower, and file watching from WSL into `/mnt/c` is unreliable, so run
`qmd update` explicitly instead of expecting an index to refresh itself.

```bash
qmd collection add /mnt/c/Users/<you>/Notes/vault --name vault --mask "**/*.md"
```

**Vault inside WSL, opened from Windows over `\\wsl.localhost\`** — qmd and the kit are fast, and
Obsidian can open `\\wsl.localhost\Ubuntu\home\<you>\Notes\vault` as a vault. Obsidian over the
network path is slower to start and occasionally misses external changes.

Pick the first if you mostly read and write in Obsidian; the second if you mostly work from a shell.
Do not keep two copies.

## Reaching the model from WSL

With `networkingMode=mirrored` in `%USERPROFILE%\.wslconfig` (Windows 11 22H2+),
`http://localhost:1234/v1` works from inside WSL with nothing else to configure. Otherwise find the
Windows host and tell LM Studio to serve on the network (*Developer → Serve on local network*):

```bash
export KIT_LLM_BASE_URL="http://$(ip route | awk '/default/ {print $3}'):1234/v1"
uv run kit.py llm smoke
```

Or skip the GUI entirely and run the model in WSL: `llama-server -m model.gguf --port 1234` speaks
the same API, and then nothing crosses the boundary.

## MCP across the boundary

**An MCP client on Windows cannot spawn a stdio server that lives in WSL.** That is the one real
constraint of this lane. Two ways round it:

```bash
# in WSL: serve the bridge over HTTP on loopback
uv run mcp/obsidian_bridge.py --backend fs --vault ~/Notes/vault --http --port 8765 --token new
qmd mcp --http --port 8181
```
```bash
# then print the config for the Windows-side client — no --write here
uv run kit.py mcp-config --qmd-http --bridge-http --bridge-token <token> --vault ~/Notes/vault
```

`--write` would put the file in the WSL home (`~/.lmstudio/mcp.json`), which LM Studio running on
Windows never reads, so paste the printed JSON into `C:\Users\<you>\.lmstudio\mcp.json` yourself —
merge it with the servers already in there — and restart LM Studio. (`mcp-config` says the same thing
when it detects WSL.)

With mirrored networking the client reaches `127.0.0.1:8765` and `127.0.0.1:8181` directly, and the URLs
`mcp-config` prints are already right. Without it, both ports live on the WSL VM's own address: start the
bridge with `--host 0.0.0.0` (keep `--token`; the bearer header is then the only thing in front of your
vault), read the address with `ip addr show eth0`, and edit the bridge URL in the JSON by hand —
`mcp-config` always writes `127.0.0.1` and has no flag for the host. qmd's HTTP MCP server has no host
flag at all and stays on localhost, so on this lane it is reachable only with mirrored networking on —
which is the strongest argument for turning it on. Keep both ports on your own machine; never forward
them beyond it.

If none of that is workable, `kit.py llm ask` needs no MCP client at all: it searches, builds the
prompt and calls the endpoint itself.

## Gotchas worth knowing in advance

- `core.autocrlf=false` in git. Obsidian is happy with LF and the kit writes LF everywhere.
- A vault on `/mnt/c` indexes more slowly than one inside WSL. Expect the first `qmd embed` to take
  minutes, not seconds.
- `bash` inside Windows PowerShell resolves to the WSL launcher, which is a fine way to run one
  command and a confusing way to debug a script. Be explicit: `wsl -d Ubuntu -- <command>`.
