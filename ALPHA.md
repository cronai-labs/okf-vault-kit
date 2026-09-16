# Alpha — what we are asking you to do

Thank you for trying this. You are among the first people outside CronAI to run it, which means you
will find things we could not. That is the point; nothing here is polished enough to be embarrassing
about.

**Version:** 0.1.0 · **MIT licensed** · notes stay on your machine
([why we can say that](docs/it-review.md))

## What it is

An Obsidian vault that a machine can read: Markdown files with a small amount of structured
frontmatter, an on-device search index, and a local language model that can search and edit the
vault through tools — without anything leaving the laptop.

## What we want to learn

Three questions, in order of how much they matter to us:

1. **Did you get it running, and what stopped you?** Especially on a managed machine. If you hit the
   proxy, tell us where — that failure is more useful to us than any feature feedback.
2. **After a week, is the vault something you keep opening, or something you set up once?**
3. **Was the local model useful enough to be worth the disk space,** or did you go back to the
   hosted one?

## The path

About 30 minutes, most of it downloads.

1. Read [docs/quickstart.md](docs/quickstart.md). On a work machine, read
   [docs/platforms/proxy.md](docs/platforms/proxy.md) **first**.
2. `uv run kit.py doctor` — tells you what is missing before you invest time.
3. `uv run kit.py init --target ~/Notes/vault --actor human:<you>`
4. Open the folder in Obsidian, read `99-system/getting-started.md` inside it.
5. Install qmd and index the vault (quickstart step 3). If the registry is blocked, skip it — the
   kit falls back to plain search and everything else still works.
6. Point it at a local model (quickstart step 4). LM Studio is the easy route, `llama-server` and
   Ollama work equally well.
7. `uv run kit.py mcp-config --client lmstudio --vault ~/Notes/vault --write`, restart the client, and
   ask it something about your notes. The `--vault` is not optional: without it the client is pointed at
   the sample vault inside the checkout, and the command refuses to write.

Then use it for real for a week. Put actual work in it — a project you care about, real meeting
notes. A vault filled with test data tells neither of us anything.

## What is deliberately not here

- **Scheduling and unattended runs.** Nothing runs in the background, nothing is installed as a
  service, no scheduled task is created. A knowledge vault that edits itself on a timer needs a
  permission model we have not built yet, so for now every change is one you asked for.
- **Containers.** Nothing to install, no Docker licence question. The protection that matters —
  what the model may do to your notes — is in the bridge's policy layer, not in a sandbox.
- **Anything hosted.** No account, no sync service, no telemetry.

## Known rough edges

- The Bases views need one visual check in Obsidian after any template edit; we validate them
  structurally, not visually.
- German notes: semantic search is English-tuned by default. Switch the embedding model before the
  first index — [docs/models.md](docs/models.md).
- A 2B model is a 2B model. It is good at "find this and summarise it", unreliable at reasoning
  across many notes. Use `--graph` for anything involving owners, dates or relations.
- The MCP SDK is pinned to v1; the 2.x API landed recently and we are not migrating mid-alpha.
- Windows native is the fallback lane. If you have WSL2, use it —
  [docs/platforms/wsl2.md](docs/platforms/wsl2.md).

## Telling us what happened

```bash
uv run kit.py doctor --report doctor.txt
```

That writes a file with versions, what is installed, which providers are active and whether your
endpoint answers. **It contains no note content, no file paths, no username, and none of your
endpoint's hostname, credentials, path, query string or fragment** — a remote endpoint is reduced
to its scheme and port, a loopback one keeps its path because that is where misconfigurations
show. Model ids that are file paths become the bare filename. There are tests that fail if any of
that changes.

Three things survive, and it is better you know than find out. A model id that is *not* a path,
such as `acme-internal/codename-x`, is printed as written — knowing which model you ran is the
point of the report. A **bare relative** path (`--vault Client/notes`, no leading `./`) is not
recognised as a path: half this report is install hints that look the same (`@tobilu/qmd`,
`docs/quickstart.md`), and mangling those costs more than it saves — pass an absolute path, or
`./Client/notes`, if the folder name is confidential. A directory name containing `[` or `]` can
survive for the same reason: the report brackets its own values. Rename or re-invoke before you
send if any of those names is confidential. Send it with your notes, and
see [FEEDBACK.md](FEEDBACK.md).

If something crashed, the traceback is worth more than a description of it.

## Removing it

```bash
qmd collection remove vault          # if you indexed one
rm -rf ~/.cache/qmd ~/.config/qmd    # qmd's models and index
rm -rf ~/.lmstudio/models            # the models you downloaded; uninstalling LM Studio leaves them
```

Delete the `qmd` and `obsidian-vault` entries that `kit.py mcp-config --write` added to
`~/.lmstudio/mcp.json` (if the file already existed, the original is next to it as `mcp.json.bak`).
Left in place they point at a bridge script that is no longer there, and LM Studio tries to spawn it
on every start.

Then delete the kit folder, and uninstall uv/bun/Obsidian/LM Studio through the same package manager
you installed them with. Your vault is a normal folder of Markdown files — it is yours, it outlives
the kit, and it stays readable in anything.

## Contact

Tobias Krug · CronAI UG (haftungsbeschränkt) · hashkode@posteo.de
