"""kitrecon — automated reconciliation for the vault.

Three concerns, all deterministic, all dry-run by default:

  reconcile  hub tables (projects.md one-liners, decision-log.md register, priorities.md) vs. note frontmatter,
             and derived-state conflicts from the graph (superseded decisions). Policy: structured frontmatter in the
             note is the source of truth; hub rows are views. --apply rewrites hub cells, adds missing rows, sets
             derived states; it never deletes and never touches free-text cells.
  todos      every `- [ ]` / `- [x]` in the vault with owner and due parsed from `— owner, due YYYY-MM-DD`;
             overdue / due-soon / duplicates / done-in-one-place-open-in-another; digest page; --sync-done.
  minutes    file a pasted recap as a meeting note: entities resolved through the graph, actions and decisions
             extracted by pattern, template-rendered, logged; optional LLM structuring on top.

Standard library + PyYAML only.
"""
from __future__ import annotations

import datetime as dt
import posixpath
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import kitgraph
import kitlib

PROJECT_HUB = "03-projects/projects.md"
DECISION_HUB = "06-decisions/decision-log.md"
PRIORITIES = "00-home/priorities.md"
DIGEST = "00-home/todo-digest.md"
CLOSED_STATES = {"done", "decided", "superseded", "archived", "cancelled"}

TASK_RE = re.compile(r"^(?P<indent>\s*)[-*]\s+\[(?P<mark> |x|X)\]\s+(?P<text>.*?)\s*$")
OWNER_DUE_RE = re.compile(r"^(?P<text>.*?)\s+(?:—|–|--)\s+(?P<owner>[^,]+?)(?:,\s*due\s+(?P<due>\d{4}-\d{2}-\d{2}))?\s*$")
DUE_EMOJI_RE = re.compile(r"\s*(?:📅|due:)\s*(\d{4}-\d{2}-\d{2})")
TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")                       # `\|` is a literal pipe inside a cell, not a border
LINK_TARGET_RE = re.compile(r"\[[^\]]*\]\(\s*(?:<(?P<angle>[^>]*)>|(?P<plain>[^)]*?))(?:\s+\"[^\"]*\")?\s*\)")
TEMPLATE_DIR = "90-templates"


# ================================================================ hub reconciliation

@dataclass
class Change:
    code: str
    file: str
    message: str
    applied: bool = False

    def render(self) -> str:
        return f"{'applied' if self.applied else 'finding'}  {self.code:24} {self.file}: {self.message}"


def _split_row(line: str) -> list[str]:
    """Cells of a markdown table row, split on real cell borders only.

    An escaped pipe belongs to a cell's free text: splitting on it would invent a column and break
    the escape, and the cells this module does not rewrite are put back exactly as they came in.
    """
    m = TABLE_ROW_RE.match(line.rstrip())
    if not m:
        return []
    return [c.strip() for c in CELL_SPLIT_RE.split(m.group(1))]


def _find_table(lines: list[str], after_heading: str | None = None) -> tuple[int, int, int] | None:
    """Return (header_idx, first_row_idx, end_idx_exclusive) of the first table (after a heading if given)."""
    start = 0
    if after_heading:
        for i, l in enumerate(lines):
            if l.strip().lower().startswith(after_heading.lower()):
                start = i; break
    for i in range(start, len(lines) - 1):
        if TABLE_ROW_RE.match(lines[i]) and re.match(r"^\|\s*:?-+", lines[i + 1]):
            j = i + 2
            while j < len(lines) and TABLE_ROW_RE.match(lines[j]):
                j += 1
            return i, i + 2, j
    return None


def _first_link_target(cell: str) -> str | None:
    """The first markdown link target in a cell, percent-decoded and without its fragment.

    A row whose link this cannot read is a row that is reported broken and appended again on every
    run, so it reads what people and Obsidian actually write: spaces raw, percent-encoded, or in
    angle brackets. `_link` writes targets that come back through here unchanged.
    """
    m = LINK_TARGET_RE.search(cell)
    if not m:
        return None
    target = m.group("angle") if m.group("angle") is not None else m.group("plain")
    return urllib.parse.unquote(target.split("#", 1)[0]).strip() or None


def _row_target_id(g: kitgraph.Graph, hub: str, cell: str) -> str | None:
    """Node id a hub row's link points at, resolved against the hub's own folder."""
    target = _first_link_target(cell)
    if not target:
        return None
    return kitgraph.resolve_id(g, posixpath.normpath(f"{Path(hub).parent.as_posix()}/{target}"))


def _rel(from_file: str, to_file: str) -> str:
    """Relative markdown path from one vault file to another."""
    src = Path(from_file).parent
    parts = []
    target = Path(to_file)
    # compute relative path without filesystem
    src_parts = list(src.parts)
    dst_parts = list(target.parts)
    while src_parts and dst_parts and src_parts[0] == dst_parts[0]:
        src_parts.pop(0); dst_parts.pop(0)
    parts = [".."] * len(src_parts) + dst_parts
    return "/".join(parts)


def _link(from_file: str, to_file: str) -> str:
    """A markdown link target: the relative path, percent-encoded like Obsidian writes it."""
    return urllib.parse.quote(_rel(from_file, to_file), safe="/")


def _milestone_text(props: dict) -> str:
    ms = str(props.get("milestone") or "").strip()
    due = props.get("due")
    due_s = kitgraph._iso(due) if due not in (None, "") else ""
    return " ".join(x for x in (ms, due_s) if x)


def reconcile(vault: Path, apply: bool = False, g: kitgraph.Graph | None = None, today: dt.date | None = None) -> list[Change]:
    vault = Path(vault)
    g = g or kitgraph.build_graph(vault)
    changes: list[Change] = []
    changes += _reconcile_superseded(vault, g, apply)       # derived states first …
    if apply and any(c.applied for c in changes):
        g = kitgraph.build_graph(vault)                      # … so the hub passes see the new states
    changes += _reconcile_projects(vault, g, apply)
    changes += _reconcile_decisions(vault, g, apply)
    changes += _reconcile_priorities(vault, g)
    if apply and any(c.applied for c in changes):
        n = sum(1 for c in changes if c.applied)
        for skip in _log_entry(vault, f"Reconciled hubs and derived states ({n} change(s)) — kit.py reconcile --apply", "Update", today):
            changes.append(Change("log_unreadable", "log.md", skip))
    return changes


def _log_entry(vault: Path, message: str, kind: str, today: dt.date | None = None) -> list[str]:
    """Write the provenance entry, or name log.md as the one thing that did not happen.

    Every tool here logs what it wrote as its last step. An undecodable log.md used to raise out
    of that step, after the note was on disk — so the caller reported failure for work the vault
    had already taken. The entry is skipped instead, and the caller says so.
    """
    if kitlib.append_log(vault, message, kind, today) is None:
        return ["log.md (not valid UTF-8; the provenance entry was not written)"]
    return []


def _unreadable_hub(rel: str) -> Change:
    """A hub we cannot decode is a finding, not the end of the run.

    Reconcile touches three hubs and every note; one of them written by PowerShell 5.1 must not
    cost the user the other two. Rewriting it is out of the question — we cannot read what is in
    it — so the pass is skipped and named.
    """
    return Change("hub_unreadable", rel, "not valid UTF-8 — skipped; re-save it as UTF-8 and run reconcile again")


def _reconcile_projects(vault: Path, g: kitgraph.Graph, apply: bool) -> list[Change]:
    hub = vault / PROJECT_HUB
    if not hub.exists():
        return []
    text = kitlib.read_text(hub)
    if text is None:
        return [_unreadable_hub(PROJECT_HUB)]
    lines = text.splitlines()
    loc = _find_table(lines, "## One-liners")
    if not loc:
        return [Change("hub_no_table", PROJECT_HUB, "no one-liner table found under '## One-liners'")]
    header_i, first, end = loc
    header = [h.lower() for h in _split_row(lines[header_i])]
    col = {name: header.index(name) for name in ("project", "health", "state", "next milestone", "one line") if name in header}
    if "project" not in col:
        return [Change("hub_no_table", PROJECT_HUB, "table lacks a 'Project' column")]
    changes: list[Change] = []
    seen: set[str] = set()
    for i in range(first, end):
        cells = _split_row(lines[i])
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        nid = _row_target_id(g, PROJECT_HUB, cells[col["project"]])
        if not nid:
            changes.append(Change("hub_broken_row", PROJECT_HUB, f"row '{cells[col['project']]}' does not link to a project note"))
            continue
        seen.add(nid)
        props = g.nodes[nid].props
        expected = {"health": str(props.get("health") or ""), "state": str(props.get("state") or ""), "next milestone": _milestone_text(props)}
        drift = {k: (cells[col[k]], v) for k, v in expected.items() if k in col and cells[col[k]] != v}
        if drift:
            desc = "; ".join(f"{k}: hub '{a}' vs note '{b}'" for k, (a, b) in drift.items())
            ch = Change("hub_drift", PROJECT_HUB, f"{nid} — {desc}")
            if apply:
                for k, (_, v) in drift.items():
                    cells[col[k]] = v
                lines[i] = "| " + " | ".join(cells) + " |"
                ch.applied = True
            changes.append(ch)
    missing = [nid for nid, n in g.nodes.items() if n.kind == "note" and n.type == "project"
               and str(n.props.get("state") or "") not in CLOSED_STATES and nid not in seen]
    new_rows = []
    for nid in sorted(missing):
        n = g.nodes[nid]
        ch = Change("hub_missing_row", PROJECT_HUB, f"active project {nid} has no one-liner")
        if apply:
            cells = [""] * len(header)
            cells[col["project"]] = f"[{n.title}]({_link(PROJECT_HUB, nid)})"
            if "health" in col: cells[col["health"]] = str(n.props.get("health") or "")
            if "state" in col: cells[col["state"]] = str(n.props.get("state") or "")
            if "next milestone" in col: cells[col["next milestone"]] = _milestone_text(n.props)
            if "one line" in col: cells[col["one line"]] = n.description
            new_rows.append("| " + " | ".join(cells) + " |")
            ch.applied = True
        changes.append(ch)
    if apply and any(c.applied for c in changes):
        lines[end:end] = new_rows
        hub.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8", newline="\n")
    return changes


def _reconcile_decisions(vault: Path, g: kitgraph.Graph, apply: bool) -> list[Change]:
    hub = vault / DECISION_HUB
    if not hub.exists():
        return []
    text = kitlib.read_text(hub)
    if text is None:
        return [_unreadable_hub(DECISION_HUB)]
    lines = text.splitlines()
    loc = _find_table(lines, "## Register")
    if not loc:
        return [Change("hub_no_table", DECISION_HUB, "no register table found under '## Register'")]
    header_i, first, end = loc
    header = [h.lower() for h in _split_row(lines[header_i])]
    col = {name: header.index(name) for name in ("date", "decision", "state", "where decided", "note") if name in header}
    if "note" not in col:
        return [Change("hub_no_table", DECISION_HUB, "table lacks a 'Note' column")]
    changes: list[Change] = []
    seen: set[str] = set()
    rows: list[tuple[str, str]] = []   # (date, line)
    for i in range(first, end):
        cells = _split_row(lines[i])
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        nid = _row_target_id(g, DECISION_HUB, cells[col["note"]])
        if not nid:
            changes.append(Change("hub_broken_row", DECISION_HUB, f"row '{cells[col.get('decision', 0)]}' does not link to a decision note"))
            rows.append(("", lines[i])); continue
        seen.add(nid)
        props = g.nodes[nid].props
        expected = {"date": kitgraph._iso(props.get("date") or ""), "state": str(props.get("state") or "")}
        drift = {k: (cells[col[k]], v) for k, v in expected.items() if k in col and cells[col[k]] != v}
        if drift:
            desc = "; ".join(f"{k}: hub '{a}' vs note '{b}'" for k, (a, b) in drift.items())
            ch = Change("hub_drift", DECISION_HUB, f"{nid} — {desc}")
            if apply:
                for k, (_, v) in drift.items():
                    cells[col[k]] = v
                lines[i] = "| " + " | ".join(cells) + " |"
                ch.applied = True
            changes.append(ch)
        rows.append((cells[col["date"]] if "date" in col else "", lines[i]))
    missing = [nid for nid, n in g.nodes.items() if n.kind == "note" and n.type == "decision" and nid not in seen]
    for nid in sorted(missing):
        n = g.nodes[nid]
        ch = Change("hub_missing_row", DECISION_HUB, f"decision {nid} is not in the register")
        if apply:
            cells = [""] * len(header)
            if "date" in col: cells[col["date"]] = kitgraph._iso(n.props.get("date") or "")
            if "decision" in col: cells[col["decision"]] = n.title
            if "state" in col: cells[col["state"]] = str(n.props.get("state") or "")
            cells[col["note"]] = f"[note]({_link(DECISION_HUB, nid)})"
            rows.append((cells[col["date"]] if "date" in col else "", "| " + " | ".join(cells) + " |"))
            ch.applied = True
        changes.append(ch)
    if apply and any(c.applied for c in changes):
        rows.sort(key=lambda r: r[0], reverse=True)   # newest first
        lines[first:end] = [r[1] for r in rows]
        hub.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8", newline="\n")
    return changes


def _reconcile_priorities(vault: Path, g: kitgraph.Graph) -> list[Change]:
    p = vault / PRIORITIES
    if not p.exists():
        return []
    text = kitlib.read_text(p)
    if text is None:
        return [_unreadable_hub(PRIORITIES)]
    m = re.search(r"^## Open decisions\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    section = m.group(1) if m else ""
    changes = []
    for nid, n in g.nodes.items():
        if (n.kind == "note" and n.type == "decision" and n.props.get("state") == "open"
                and Path(nid).name not in section and n.title not in section):
            changes.append(Change("priorities_missing_open_decision", PRIORITIES, f"open decision {nid} is not listed under '## Open decisions'"))
    return changes


def _reconcile_superseded(vault: Path, g: kitgraph.Graph, apply: bool) -> list[Change]:
    changes = []
    for f in g.findings:
        if f.code == "state_conflict":
            ch = Change("state_conflict", f.node, f.message)
            if apply:
                kitlib.set_frontmatter(vault / f.node, {"state": "superseded"})
                ch.applied = True
            changes.append(ch)
    return changes


# ================================================================ todos

@dataclass
class Task:
    file: str
    line: int
    done: bool
    text: str
    owner: str = ""
    due: str = ""
    note_type: str = ""
    key: str = ""        # normalised text for duplicate detection

    @property
    def due_date(self) -> dt.date | None:
        try:
            return dt.date.fromisoformat(self.due) if self.due else None
        except ValueError:
            return None


def _task_key(text: str) -> str:
    t = re.sub(r"\(\?\)", "", text.lower())
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_task_line(line: str) -> tuple[bool, str, str, str] | None:
    m = TASK_RE.match(line)
    if not m:
        return None
    done = m.group("mark").lower() == "x"
    text = m.group("text")
    due = ""
    de = DUE_EMOJI_RE.search(text)
    if de:
        due = de.group(1); text = DUE_EMOJI_RE.sub("", text).strip()
    owner = ""
    od = OWNER_DUE_RE.match(text)
    if od and _plausible_owner(od.group("owner"), bool(od.group("due"))):
        text, owner, due = od.group("text").strip(), od.group("owner").strip(), od.group("due") or due
    return done, text, owner, due


def _plausible_owner(owner: str, has_due: bool) -> bool:
    owner = owner.strip()
    if not owner or any(ch.isdigit() for ch in owner) or owner.endswith("."):
        return False
    return has_due or len(owner.split()) <= 3


def scan_todos(vault: Path, g: kitgraph.Graph | None = None) -> list[Task]:
    vault = Path(vault)
    tasks: list[Task] = []
    for note in kitlib.load_vault(vault):
        if note.path.name in kitlib.RESERVED_FILENAMES or note.rel.startswith(("90-templates/", "09-archive/")) or note.rel == DIGEST:
            continue
        ntype = str((note.frontmatter or {}).get("type") or "")
        full = kitlib.read_text(note.path)
        if full is None:
            continue          # it decoded a moment ago, in load_vault; it does not now
        offset = full[: len(full) - len(note.body)].count("\n")
        in_code = False
        for i, line in enumerate(note.body.splitlines()):
            if line.strip().startswith("```"):
                in_code = not in_code; continue
            if in_code:
                continue
            parsed = parse_task_line(line)
            if not parsed:
                continue
            done, text, owner, due = parsed
            if not text or text.startswith("verb —") or "{{" in text or text in ("change — owner, due YYYY-MM-DD",):
                continue
            tasks.append(Task(note.rel, offset + i + 1, done, text, owner, due, ntype, _task_key(text)))
    return tasks


@dataclass
class TodoReport:
    tasks: list[Task]
    today: dt.date
    overdue: list[Task] = field(default_factory=list)
    due_soon: list[Task] = field(default_factory=list)
    open_no_owner: list[Task] = field(default_factory=list)
    duplicates: list[list[Task]] = field(default_factory=list)
    done_conflicts: list[list[Task]] = field(default_factory=list)
    synced: list[Task] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # what the run could not read, named for the user

    @property
    def open(self) -> list[Task]:
        return [t for t in self.tasks if not t.done]


def todo_report(vault: Path, today: dt.date | None = None, horizon_days: int = 7, sync_done: bool = False) -> TodoReport:
    vault = Path(vault)
    today = today or dt.date.today()
    tasks = scan_todos(vault)
    rep = TodoReport(tasks, today)
    for t in tasks:
        if t.done:
            continue
        d = t.due_date
        if d and d < today:
            rep.overdue.append(t)
        elif d and d <= today + dt.timedelta(days=horizon_days):
            rep.due_soon.append(t)
        if not t.owner and t.note_type not in ("daily",):
            rep.open_no_owner.append(t)
    groups: dict[str, list[Task]] = {}
    for t in tasks:
        if len(t.key) >= 12:
            groups.setdefault(t.key, []).append(t)
    for grp in groups.values():
        files = {t.file for t in grp}
        if len(files) > 1:
            rep.duplicates.append(grp)
            if any(t.done for t in grp) and any(not t.done for t in grp):
                rep.done_conflicts.append(grp)
    if sync_done:
        for grp in rep.done_conflicts:
            done = [t for t in grp if t.done]
            for t in grp:
                if not t.done and any(_same_commitment(t, d) for d in done):
                    _tick(vault / t.file, t.line)
                    t.done = True
                    rep.synced.append(t)
        if rep.synced:
            rep.skipped += _log_entry(vault, f"Ticked {len(rep.synced)} task(s) already done elsewhere — kit.py todos --sync-done", "Update", today)
    rep.overdue.sort(key=lambda t: t.due); rep.due_soon.sort(key=lambda t: t.due)
    return rep


def _same_commitment(a: Task, b: Task) -> bool:
    """Whether two copies of the same task text are the same commitment, and may close each other.

    The text alone is not identity: a differing owner or due date means the checkboxes belong to
    different people or different rounds, and the same task repeated in two daily notes is a
    recurring one — ticking those would close work nobody did.
    """
    if a.owner.strip().lower() != b.owner.strip().lower() or a.due != b.due:
        return False
    return not (a.note_type == "daily" and b.note_type == "daily")


def _tick(path: Path, line_no: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    idx = line_no - 1
    if 0 <= idx < len(lines):
        lines[idx] = re.sub(r"\[ \]", "[x]", lines[idx], count=1)
        path.write_text("".join(lines), encoding="utf-8", newline="\n")


def _task_line(t: Task, from_file: str) -> str:
    where = f"[{Path(t.file).stem}]({_link(from_file, t.file)})"
    meta = " — ".join(x for x in (t.owner, f"due {t.due}" if t.due else "") if x)
    return f"- [{'x' if t.done else ' '}] {t.text}" + (f" — {meta}" if meta else "") + f" · {where}"


def render_digest(rep: TodoReport, from_file: str = DIGEST, actor: str = "process:kit-todos") -> str:
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    lines = ["---", "type: system", "title: Todo Digest",
             f"description: Generated task digest as of {rep.today.isoformat()} — {len(rep.overdue)} overdue, {len(rep.due_soon)} due soon, {len(rep.done_conflicts)} conflict(s); regenerate with kit.py todos --digest.",
             "tags: [system, generated]", "sensitivity: internal", f"generated: {{ by: {actor}, at: \"{now}\" }}", "---", "",
             "# Todo Digest", "", f"{len(rep.open)} open tasks across {len({t.file for t in rep.tasks})} notes. Ticking a task here does nothing — tick it where it lives (links on every line).", ""]
    def section(title: str, items: list[Task], empty: str):
        lines.append(f"## {title}"); lines.append("")
        if not items:
            lines.append(f"_{empty}_")
        for t in items:
            lines.append(_task_line(t, from_file))
        lines.append("")
    section("Overdue", rep.overdue, "nothing overdue")
    section("Due in the next 7 days", rep.due_soon, "nothing due")
    section("Open without owner", rep.open_no_owner, "every open task has an owner")
    lines.append("## Same task in several notes"); lines.append("")
    if not rep.duplicates:
        lines.append("_no duplicates_")
    for grp in rep.duplicates:
        conflict = grp in rep.done_conflicts
        lines.append(f"- {'⚠ done in one note, open in another: ' if conflict else ''}{grp[0].text}")
        for t in grp:
            lines.append(f"  - [{'x' if t.done else ' '}] [{Path(t.file).stem}]({_link(from_file, t.file)}) line {t.line}")
    lines.append("")
    by_owner: dict[str, list[Task]] = {}
    for t in rep.open:
        by_owner.setdefault(t.owner or "(no owner)", []).append(t)
    lines.append("## By owner"); lines.append("")
    for owner in sorted(by_owner):
        lines.append(f"### {owner}"); lines.append("")
        for t in sorted(by_owner[owner], key=lambda x: (x.due or "9999", x.file)):
            lines.append(_task_line(t, from_file))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_digest(vault: Path, rep: TodoReport, path: str = DIGEST) -> Path:
    out = Path(vault) / path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_digest(rep, path), encoding="utf-8", newline="\n")
    return out


# ================================================================ minutes filing

HINT_RE = re.compile(r"^\s*[-*]\s+\(.*\)\s*$")          # a template placeholder line: '- (do this here)'
ACTION_LINE = re.compile(r"^\s*(?:[-*•]\s*)?(?:\[ \]\s*)?(?:action(?:\s*item)?|todo|ai|task)\s*[:\-–—]\s*(.+?)\s*$", re.I)
DECISION_LINE = re.compile(r"^\s*(?:[-*•]\s*)?(?:decision|decided|agreed|agreement)\s*[:\-–—]\s*(.+?)\s*$", re.I)
OUTCOME_LINE = re.compile(r"^\s*(?:[-*•]\s*)?(?:outcome|result|conclusion)\s*[:\-–—]\s*(.+?)\s*$", re.I)


@dataclass
class Minutes:
    title: str
    date: dt.date
    text: str
    people: list[str] = field(default_factory=list)        # node ids
    project: str | None = None                             # node id
    actions: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    path: str = ""
    skipped: list[str] = field(default_factory=list)   # what the run could not read, named for the user


def extract_minutes(text: str) -> tuple[list[str], list[str], list[str]]:
    actions, decisions, outcomes = [], [], []
    for line in text.splitlines():
        parsed = parse_task_line(line)
        if parsed and not parsed[0]:
            _, t, owner, due = parsed
            actions.append(_format_action(t, owner, due)); continue
        m = ACTION_LINE.match(line)
        if m:
            p2 = OWNER_DUE_RE.match(m.group(1))
            if p2 and _plausible_owner(p2.group("owner"), bool(p2.group("due"))):
                actions.append(_format_action(p2.group("text").strip(), p2.group("owner").strip(), p2.group("due") or ""))
            else:
                actions.append(_format_action(m.group(1), "", ""))
            continue
        m = DECISION_LINE.match(line)
        if m:
            decisions.append(m.group(1)); continue
        m = OUTCOME_LINE.match(line)
        if m:
            outcomes.append(m.group(1))
    return actions, decisions, outcomes


def _format_action(text: str, owner: str, due: str) -> str:
    text = text.strip().rstrip(".")
    meta = ", ".join(x for x in (owner, f"due {due}" if due else "") if x)
    return f"- [ ] {text}" + (f" — {meta}" if meta else "")


def detect_entities(text: str, g: kitgraph.Graph) -> tuple[list[str], list[str]]:
    """People and projects mentioned in free text, by title/alias (word-boundary, case-insensitive).

    A bare first name is matched capitalised and case-sensitively instead: 'will', 'mark', 'max'
    and 'grant' are ordinary English words, and a person named Will must not be an attendee of
    every meeting whose recap contains a future tense.
    """
    people, projects = [], []
    for nid, n in g.nodes.items():
        if n.kind != "note" or n.type not in ("person", "project"):
            continue
        names = [(x, re.I) for x in [n.title] + [a for a in (n.props.get("aliases") or []) if isinstance(a, str)]]
        if n.type == "person" and n.title:
            first = re.sub(r"\(example\)", "", n.title, flags=re.I).strip().split(" ")[0]
            if len(first) >= 3:
                names.append((first[:1].upper() + first[1:], 0))
        for name, mode in names:
            clean = re.sub(r"\s*\(example\)", "", name, flags=re.I).strip()
            if clean and re.search(r"(?<![\w-])" + re.escape(clean) + r"(?![\w-])", text, mode):
                (people if n.type == "person" else projects).append(nid); break
    return people, projects


def file_minutes(vault: Path, text: str, title: str, date: dt.date | None = None, project: str | None = None,
                 people: list[str] | None = None, kind: str = "project", g: kitgraph.Graph | None = None,
                 dry_run: bool = False, force: bool = False, daily: bool = False, actor: str = "process:kit-minutes",
                 extra: dict[str, list[str]] | None = None) -> Minutes:
    vault = Path(vault)
    g = g or kitgraph.build_graph(vault)
    date = date or dt.date.today()
    resolver = kitgraph.Resolver(g.nodes, kitgraph.load_ontology(vault), g.nodes[kitgraph.SELF_ID].props.get("actor"))
    m = Minutes(title=title, date=date, text=text)
    found_people, found_projects = detect_entities(text, g)
    if people:
        for p in people:
            nid, _method, _cands = resolver.resolve(p, ["person"])
            (m.people.append(nid) if nid else m.unresolved.append(p))
    else:
        m.people = found_people
    if project:
        nid, _method, _cands = resolver.resolve(project, ["project", "area"])
        if nid:
            m.project = nid
        else:
            m.unresolved.append(project)
    elif len(found_projects) == 1:
        m.project = found_projects[0]
    m.actions, m.decisions, m.outcomes = extract_minutes(text)
    if extra:
        for k in ("actions", "decisions", "outcomes"):
            for item in extra.get(k, []):
                lst = getattr(m, k)
                if _task_key(item) not in {_task_key(x) for x in lst}:
                    lst.append(item if k != "actions" or item.startswith("- [ ]") else _format_action(item, "", ""))
    rel = f"02-meetings/{date.isoformat()}-{kitlib.slugify(title)}.md"
    m.path = rel
    if dry_run:
        return m
    out = vault / rel
    if out.exists() and not force:
        raise FileExistsError(f"{rel} exists — pass force=True/--force to overwrite")
    tpl = vault / TEMPLATE_DIR / "meeting.md"
    tpl_text = kitlib.read_text(tpl) if tpl.exists() else None
    if tpl.exists() and tpl_text is None:
        m.skipped.append(f"{TEMPLATE_DIR}/meeting.md (not valid UTF-8; the note was written from the built-in shape)")
    body = kitlib.render_template(tpl_text, title, date) if tpl_text is not None else f"---\ntype: meeting\ntitle: {title}\n---\n# {title}\n"
    raw, rest = kitlib.split_frontmatter(body)
    import yaml
    fm = yaml.safe_load(raw) if raw else {}
    fm = fm or {}
    fm.update({
        "type": "meeting", "title": title, "date": date, "meeting_type": kind,
        "description": _describe(m),
        "people": [g.nodes[p].title for p in m.people],
        "project": f"[{g.nodes[m.project].title}]({_link(rel, m.project)})" if m.project else "",
        "state": "open",
        "generated": {"by": actor, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")},
    })
    rest = _fill_section(rest, "## Outcomes", m.outcomes)
    rest = _fill_section(rest, "## Decisions", m.decisions, hint_target=rel)
    rest = _fill_section(rest, "## Actions", m.actions, raw_lines=True)
    rest = _fill_recap(rest, text)
    fm_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=None).rstrip("\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"---\n{fm_text}\n---\n{rest}", encoding="utf-8", newline="\n")
    m.skipped += _log_entry(vault, f"Filed meeting note [{title}]({rel}) from a recap ({len(m.actions)} actions, {len(m.decisions)} decisions)", "Creation", date)
    if daily:
        dpath = kitlib.daily_note_path(vault, date)
        if dpath.exists() and kitlib.read_text(dpath) is None:
            # UTF-8 appended to a note we cannot decode is a second kind of damage, not a repair.
            m.skipped.append(f"{dpath.relative_to(vault).as_posix()} (not valid UTF-8; the daily-note line was not written)")
            return m
        if not dpath.exists():
            dtpl = vault / kitlib.obsidian_config(vault, "daily-notes.json").get("template", "90-templates/daily.md")
            dtpl_text = kitlib.read_text(dtpl) if dtpl.exists() else None
            if dtpl.exists() and dtpl_text is None:
                m.skipped.append(f"{dtpl.relative_to(vault).as_posix()} (not valid UTF-8; the daily note was created empty)")
            dpath.parent.mkdir(parents=True, exist_ok=True)
            dpath.write_text(kitlib.render_template(dtpl_text, date.isoformat(), date) if dtpl_text is not None else f"# {date}\n", encoding="utf-8", newline="\n")
            kitlib.set_frontmatter(dpath, {"description": f"Daily note for {date.isoformat()} (created while filing minutes)."})
        tail = dpath.read_bytes()[-1:]  # an Obsidian-edited note need not end in a newline
        with dpath.open("a", encoding="utf-8", newline="\n") as fh:
            if tail not in (b"", b"\n"):
                fh.write("\n")
            fh.write(f"- Meeting filed: [{title}]({_rel(dpath.relative_to(vault).as_posix(), rel)})\n")
    return m


def _describe(m: Minutes) -> str:
    first = next((s.strip() for s in re.split(r"(?<=[.!?])\s+", m.text.strip()) if len(s.strip()) > 20), "")
    first = first[:160].rstrip(".") if first else "Filed from a recap"
    return f"{first} ({len(m.actions)} actions, {len(m.decisions)} decisions)."


def _fill_section(body: str, heading: str, items: list[str], hint_target: str = "", raw_lines: bool = False) -> str:
    """Replace the lines under `heading` with `items`.

    `hint_target` is the vault path of the note being written; passing it keeps the template's
    parenthetical hint line below the items (the pointer at the decision template is guidance the
    filed note still needs), with its relative links retargeted so they resolve from there.
    """
    if not items:
        return body
    lines = body.splitlines()
    try:
        i = lines.index(heading)
    except ValueError:
        return body.rstrip("\n") + f"\n\n{heading}\n\n" + "\n".join(items if raw_lines else [f"- {x}" for x in items]) + "\n"
    j = i + 1
    while j < len(lines) and not lines[j].startswith("## "):
        j += 1
    hint = [_retarget_links(l, hint_target) for l in lines[i + 1:j] if hint_target and HINT_RE.match(l)][:1]
    new = [heading, ""] + (items if raw_lines else [f"- {x}" for x in items]) + hint + [""]
    lines[i:j] = new
    return "\n".join(lines) + ("\n" if body.endswith("\n") else "")


def _retarget_links(line: str, note_rel: str) -> str:
    """Rewrite a line taken from a template (its links are relative to `TEMPLATE_DIR`) for `note_rel`."""
    def repl(m: re.Match) -> str:
        target = m.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#", "obsidian://", "/")):
            return m.group(0)
        return "](" + _link(note_rel, posixpath.normpath(TEMPLATE_DIR + "/" + target)) + ")"
    return re.sub(r"\]\(([^)\s]+)\)", repl, line)


def _fill_recap(body: str, text: str) -> str:
    lines = body.splitlines()
    idx = next((i for i, l in enumerate(lines) if l.startswith("## Recap")), None)
    quoted = "\n".join(f"> {l}" if l.strip() else ">" for l in text.strip().splitlines())
    if idx is None:
        return body.rstrip("\n") + f"\n\n## Recap\n\n{quoted}\n"
    j = idx + 1
    while j < len(lines) and not lines[j].startswith("## ") and not lines[j].startswith("*Before closing"):
        j += 1
    lines[idx:j] = ["## Recap", "", quoted, ""]
    return "\n".join(lines) + ("\n" if body.endswith("\n") else "")
