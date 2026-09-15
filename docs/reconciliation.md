# Reconciliation — hubs vs. notes, todos, minutes filing, conflict policy

Everything here is deterministic, runs offline, and is **dry-run by default**. Writes happen only with `--apply`, `--sync-done`, or when you explicitly file a note — and every write leaves a line in `log.md`.

## The conflict policy (one paragraph)

Structured frontmatter in the individual note is the **source of truth**. The hub pages (`03-projects/projects.md` one-liners, `06-decisions/decision-log.md` register, `00-home/priorities.md`) are views written for humans and for models that read literal text. When a view and a note disagree, the view is updated from the note; free-text cells (the "one line", "where decided") are never touched, rows are never deleted, and derived states (a decision that something newer `supersedes`) are written back to the note because the graph proves them. A task ticked in one note and open in another is a conflict resolved in favour of *done* — you can only complete something once. Anything the kit cannot decide mechanically is reported, not guessed.

## `kit.py reconcile`

```bash
uv run kit.py reconcile                 # report only; exit 2 when there is something to apply
uv run kit.py reconcile --apply         # rewrite hub cells, add missing rows, set derived states, log it
```

| Finding | Meaning | `--apply` does |
|---|---|---|
| `hub_drift` | one-liner or register cell disagrees with the note's `health` / `state` / `milestone`+`due` / `date` | overwrite the cell from the note |
| `hub_missing_row` | an active project or any decision has no row | append a row (`description` becomes the one-liner; register is re-sorted newest first) |
| `hub_broken_row` | a row links to a note that does not exist | nothing — you decide whether to delete or re-link |
| `priorities_missing_open_decision` | a decision note is `state: open` but not listed under *Open decisions* in Priorities | nothing — Priorities is prose you write |
| `state_conflict` | decision B `supersedes` A, but A is not `state: superseded` | set `state: superseded` on A (then the register row follows in the same run) |

The passes run in a fixed order: derived states → project hub → decision hub → priorities, so one `--apply` is enough.

## `kit.py todos`

Scans every `- [ ]` / `- [x]` in the vault (templates, archive and the digest itself excluded, code blocks ignored) and parses the convention `verb — owner, due YYYY-MM-DD` (em/en dash or `--`; `📅 YYYY-MM-DD` and `due: YYYY-MM-DD` are understood too).

```bash
uv run kit.py todos                     # summary: overdue, due within 7 days, conflicts
uv run kit.py todos --owner Alex --json # scriptable
uv run kit.py todos --digest            # writes 00-home/todo-digest.md (OKF-valid, linked, generated-by process:kit-todos)
uv run kit.py todos --sync-done         # tick open copies of tasks that are already ticked in another note
```

What it reconciles:

- **Overdue / due soon** — surfaced with file and line, grouped by owner in the digest.
- **Duplicates** — the same task text (normalised) in several notes: typical after a meeting action is copied into the project note. Reported with every location.
- **Done/open conflicts** — a duplicate where one copy is ticked and another is not. `--sync-done` ticks the open copies in place (only `[ ]` → `[x]` on that line; nothing else changes) and logs it. It ticks a copy only when the ticked one carries the same owner and due date — a different owner is a different person's task, and the same line in two daily notes is a recurring one — so the rest stay listed as conflicts for you to settle.
- **Open without owner** — the convention's weak spot; listed so the weekly review can fix it.

The digest page is a generated view: tick tasks where they live (each line links there). Regenerate whenever you like; it never contains anything the notes don't.

## `kit.py minutes` — filing meeting notes

```bash
uv run kit.py minutes recap.txt --title "Search relaunch sync" [--date 2026-09-16] [--project search-relaunch] [--people "Alex, Sam"] [--kind project] [--daily] [--llm] [--dry-run]
cat recap.txt | uv run kit.py minutes - --title "Sync"
```

What happens, in order:

1. **Entity resolution** through the graph: people and projects mentioned in the text (title, `aliases`, unique first names — the bare first name only where it is capitalised, so 'will' and 'mark' stay verbs) are resolved to notes; `--people`/`--project` override detection and are resolved the same way. Unresolved names are reported, never invented.
2. **Extraction by pattern**: `Action:` / `TODO:` / `AI:` / `Task:` lines and `- [ ]` lines become actions in the vault's task format (owner and due parsed when present); `Decision:` / `Decided:` / `Agreed:` lines become decisions; `Outcome:` / `Result:` lines become outcomes.
3. **Optional model pass** (`--llm`): the local endpoint gets the recap with a strict JSON schema and its outcomes/decisions/actions are merged (deduplicated) with the pattern results. If the endpoint is down, the deterministic result stands and you are told.
4. **Note creation** from `90-templates/meeting.md` at `02-meetings/YYYY-MM-DD-<slug>.md`: frontmatter filled (`type`, `title`, `date`, `meeting_type`, `people`, `project` as a relative link, `description` from the first sentence, `generated.by: process:kit-minutes`), sections filled, recap kept verbatim under *Recap*. Existing files are never overwritten without `--force`.
5. **Bookkeeping**: an entry in `log.md`; with `--daily`, a line in today's daily note (created from the daily template if missing).

The same capability is an MCP tool (`file_meeting_minutes`), so in LM Studio you can paste a recap and say "file this as the Tuesday sync with Alex and Sam" — the model calls the tool, you confirm, the note appears with the extraction already done. `vault_todos` and the `graph_*` tools cover the surfacing side in chat.

## Graph-assisted chat replies

`uv run kit.py llm ask "who owns the search relaunch and what is at risk" --graph` prepends **context packs** for the top search hits — type, state/health/status, derived trust tier and staleness, and every relation (owner, people, project, decisions, citations, backlinks). The system prompt tells the model to prefer these facts for relationships, owners, states and dates and to use the prose sources for detail. `--dry-run` prints the assembled prompt so you can see exactly what the model sees. Inside LM Studio the equivalent is the `graph_context` tool.

## Ontology mapping

`99-system/ontology.yml` carries `property_aliases` (`summary → description`, `attendees → people`, `deadline → due`, …) and `value_aliases` (`health: amber → yellow`, `status: wip → draft`). They are applied during graph building and validation, so a vault (or an export from another tool) that uses those names is understood immediately; `kit.py validate` warns once per note so you can rename at leisure. `context.jsonld` maps the canonical names onto Dublin Core, schema.org, PROV-O and SKOS for the RDF/JSON-LD exports — see [knowledge-graph.md](knowledge-graph.md).
