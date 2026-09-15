# Plugins — 2026 recommendations, and why the vault ships with none

Obsidian **core plugins** cover everything this vault needs: Daily notes, Templates, Properties, Bases, Bookmarks, Backlinks, Outline, File recovery, Canvas. Bases (GA since Obsidian 1.9) replaced the most common reason people installed Dataview — property-driven tables and views. The template ships with `community-plugins.json` empty on purpose.

## Get community plugins approved first

Community plugins are third-party JavaScript running with full access to your vault and your machine (network, file system). Before installing one on a work device:

1. Check the plugin is listed in Obsidian's official directory (reviewed at submission, not audited continuously), read its repository, note the last release date and open issues.
2. If your organisation has a software-approval process, file it: name, repository, author, permissions used (network? shell?), data it touches, why core plugins are not enough. Attach the vault's `sensitivity` model from the conventions page.
3. Pin the version, disable auto-update on work devices, and record the approval as a `decision` note in `06-decisions` — the vault's own ADR format is the right place.
4. Re-review annually or when ownership of the plugin changes.

Obsidian itself is free for commercial use (licence change in February 2025), so the approval question is about plugins, not the app.

## Shortlist (September 2026)

| Plugin | Adds | Fits which lane | Caveat |
|---|---|---|---|
| **Templater** | scripted templates, JS in templates, prompts on insert | all | more power than the core Templates plugin; only worth it once you want conditional or computed frontmatter |
| **Tasks** | queries over `- [ ]` with due dates, recurrence, priorities | manager, PM | the vault's `Open Actions` page already does this with core search; Tasks adds due-date logic |
| **Calendar** / **Periodic Notes** | calendar sidebar; weekly/quarterly notes with the same `YYYY/MM` discipline as dailies | all | Periodic Notes replaces the manual `01-journal/weekly` naming |
| **Dataview** | inline queries, JS views | engineer | prefer Bases; keep Dataview only for what Bases cannot do yet (computed roll-ups across notes) |
| **Kanban** | board views backed by markdown lists | PM | fine for a single project board; not a substitute for project notes |
| **Excalidraw** | diagrams as `.excalidraw.md` | engineer, PM | large files; keep in `08-resources` |
| **Linter** | enforce frontmatter and formatting rules on save | engineer | can be configured to keep `description` present and dates ISO — complements `kit.py validate` |
| **obsidian-qmd** (achekulaev) | semantic search *inside* Obsidian, powered by your qmd index | all | requires qmd installed; the plugin shells out to it |
| **Local REST API** | HTTP access to the vault for external tools | engineer | alternative to the official CLI for agents; secure the token |
| **Web Clipper** (official browser extension, not a plugin) | clean captures into `08-resources/clippings` | all | set the template to include `type: resource` and a `description` |

Skip: anything that rewrites frontmatter silently, anything that syncs to a third-party cloud by default, and "AI assistant" plugins that send note content to hosted APIs — the kit's local setup gives you the same features with the data staying on the machine.

## Obsidian settings that matter more than plugins

- Files & links: *Use [[Wikilinks]]* **off**, *New link format* **relative to file** (both preset in the template).
- Files & links: attachment folder `08-resources/attachments`; *Excluded files* `09-archive/`.
- Editor: *Properties in document* visible — you want to see `description` while writing.
- Core plugins: enable **Command line interface** (Settings → General) if you use the official CLI.
- Appearance: readable line length on; this is a writing tool.
