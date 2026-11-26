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
import hashlib
import importlib.metadata
import json
import os
import posixpath
import re
import stat
import sys
import unicodedata
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

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
PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")   # core Templates: {{title}}, {{date:YYYY-MM}}, ...

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
# Comments are not rendered, so nothing inside one is a link or a citation: HTML, and Obsidian's own.
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
OBSIDIAN_COMMENT_RE = re.compile(r"%%.*?%%", re.DOTALL)
MD_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
WIKILINK_RE = re.compile(r"(?<!\!)\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")
BASE_EMBED_RE = re.compile(r"!\[\[([^\]#|]+\.base)(?:#([^\]|]+))?(?:\|[^\]]*)?\]\]")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^[^\]]+\]:", re.MULTILINE)
DAY_NOTE_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")   # a day note is named for its date, nothing else


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


def read_text(path: Path) -> str | None:
    """The file's text, or None when the kit cannot read it.

    The rule `load_vault` follows, spelled once for every reader that does not go through it:
    a file we cannot decode is skipped, never fatal. Returning None instead of raising is the
    point — each caller has to say what "skipped" means there (no template, no ontology, a log
    entry that does not get appended), and none of them may leave that decision to a traceback.

    utf-8-sig for the same reason `parse_note` uses it: a byte-order mark is a legacy editor's
    doing, not a decoding failure.
    """
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        return None


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


def template_folder(root: Path) -> tuple[str | None, str | None]:
    """(folder, error) for core Templates' `folder` setting in .obsidian/templates.json.

    No file, or no folder set: (None, None). Obsidian has no template folder then, and neither
    has the kit. A setting we cannot use: (None, error). Callers exempt nothing then, and
    `check_links` reports the error, so a broken config can never widen an exemption.
    """
    name = ".obsidian/templates.json"
    p = Path(root) / name
    if not p.exists():
        return None, None
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{name}: cannot be read as JSON: {exc}"
    if not isinstance(cfg, dict):
        return None, f"{name}: expected a JSON object, found {type(cfg).__name__}"
    folder = cfg.get("folder")
    if folder in (None, ""):
        return None, None
    if not isinstance(folder, str):
        return None, f"{name}: `folder` must be a string, found {folder!r}"
    rel = PurePosixPath(folder)
    if rel.is_absolute() or Path(folder).is_absolute() or ".." in rel.parts:
        return None, f"{name}: `folder` must be a path inside the vault, found {folder!r}"
    if not (Path(root) / rel).is_dir():
        return None, f"{name}: `folder` {folder!r} is not a folder in this vault"
    return rel.as_posix(), None


def in_template_dir(rel: str, tdir: str | None) -> bool:
    """True for a vault-relative POSIX path inside the template folder `tdir` (None: no folder)."""
    return tdir is not None and (rel == tdir or rel.startswith(tdir + "/"))


def iter_markdown(root: Path, include_templates: bool = True) -> Iterable[Path]:
    root = Path(root)
    tdir = None if include_templates else template_folder(root)[0]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not is_private_dir(d))
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir.parts and in_template_dir(rel_dir.as_posix(), tdir):
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


def check_concept(note: Note, rep: Report, tdir: str | None) -> None:
    rel = note.rel
    is_template = in_template_dir(rel, tdir)
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
    tdir = template_folder(Path(root))[0]   # a broken config is check_links' finding
    daily = daily_notes_folder(Path(root))
    for note in load_vault(Path(root)):
        rep.checked += 1
        if note.path.name in RESERVED_FILENAMES:
            check_reserved(note, rep)
        elif not is_raw_root(note.rel):
            check_concept(note, rep, tdir)
            check_day_note_placement(note, rep, Path(root), daily)
    return rep


def check_day_note_placement(note: Note, rep: Report, root: Path, daily: str) -> None:
    """Warn when a day note sits outside the folder `.obsidian/daily-notes.json` files it in.

    A journal that keeps every day in one folder floods it within a year; the shipped format nests
    days by year and month. Notes made with `Ctrl/Cmd + D` land in the right place, so what this
    catches is a note filed or moved by hand, by a sync tool, or by an agent. A kit rule, not an
    OKF one: a warning, which only `validate --strict` turns into a failure.
    """
    m = DAY_NOTE_NAME_RE.match(note.path.name)
    if not m or not daily:
        return                    # at the vault root a bare date cannot be told apart from any other dated note
    if not note.rel.startswith(f"{daily}/"):
        return
    try:
        day = datetime.date.fromisoformat(m.group(1))
    except ValueError:
        return
    expected = daily_note_path(root, day).relative_to(root).as_posix()
    if note.rel != expected:
        rep.warnings.append(f"{note.rel}: day note outside its year/month folder — the daily-notes config "
                            f"files it at {expected}; move it there so one folder does not collect every day")


# ---------------------------------------------------------------- links

def _strip_unrendered(text: str) -> str:
    """Drop what a reader never sees as a link: code first (a `<!--` in code opens nothing), then comments."""
    text = CODE_FENCE_RE.sub("", text)
    text = CODE_SPAN_RE.sub("", text)
    text = HTML_COMMENT_RE.sub("", text)
    return OBSIDIAN_COMMENT_RE.sub("", text)


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
    tdir, config_error = template_folder(root)
    if config_error:
        rep.errors.append(config_error)
    for path in iter_markdown(root):
        rel = path.relative_to(root).as_posix()
        raw_text = read_text(path)
        if raw_text is None:
            continue          # validate_okf already reports it; a second finding would only repeat
        text = _strip_unrendered(raw_text)
        rep.checked += 1
        # a placeholder target is filled in when a note is created from the template; any other
        # link in a template must still resolve
        is_template = in_template_dir(rel, tdir)
        for m in list(MD_LINK_RE.finditer(text)) + list(MD_IMAGE_RE.finditer(text)):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#", "obsidian://")):
                continue
            if is_template and PLACEHOLDER_RE.search(target):
                continue
            target = urllib.parse.unquote(target.split("#", 1)[0])
            if not target:
                continue
            resolved = root / target.lstrip("/") if target.startswith("/") else (path.parent / target).resolve()
            try:
                resolved.stat()
            except (FileNotFoundError, NotADirectoryError):
                rep.errors.append(f"{rel}: broken link -> {target}")
            except OSError as exc:
                # A folder we may not read (permissions, an agent's sandbox) is not a broken link, and
                # before Python 3.14 Path.exists() raised here and took the whole validate run down.
                rep.warnings.append(f"{rel}: link target cannot be checked ({exc.__class__.__name__}) -> {target}")
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip()
            if is_template and PLACEHOLDER_RE.search(target):
                continue
            stem = Path(target).name.lower()
            stem = stem.removesuffix(".md")
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
    """Return the OKF root index.md content (deterministic, sorted).

    Confidential notes are left out entirely and only counted (#52). index.md is an ordinary
    file that agents are told to read first and the bridge serves whole, so a title, path or
    description listed here would undo the bridge's hiding of the note itself. The decision is
    `note_confidential`'s, so a note whose frontmatter cannot be parsed is left out as well.
    A file no reader can open or decode is counted with them: the count tells a reader the index
    is incomplete, and one that skipped such files silently said less than the truth.
    """
    root = Path(root)
    sections: dict[str, list[tuple[str, str, str]]] = {}
    hidden = 0
    for path in iter_markdown(root):
        rel = path.relative_to(root)
        if path.name in RESERVED_FILENAMES or is_raw_root(rel.as_posix()):
            continue
        try:
            note = parse_note(path, root)
        except (UnicodeDecodeError, OSError):
            hidden += 1       # an index cannot describe a file no reader can decode, but it can count it
            continue
        if note_confidential_in(root, note):
            hidden += 1
            continue
        fm = note.frontmatter or {}
        section = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        t = fm.get("title") if isinstance(fm.get("title"), str) else path.stem
        d = fm.get("description") if isinstance(fm.get("description"), str) else ""
        sections.setdefault(section, []).append((t, rel.as_posix(), d.strip()))
    lines = ["---", f'okf_version: "{OKF_VERSION}"', "---", "", f"# {title}", "",
             "Generated by `kit.py index`. One entry per concept, grouped by directory, with each note's `description`.", ""]
    if hidden:
        # The count only: it tells a reader the index is deliberately incomplete, and names nothing.
        lines += [f"{hidden} file(s) not listed: confidential, or unreadable.", ""]
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


def daily_notes_folder(root: Path) -> str:
    """The vault-relative folder that holds day notes: the configured folder plus the format's literal leading
    directories (`01-journal` + `[daily]/YYYY/...` -> `01-journal/daily`). Empty when that is the vault root."""
    cfg = obsidian_config(root, "daily-notes.json")
    parts = [p for p in str(cfg.get("folder", "")).split("/") if p]
    for segment in str(cfg.get("format", "YYYY-MM-DD")).split("/")[:-1]:
        if not (segment.startswith("[") and segment.endswith("]") and segment.count("[") == 1):
            break
        parts.append(segment[1:-1])
    return "/".join(parts)


def month_note_path(root: Path, day) -> Path:
    """The month note for `day`: `<journal folder>/monthly/YYYY-MM.md`.

    The journal folder is the daily-notes `folder`, so day and month notes move together when a
    user re-points it. One folder per period type, like `weekly/` and `quarterly/`.
    """
    folder = obsidian_config(root, "daily-notes.json").get("folder", "")
    rel = Path("monthly") / f"{day.year:04d}-{day.month:02d}.md"
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
    # utf-8-sig for the same reason parse_note uses it: split_frontmatter is anchored at the start
    # of the text, so a byte-order mark hides the existing block and this would prepend a second
    # one, orphaning the real type/title into the body. The mark is not written back.
    text = path.read_text(encoding="utf-8-sig")
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


def unparsed_frontmatter(body: str) -> bool:
    """True when a note kept its `---` block but parse_note handed back no frontmatter.

    A UTF-8 BOM (PowerShell 5.1, legacy Notepad) defeats the `\\A---` match, so the note reads as
    "no sensitivity set". The block is there; we simply could not read it — which is not the same
    as "not confidential". Lives here because the bridge and `llm ask` must answer identically:
    they diverged once, and the CLI failed open on exactly this input.
    """
    return body.lstrip("\ufeff \t\r\n").startswith("---")


CONFIDENTIAL = "confidential"


def note_confidentiality(note: Note) -> bool | None:
    """What this parsed note's frontmatter says: True (confidential), False (not), None (could not tell).

    None is frontmatter we could not parse (a YAML error, a block a BOM hid). Readers treat it as
    True; only a caller that destroys something on a True answer needs to tell the two apart.
    """
    if note.frontmatter is None:
        return None if (note.fm_error or unparsed_frontmatter(note.body)) else False
    return str(note.frontmatter.get("sensitivity", "")).strip().lower() == CONFIDENTIAL


def note_confidential(note: Note) -> bool:
    """Is this parsed note confidential? The one predicate every reader and writer of the kit shares.

    The bridge, `llm ask`, `index.md`, the hub tables and the todo digest used to decide this in
    three places, and the generated files did not decide it at all: they copied a confidential
    note's title, description and tasks into ordinary files the bridge then served whole (#52).
    Fails closed like the bridge: frontmatter we could not parse (a YAML error, a block a BOM
    hid) counts as confidential, because "I could not tell" is not "it is not confidential".
    """
    return note_confidentiality(note) is not False


# ---------------------------------------------------------------- confidentiality floors (#120)

CONFIG_REL = ".kit/config.yml"          # the vault's kit config; kitproviders reads the same file, leniently
CONFIDENTIALITY_KEYS = ("confidential_folders", "confidential_paths")


class PolicyConfigError(ValueError):
    """The vault's `.kit/config.yml` cannot say what is confidential: unreadable, unparsable, or a
    `confidentiality:` block of the wrong shape. Readers then treat every path as confidential."""


def _match_form(rel: str) -> str:
    """One spelling for matching: POSIX separators, normalised, NFC, case-folded, no `./` or edge `/`."""
    rel = unicodedata.normalize("NFC", rel.replace("\\", "/"))
    rel = posixpath.normpath(rel) if rel else rel
    return rel.strip("/").casefold() if rel != "." else ""


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """A vault-relative glob as a regex: `*` and `?` stay within one segment, `**` crosses them.

    Written out, not fnmatch (whose `*` crosses `/`) and not `glob.translate` (Python 3.13+).
    """
    out: list[str] = []
    segs = pattern.split("/")
    for i, seg in enumerate(segs):
        last = i == len(segs) - 1
        if seg == "**":
            out.append(".*" if last else "(?:.*/)?")
            continue
        out.append("".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in seg))
        if not last:
            out.append("/")
    return re.compile("".join(out), re.DOTALL)


@dataclass(frozen=True)
class ConfidentialityPolicy:
    """What `.kit/config.yml` declares confidential regardless of any note's own frontmatter.

    `folders` are vault-relative folders: the folder and everything below it. `paths` are
    vault-relative globs for single files of any type. Both are stored in match form.
    """
    folders: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    _regexes: tuple[re.Pattern[str], ...] = field(default=(), compare=False, repr=False)

    @classmethod
    def build(cls, folders: Iterable[str] = (), paths: Iterable[str] = ()) -> ConfidentialityPolicy:
        f = tuple(_match_form(x) for x in folders)
        p = tuple(_match_form(x) for x in paths)
        return cls(f, p, tuple(_glob_regex(x) for x in p))

    def covers(self, rel: str) -> bool:
        """Does a floor or a path rule make this vault-relative reference confidential? (`.md` optional)"""
        if not (self.folders or self.paths):
            return False
        forms = {_match_form(rel)}
        forms |= {f + ".md" for f in forms if not f.endswith(".md")}
        for form in forms:
            if form.startswith("../") or form == "..":
                continue
            if any(form == d or form.startswith(d + "/") for d in self.folders):
                return True
            if any(rx.fullmatch(form) for rx in self._regexes):
                return True
        return False


_GLOB_CHARS = set("*?[]")


def _policy_entries(block: dict, key: str) -> list[str]:
    value = block.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise PolicyConfigError(f"{CONFIG_REL}: confidentiality.{key} must be a list of strings (quote each entry)")
    out = []
    for raw in value:
        entry = raw.strip().replace("\\", "/")
        parts = [x for x in entry.split("/") if x not in ("", ".")]
        if not parts:
            raise PolicyConfigError(f"{CONFIG_REL}: confidentiality.{key} has an empty entry {raw!r}")
        if entry.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", entry):
            raise PolicyConfigError(f"{CONFIG_REL}: confidentiality.{key} entry {raw!r} must be relative to the vault")
        if ".." in parts:
            raise PolicyConfigError(f"{CONFIG_REL}: confidentiality.{key} entry {raw!r} must not use `..`")
        if key == "confidential_folders" and _GLOB_CHARS.intersection(entry):
            raise PolicyConfigError(f"{CONFIG_REL}: confidentiality.confidential_folders entry {raw!r} is a pattern; "
                                    "folders are literal, put patterns under confidential_paths")
        out.append("/".join(parts))
    return out


def parse_confidentiality(data: Any) -> ConfidentialityPolicy:
    """The `confidentiality:` block of a parsed config, strictly. Missing block: no floors.

    Strict where kitproviders is lenient: a block someone half-edited or misspelt must not read as
    "nothing is confidential". A null block and an unknown key are errors, not an empty policy.
    """
    if data is None:
        return ConfidentialityPolicy()
    if not isinstance(data, dict):
        raise PolicyConfigError(f"{CONFIG_REL}: the file must be a mapping of keys")
    if "confidentiality" not in data:
        return ConfidentialityPolicy()
    block = data["confidentiality"]
    if not isinstance(block, dict):
        raise PolicyConfigError(f"{CONFIG_REL}: `confidentiality:` must be a mapping with "
                                f"{' and/or '.join(CONFIDENTIALITY_KEYS)}")
    unknown = sorted(str(k) for k in block if k not in CONFIDENTIALITY_KEYS)
    if unknown:
        raise PolicyConfigError(f"{CONFIG_REL}: unknown key(s) under confidentiality: {', '.join(unknown)} "
                                f"(known: {', '.join(CONFIDENTIALITY_KEYS)})")
    return ConfidentialityPolicy.build(_policy_entries(block, "confidential_folders"),
                                       _policy_entries(block, "confidential_paths"))


_POLICY_CACHE: dict[str, tuple[Any, ConfidentialityPolicy | PolicyConfigError]] = {}


def confidentiality_policy(root: Path) -> ConfidentialityPolicy:
    """The vault's declared floors and path rules; raises PolicyConfigError when they cannot be known.

    A missing `.kit/config.yml` (or a `.kit` that is a file) means no floors. Anything else that
    stops the file being read and parsed is an error, never an empty policy: "could not tell" is
    not "nothing is confidential". Cached on the resolved root and the file's mtime_ns and size,
    because every predicate call asks.
    """
    root = Path(root).resolve()
    path = root / CONFIG_REL
    try:
        st = path.stat()
        stamp: Any = (st.st_mtime_ns, st.st_size, st.st_mode)
    except (FileNotFoundError, NotADirectoryError):
        stamp = None
    except OSError as exc:
        raise PolicyConfigError(f"{CONFIG_REL}: cannot be read ({exc.__class__.__name__})") from exc
    cached = _POLICY_CACHE.get(str(root))
    if cached and cached[0] == stamp:
        result = cached[1]
    else:
        result = _load_policy(path, stamp)
        _POLICY_CACHE[str(root)] = (stamp, result)
    if isinstance(result, PolicyConfigError):
        raise result
    return result


def _load_policy(path: Path, stamp: Any) -> ConfidentialityPolicy | PolicyConfigError:
    if stamp is None:
        return ConfidentialityPolicy()
    if not stat.S_ISREG(stamp[2]):
        return PolicyConfigError(f"{CONFIG_REL}: is not a regular file")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return PolicyConfigError(f"{CONFIG_REL}: cannot be read as UTF-8 ({exc.__class__.__name__})")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return PolicyConfigError(f"{CONFIG_REL}: not valid YAML ({str(exc).splitlines()[0]})")
    try:
        return parse_confidentiality(data)
    except PolicyConfigError as exc:
        return exc


def config_fingerprint(root: Path) -> int:
    """A number that changes whenever `.kit/config.yml` appears, goes, or changes; never raises.

    For the graph's note-set signature: declaring a floor changes what a redacted graph holds, so
    a graph built before it must read as stale. From the bytes, not the parsed policy, so a broken
    file has a fingerprint too, and fixing it changes the number.
    """
    try:
        data = (Path(root) / CONFIG_REL).read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return -1
    except OSError:
        return -2         # there but unreadable: fixed, so a database on disk compares equal across runs
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")


def confidential_floor(root: Path, rel: str) -> bool | None:
    """Does the vault's config alone make this reference confidential? None when the config is broken."""
    try:
        return confidentiality_policy(root).covers(rel)
    except PolicyConfigError:
        return None


def own_confidentiality(root: Path, rel: str) -> bool | None:
    """What the referenced note's own frontmatter says, floors aside (`.md` optional).

    True, False, or None for "could not tell" (unreadable, undecodable, unstatable, unparsable).
    A reference to no file is False. A reference to an existing file that is not Markdown has no
    frontmatter to raise it, so it is False too. Only a caller that must not act on a floor (one
    that writes the note's own `sensitivity`) asks this; readers ask `confidentiality`.
    """
    root = Path(root)
    is_md = rel.lower().endswith(".md")
    try:
        if not is_md:
            try:
                st = (root / rel).stat()
            except (FileNotFoundError, NotADirectoryError):
                st = None
            if st is not None and stat.S_ISREG(st.st_mode):
                return False      # a CSV, an image, a PDF: no frontmatter, the floor alone decides
        path = root / (rel if is_md else rel + ".md")
        if not stat.S_ISREG(path.stat().st_mode):
            return False
        note = parse_note(path, root)
    except (FileNotFoundError, NotADirectoryError):
        return False      # no such file, also when a parent of the path is a file
    except Exception:  # noqa: BLE001 — unreadable or undecodable: we cannot prove it is safe
        return None
    return note_confidentiality(note)


def confidentiality(root: Path, rel: str) -> bool | None:
    """Is this vault-relative reference confidential? True, False, or None for "could not tell".

    The floor first: a `.kit/config.yml` that cannot be read or parsed is None for every path, and
    a folder or path rule it declares is True whatever the note says (a note may raise its level,
    never lower it). Then the note's own frontmatter (`own_confidentiality`).

    A reference to no file is False: there is nothing to hide, and callers that must treat an
    unresolvable reference as the worst case (the bridge) check that before calling. A file we
    cannot read or decode is None. So is one we cannot even stat: under a chmod-000 folder,
    `Path.is_file()` raises PermissionError up to Python 3.13 and returns False on 3.14, a
    traceback on one and "not confidential" on the other, so the stat is made here and judged.
    """
    floor = confidential_floor(root, rel)
    if floor is not False:
        return floor
    return own_confidentiality(root, rel)


def is_confidential(root: Path, rel: str) -> bool:
    """`confidentiality` as a reader needs it: anything but a proven "no" hides the reference."""
    return confidentiality(root, rel) is not False


def note_confidential_in(root: Path, note: Note) -> bool:
    """`note_confidential` for a note parsed from `root`, with the vault's floors applied."""
    return confidential_floor(root, note.rel) is not False or note_confidential(note)


def check_confidentiality(root: Path) -> Report:
    """`validate`'s view of the floors: a broken config, and notes that claim less than their floor.

    A note inside a floor whose own `sensitivity` says something else stays confidential; the
    warning is there so the label in the note does not mislead the person reading it.
    """
    root = Path(root)
    rep = Report()
    try:
        policy = confidentiality_policy(root)
    except PolicyConfigError as exc:
        rep.errors.append(f"{exc} — until this is fixed every reader treats every note as confidential")
        return rep
    if not (policy.folders or policy.paths):
        return rep
    for note in load_vault(root):
        rep.checked += 1
        if not policy.covers(note.rel) or not note.frontmatter:
            continue
        own = note.frontmatter.get("sensitivity")
        if own is None or str(own).strip() == "":
            continue
        if str(own).strip().lower() != CONFIDENTIAL:
            rep.warnings.append(f"{note.rel}: `sensitivity: {own}` inside a folder or path `{CONFIG_REL}` declares "
                                "confidential; the note stays confidential (a note may raise its level, never lower it)")
    return rep


def slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"\(example\)", "", text, flags=re.I).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:max_len].rstrip("-") or "note"


def append_log(root: Path, message: str, kind: str = "Update", today: datetime.date | None = None) -> Path | None:
    """Append an entry to the OKF log.md under today's heading (newest first).

    Returns None when log.md is there but cannot be decoded, having written nothing. Callers must
    report that: this is the last step of tools that have already created a note, and raising here
    left the vault changed while the tool reported failure. Rewriting a log we cannot read is the
    one thing worse than skipping the entry — it would throw away the user's history.
    """
    root = Path(root)
    log = root / "log.md"
    today = (today or datetime.date.today()).isoformat()
    entry = f"* **{kind}**: {message}"
    if not log.exists():
        log.write_text(f"# Vault Update Log\n\n## {today}\n{entry}\n", encoding="utf-8", newline="\n")
        return log
    text = read_text(log)
    if text is None:
        return None
    lines = text.splitlines()
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
