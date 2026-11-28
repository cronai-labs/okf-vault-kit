# Vault guide — structure, conventions, and the three lanes

The vault is a directory of Markdown files with YAML frontmatter. Obsidian renders it; `qmd` indexes it; a local model reads and writes it through MCP; any OKF consumer can parse it. Nothing in it depends on a plugin.

## Design decisions (and why)

| Decision | Why |
|---|---|
| Numbered, **kebab-case** folders and file names, no spaces | deterministic sort order in every tool; links without `%20`; shell- and agent-friendly. The `title` property carries the display name |
| **Markdown links, relative paths** (`useMarkdownLinks: true`, `newLinkFormat: relative`) | OKF §6, GitHub, qmd, and agents all understand them; wikilinks are Obsidian-only. Base embeds still use `![[file.base#View]]` |
| Only `type` is required; `description` is expected on everything | OKF conformance is cheap; one good sentence per note powers the dashboard and every AI prompt |
| `state` for workflow, `status` for OKF lifecycle | they answer different questions ("what am I doing with this" vs. "can an agent trust this"); both stay valid. `state` is a plain property with suggested words — the kit deliberately ships no formal workflow/state machine |
| Daily notes in `01-journal/daily/YYYY/MM/` | a year of notes stays browsable; the path is computed, never typed. `kit.py validate` warns about a day note filed outside its month folder |
| One journal folder per period type: `weekly/`, `monthly/YYYY-MM.md`, `quarterly/` | no folder collects every note of the journal; a month note links its days and shows its tasks through live queries, never copies, so one task lives in one place |
| Templates via the core Templates plugin, `Alt + T` | zero plugins; `{{date:…}}` formats give ISO-8601 timestamps with offset for `generated.at` |
| Example notes marked **(example)** | show the patterns; the tests use them as fixtures; remove them in week one with `kit.py examples remove` (they cross-link on purpose, so deleting by hand breaks the vault) |

## Folders

```text
00-home/       home · dashboard · inbox · priorities · open-actions · bases/*.base
01-journal/    daily/YYYY/MM/ · weekly/ · monthly/YYYY-MM.md · quarterly/
02-meetings/   YYYY-MM-DD-topic.md
03-projects/   projects.md (one-liners) · one note per project
04-areas/      standing responsibilities
05-people/     one note per person, 1:1 inside
06-decisions/  decision-log.md (register) · YYYY-MM-DD-what-was-decided.md
07-knowledge/  concepts · specs · runbooks — the OKF concept heartland
08-resources/  attachments/ · clippings/
09-archive/    inactive; ignored by views and (configure it) by qmd
90-templates/  14 templates
99-system/     conventions · ai-workflows · getting-started
index.md       generated OKF index (kit.py index)
log.md         OKF update log (kit.py log)
```

## Properties

The complete table is in the vault: `99-system/conventions.md`. The short version:

- **every note**: `type`, `title`, `description`, `tags`, `sensitivity`
- **workflow**: `state` ∈ open · active · waiting · on-hold · done · decided · superseded; `health` ∈ green · yellow · red; `owner`, `milestone`, `due`
- **OKF trust & lifecycle** (mostly on knowledge and decisions): `generated {by, at}`, `verified {by, at}` (list allowed), `status` ∈ draft · stable · deprecated, `stale_after`, `sources [{id, resource, title, last_modified}]` with `[^id]` footnotes in the body
- **people**: `role`, `team`, `next_1_1`; **meetings**: `date`, `meeting_type`, `project`, `people`

`kit.py validate` enforces the OKF rules and warns about missing descriptions; the Obsidian property panel shows nested `generated`/`verified` as raw text — that is expected.

## The dashboard

`00-home/dashboard.md` embeds views from `00-home/bases/overview.base`, `people.base`, `journal.base`: Attention, Projects, Open decisions, Waiting, Recent meetings, Knowledge, Needs review (drafts + past `stale_after`), Reviews, Recent dailies, Recently touched, People, Teams. Views are filters over properties — no maintenance. Add a view by copying a block in the `.base` file; the tests check that every view name embedded in a page exists.

## Three lanes, one vault

**Manager.** Daily note → meeting notes → person notes with the rolling 1:1 → weekly review that rewrites `priorities.md`. Dashboard sections: Attention, Waiting, People. Briefing pack for any AI: `priorities.md`, `projects.md`, `decision-log.md`.

**Project / product manager.** Project notes with `milestone`/`due`/`health`, a `spec` per initiative, decisions as they happen, a `retro` per milestone. Sections: Projects, Open decisions, Knowledge. The `projects.md` one-liners are the status report.

**Engineer.** `concept` notes for things worth explaining once, `runbook` notes for things that page you, ADR-shaped `decision` notes (context → options → decision → consequences), the daily note as a lab log. Sections: Knowledge, Needs review, Recently touched. `verified` + `stale_after` keep the wiki honest; `kit.py log` keeps the OKF history.

The lanes are documented on the vault's Home page; nothing structural differs between them.

## Rhythm

Daily (5 min) · after key meetings (5 min) · Friday weekly review (25 min, the template walks you through inbox, actions, projects, decisions, knowledge upkeep, priorities) · month end (20 min, the month's days and tasks through live queries, then a reflection) · quarterly. The `weekly-review` template is the maintenance contract; if it is kept, the vault stays trustworthy for both humans and agents.

## Sync and safety

- One machine at a time on synced folders (OneDrive, iCloud, Nextcloud); conflict copies appear as `… (conflicted copy).md`.
- Git works well for engineers; OKF explicitly recommends git as the bundle distribution form. `kit.py init` writes the vault's `.gitignore`, so `git add .` leaves out what is per-machine:

  ```gitignore
  # per-machine kit state: graph, bridge audit log, proposals, agent settings
  .kit/
  # Obsidian window layout: changes with every window, conflicts across machines
  .obsidian/workspace*.json
  # Obsidian cache: rebuilt on each machine
  .obsidian/cache
  # plugin settings; some hold credentials (Local REST API's key) - re-include one with !.obsidian/plugins/<id>/data.json
  .obsidian/plugins/*/data.json
  ```

  `init` writes it only when the vault has none; under `--force` an existing `.gitignore` is left byte for byte, so add these lines yourself to a vault that predates them; while its config is still at `.kit/config.yml`, run `kit.py config migrate --apply` first, since `.kit/` would ignore that file and a clone would have no floors (`init` itself writes the old `.kit/*` and `!.kit/config.yml` rules to such a vault, and doctor's `vault git` row warns when the config in force is ignored). Plugin settings then stay on each machine; re-include one plugin's settings, such as the Linter's, with `!.obsidian/plugins/<id>/data.json`. `init` also leaves the template's own `.kit/` state, workspace files and cache (`.obsidian/workspace*.json`, `.obsidian/cache`) out of the copy; a template `.okf-vault-kit.yaml` is copied, but never over a vault's own config under `--force`. The kit config, `.okf-vault-kit.yaml` at the vault root, is not in `.kit/` and stays in git: it holds the [confidential folders and path rules](security.md#scoped-hiding-confidential-folders-and-path-rules) and the open folders, so a clone enforces the same floors as the machine it came from. It also carries what else the file declares, the qmd collection name and provider choices, into the history and every clone. The kit reads no credential from it; keep it that way, since git keeps whatever you write there. Commit it with the vault. In a vault inside a git work tree, `kit.py doctor` has a `vault git` row: a warning when `.kit/` or plugin settings are not ignored, before the first commit, a warning when a rule ignores `.okf-vault-kit.yaml` (a clone would lose the floors), and the count of `.kit/` or plugin files already tracked with the fix: `git rm --cached` on each file `git ls-files -ci --exclude-standard` lists, after `kit.py config migrate --apply` if the config is still at `.kit/config.yml` (a pushed history also needs rewriting, and any key in it rotating). A file you re-included with a `!` line is your choice: it is neither warned about nor counted, and the fix leaves it tracked. The row names no path but the config's and does not change doctor's exit code; it is not shown for the kit's own sample vault.
- **Moving the config from `.kit/config.yml`.** Up to 0.3 the kit config lived at `.kit/config.yml`, and `init` wrote the rules `.kit/*` and `!.kit/config.yml` to keep it in git. The kit still reads it there, with a warning on every run, while no `.okf-vault-kit.yaml` exists; when both exist, `.okf-vault-kit.yaml` is in force, the old file is ignored, and `kit.py doctor` and `kit.py validate` say so. `kit.py config migrate --vault ~/Notes/vault` shows what it would do; `--apply` moves the file, bytes unchanged, and turns those two rules into one `.kit/`, leaving every other line of `.gitignore` as it is. With both files present it refuses unless `--force`, which sets the old one aside as `.kit/config.yml.migrated` rather than deleting it; an old file with the same bytes as the new one, which a run stopped halfway leaves, is removed without `--force`. A `!.kit/config.yml` rule in any other shape, or a `.gitignore` that is a link, is left for you to change by hand, and the output says so. Run again, it says there is nothing to do. A kit up to 0.3 reads only `.kit/config.yml` and applies no floors once it is gone, so upgrade the kit on every machine that uses the vault, and restart any running bridge, before you migrate and push. Then commit the move (`git rm -q --cached --ignore-unmatch .kit/config.yml`, `git add .okf-vault-kit.yaml .gitignore`), and run `kit.py agent-settings claude-code --apply` again if you use it: it names the new file and denies `.kit/` whole now. A cached graph is rebuilt once, since the config's place is part of its change check.
- `sensitivity: confidential` notes (people) should never be attached to a cloud model. With its default policy the bridge hides them from the model, and the kit leaves their titles, descriptions, paths and tasks out of the files it generates. A coding agent's own file and shell tools are not the bridge: started in the vault or above it, the agent reads every note unless its settings deny the folder — [security.md §5](security.md#5-coding-agents-and-other-direct-readers).
- No credentials in any note, at any sensitivity: passwords, API keys, tokens, recovery codes, and links that grant access to whoever holds them. Write where the secret is kept, never the secret — the model reads every note that is not confidential through the bridge, a coding agent's own tools can read every note, and git keeps whatever was committed.
- Obsidian's *File recovery* core plugin keeps snapshots; `09-archive` is excluded from views, and the qmd example config ignores it.
