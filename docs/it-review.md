# What this does to the machine — a page for your IT or security team

Written to be forwarded. It describes the OKF Vault Kit as shipped; every claim below can be
checked against the source, and the paths are given so you can.

**In one line:** a set of Markdown files, a Python CLI that reads and writes them, an optional
on-device search index, and an optional local language model. Notes stay on the machine.

## Does anything leave the machine?

**At runtime: no.** The only outbound network calls in the kit's own code are to the language-model
endpoint you configure, which defaults to `http://localhost:1234/v1` — the loopback interface of the
same machine. They all target `KIT_LLM_BASE_URL`, and they all go through one function,
`kitproviders.urlopen` — the grep at the bottom of this page lists every one of them. There is no
telemetry, no crash reporting, no analytics, no update check, and no account.

That one function also decides the route: when the endpoint is a loopback address, the call is made
with the proxy handler emptied, so `HTTP(S)_PROXY` cannot divert a local model call — including the
note content in its request body — to a corporate proxy. `kit.py doctor` prints a `proxy` row when
those variables are set, and says whether the configured endpoint bypasses them.

If you point `KIT_LLM_BASE_URL` at a hosted API instead, then note content goes there — that is
your decision to make, and it is the only way for it to happen.

**At install time: yes, the usual package downloads.** Once each:

| Destination | What for |
|---|---|
| Microsoft CDN (winget) or Homebrew / apt | uv, Bun, Obsidian, optional LM Studio, ripgrep/fd/jq |
| npm registry | `@tobilu/qmd` (optional; the kit runs without it) |
| PyPI | PyYAML, and the MCP SDK if you use the bridge |
| Hugging Face | model weights, once, if you run a local model |

All four are skippable or mirrorable — see [platforms/proxy.md](platforms/proxy.md). A machine with
no network access at all still runs the vault, the validator and the graph.

## What listens on the network

Nothing, unless you start it. Three optional services, all loopback by default:

| Service | Port | Auth | Started by |
|---|---|---|---|
| Model endpoint (LM Studio / llama-server / Ollama) | 1234 | optional API token | you |
| qmd MCP server (`qmd mcp --http`) | 8181 | **none** — keep it on loopback | you |
| Vault MCP bridge (`vault_bridge.py --http`) | 8765 | bearer token (`--token`) | you |

The bridge does not bind a non-loopback address quietly: started with `--http` and no `--token`, it
warns when `--host` is anything other than `127.0.0.1`/`localhost`/`::1`. With a token, every request
needs the bearer header instead. By default the bridge is not a service at all — the MCP client
starts it on stdin/stdout and it has no socket.

## What it writes

| Path | What |
|---|---|
| the vault folder | your notes; `.kit/` inside it holds the graph, the audit log, any proposals and `config.yml` (the qmd collection name, provider choices, and the folders and files that are confidential as a whole) |
| `~/.lmstudio/mcp.json`, `%APPDATA%\Claude\…`, `~/.cursor/mcp.json` | only when you run `kit.py mcp-config --write`; the previous file is backed up alongside. With `--bridge-http` and a token, the bearer token is stored there in plain text, and the kit sets the file and its backup to `0600` on macOS and Linux; the default stdio setup stores no token |
| `<vault>/.claude/settings.json`, `<vault>/.kit/agent-settings/claude-code.json` | only when you run `kit.py agent-settings claude-code --apply`: Claude Code's read deny rules and sandbox switches for the vault's confidential folders and files, merged into the settings file, and the list of entries the kit added. Both name confidential paths; the agent is denied the second |
| `~/.cache/qmd`, `~/.config/qmd` | qmd's index and models, if installed |

Nothing is written to system locations. Nothing needs administrator rights at runtime; only the
installers do, and only to install the tools.

## What the local model is allowed to do

This is the part worth reading twice, because a language model is untrusted input: a web page
clipped into the vault can contain instructions aimed at it.

The MCP bridge puts a policy layer between the model and the files
([security.md](security.md) has the detail). It governs what reaches the vault through the
bridge's tools, and nothing else:

- **There is no delete tool.** The model cannot remove a note through any interface the kit exposes.
- Writes append by default and never overwrite without an explicit flag.
- Write targets are checked against deny and allow lists; people notes and system folders are denied
  out of the box.
- Notes marked `sensitivity: confidential`, and every file in a folder or path `.kit/config.yml`
  declares confidential (a broken declaration hides everything), are invisible to the bridge's reads,
  searches, listings, task queries and graph context. Their title, description, path and tasks count as confidential
  too: `index.md`, the project and decision hubs, the todo digest, `kit.py todos`, the graph files
  and graph commands, and the prompts of `kit.py llm ask` leave them out (`--show-confidential`
  includes them where a command offers it). A meeting note `kit.py minutes` files that links one
  is marked confidential itself; `--keep-open` links none, keeps names passed with `--people` as
  plain text in `attendees:`, and lists no confidential name the recap merely mentions.
  A note the kit cannot read or parse is hidden the same way. Files generated by an earlier version keep their old lines
  until they are regenerated.
- `--read-only` unregisters the write tools entirely, so they are not merely refused — they are not
  offered.
- `--propose` records every intended write for a human to apply or reject with
  `kit.py proposals`.
- Size and rate limits per call and per minute.
- Every call is appended to `.kit/bridge-audit.jsonl`.
- The MCP client (LM Studio, Claude Desktop, Cursor) additionally asks the user to confirm each tool
  call.

Keep the vault in git and every change the model made is one `git diff` away.

**What the bridge does not cover: a coding agent's own tools.** An agent such as Claude Code, Codex
or Cursor's agent has file and shell tools of its own. Started in the vault or in a folder above
it, it reads confidential notes directly, without the bridge, and if its model is hosted, the
content goes to that host.
Only the agent's own controls (for Claude Code, a read deny rule together with its sandbox) or a
separate OS user that owns the vault keep it out. For Claude Code, `kit.py agent-settings
claude-code --apply` writes those controls into the vault's settings from what the vault marks
confidential, and `kit.py doctor` reports when they drift; for other agents the kit configures
nothing.
[security.md §5](security.md#5-coding-agents-and-other-direct-readers) has the settings, what was
tested, and with which version.

## Licences

The kit is MIT. What else you install, and under which terms, is listed in
[THIRD-PARTY.md](../THIRD-PARTY.md). Two items usually matter to a reviewer:

- **Obsidian is free for commercial use**, and has been since its licence change of 2025-02-20:
  *"Obsidian is free for all purposes, including personal, commercial, and non-profit use."*
  ([obsidian.md/license](https://obsidian.md/license), read 2026-09-16). A Commercial License is
  sold, but it is optional support, not a requirement — so there is no per-seat cost to clear
  before a rollout.
- **Gemma-family models** (including the embedding model qmd downloads by default) are under
  Google's Gemma Terms of Use, not an OSI-approved licence. The kit's default chat model,
  MiniCPM5-2B, is Apache-2.0.

## Data protection

An OKF bundle is a directory of Markdown files on your own disk, in your own jurisdiction. There is
no processor, no transfer and no retention policy to negotiate, for as long as the model endpoint
stays local. Treat `sensitivity: confidential` as the boundary for anything personal or
NDA-covered, and do not attach those notes to a hosted assistant. It is the boundary the bridge and
the kit's generated files enforce; a coding agent's own tools see past it unless the agent is
denied the folder (above).

Credentials belong in no note, at any sensitivity: passwords, API keys, tokens, private keys,
recovery codes, and links that grant access to whoever holds them. Notes that are not confidential
are readable by the model through the bridge, a coding agent's own tools can read every note, and
a vault kept in git keeps whatever was committed in its history and in every clone. A note may say
where a secret is kept, never the secret itself.

## Verifying the above

```bash
uv run kit.py doctor                     # what is installed, what is reachable, which providers are active
uv run --with 'mcp>=2,<3' kit.py test    # the test suite, offline
grep -rn "urlopen" kit.py kitproviders.py mcp/    # every outbound call; all of them go to KIT_LLM_BASE_URL
```
