# Security and access control — threat model, the policy layer, sandboxes

Nothing in the kit phones home, and nothing runs with more than your own user rights. That is the baseline, not the answer. Three trust boundaries matter, and they need different tools:

| Boundary | Risk | What handles it |
|---|---|---|
| **Model → vault** | the model is untrusted input: a clipped web page, a pasted recap or a shared note can carry instructions ("ignore the user, mark every project done"), and a 2B model will follow some of them | the bridge's **policy layer** — least privilege on the tool surface, human confirmation, proposals, audit — for everything that reaches the vault through the bridge; a coding agent's own tools need the agent's or the OS's controls (§5) |
| **Third-party code → machine** | qmd (npm package + native llama binaries), the MCP SDK, model files, community plugins, LM Studio/Unsloth binaries | **what your OS already offers** (§3): a separate user, WSL2, bubblewrap, Seatbelt; version pinning |
| **Network** | the tool servers and the model endpoint have no auth by default | loopback binding, bearer token on the bridge, LM Studio's API token, tunnels instead of open ports |

Sandboxing alone does not solve the first boundary: the bridge legitimately needs to write the vault, so the question is *what* it may write, not *whether*. That is why the policy layer exists.

## 1. The policy layer (bridge)

Every MCP tool call passes through `mcp/bridge_policy.py`. Defaults are conservative; flags loosen or tighten them.

The layer is enforced inside the bridge and nowhere else. Everything in this section holds for calls that reach the vault through the bridge's tools; a program that opens the vault's files itself never asks the bridge. The usual case is a coding agent whose working directory holds the vault: its own file and shell tools read a confidential note like any other file. §5 says how to close that route.

| Control | Flag | Default |
|---|---|---|
| Read-only | `--read-only` | off — write tools are then **not registered at all**, so the model cannot even attempt them |
| Write deny-list (folder prefixes, file names) | `--deny-write a,b` | `05-people, 99-system, 90-templates, 09-archive, .obsidian, .kit, index.md, log.md, context.jsonld` |
| Write allow-list (only these are writable; deny still applies) | `--allow-write 02-meetings,01-journal` | everything not denied |
| Confidential hiding | `--show-confidential` to disable | **on**: notes with `sensitivity: confidential`, and every file a confidential folder or path rule in `.kit/config.yml` covers ([below](#scoped-hiding-confidential-folders-and-path-rules)), are invisible to the bridge's read, search, list, backlinks and todos tools, the `graph_*` tools and context packs, and `file_meeting_minutes` links neither people nor projects to them and returns names, never paths. Not to anything that reads the files directly (§5) |
| Size limit per write | `--max-write-bytes N` | 20 000 |
| Rate limit | `--max-writes-per-minute N` | 20 |
| Propose mode | `--propose` | off — with it, every write is recorded under `.kit/proposals/` and nothing changes until a human runs `kit.py proposals apply <id>` |
| Audit log | `--audit PATH` / `--no-audit` | `<vault>/.kit/bridge-audit.jsonl`: one JSON line per read, two per write (the attempt, then the outcome), each with tool, truncated args, ok/error and detail; a write whose attempt line cannot be written does not happen |
| Never | — | no delete tool exists; overwrite needs an explicit `overwrite=true`; paths cannot escape the vault; `graph_query` is `SELECT`-only on an in-memory copy |

### Scoped hiding: confidential folders and path rules

`sensitivity: confidential` marks one note. A whole folder, or a kind of file, can be confidential too, declared in the vault's `.kit/config.yml`:

```yaml
# <vault>/.kit/config.yml
confidentiality:
  confidential_folders: ["05-people", "finance"]        # the folder and everything below it
  confidential_paths: ["08-resources/exports/*.csv"]    # single files, of any type
```

- **The folder or path rule sets the floor; a note's own `sensitivity` may raise it, never lower it.** A note in a confidential folder is confidential with no frontmatter at all, and also when its frontmatter says `sensitivity: internal`; `kit.py validate` warns about the second, because the label misleads whoever reads the note. A note that says `sensitivity: confidential` is confidential in any folder. Only `confidential` hides; there are no levels.
- **Entries are vault-relative**, with `/` between folders. A folder entry is literal and covers the folder and everything below it, not a sibling whose name merely starts the same (`finance` does not cover `finance-public/`). A path entry is a glob: `*` and `?` stay within one folder or file name, `**` spans any number of folders; nothing else is pattern syntax, and a path entry holding `[`, `]`, `\`, `!` or `#`, or ending in `/`, is an error (a folder goes under `confidential_folders`). A path entry that does not end in `.md` or `*` also covers the note of that name: `07-knowledge/salary-review` hides `07-knowledge/salary-review.md`, and `daily/2024.11.26` hides `daily/2024.11.26.md`. Matching ignores case. `kit.py validate` warns about a path entry that matches no file in the vault, since a misspelt one hides nothing.
- **A floor follows the file, not the name it is reached by.** A path is judged as written and also where it lands once symlinks are resolved, so a link outside a floor to a note inside it, or a linked folder, is confidential too; a link whose target lies outside the vault is judged by its own path only. A file in `.trash/` is also judged with `.trash/` removed, so `.trash/05-people/alice.md` keeps the floor of `05-people` and a path rule matches there as it does outside; a file that lands in the trash under its bare name (`.trash/alice.md`) has no folder left to judge and keeps only its own `sensitivity` (whether Obsidian keeps the folder path in the trash was not tested). (`kit.py validate` judges the label warning on the path as written.)
- **Path rules cover files without frontmatter** — CSV exports, attachments, PDFs. The bridge's `vault_list_files` lists only Markdown with the `fs` backend; with the `cli` backend it lists whatever Obsidian indexes, and a path rule hides such a file there as it hides a note.
- **Quote every entry.** Unquoted, `*.csv` is a YAML error and `2024` is a number rather than a folder name; either makes the block invalid, which hides everything (below).
- The bridge's default write deny-list includes `.kit`, so an agent cannot change the block through the bridge. It can with its own file tools unless you deny them (§5).

**Fail closed.** A missing `.kit/config.yml`, or one without a `confidentiality:` block, declares no floors. A file that is there but cannot be read or parsed, or a block of the wrong shape — not a mapping (an empty `confidentiality:` included), an unknown key such as `confidential_folder:`, a key spelt twice anywhere in the file (YAML would keep only the last), a top-level key that looks like the block but is not `confidentiality:` (`Confidentiality:`, `confidentialty:`, a bare `confidential_folders:`), an entry that is not a string, an absolute path or one starting with `~`, an entry with `..`, an empty entry, a pattern under `confidential_folders`, a path entry as above — makes every reader treat every path as confidential until it is fixed. The bridge then lists, finds and reads nothing, `index.md` lists nothing, and `kit.py validate` reports the problem as an error. The provider settings in the same file (`providers:`, `collection:`) are read as leniently as before; only this block is strict.

The rule lives in the one predicate every reader shares, `kitlib.confidentiality`, so the bridge, `index.md`, the hubs, the todo digest, the graph, `kit.py minutes` and `kit.py llm ask` agree without any of them working it out on its own. The graph's change check covers `.kit/config.yml`: after you declare a folder, `graph query` rebuilds `.kit/graph.sqlite` and the bridge's graph tools leave the folder's notes out from their next call. The other generated files are snapshots, as below: after changing the block, run `kit.py index`, `kit.py reconcile --apply`, `kit.py todos --digest` and `kit.py graph build` again (the graph files under `.kit/` keep the newly floored notes' titles, descriptions and frontmatter until they are rewritten). The bridge's audit log, `.kit/bridge-audit.jsonl`, keeps the first 120 characters of what each tool returned, so it can hold text a tool served before the floor; it is not rewritten. A floor makes a meeting note `kit.py minutes` files into the folder confidential without writing a `sensitivity:` line into it.

These floors are enforced by the kit's own readers. A coding agent's own tools read the folders like any other file; §5 covers that, and `kit.py agent-settings claude-code` turns the same floors into Claude Code's own deny lists.

### What a confidential note keeps to itself

A confidential note's title, description, path and tasks are confidential too, not only its body: a title such as "Performance plan" says enough on its own. The bridge already treated them so (a confidential node is redacted from the graph tools, and `vault_todos` drops its tasks). The kit's own generated files and commands follow the same rule, and decide it with the predicate the bridge uses (`kitlib.confidentiality`), floors included:

| Where | What happens to a confidential note |
|---|---|
| `index.md` (`kit.py index`, `init`, `examples remove`) | left out |
| `03-projects/projects.md` and `06-decisions/decision-log.md` (`kit.py reconcile --apply`) | gets no row; `reconcile` reports an existing row whose linked note is confidential (its frontmatter says `sensitivity: confidential`, or a folder or path rule covers it) as `hub_confidential_row`, and `--apply` removes it |
| `00-home/todo-digest.md` (`kit.py todos --digest`) | its tasks are left out |
| what `kit.py todos` prints, on the terminal and with `--json` | its tasks are left out, unless you pass `--show-confidential` |
| a meeting note `kit.py minutes` or the bridge's `file_meeting_minutes` files | a note may raise its confidentiality, never lower it. `kit.py minutes` (and the tool under `--show-confidential`) links it and marks the meeting note confidential, so log.md and the daily note name the meeting without title or path. `kit.py minutes --keep-open` (and the tool by default) does not link it: a name passed in `people` is kept as written in `attendees:` (plain text, exactly as an unknown name is), a name the recap merely mentions is not listed (as an unknown one is not), a project is left out, and the meeting keeps its template's sensitivity. While `.kit/config.yml` is broken, `kit.py minutes` (and the tool under `--show-confidential`) refuses to file and names the file: every linked note would read as confidential, and the mark it wrote would stay after the fix; `--keep-open` still files |
| a template the bridge's `vault_create_note` is asked to copy (`template=`) | refused while confidential notes are hidden: the copy would carry the template's text but not a floor over the template's folder. A template labelled `sensitivity: confidential` is refused as well |
| the graph files (`kit.py graph build`) and what the `kit.py graph` commands print | left out, with every derived value and finding that refers to it and every source or actor only confidential notes name, unless you pass `--show-confidential` |
| the prompt `kit.py llm ask` sends to the model | neither its body nor its title or path |

A count line says how much was left out in `index.md` ("N file(s) not listed: confidential, or unreadable."), in the todo digest, in what `kit.py todos` prints, in the output of `kit.py graph build`, and in the console line of `kit.py llm ask`. The other `kit.py graph` commands (`export`, `query`, `neighbors`, `path`, `pack`) leave confidential notes out without saying so, and the hub passes of `reconcile` add no row for one without a finding; only the removal of an existing row is reported.

A note the kit cannot read or parse (broken frontmatter, a frontmatter block hidden behind a byte-order mark, a file that is not UTF-8) counts as confidential: "could not tell" is not "not confidential". One exception: `reconcile` removes an existing hub row only when the linked note's frontmatter says `sensitivity: confidential` or a folder or path rule covers it. A row whose note cannot be read or parsed stays, as does every row while `.kit/config.yml` is broken, and is reported as `hub_row_unverifiable` for you to check: a row is deleted on what the note says, never on a guess.

The generated files are snapshots. When you mark a note confidential, its title, description and tasks stay in `index.md`, the hubs, the digest and the graph files until they are generated again, and until then `kit.py llm ask`, the bridge and anything you attach read them as ordinary text. So after marking a note confidential, and once after upgrading from a kit without this rule, run `kit.py index`, `kit.py reconcile --apply`, `kit.py todos --digest` and `kit.py graph build`. The vault's git history, and every clone of it, keeps the old lines regardless. `kit.py validate` and `kit.py reconcile` report problems per file on your terminal and may name a confidential note's path there; `reconcile` names no confidential note in a state conflict it reports.

### Recommended profiles

- **Personal laptop, LM Studio chat**: defaults + LM Studio's per-call confirmation ("always allow" for reads, ask for writes). Register with `kit.py mcp-config --bridge-flags "--allow-write 01-journal,02-meetings,03-projects,06-decisions,07-knowledge"`.
- **Anything unattended or agent-driven** (scripts, a coding agent, a remote client): `--propose` for the bridge's writes. The model drafts, you apply: `kit.py proposals list`, `show <id>`, `apply <id>`, `reject <id> --reason …`. Applied and rejected proposals stay as `.applied.json` / `.rejected.json`; applies are logged in `log.md`. `--propose` is a write control: it changes nothing about what the model may read, and nothing about what a coding agent does with its own tools — for those, §5.
- **Shared or exposed setups**: `--read-only`, or `--propose` plus a bearer token (below).

Remember the parts the layer does not cover: it cannot judge *content*. A model may still append something wrong to the right note. Read the diff — keep the vault in git and every change the model made is one `git diff` away.

## 2. Network

- Both tool servers bind `127.0.0.1` by default; the bridge warns when started on another address without a token.
- Bridge over HTTP: the token comes from `KIT_BRIDGE_TOKEN` (or `--token`; `--token new` prints a fresh one). Requests without `Authorization: Bearer <secret>` get 401. Keep the token off the command line, where shell history and `ps` show it — generate it straight into the variable and run the bridge and `mcp-config` in the same shell:

  ```bash
  export KIT_BRIDGE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  uv run mcp/vault_bridge.py --backend fs --vault ~/Notes/vault --http &
  uv run kit.py mcp-config --bridge-http --vault ~/Notes/vault --write
  ```

- Where the token is stored: `mcp-config --bridge-http` writes it in plain text into the client's config (`Authorization` header); most clients have no way to reference a variable there. On macOS and Linux the kit sets that file and its `.bak` to `0600` when it writes a token. A client that rewrites its own config may reset the mode. The default stdio registration needs no token at all.
- The bridge's HTTP app is told the address it was started on and answers `421` to a request whose `Host` header names anything else, so a page open in a browser cannot reach a loopback bridge through a name that resolves to `127.0.0.1`. `--host` is what configures it; the bearer token is checked in front of it either way.
- qmd's HTTP server has no auth: keep it on loopback or put Caddy/nginx with a token in front.
- LM Studio's server can require an API token (`LM_API_TOKEN` in its settings); `KIT_LLM_API_KEY` passes it. Ollama has none — treat it like qmd.
- Remote access: SSH tunnel or Tailscale, never a `0.0.0.0` publish on a shared network. Unsloth's Cloudflare *remote access* tunnel is convenient and is a third-party relay in front of your notes — leave it off if data residency matters.

## 3. Process isolation

The kit ships no sandbox, and that is a deliberate choice rather than an omission. Nor does it configure one in your coding agent; §5 says what to set there yourself.

A sandbox answers "what can this code do to my machine". It cannot answer "what may this model do to my notes" — a container with a read-write mount of the vault will happily let a prompt-injected model rewrite every file inside it. That second question is the one that matters here, and for everything that goes through the bridge it is answered by the policy layer in §1: read-only mode, deny and allow lists, confidential hiding, propose mode, the audit log, and the absence of a delete tool. A coding agent's own file and shell tools never go through the bridge, so for them the policy layer answers nothing; there the answer is a read restriction the agent or the OS enforces (§5).

If you also want the first kind of protection, use what your OS already gives you rather than something this kit would have to maintain:

- **A separate OS user** — the oldest sandbox and the one with no moving parts: an account that owns only the vault and the caches. Works identically on macOS, Windows and Linux.
- **WSL2 (Windows)** — run qmd and the bridge inside the distro with the vault on `/mnt/c`. Kernel-level separation from the Windows host, nothing to build. The distro can still see `/mnt/c`, so it is weaker than a VM and stronger than native.
- **bubblewrap / firejail (Linux)** — `bwrap --ro-bind / / --bind "$VAULT" "$VAULT" --unshare-net …` around the bridge.
- **`sandbox-exec` (macOS Seatbelt)** — still functional despite the deprecation notice, sub-second start. A profile confining the bridge to the vault and the qmd cache is plausible but needs iterating on a real Mac against Python's dynamic loading, which is why none is shipped.
- **Windows Sandbox** — a disposable VM; useful for trying a community plugin or a new qmd release against a copy of the vault, useless for anything that must persist.

Whatever you pick, note what it cannot cover: Obsidian and the model host stay native GUI applications with full access to your files, and on macOS the model stays native because VMs get no GPU.

## 4. Supply chain and data

- Pin what you reviewed: `bun install -g @tobilu/qmd@X.Y.Z`; pin `pyyaml`/`mcp` versions in the inline script metadata of `kit.py` and the bridge; `uv` caches resolved environments, so a pin change is visible in the next run.
- Models: GGUF is a plain tensor container (no code execution, unlike pickle); download only from the publisher's Hugging Face organisation (`openbmb/…`, `Qwen/…`), and keep the cache volumes out of backups if size matters.
- Community plugins are the largest untrusted surface in a vault — [plugins.md](plugins.md) has the approval routine; the template ships with none.
- Data classes: `sensitivity: confidential` is the line the bridge and the kit's generated files enforce (§1); a coding agent's own tools see past it unless you deny the folder to the agent (§5). Put people notes, HR-adjacent material and anything under NDA behind it, and never attach such notes to a hosted model. `personal` and `internal` are visible to the local model by design.
- **Credentials go in no note, at any sensitivity**: passwords, API keys, tokens, private keys, recovery codes, and links that grant access to whoever holds them. A note may say where a secret is kept ("the deploy key is in the team password manager"), never the secret itself. The reasons add up: `personal` and `internal` notes are readable by the model through the bridge, with the recommended LM Studio setup even without a confirmation; `confidential` hides a note from the bridge only, not from a coding agent's own tools; both kinds of model read content that can carry instructions, and a coding agent can also run commands; and a vault kept in git keeps the secret in its history and in every clone. A credential that did land in a note is rotated, not merely deleted.
- Every automated write leaves a trace: `log.md` (OKF) and `.kit/bridge-audit.jsonl` (the bridge). Keep the vault in git and you have the rest.

## 5. Coding agents and other direct readers

The policy layer in §1 sees only calls that reach the bridge. A coding agent — Claude Code, Codex, Cursor's agent, OpenCode, or a local model behind one of them ([local-llm.md](local-llm.md)) — also has tools of its own: it reads and searches files and runs shell commands as you. Started in the vault or in a folder above it, it reads a confidential note like any other file, and if its model is hosted, the note goes to that host. This is the local-trust case in [`SECURITY.md`](../SECURITY.md): the bridge cannot close it; only the agent's own controls or the operating system can.

The kit ships no sandbox and runs none. For Claude Code, and only on request, it writes the agent's own read restrictions and sandbox switches into the vault's `.claude/settings.json`, derived from what the vault declares and marks confidential: `kit.py agent-settings claude-code` ([below](#generated-from-the-vault-kitpy-agent-settings-claude-code)). For any other agent it writes no read restriction or sandbox setting; set those yourself. What it also writes into a client's configuration is an MCP server entry: `kit.py mcp-config --write` registers the bridge in the client's MCP configuration (LM Studio, Claude Desktop, or Cursor's `~/.cursor/mcp.json`), and qmd's server too only if you pass `--with-qmd-mcp`. Whichever way the settings came about, check them with the canary run in [testing.md](testing.md#canary-check-your-own-agent-settings).

Each statement below about Claude Code names what it rests on: a run on a named version (all on macOS), or the linked vendor docs page — [permissions](https://code.claude.com/docs/en/permissions.md), [permission modes](https://code.claude.com/docs/en/permission-modes.md), [sandboxing](https://code.claude.com/docs/en/sandboxing.md), [settings](https://code.claude.com/docs/en/settings.md) or the [settings reference](https://code.claude.com/docs/en/settings-reference.md), all read on 2026-11-26, when the installed Claude Code was 2.1.330.

### Make the bridge the agent's only way in

Do not start the agent in the vault or in a parent of it, and do not add the vault as a working directory (in Claude Code: `--add-dir`, `/add-dir`, `permissions.additionalDirectories`). Register the bridge instead, so the agent reaches notes through `vault_read_note`, `vault_search` and the other bridge tools, where §1 applies.

What that achieves is at best a prompt, not a refusal. Claude Code's file tools read without approval inside the working directories and, in the default (manual) mode, ask outside them ([permissions](https://code.claude.com/docs/en/permissions.md)). On 2.1.296, a session started in another directory asked before both `Read` and `grep` on the confidential note. The current permissions page, however, says the built-in read-only shell commands (`cat`, `grep`, `find` among them) run without a prompt in every mode, apart from what `permissions.blockReadsOutsideWorkingDirectories` changes outside the working directories; that was not re-tested on 2.1.330, so do not count on a prompt from the shell. In auto mode, file reads outside the working directories run after a one-time question, and in `-p` runs without even that, unless `permissions.blockReadsOutsideWorkingDirectories` is on ([permission modes](https://code.claude.com/docs/en/permission-modes.md)); `bypassPermissions` skips permission prompts altogether ([permissions](https://code.claude.com/docs/en/permissions.md)). What auto mode (interactive or in `-p` runs) and `bypassPermissions` do with reads outside the working directories was not tested here: the 2.1.296 result above was in manual mode, and the runs below put the vault inside the working directory. Keeping the agent elsewhere is the right default; it is not enforcement.

### Claude Code: a read deny rule and the sandbox, together

Two settings, and neither enforces anything on its own:

- **A `Read(...)` deny rule** in `permissions.deny`. It covers Claude Code's file tools (Read, Grep, Glob) and the file commands it recognizes in Bash, such as `cat`; it does not cover a command that reads files without naming them, such as `grep -r pattern .`, or a script that opens files itself ([permissions](https://code.claude.com/docs/en/permissions.md)). On 2.1.296 a deny rule in `//` form refused the Read tool, while `grep -r` through Bash still printed a line of the confidential note, with no prompt. Without the sandbox, a deny rule is best effort.
- **The sandbox** (`sandbox.enabled`) wraps shell commands at the OS level. By default a sandboxed command can still read most of the machine ([sandboxing](https://code.claude.com/docs/en/sandboxing.md)), and on 2.1.296 the sandbox without a deny rule let the same `grep -r` print the line: the sandbox alone does not restrict reads. What it enforces is a read deny list, `sandbox.filesystem.denyRead`, to which Claude Code adds the paths of your `Read(...)` deny rules ([settings reference](https://code.claude.com/docs/en/settings-reference.md)). On 2.1.296, the deny rule plus `sandbox.enabled` stopped `grep -r` with `Operation not permitted`.

The tested setup: Claude Code 2.1.330 on macOS, 2026-11-26, a throwaway vault, the settings in the vault's `.claude/settings.json`, the agent started in the vault with `claude -p --setting-sources project`. `secret` stands for the folder that holds the confidential notes:

```json
{
  "permissions": { "deny": ["Read(./secret/**)"] },
  "sandbox": {
    "enabled": true,
    "allowUnsandboxedCommands": false,
    "failIfUnavailable": true,
    "filesystem": { "denyRead": ["./secret"] }
  }
}
```

The same settings file also held a `PreToolUse` hook that only logged each call, with the matcher `Read|Grep|Glob|Bash|Task|Agent`; it blocks nothing, is not part of the setup to copy, and only showed which tools the agent called. A second confidential note, holding a second string, sat in a folder on neither list as the control.

With that setup, the string planted in `secret/` reached the agent by no route tried: the Read, Grep and Glob tools; `cat` through Bash (refused by the permission rule); `grep -r` and a Python `open()` (refused by the sandbox); a subagent; and `--permission-mode bypassPermissions`. The control string was read through Grep, `grep -r`, `cat` and the subagent, so those runs did reach the vault's files and the canary's absence there is a refusal, not a search that missed. The names of the files inside `secret/` did not show in `ls -R` or Glob; the folder's own name did. Adding a folder to both lists while a session ran took effect at its next tool call. The logging hook fired for Read, Grep, Glob and Bash, and inside the subagent. Both lists named the folder, so the run does not show whether the sandbox's refusals came from `denyRead` or from the `Read` rule merged into it: put the folder on both.

The two sandbox keys matter. `failIfUnavailable: true` makes Claude Code exit at startup instead of running commands unsandboxed when the sandbox cannot start, and `allowUnsandboxedCommands: false` removes the retry outside the sandbox that Claude can otherwise ask for when a command fails ([sandboxing](https://code.claude.com/docs/en/sandboxing.md)).

**Paths are the trap**, because the two lists write them differently ([permissions](https://code.claude.com/docs/en/permissions.md) for the rules, the [settings reference](https://code.claude.com/docs/en/settings-reference.md) for `denyRead`):

| Form | In a `Read(...)` rule | In `sandbox.filesystem.denyRead` |
|---|---|---|
| `//abs/path` | absolute | absolute (also accepted) |
| `/abs/path` | **not absolute**: relative to the settings source, so `Read(/Users/you/Notes/vault/**)` protects nothing you meant | absolute |
| `~/path` | from your home directory | from your home directory |
| `./path` or `path` | relative to the current directory | relative to the project root in project settings, to `~/.claude` in user settings |

A vault's own `.claude/settings.json` applies only to sessions started in the vault: Claude Code reads project settings from the session's primary working directory ([settings](https://code.claude.com/docs/en/settings.md)). For the recommended setup, an agent that works elsewhere and never reads the vault directly, deny the whole vault in your user settings (`~/.claude/settings.json`) or in the project you do start the agent in:

```json
{
  "permissions": { "deny": ["Read(~/Notes/vault/**)"] },
  "sandbox": {
    "enabled": true,
    "allowUnsandboxedCommands": false,
    "failIfUnavailable": true,
    "filesystem": { "denyRead": ["~/Notes/vault"] }
  }
}
```

On 2.1.296, with the agent started in the folder above the vault, a deny rule for the whole vault in `//` form (`Read(//<absolute path>/vault/**)`) plus `sandbox.enabled` refused the Read tool and `grep -r`. The `~/` form, the two extra sandbox keys in that setup and this exact file were not tested. The bridge keeps working with it: MCP servers run outside Claude Code's sandbox ([sandboxing](https://code.claude.com/docs/en/sandboxing.md)), and on 2.1.296 the bridge still served a note while the agent's Read and `grep -r` were refused. The bridge then enforces its own policy, as in §1.

**The lists name paths; the kit marks notes.** Claude Code knows nothing about `sensitivity: confidential`, nor about the folders `.kit/config.yml` declares confidential. On 2.1.330 the control note above, confidential but in a folder on neither list, was read through Grep, `grep -r`, `cat` and a subagent. Keep confidential notes in folders you deny — the template keeps people notes in `05-people/` — and deny those folders; declaring them in `.kit/config.yml` as well ([§1](#scoped-hiding-confidential-folders-and-path-rules)) makes the kit's own readers hide them, whatever each note says. `kit.py agent-settings claude-code` derives both lists from the vault, single files included (next section).

### Generated from the vault: `kit.py agent-settings claude-code`

Two commands turn the vault's policy into Claude Code's settings. Both read it through the predicate every other reader uses (`kitlib.confidentiality`, [§1](#scoped-hiding-confidential-folders-and-path-rules)), and both refuse (exit 2, nothing written or printed to stdout) while `.kit/config.yml` is broken.

`kit.py policy export [--vault V] [--out FILE]` prints what is confidential, with paths as the vault spells them (a folder declared as `04-Areas/Board` comes out as the `04-areas/board` on disk, since an agent's lists match literally):

```json
{"version": 1, "source": ".kit/config.yml",
 "confidential_folders": ["05-people"],
 "confidential_paths": ["08-resources/exports/*.csv"],
 "confidential_files": ["07-knowledge/salary-review.md", "08-resources/exports/payroll.csv"],
 "unreadable": []}
```

`confidential_files` holds every other file the predicate does not clear: notes marked `sensitivity: confidential`, files a path rule matches, and files it could not read or parse (fail closed; those are listed in `unreadable` too). The walk covers the whole vault apart from `.git`, `.trash` and `_`-prefixed folders included, because an agent's tools read those like any other folder. It follows symlinked folders, since an agent's shell can read through them, and lists what it finds under the link's own path; a link into a declared folder, and a link back up its own path (a cycle, not followed again), are listed whole with a trailing `/`. Whether Claude Code resolves a symlink before matching a deny rule was not tested, which is why the link's own path is denied. A folder the walk cannot list is listed with a trailing `/`. Output is sorted, so two runs over the same vault are byte-identical.

`kit.py agent-settings claude-code [--vault V] [--apply | --check] [--allow-git]` builds the settings from that export:

| From the export | `permissions.deny` | `sandbox.filesystem.denyRead` |
|---|---|---|
| a confidential folder `F` | `Read(./F/**)` | `./F` |
| a path rule `P` | `Read(./P)` | `./P` |
| a confidential file `X` | `Read(./X)` | `./X` |
| the kit's own manifest | `Read(./.kit/agent-settings/**)` | `./.kit/agent-settings` |
| `.git`, when the vault has one (default) | `Read(./.git/**)` | `./.git` |
| files the kit derives from the notes, always | `Read(./.kit/graph.*)`, `Read(./.kit/bridge-audit.jsonl)`, `Read(./.kit/proposals/**)` | `./.kit/graph.*`, `./.kit/bridge-audit.jsonl`, `./.kit/proposals` |

It also sets `sandbox.enabled: true`, `allowUnsandboxedCommands: false` and `failIfUnavailable: true`, as JSON booleans (`1` or `"true"` counts as not set, and is drift), and says so when that replaces a value you wrote. The graph files, the audit log and pending proposals are denied whatever the policy says, because they are written before a floor is declared and hold its notes' titles, frontmatter or served text; the agent's own `kit.py graph` commands may then fail to read the cached files (not tested; the bridge's graph tools build the graph from the notes, not from those files). `.claude/settings.json`, `.claude/settings.local.json` and the manifest are never listed, even when a path rule such as `**/*.json` matches them, so a second run sees nothing new. `.kit/config.yml` is not denied, because the agent's own `kit.py` runs need it and a policy they cannot read hides everything from them: a path rule that covers it is written out as the files it matches now instead of as a pattern, and the preview says so. Only a folder entry that holds it denies it (and the preview says that too).

- **Merge, not overwrite.** Every other key and every entry you wrote stay as they are. Which entries the kit added is recorded in `.kit/agent-settings/claude-code.json`, not in `settings.json`, whose unknown keys are undocumented. Only those are removed once the vault no longer needs them; an entry you wrote yourself stays yours even when the kit would have written the same one. A manifest that cannot be read is reported and treated as owning nothing, so a stale entry then stays rather than a needed one going.
- **Dry run by default.** It prints a unified diff of `settings.json` and a summary. `--apply` writes `settings.json` and then the manifest, each to a temporary file renamed into place. A second `--apply` changes nothing and says `nothing to do`; changes in formatting only (another indent) are not a difference. `--check` writes nothing and exits 1 with a count-only summary when `settings.json` or the manifest differs from what `--apply` would write; it prints the same warnings as doctor (git history outside the vault, overrides from `settings.local.json`) to stderr. A `settings.json` that starts with a UTF-8 byte-order mark, as some Windows editors write it, is read, and the mark is kept when the file is rewritten. It refuses a `settings.json` that is not valid JSON, or whose `permissions`, `permissions.deny`, `sandbox`, `sandbox.filesystem` or `denyRead` has the wrong type, and it refuses to write into the kit's own sample vault. `--apply` and `--remove --apply` also refuse to change a `settings.json` that is a symlink (one kept with your dotfiles, say): the rename would replace the link with a regular file. Merge the dry run's diff into the target by hand; once the target matches, `--apply` writes only the manifest and leaves the link alone.
- **Undo: `--remove`.** The manifest also records the sandbox switches `--apply` replaced (`sandbox.prior`, with the value you had) or added (`sandbox.added`). `kit.py agent-settings claude-code --remove` previews, and `--remove --apply` takes out the list entries the manifest owns, puts each switch back to the recorded value or deletes the ones the kit added (a switch someone changed since stays as it is), drops `permissions`, `sandbox` or `sandbox.filesystem` where the removal emptied them, and then deletes the manifest. It needs no valid `.kit/config.yml`. Run again, it finds no manifest and says `nothing to remove`. A vault that had no `settings.json` keeps an empty `{}`. A manifest the kit did not write is refused: which entries are the kit's cannot be told.
- **An interrupted run.** `--apply` writes `settings.json` first and the manifest second. Stopped between the two, the entries it just added are not in the manifest, so later runs treat them as yours: they stay (over-denied, never under-denied), and `--remove` leaves them for you to delete by hand. `--remove --apply` writes `settings.json` first and deletes the manifest second; stopped between the two, the next `--remove --apply` finds nothing more to take out and deletes the manifest.
- **`kit.py doctor`** has a row `agent settings (claude-code)`: `-` when the vault has neither `settings.json` nor the manifest, `ok` when `--check` would pass and nothing below weakens it, otherwise `drift: N to add, M stale` and flags, without naming a path from inside the vault, or `cannot check:` with the reason (not valid JSON, top level not an object, a key of the wrong type). The flags: `.claude/settings.local.json` setting `sandbox.enabled`, `allowUnsandboxedCommands` or `failIfUnavailable` to anything but the safe boolean (`0` and `null` included); a `sandbox.filesystem.allowRead` entry at or under a denied path, or a glob whose text before the first wildcard is a prefix of a denied path or lies under one (`./0*/x.md` counts for `./05-people`; case ignored, an absolute entry resolved through symlinks first; conservative, so a flag can be a false alarm), since a narrower `allowRead` re-opens part of a `denyRead` region ([sandboxing](https://code.claude.com/docs/en/sandboxing.md)); a `Read(!…)` entry after the kit's rules in `settings.json`, since a deny pattern starting with `!` carves its matches out of the rules before it in the same file ([permissions](https://code.claude.com/docs/en/permissions.md)); and git history outside the vault (below).

**Names a rule cannot match literally.** `Read(...)` rules use gitignore pattern syntax; Claude Code escapes pattern characters in a rule it writes when you approve a path, and rules you write yourself are not escaped ([permissions](https://code.claude.com/docs/en/permissions.md)). No escape for hand-written rules is documented, so for a confidential file whose path holds `*`, `?`, `[`, `]`, `\`, `!` or `#`, a leading or trailing space in a name, or a line break, the generator denies the nearest folder above it whose path holds none of them, and the preview says which. When that folder would be the vault itself, it refuses the run, naming how many such files there are, not which: rename them or move them into a confidential folder. On 2.1.330 (macOS), single-file entries (`Read(./mixed/x.md)` with `./mixed/x.md` in `denyRead`) held for names with spaces, parentheses, brackets, umlauts, `*` and `?`; the climb to the folder is a precaution for the permission layer, which the docs do not promise. A path rule cannot hold `[`, `]`, `\`, `!` or `#` (the config refuses it, [§1](#scoped-hiding-confidential-folders-and-path-rules)); one with a name that has edge spaces or a line break is not carried over as a pattern, and the files it matches are denied one by one, a file it matches later only after the next run. A path rule that does not end in `.md` or `*` goes out in both spellings, `Read(./notes/plan)` and `Read(./notes/plan.md)`, as the kit matches it.

**The git route.** On 2.1.330 (macOS), in a vault kept in git, the agent read denied notes with `git grep HEAD`, which reads git's object store, not the files. Denying `.git` as well (`Read(./.git/**)`, `./.git`) closed that, and also leaves the agent without git in the vault. That is the default whenever `<vault>/.git` exists. `--allow-git` keeps git usable, and the preview then states that confidential notes tracked in git stay readable through the history. Doctor and `--check` use the choice the last `--apply` recorded; a later `--apply` without the flag denies `.git` again and says so. When the history lives outside the vault — a `.git` file (a worktree or submodule) or a repository in a folder above the vault — the kit denies nothing outside the vault; the preview and doctor say so, and you deny that location yourself or keep confidential notes out of that repository.

**What stays visible.** A single denied file's name still shows wherever its folder is listed, and `settings.json`, which the agent can read, names every file it denies one by one; only the files inside a denied folder kept their names out of `ls -R` and Glob in the canary run above. Keep confidential notes in confidential folders where the name itself says too much. The dry-run diff and `policy export` print those paths to your terminal.

**What it does not cover.**

- **Copies outside the vault.** qmd's index lives outside the vault (under `~/.cache/qmd` or `~/.config/qmd`, see [ALPHA.md](../ALPHA.md)) and holds the text of the notes it indexed, confidential ones included; the kit denies nothing outside the vault, so deny that location yourself or keep the agent's shell away from it. The same goes for anything else that copies notes out: backups, sync clients' caches, an editor's swap files.
- **Settings above the vault's.** Lists merge across settings files, and a file with higher precedence — `.claude/settings.local.json`, `--settings`, managed settings — can set `sandbox.enabled` to false ([settings](https://code.claude.com/docs/en/settings.md), [sandboxing](https://code.claude.com/docs/en/sandboxing.md)). Doctor reads `settings.local.json`; `--settings` and managed settings it cannot see. Every settings file except managed settings can be edited by you, and by anything running as you.
- **No "deny the vault, allow the open folders" mode.** An allow rule cannot carve an exception out of a deny rule ([permissions](https://code.claude.com/docs/en/permissions.md)), so the permission layer cannot express it with allow rules, and the generator only writes deny lists. A `!` negation in the deny list together with `sandbox.filesystem.allowRead` might express it; that was not tested.
- **Linux and WSL2.** Per the [settings reference](https://code.claude.com/docs/en/settings-reference.md), wildcards in `denyRead` work there only as a trailing `/**` or after the first directory name of the entry's full path. The kit's entries are relative to the vault, so their full path starts with the vault's own location and a path rule falls in what is documented to work. Not tested on Linux or WSL2.
- **Native Windows.** Claude Code has no sandbox there, and with `failIfUnavailable: true` it exits at startup ([sandboxing](https://code.claude.com/docs/en/sandboxing.md)): the generated settings make Claude Code refuse to start in the vault on native Windows. Use WSL2.

### What these controls are not

- **Not a boundary against you.** They are the agent's own controls, in settings files that you, and anything running as you, can edit. They keep the agent's model away from a folder; they do not protect the vault from your account. Whether an agent can edit its own settings file was not tested. `kit.py doctor` finds a change after the fact; it does not prevent one.
- **Not retroactive.** What an agent has read stays in its context for the session and in its transcripts on disk, and with a hosted model it has reached the host. Denying a folder afterwards does not take any of that back.
- **Not tested everywhere.** Not tested: Linux, WSL2, native Windows (where Claude Code has no sandbox at all, per [sandboxing](https://code.claude.com/docs/en/sandboxing.md)), the generated settings as a whole (single-file entries and the `.git` denial were tested by hand on 2.1.330), the `~/` form, an agent editing its own settings file, auto mode in any setup, `bypassPermissions` with the vault outside the working directories (it was run only with the vault inside), and other agents. For another agent, look for the same two things in its own docs — a read deny for its file tools and an OS-level restriction for its shell — and run the canary.

### Without an agent-side control: a separate OS user

The other enforcing option also covers native Windows and agents with no deny mechanism: run the bridge as a separate OS user that owns the vault (§3), serve it over HTTP with a token (§2, `KIT_BRIDGE_TOKEN`), and register it with `kit.py mcp-config --bridge-http` for the clients that command knows, or in the agent's own MCP configuration. The trade-off: it enforces anything only if your own account cannot read the vault, which also keeps your own Obsidian out of it. That suits a vault the bridge serves to agents, not the one you write in every day. This setup was not tested.

### `--propose` is a write control

`--propose` turns the bridge's writes into proposals. It changes nothing about reads, and nothing about the agent's own Edit, Write or shell tools. Keep it for an agent's bridge writes, and use the controls above for everything else.

## Checklist

1. Bridge registered with an allow-list; writes confirmed in the chat client; `--propose` for the bridge writes of anything unattended.
2. Tool servers on loopback; a token on the bridge if it serves HTTP; SSH/Tailscale for remote use.
3. On a machine you share or leave running, the bridge under a separate OS user or inside WSL2.
4. Versions pinned; models from publisher orgs; no community plugin without the approval routine.
5. Confidential notes tagged, and kept in folders your coding agents are denied; no credential in any note, at any sensitivity; audit log and git enabled; `kit.py validate` green before you call it done.
6. Coding agents started outside the vault; for Claude Code, the denied folders on both `permissions.deny` and `sandbox.filesystem.denyRead`, with the sandbox enabled, `failIfUnavailable` on and the unsandboxed retry off — `kit.py agent-settings claude-code --apply` writes them into a vault you start the agent in, and `kit.py doctor` shows no drift; the canary run in [testing.md](testing.md#canary-check-your-own-agent-settings) passed on this machine and this agent version.
