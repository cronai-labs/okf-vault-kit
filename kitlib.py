"""kitlib — shared logic for the OKF Vault Kit.

Pure Python (3.11+) plus PyYAML. Used by `kit.py` (CLI) and by `tests/`.

Responsibilities:
  * frontmatter parsing for Obsidian/OKF markdown notes
  * OKF v0.2 conformance checks (spec: GoogleCloudPlatform/knowledge-catalog, okf/SPEC.md)
  * link and Bases-embed resolution inside a vault
  * deterministic `index.md` generation (OKF §8, progressive disclosure)
"""
from __future__ import annotations

import datetime
import importlib.metadata
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

OKF_VERSION = "0.2"


def _kit_version() -> str:
    """The VERSION file is the source of truth in a checkout; a wheel carries only its metadata.

    A flat layout has no package directory to install VERSION into, so from a wheel this returns
    the PEP 440-normalised form of the same number rather than the string in the file.
    """
    version_file = Path(__file__).resolve().parent / "VERSION"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip()
    try:
        return importlib.metadata.version("okf-vault-kit")
    except importlib.metadata.PackageNotFoundError:
        return "0+unknown"


KIT_VERSION = _kit_version()
RESERVED_FILENAMES = {"index.md", "log.md"}
RAW_ROOT_FILENAMES = {"Inbox.md"}   # raw capture at the bundle root: reserved by basename there, and only there
IGNORED_DIRS = {".obsidian", ".git", ".qmd", ".kit", "node_modules", ".trash"}
TEMPLATE_DIRS = {"90-templates"}

ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
ACTOR = re.compile(r"^(human:\S+|process:\S+|[^/\s]+/[^\s]+)$")
LIFECYCLE = {"draft", "stable", "deprecated"}
SUGGESTED_STATES = {"open", "active", "waiting", "on-hold", "done", "decided", "superseded"}  # suggestion only; not validated
LOG_HEADING = re.compile(r"^## \d{4}-\d{2}-\d{2}\s*$")

FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
EMPTY_FM_RE = re.compile(r"\A---[ \t]*\r?\n---[ \t]*\r?\n?")   # FM_RE needs a line between the fences
FM_KEY_RE = re.compile(r"^([A-Za-z_][\w.-]*)\s*:")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
MD_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
WIKILINK_RE = re.compile(r"(?<!\!)\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")
BASE_EMBED_RE = re.compile(r"!\[\[([^\]#|]+\.base)(?:#([^\]|]+))?(?:\|[^\]]*)?\]\]")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^[^\]]+\]:", re.MULTILINE)


# ---------------------------------------------------------------- stdio

def use_utf8_io(stdout: bool = True) -> None:
    """Force UTF-8 on stdin and stderr and, unless told otherwise, stdout.

    A Windows console defaults to a legacy code page, so the first em dash or arrow this
    CLI prints raises UnicodeEncodeError and kills the command half-way through its output,
    and a piped stdin (not a console, so PEP 528 does not cover it) decodes UTF-8 text with
    the ANSI code page, turning umlauts into mojibake before anything can validate them.
    Vault content is UTF-8 by contract; the terminal must not decide otherwise.

    `stdout=False` is for the MCP bridge in stdio mode, where stdout carries JSON-RPC frames
    owned by the SDK rather than text we are allowed to re-encode.
    """
    streams = (sys.stdin, sys.stdout, sys.stderr) if stdout else (sys.stdin, sys.stderr)
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- frontmatter

@dataclass
class Note:
    path: Path
    rel: str
    frontmatter: dict[str, Any] | None  # None = no frontmatter block
    body: str
    fm_error: str | None = None


def split_frontmatter(text: str) -> tuple[str | None, str]:
    m = FM_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def parse_note(path: Path, root: Path) -> Note:
    # utf-8-sig, not utf-8: FM_RE is anchored at the start of the text, so a byte-order mark
    # (PowerShell 5.1, legacy Notepad) would hide the frontmatter of an otherwise valid note.
    text = path.read_text(encoding="utf-8-sig")
    raw, body = split_frontmatter(text)
    rel = path.relative_to(root).as_posix()
    if raw is None:
        return Note(path, rel, None, body)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:  # pragma: no cover - exercised via tests with bad yaml
        return Note(path, rel, None, body, fm_error=f"YAML error: {exc}")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return Note(path, rel, None, body, fm_error="frontmatter is not a mapping")
    return Note(path, rel, data, body)


def is_private_dir(name: str) -> bool:
    """A directory the kit never reads: one it owns, or any `_`-prefixed name.

    `_`-prefixed folders are tool-private by convention in the Obsidian ecosystem, so nothing
    the kit surfaces — validation, links, index, graph, the MCP bridge — may look inside one.
    """
    return name in IGNORED_DIRS or name.startswith("_")


def is_raw_root(rel: str) -> bool:
    """A raw capture file at the bundle root: exempt from OKF checks and from `index.md`."""
    return rel in RAW_ROOT_FILENAMES


def _visible(rel: Path) -> bool:
    """False for a vault-relative path that sits inside a private directory."""
    return not any(is_private_dir(part) for part in rel.parts[:-1]) and rel.name not in IGNORED_DIRS


def iter_markdown(root: Path, include_templates: bool = True) -> Iterable[Path]:
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not is_private_dir(d))
        rel_dir = Path(dirpath).relative_to(root)
        if not include_templates and rel_dir.parts and rel_dir.parts[0] in TEMPLATE_DIRS:
            continue
        for name in sorted(filenames):
            if name.lower().endswith(".md"):
                yield Path(dirpath) / name


def load_vault(root: Path) -> list[Note]:
    """Every note we can decode. A file we cannot is skipped, not fatal.

    One note PowerShell 5.1 wrote (`Out-File` defaults to UTF-16) must not take out `todos`, the
    graph, `validate` and five MCP read tools for the whole vault — the bridge already skips such
    a file per call, and this is the same rule for every other reader. Skipping is only safe if
    somebody says so: `undecodable()` names what was dropped, the CLI warns and `validate`
    reports it as an error, so a skipped file is never silently invisible.
    """
    root = Path(root)
    notes: list[Note] = []
    for p in iter_markdown(root):
        # Only these two: a bad codec name or a YAML bug is ours to fix, not a file to skip past.
        try:
            notes.append(parse_note(p, root))
        except (UnicodeDecodeError, OSError):
            continue
    return notes


def undecodable(root: Path) -> list[str]:
    """Paths, relative to root, of the markdown files every reader skips.

    Makes the same read `parse_note` makes, without the YAML parse, so a caller can name the
    files `load_vault` dropped without paying for a second full load.
    """
    root = Path(root)
    out: list[str] = []
    for p in iter_markdown(root):
        try:
            p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            out.append(p.relative_to(root).as_posix())
        except OSError:
            continue                      # unreadable for another reason; not an encoding finding
    return out


# ---------------------------------------------------------------- OKF checks

@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"checked {self.checked} markdown files"]
        for e in self.errors:
            lines.append(f"ERROR  {e}")
        for w in self.warnings:
            lines.append(f"warn   {w}")
        lines.append("OK" if self.ok else f"FAILED with {len(self.errors)} error(s)")
        return "\n".join(lines)


def _is_iso(value: Any) -> bool:
    """ISO-8601 instant with an explicit offset — as a string, or as the tz-aware datetime YAML parses it into."""
    if isinstance(value, datetime.datetime):
        return value.tzinfo is not None
    return isinstance(value, str) and bool(ISO_DATETIME.match(value))


def _check_actor_event(prefix: str, value: Any, rep: Report, rel: str, is_template: bool) -> None:
    if not isinstance(value, dict):
        rep.errors.append(f"{rel}: {prefix} must be a mapping with `by` (and optional `at`)")
        return
    by = value.get("by")
    if not isinstance(by, str) or not ACTOR.match(by):
        rep.errors.append(f"{rel}: {prefix}.by must follow the actor convention (human:<id>, process:<id>, <producer>/<version>); got {by!r}")
    at = value.get("at")
    if at is not None and not is_template and not _is_iso(at):
        rep.errors.append(f"{rel}: {prefix}.at must be ISO-8601 with an explicit offset (e.g. 2026-09-14T08:00:00+02:00); got {at!r}")


def check_reserved(note: Note, rep: Report) -> None:
    name = note.path.name
    if name == "index.md":
        if note.frontmatter is not None:
            extra = set(note.frontmatter) - {"okf_version"}
            if extra or note.rel != "index.md":
                rep.errors.append(f"{note.rel}: index.md may only carry `okf_version` in frontmatter, and only at the bundle root")
    elif name == "log.md":
        headings = [l for l in note.body.splitlines() if l.startswith("## ")]
        bad = [h for h in headings if not LOG_HEADING.match(h)]
        if bad:
            rep.errors.append(f"{note.rel}: log.md date headings must be `## YYYY-MM-DD`; found {bad[:2]}")
        if note.frontmatter is not None:
            rep.errors.append(f"{note.rel}: log.md must not carry frontmatter")


def check_concept(note: Note, rep: Report) -> None:
    rel = note.rel
    is_template = rel.split("/")[0] in TEMPLATE_DIRS
    if note.fm_error:
        rep.errors.append(f"{rel}: {note.fm_error}")
        return
    fm = note.frontmatter
    if fm is None:
        rep.errors.append(f"{rel}: missing YAML frontmatter (OKF §11 requires it on every non-reserved .md)")
        return
    t = fm.get("type")
    if not isinstance(t, str) or not t.strip():
        rep.errors.append(f"{rel}: `type` is required and must be a non-empty string")
    desc = fm.get("description")
    if not is_template and (not isinstance(desc, str) or not desc.strip()):
        rep.warnings.append(f"{rel}: no `description` — views and AI snippets will be empty for this note")
    if "status" in fm and fm["status"] not in LIFECYCLE and fm["status"] is not None:
        rep.errors.append(f"{rel}: `status` is the OKF lifecycle field and must be one of {sorted(LIFECYCLE)}; use `state` for workflow (got {fm['status']!r})")
    if "generated" in fm and fm["generated"] is not None:
        _check_actor_event("generated", fm["generated"], rep, rel, is_template)
    if "verified" in fm and fm["verified"] is not None:
        events = fm["verified"] if isinstance(fm["verified"], list) else [fm["verified"]]
        for ev in events:
            _check_actor_event("verified[]", ev, rep, rel, is_template)
    sa = fm.get("stale_after")
    if sa not in (None, "") and not is_template and not _is_iso(sa):
        rep.errors.append(f"{rel}: `stale_after` must be an ISO-8601 instant with offset; got {sa!r}")
    src = fm.get("sources")
    if src not in (None, []):
        if not isinstance(src, list):
            rep.errors.append(f"{rel}: `sources` must be a list")
        else:
            for i, s in enumerate(src):
                if not isinstance(s, dict) or "resource" not in s:
                    rep.errors.append(f"{rel}: sources[{i}] needs a `resource`")
                elif "last_modified" in s and not _is_iso(s["last_modified"]):
                    rep.errors.append(f"{rel}: sources[{i}].last_modified must be ISO-8601 with offset")
    if "timestamp" in fm:
        rep.warnings.append(f"{rel}: `timestamp` is the OKF v0.1 field — v0.2 uses `generated.at`")


def validate_okf(root: Path) -> Report:
    rep = Report()
    for rel in undecodable(Path(root)):
        rep.errors.append(f"{rel}: not valid UTF-8 — every tool skips it, so the note is invisible to "
                          "search, the graph and todos; re-save it as UTF-8")
    for note in load_vault(Path(root)):
        rep.checked += 1
        if note.path.name in RESERVED_FILENAMES:
            check_reserved(note, rep)
        elif not is_raw_root(note.rel):
            check_concept(note, rep)
    return rep


# ---------------------------------------------------------------- links

def _strip_code(text: str) -> str:
    text = CODE_FENCE_RE.sub("", text)
    return CODE_SPAN_RE.sub("", text)


def _base_views(base_path: Path) -> list[str]:
    data = yaml.safe_load(base_path.read_text(encoding="utf-8-sig")) or {}
    return [v.get("name", "") for v in data.get("views", [])]


def check_links(root: Path) -> Report:
    root = Path(root)
    rep = Report()
    all_files = [p for p in root.rglob("*") if p.is_file() and _visible(p.relative_to(root))]
    by_stem: dict[str, list[Path]] = {}
    by_name: dict[str, list[Path]] = {}
    for p in all_files:
        # lowercased keys: Obsidian resolves wikilinks case-insensitively, and so does kitgraph's Resolver
        by_stem.setdefault(p.stem.lower(), []).append(p)
        by_name.setdefault(p.name.lower(), []).append(p)
    base_views: dict[str, list[str]] = {}
    for p in all_files:
        if p.suffix == ".base":
            try:
                base_views[p.name] = _base_views(p)
            except yaml.YAMLError as exc:
                rep.errors.append(f"{p.relative_to(root).as_posix()}: invalid Bases YAML: {exc}")
            except (UnicodeDecodeError, OSError) as exc:
                rep.errors.append(f"{p.relative_to(root).as_posix()}: cannot be read as UTF-8: {exc}")
    for path in iter_markdown(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = _strip_code(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError):
            continue          # validate_okf already reports it; a second finding would only repeat
        rep.checked += 1
        try:
            # utf-8-sig so a BOM'd note still parses; skip what cannot be decoded at all.
            text = _strip_code(path.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, OSError):
            continue          # validate_okf already reports it; a second finding would only repeat
        rep.checked += 1
        for m in list(MD_LINK_RE.finditer(text)) + list(MD_IMAGE_RE.finditer(text)):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#", "obsidian://")):
                continue
            target = urllib.parse.unquote(target.split("#", 1)[0])
            if not target:
                continue
            if target.startswith("/"):
                resolved = root / target.lstrip("/")
            else:
                resolved = (path.parent / target).resolve()
            if not resolved.exists():
                rep.errors.append(f"{rel}: broken link -> {target}")
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip()
            stem = Path(target).name.lower()
            stem = stem[:-3] if stem.endswith(".md") else stem
            if stem not in by_stem and stem not in by_name:
                rep.errors.append(f"{rel}: broken wikilink [[{target}]]")
        for m in BASE_EMBED_RE.finditer(text):
            base_name = Path(m.group(1).strip()).name
            view = m.group(2)
            if base_name not in base_views:
                rep.errors.append(f"{rel}: embedded base not found: {base_name}")
            elif view and view.strip() not in base_views[base_name]:
                rep.errors.append(f"{rel}: view '{view.strip()}' not in {base_name} (has {base_views[base_name]})")
    return rep


# ---------------------------------------------------------------- index.md

def build_index(root: Path, title: str = "Vault index") -> str:
    """Return the OKF root index.md content (deterministic, sorted)."""
    root = Path(root)
    sections: dict[str, list[tuple[str, str, str]]] = {}
    for path in iter_markdown(root):
        rel = path.relative_to(root)
        if path.name in RESERVED_FILENAMES or is_raw_root(rel.as_posix()):
            continue
        try:
            note = parse_note(path, root)
        except (UnicodeDecodeError, OSError):
            continue          # an index cannot describe a file no reader can decode
        fm = note.frontmatter or {}
        section = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        t = fm.get("title") if isinstance(fm.get("title"), str) else path.stem
        d = fm.get("description") if isinstance(fm.get("description"), str) else ""
        sections.setdefault(section, []).append((t, rel.as_posix(), d.strip()))
    lines = ["---", f'okf_version: "{OKF_VERSION}"', "---", "", f"# {title}", "",
             "Generated by `kit.py index`. One entry per concept, grouped by directory, with each note's `description`.", ""]
    for section in sorted(sections):
        lines.append(f"# {section}")
        lines.append("")
        for t, rel, d in sorted(sections[section], key=lambda x: x[1]):
            entry = f"* [{t}]({rel})"
            if d:
                entry += f" - {d}"
            lines.append(entry)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_index(root: Path) -> Path:
    root = Path(root)
    out = root / "index.md"
    out.write_text(build_index(root), encoding="utf-8", newline="\n")
    return out


# ---------------------------------------------------------------- helpers

def obsidian_config(root: Path, name: str) -> dict[str, Any]:
    p = Path(root) / ".obsidian" / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def daily_note_path(root: Path, day) -> Path:
    """Compute today's daily-note path from .obsidian/daily-notes.json (moment-style tokens)."""
    cfg = obsidian_config(root, "daily-notes.json")
    fmt = cfg.get("format", "YYYY-MM-DD")
    folder = cfg.get("folder", "")
    out = []
    i = 0
    while i < len(fmt):
        if fmt[i] == "[":
            j = fmt.index("]", i)
            out.append(fmt[i + 1:j]); i = j + 1; continue
        for token, val in (("YYYY", f"{day.year:04d}"), ("MM", f"{day.month:02d}"), ("DD", f"{day.day:02d}")):
            if fmt.startswith(token, i):
                out.append(val); i += len(token); break
        else:
            out.append(fmt[i]); i += 1
    rel = "".join(out) + ".md"
    return Path(root) / folder / rel if folder else Path(root) / rel


# ---------------------------------------------------------------- templates, frontmatter edits, slugs

def render_template(text: str, title: str, day: datetime.date | None = None) -> str:
    """Minimal core-Templates emulation: {{title}}, {{date}}, {{time}}, {{date:FORMAT}} (moment-style tokens)."""
    now = datetime.datetime.now().astimezone()
    if day:
        now = now.replace(year=day.year, month=day.month, day=day.day)
    tokens = {
        "YYYY": f"{now.year:04d}", "MM": f"{now.month:02d}", "DD": f"{now.day:02d}", "D": str(now.day),
        "HH": f"{now.hour:02d}", "mm": f"{now.minute:02d}", "ss": f"{now.second:02d}",
        "dddd": now.strftime("%A"), "MMMM": now.strftime("%B"), "WW": f"{now.isocalendar()[1]:02d}",
        "Q": str((now.month - 1) // 3 + 1), "Z": now.strftime("%z")[:3] + ":" + now.strftime("%z")[3:],
    }

    def fmt(spec: str) -> str:
        out, i = [], 0
        while i < len(spec):
            if spec[i] == "[":
                j = spec.find("]", i)
                out.append(spec[i + 1:j]); i = j + 1; continue
            for tok in sorted(tokens, key=len, reverse=True):
                if spec.startswith(tok, i):
                    out.append(tokens[tok]); i += len(tok); break
            else:
                out.append(spec[i]); i += 1
        return "".join(out)

    text = text.replace("{{title}}", title)
    text = re.sub(r"\{\{date:([^}]+)\}\}", lambda m: fmt(m.group(1)), text)
    return text.replace("{{date}}", fmt("YYYY-MM-DD")).replace("{{time}}", fmt("HH:mm"))


def _rewrite_fm_lines(raw: str, values: dict[str, Any]) -> str | None:
    """Rewrite whole `key: value` lines in a raw frontmatter block, or None when that is not safe.

    PyYAML's data model carries no comments, quoting or scalar styles, so a key the caller never
    named must not pass through it. Keys that are one plain line are edited in place and everything
    else is returned untouched; a shape this cannot express (nested or multi-line value, a key that
    is absent) is refused so the caller can fall back to re-serialising the block.
    """
    lines = raw.split("\n")
    tops: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#") or line[:1] in (" ", "\t"):
            continue                       # blank, comment, or a continuation of the key above
        m = FM_KEY_RE.match(line)
        if not m:
            return None                    # not a plain `key:` line; only the full dump knows this shape
        tops.append((m.group(1), i))
    span = {key: (i, tops[n + 1][1] if n + 1 < len(tops) else len(lines)) for n, (key, i) in enumerate(tops)}
    out, appended = list(lines), []
    for key, value in values.items():
        one = yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip("\n")
        if "\n" in one:
            return None                    # a value PyYAML lays out over several lines
        if key not in span:
            appended.append(one); continue
        i, end = span[key]
        rest = lines[i].split(":", 1)[1].strip()
        if rest[:1] in ("|", ">") or any(l.strip() and not l.lstrip().startswith("#") for l in lines[i + 1:end]):
            return None
        out[i] = one + ("\r" if lines[i].endswith("\r") else "")
    return "\n".join(out + appended)


def set_frontmatter(path: Path, values: dict[str, Any]) -> None:
    """Set one or more top-level frontmatter keys, preserving the body and every untouched key.

    Untouched keys keep the text their author wrote — comments, quoting and block scalars included
    — whenever the named keys can be rewritten line by line; the line of a key that *is* set is
    replaced whole, its trailing comment with it. Only a block this cannot edit line by line is
    re-serialised as a whole, and then in block style, never collapsed onto one flow line.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    raw, body = split_frontmatter(text)
    if raw is None and EMPTY_FM_RE.match(text):
        raw, body = "", EMPTY_FM_RE.sub("", text)   # '---\n---': a block with no keys, not body text
    fm = _rewrite_fm_lines(raw, values) if raw else None
    if fm is None:
        data = yaml.safe_load(raw) if raw else {}
        data = data or {}
        data.update(values)
        fm = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip("\n")
    path.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8", newline="\n")


def slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"\(example\)", "", text, flags=re.I).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:max_len].rstrip("-") or "note"


def append_log(root: Path, message: str, kind: str = "Update", today: datetime.date | None = None) -> Path:
    """Append an entry to the OKF log.md under today's heading (newest first)."""
    root = Path(root)
    log = root / "log.md"
    today = (today or datetime.date.today()).isoformat()
    entry = f"* **{kind}**: {message}"
    if not log.exists():
        log.write_text(f"# Vault Update Log\n\n## {today}\n{entry}\n", encoding="utf-8", newline="\n")
        return log
    lines = log.read_text(encoding="utf-8").splitlines()
    heading = f"## {today}"
    if heading in lines:
        lines.insert(lines.index(heading) + 1, entry)
    else:
        insert_at = 1
        if lines and lines[0].startswith("# "):
            insert_at = 2 if len(lines) > 1 and lines[1].strip() == "" else 1
        # the log is newest first, and a back-dated entry (filing last week's minutes) belongs at
        # its date, not on top: go in above the first older heading, or after every existing one.
        headings = [i for i, l in enumerate(lines) if LOG_HEADING.match(l)]
        older = next((i for i in headings if lines[i][3:].strip() < today), None)
        if older is not None:
            insert_at = older
        elif headings:
            insert_at = len(lines)
        block = [heading, entry, ""]
        if insert_at == len(lines) and lines and lines[-1].strip():
            block.insert(0, "")
        lines[insert_at:insert_at] = block
    log.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    return log
