# FAQ

**Why kebab-case file names? My old vault used "Project - Name".**
Spaces become `%20` in markdown links, break naive shell scripts, and make agents mistype paths. The `title` property keeps the readable name; Obsidian's search finds both. If you must keep spaces, everything still works — only the links get uglier.

**Why markdown links instead of `[[wikilinks]]`?**
OKF consumers, qmd, GitHub and most agents read markdown links; only Obsidian reads wikilinks. Obsidian writes the markdown form for you once *Use [[Wikilinks]]* is off, and `[[` autocomplete still works while typing. Base embeds keep the `![[…]]` form — that is an Obsidian feature, not a link.

**Do I need `index.md` and `log.md`?**
No — OKF makes both optional. They exist for agents: `index.md` is the table of contents a model reads first; `log.md` is history. `kit.py index` regenerates one, `kit.py log` appends to the other. Delete them and nothing else breaks.

**`state` vs `status` — which one do I set?**
`state` is your workflow (open, waiting, done…) — a plain property with suggested words, deliberately not a formal state model. `status` is the OKF lifecycle (draft, stable, deprecated) and belongs on knowledge notes. The validator rejects workflow words in `status` because agents would misread them; it does not police `state`.

**Why bun and uv instead of npm and pip?**
Fewer moving parts: bun and uv are single binaries from Homebrew/winget, qmd installs with one `bun install -g`, and `kit.py`/the bridge declare their own dependencies inline so `uv run` provisions them in an isolated cache — no global npm bin wrappers, no venv bookkeeping, no version drift between machines. npm and pip still work; they are just not what the installers do.

**My notes are mostly German. Does search work?**
Keyword search always does. For semantic search set `QMD_EMBED_MODEL` to the Qwen3-Embedding model before embedding (see models.md); the default embedding model is English-optimised. Chat models: Qwen3.5-2B and Gemma 4 E2B are stronger in German than MiniCPM5-2B; compare with your own notes.

**Can I still use Microsoft 365 Copilot / Claude / ChatGPT with this vault?**
Yes — attach the three briefing files (`priorities.md`, `projects.md`, `decision-log.md`); they are plain text by design. Do not attach `05-people` notes (`sensitivity: confidential`) to hosted models.

**Does anything leave my machine?**
qmd, LM Studio/Unsloth/Ollama and the kit run locally; models are downloaded once from Hugging Face. The only outbound paths are ones you create: attaching files to a hosted model, or Unsloth's Cloudflare remote-access tunnel. GDPR-wise, an OKF bundle is just files in your jurisdiction.

**One of my notes has gone missing from search, the graph and `vault_todos`.**
It is probably not UTF-8. PowerShell 5.1's `Out-File` and `>` write UTF-16 by default, and the kit reads the vault as UTF-8: a file it cannot decode is skipped by every reader rather than taken as garbled text. Nothing is lost and nothing is rewritten — `kit.py validate` names each such file, every command warns about them on stderr, and the MCP bridge tells the model its results are incomplete. Re-save the file as UTF-8 (`Set-Content -Encoding utf8 note.md (Get-Content note.md)`) and it comes back.

**The Obsidian CLI says the app is not running.**
That is how it works: it drives the desktop app. For headless use take the kit's bridge with `--backend fs`, or yakitrak's `obsidian-cli`.

**qmd crashes while embedding on Windows with an NVIDIA GPU.**
Known: parallel CUDA contexts can crash. Set `QMD_EMBED_PARALLELISM=1`, or `QMD_LLAMA_GPU=vulkan`, or `QMD_FORCE_CPU=1`. `qmd doctor` shows which backend is active.

**`qmd query` is slow on my laptop.**
Re-ranking is the expensive step: `qmd query --no-rerank` or `-C 20` (fewer candidates). Keep the HTTP MCP server running (`qmd mcp --http --daemon`) so models stay loaded between calls.

**The model calls the wrong tool or invents paths.**
Give it the index first ("read index.md"), keep one task per prompt, and use the system prompt in `config/lmstudio/system-prompt.md`. A 2B model is a competent tool user when the tool descriptions are specific — the bridge's are.

**Can the model edit notes without asking me?**
Only if you choose *always allow* on write tools in LM Studio — and even then the bridge refuses people and system folders, hides confidential notes, caps size and rate, never overwrites without an explicit flag and never touches files outside the vault. `--propose` turns every write into a proposal you apply by hand; `--read-only` removes the write tools entirely. [security.md](security.md).

**Windows: native or WSL2?**
Native works for everything (Obsidian, LM Studio, qmd via Node, Python). WSL2 is nice for people who live in Linux tooling; keep the vault on the Windows side if Obsidian runs there, and use mirrored networking to reach `localhost:1234` from WSL. Windows containers are not worth it here — nothing in the kit needs isolation.

**How do I upgrade the kit without losing my notes?**
Your vault is a copy; the kit's `vault/` is the template. Diff `90-templates`, `00-home/bases` and `99-system` between the new kit and your vault and pull what you want. `kit.py validate` tells you if a template change broke a rule.

**Can it run unattended, on a schedule?**
Not from the kit. Every command here is something you run deliberately, and the writing ones are dry-run by default. Scheduling a knowledge vault is a separate problem — one that needs a workflow engine, a permission model per job, and somewhere to put the run artifacts — and bolting a cron entry onto this CLI would give you the scheduling without any of that. Nothing stops you wiring `kit.py index` or `kit.py reconcile --apply` into your own scheduler; just know that you own the failure modes.

Nothing here needs a screen apart from Obsidian itself: the bridge's `fs` backend and every `kit.py` command work over SSH.

**Where is the Word/PDF version of the guide?**
There is none on purpose: every document is Markdown in the repository or in the vault, readable in Obsidian, GitHub, or any editor — and by the model.
