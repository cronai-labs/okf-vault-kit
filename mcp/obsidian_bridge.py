#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0", "mcp>=2,<3"]
# ///
"""obsidian_bridge — an MCP server that lets a local model read and write an Obsidian vault.

Two interchangeable backends:

  fs   Direct file access. Headless, works in CI, needs nothing but the vault path.
  cli  The official Obsidian CLI (Obsidian 1.12+, `obsidian <command> key=value`).
       Index-aware and template-aware, but the Obsidian app must be running.

Run as an MCP server (stdio). With uv nothing needs installing (deps come from the inline metadata above);
with plain Python, `pip install 'mcp>=2,<3' pyyaml` first (this bridge targets the 2.x server API):

  uv run mcp/obsidian_bridge.py --backend fs --vault ~/Notes/vault
  python mcp/obsidian_bridge.py --backend fs --vault ~/Notes/vault
  python mcp/obsidian_bridge.py --backend cli --vault-name "vault"
  uv run mcp/obsidian_bridge.py --backend fs --vault ~/Notes/vault --http --port 8765   # as a service: http://127.0.0.1:8765/mcp

Self-test without MCP:

  python mcp/obsidian_bridge.py --backend fs --vault ../vault --selftest

Access control (see docs/security.md): --read-only, --deny-write/--allow-write folder prefixes, --show-confidential
(hidden by default), --max-write-bytes, --max-writes-per-minute, --propose (writes become proposals for a human to
apply), --audit PATH / --no-audit (JSONL log of every tool call), --token TOKEN for --http (bearer auth).

Tool surface (identical for both backends), 14 tools:

  read   obsidian_search, obsidian_read_note, obsidian_list_files, obsidian_backlinks,
         graph_context, graph_neighbors, graph_path, graph_query, vault_todos
  write  obsidian_create_note, obsidian_append_note, obsidian_daily_append,
         obsidian_set_property, file_meeting_minutes   (not registered under --read-only)
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_policy  # mcp/bridge_policy.py
import kitgraph
import kitlib
import kitrecon

STOPWORDS = {"the", "and", "for", "with", "what", "why", "how", "who", "which", "when", "where", "did", "does", "was", "were", "are", "our", "this", "that", "from", "into", "about", "der", "die", "das", "und", "ist", "wie", "wer", "mit", "von", "für", "auf", "ein", "eine", "nicht", "wird"}

class BridgeError(RuntimeError):
    pass


WIN_INVALID = '<>:"|?*'
WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _check_name(name: str) -> None:
    """Refuse note names a Windows vault cannot hold — on every platform, so vaults stay portable.

    A model invents names like `03-projects/Plan: Q4`. On Windows the colon opens an alternate data
    stream (the text lands in a file nobody sees), `NUL.md` is still the device, and `? " < > | *`
    raise a bare OSError out of the tool. A vault written on macOS with such a name cannot be
    synced to Windows at all.
    """
    for seg in name.strip().replace("\\", "/").split("/"):
        stem = seg.removesuffix(".md")
        if not stem or stem in (".", ".."):
            continue
        bad = sorted({c for c in stem if c in WIN_INVALID or ord(c) < 32})
        if bad:
            raise BridgeError(f"note name contains characters a vault cannot hold: {' '.join(bad)} (in {seg!r})")
        if stem != stem.rstrip(". "):
            raise BridgeError(f"note name segment {seg!r} ends in a dot or a space, which Windows silently strips")
        if stem.split(".")[0].lower() in WIN_RESERVED:
            raise BridgeError(f"note name {seg!r} is a reserved device name on Windows; pick another")


# ---------------------------------------------------------------- fs backend

class FsBackend:
    """Operate on the vault directly. Safe defaults: never overwrite silently, stay inside the vault."""

    def __init__(self, vault: Path):
        self.vault = Path(vault).expanduser().resolve()
        if not self.vault.is_dir():
            raise BridgeError(f"vault not found: {self.vault}")

    # -- helpers
    def _resolve(self, file: str, must_exist: bool = True) -> Path:
        name = file.strip()
        if not name.endswith(".md"):
            name += ".md"
        cand = (self.vault / name).resolve()
        if self.vault not in cand.parents and cand != self.vault:
            raise BridgeError("path escapes the vault")
        if cand.exists():
            return cand
        # fall back to basename match anywhere in the vault (Obsidian-style short names)
        stem = Path(name).name
        matches = [p for p in kitlib.iter_markdown(self.vault) if p.name == stem]
        if len(matches) == 1:
            return matches[0]
        if must_exist:
            hint = ", ".join(m.relative_to(self.vault).as_posix() for m in matches[:5]) or "no similar file"
            raise BridgeError(f"note not found: {file} ({hint}). Use obsidian_search or obsidian_list_files to find the path.")
        return cand

    def _resolve_template(self, template: str) -> Path:
        """A template is a name in 90-templates, never a path to somewhere else.

        `Path(a) / b` with an absolute b discards a, and `..` walks out of the folder — so an
        unchecked join here reads any .md file on the machine into a note the model can then read.
        """
        name = template.strip().replace("\\", "/")
        rel = name if name.endswith(".md") else name + ".md"
        root = (self.vault / "90-templates").resolve()
        tpl = (root / rel).resolve()
        if root not in tpl.parents:
            raise BridgeError(f"template must be a name inside 90-templates: {template}")
        if not tpl.is_file():
            raise BridgeError(f"template not found: {tpl.name}")
        return tpl

    def _notes(self) -> list[Any]:
        """Every note we can actually read.

        One file the bridge cannot decode — UTF-16 from PowerShell's `Out-File`, a stray binary —
        must not take out search for the whole vault. Skipping it is also the safe direction: an
        unreadable note stays invisible rather than half-parsed.
        """
        out = []
        for p in kitlib.iter_markdown(self.vault):
            try:
                out.append(kitlib.parse_note(p, self.vault))
            except (UnicodeDecodeError, OSError):
                continue
        return out

    def _text(self, path: Path) -> str:
        """The note's text, or a BridgeError naming it.

        A note the kit cannot decode is not one to read out, and not one to append UTF-8 to or
        rewrite the frontmatter of — that would leave a second encoding inside the same file.
        The model gets the bridge's own error instead of a UnicodeDecodeError traceback.
        """
        text = kitlib.read_text(path)
        if text is None:
            rel = path.relative_to(self.vault).as_posix() if self.vault in path.parents else path.name
            raise BridgeError(f"{rel} is not valid UTF-8, so no tool can read it — ask the user to re-save it as UTF-8")
        return text

    # -- read side
    def read_note(self, file: str) -> str:
        return self._text(self._resolve(file))

    def list_files(self, folder: str = "") -> list[str]:
        base = self.vault / folder if folder else self.vault
        if self.vault not in base.resolve().parents and base.resolve() != self.vault:
            raise BridgeError("folder escapes the vault")
        return [p.relative_to(self.vault).as_posix() for p in kitlib.iter_markdown(base if base.exists() else self.vault)
                if p.relative_to(self.vault).as_posix().startswith(folder)]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 1 and t.lower() not in STOPWORDS]
        if not terms:
            return []
        hits = []
        for note in self._notes():
            if note.rel.startswith("90-templates") or note.path.name in kitlib.RESERVED_FILENAMES:
                continue
            hay = (note.body + "\n" + json.dumps(note.frontmatter or {}, default=str)).lower()
            title = str((note.frontmatter or {}).get("title", note.path.stem)).lower()
            phrase = " ".join(terms)
            score = sum(hay.count(t) for t in terms) + 5 * hay.count(phrase) + (10 if phrase in title else 0)
            if score:
                idx = min((hay.find(t) for t in terms if t in hay), default=0)
                snippet = re.sub(r"\s+", " ", hay[max(0, idx - 80): idx + 160]).strip()
                hits.append({"file": note.rel, "title": (note.frontmatter or {}).get("title", note.path.stem), "score": score, "snippet": snippet})
        hits.sort(key=lambda h: -h["score"])
        return hits[:limit]

    def backlinks(self, file: str) -> list[str]:
        target = self._resolve(file)
        stem = target.stem
        rel = target.relative_to(self.vault).as_posix()
        out = []
        for p in kitlib.iter_markdown(self.vault):
            if p == target:
                continue
            text = kitlib.read_text(p)
            if text is None:
                continue                      # unreadable note: skip it, do not fail the whole call
            if f"[[{stem}" in text or rel in text or f"{stem}.md" in text:
                out.append(p.relative_to(self.vault).as_posix())
        return out

    # -- write side
    def create_note(self, name: str, content: str = "", template: str | None = None, overwrite: bool = False) -> str:
        _check_name(name)
        path = self._resolve(name, must_exist=False)
        if path.exists() and not overwrite:
            raise BridgeError(f"{path.relative_to(self.vault).as_posix()} exists — pass overwrite=true or use obsidian_append_note")
        body = content
        if template:
            tpl = self._resolve_template(template)
            body = _render_template(self._text(tpl), path.stem) + ("\n" + content if content else "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8", newline="\n")
        self._graph_cache = None
        return path.relative_to(self.vault).as_posix()

    def append_note(self, file: str, content: str) -> str:
        path = self._resolve(file)
        text = self._text(path)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(("" if text.endswith("\n") else "\n") + content.rstrip("\n") + "\n")
        return path.relative_to(self.vault).as_posix()

    def daily_append(self, content: str, day: dt.date | None = None) -> str:
        day = day or dt.date.today()
        path = kitlib.daily_note_path(self.vault, day)
        if not path.exists():
            cfg = kitlib.obsidian_config(self.vault, "daily-notes.json")
            tpl = self.vault / cfg.get("template", "90-templates/daily.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            tpl_text = kitlib.read_text(tpl) if tpl.exists() else None
            text = _render_template(tpl_text, day.isoformat(), day) if tpl_text is not None else f"# {day.isoformat()}\n"
            path.write_text(text, encoding="utf-8", newline="\n")
            kitlib.set_frontmatter(path, {"description": f"Daily note for {day.isoformat()} (created by the vault bridge)."})
        return self.append_note(path.relative_to(self.vault).as_posix(), content)

    def set_property(self, file: str, name: str, value: Any) -> str:
        path = self._resolve(file)
        self._text(path)                      # set_frontmatter rewrites the whole note; it has to read it first
        kitlib.set_frontmatter(path, {name: _coerce(value)})
        self._graph_cache = None
        return path.relative_to(self.vault).as_posix()

    # -- what no reader can see
    _skipped_cache: tuple[tuple[int, float], list[str]] | None = None

    def skipped_files(self) -> list[str]:
        """Vault files every reader skips because they are not valid UTF-8.

        Cached against the note set (how many, and the newest mtime): naming them costs a read of
        every note, and the read tools ask on each call.
        """
        stamps = [p.stat().st_mtime for p in kitlib.iter_markdown(self.vault)]
        sig = (len(stamps), max(stamps, default=0.0))
        if self._skipped_cache and self._skipped_cache[0] == sig:
            return self._skipped_cache[1]
        out = kitlib.undecodable(self.vault)
        self._skipped_cache = (sig, out)
        return out

    # -- graph side (rebuilt when any note changes)
    _graph_cache: tuple[float, kitgraph.Graph] | None = None

    def graph(self) -> kitgraph.Graph:
        latest = max((p.stat().st_mtime for p in kitlib.iter_markdown(self.vault)), default=0.0)
        if self._graph_cache and self._graph_cache[0] >= latest:
            return self._graph_cache[1]
        g = kitgraph.build_graph(self.vault)
        self._graph_cache = (latest, g)
        return g

    def graph_neighbors(self, node: str, depth: int = 1, preds: list[str] | None = None) -> list[dict[str, Any]]:
        g = self.graph()
        if not kitgraph.resolve_id(g, node):
            raise BridgeError(f"no note matches {node!r}; use obsidian_search to find its path")
        return [{"src": s, "pred": p, "dst": d, "depth": dd} for s, p, d, dd in kitgraph.neighbors(g, node, depth, preds)]

    def graph_path(self, a: str, b: str) -> list[dict[str, str]]:
        return [{"src": s, "pred": p, "dst": d} for s, p, d in kitgraph.shortest_path(self.graph(), a, b)]

    def graph_query(self, sql: str, limit: int = 100) -> dict[str, Any]:
        con = kitgraph.to_sqlite(self.graph(), ":memory:")
        try:
            cols, rows = kitgraph.query_sqlite(con, sql, limit)
        finally:
            con.close()
        return {"columns": cols, "rows": [list(r) for r in rows]}

    def graph_context(self, node: str, depth: int = 1) -> str:
        return kitgraph.context_pack(self.graph(), node, depth)

    def todos(self, owner: str = "", overdue_only: bool = False) -> dict[str, Any]:
        rep = kitrecon.todo_report(self.vault)
        tasks = rep.overdue if overdue_only else rep.open
        if owner:
            tasks = [t for t in tasks if t.owner.lower() == owner.lower()]
        return {"open": len(rep.open), "overdue": len(rep.overdue), "due_soon": len(rep.due_soon),
                "conflicts": [[t.__dict__ for t in grp] for grp in rep.done_conflicts],
                "tasks": [t.__dict__ for t in tasks]}

    def file_minutes(self, text: str, title: str, date: str = "", project: str = "", people: str = "", kind: str = "project",
                     daily: bool = True) -> dict[str, Any]:
        day = dt.date.fromisoformat(date) if date else dt.date.today()
        ppl = [p.strip() for p in people.split(",") if p.strip()] or None
        title = _one_line(title)
        try:
            m = kitrecon.file_minutes(self.vault, text, title, day, project or None, ppl, kind, g=self.graph(), daily=daily)
        except FileExistsError as exc:
            raise BridgeError(str(exc)) from exc
        self._graph_cache = None
        # also_written names what the vault actually took: the log entry is skipped when log.md
        # cannot be decoded, and a result that claims it anyway is the lie this tool used to tell.
        also = [] if any(x.startswith("log.md") for x in m.skipped) else ["log.md"]
        return {"path": m.path, "people": m.people, "project": m.project, "unresolved": m.unresolved,
                "actions": m.actions, "decisions": m.decisions, "outcomes": m.outcomes,
                "also_written": also, "skipped": list(m.skipped)}


def _one_line(text: str) -> str:
    """Collapse a model-supplied title to one line.

    file_minutes interpolates the title into the log.md provenance entry, which is one line per
    entry: a newline in it forges entries no tool wrote.
    """
    return re.sub(r"\s+", " ", str(text)).strip()


def _coerce(value: Any) -> Any:
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "false"):
            return low == "true"
        if re.fullmatch(r"-?\d+", low):
            return int(low)
        if value.startswith("[") and value.endswith("]"):
            return [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
    return value



_render_template = kitlib.render_template


# ---------------------------------------------------------------- cli backend

class CliBackend:
    """Shell out to the official Obsidian CLI. Argument grammar: `obsidian <command> key=value flag`."""

    def __init__(self, vault_name: str | None = None, binary: str = "obsidian", vault_path: str | None = None):
        self.vault_name = vault_name
        self.binary = shutil.which(binary) or binary
        self._fs = FsBackend(Path(vault_path)) if vault_path else None

    def _need_fs(self) -> FsBackend:
        if not self._fs:
            raise BridgeError("graph, todo and minutes tools need the vault path: start the bridge with --vault <path> as well")
        return self._fs

    def graph(self): return self._need_fs().graph()
    def graph_neighbors(self, node, depth=1, preds=None): return self._need_fs().graph_neighbors(node, depth, preds)
    def graph_path(self, a, b): return self._need_fs().graph_path(a, b)
    def graph_query(self, sql, limit=100): return self._need_fs().graph_query(sql, limit)
    def graph_context(self, node, depth=1): return self._need_fs().graph_context(node, depth)
    def todos(self, owner="", overdue_only=False): return self._need_fs().todos(owner, overdue_only)
    def skipped_files(self): return self._fs.skipped_files() if self._fs else []
    def file_minutes(self, text, title, date="", project="", people="", kind="project", daily=True): return self._need_fs().file_minutes(text, title, date, project, people, kind, daily)

    def _resolve(self, file: str, must_exist: bool = True) -> Path:
        """Map a reference to a vault path so the policy layer can check it (needs --vault).

        The CLI resolves short names against Obsidian's index; the policy layer has to resolve the
        same reference or it checks a path that does not exist and concludes there is nothing to
        hide. Without --vault this raises, and the policy layer fails closed.
        """
        return self._need_fs()._resolve(file, must_exist)

    def _run(self, command: str, params: dict[str, Any] | None = None, flags: list[str] | None = None) -> str:
        cmd = [self.binary]
        if self.vault_name:
            cmd.append(f"vault={self.vault_name}")
        cmd.append(command)
        for k, v in (params or {}).items():
            if v is None or v == "":
                continue
            cmd.append(f"{k}={v}")
        cmd += flags or []
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False)
        except FileNotFoundError as exc:
            raise BridgeError("official Obsidian CLI not on PATH — Obsidian 1.12+, Settings → General → Command line interface → Register") from exc
        if r.returncode != 0:
            raise BridgeError(f"obsidian {command} failed: {(r.stderr or r.stdout).strip()}")
        return r.stdout

    def read_note(self, file: str) -> str:
        return self._run("read", {"file": _noext(file)})

    def list_files(self, folder: str = "") -> list[str]:
        out = self._run("files")
        files = [l.strip() for l in out.splitlines() if l.strip()]
        return [f for f in files if f.startswith(folder)] if folder else files

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        out = self._run("search", {"query": query, "limit": limit})
        return [{"file": l.strip(), "title": Path(l.strip()).stem, "score": None, "snippet": ""} for l in out.splitlines() if l.strip()]

    def backlinks(self, file: str) -> list[str]:
        return [l.strip() for l in self._run("backlinks", {"file": _noext(file)}).splitlines() if l.strip()]

    def create_note(self, name: str, content: str = "", template: str | None = None, overwrite: bool = False) -> str:
        _check_name(name)
        params = {"name": _noext(name), "content": content}
        if template:
            params["template"] = _noext(template)
        self._run("create", params, ["silent"] + (["overwrite"] if overwrite else []))
        return _noext(name) + ".md"

    def append_note(self, file: str, content: str) -> str:
        self._run("append", {"file": _noext(file), "content": content})
        return _noext(file) + ".md"

    def daily_append(self, content: str, day: dt.date | None = None) -> str:
        self._run("daily:append", {"content": content})
        return "daily"

    def set_property(self, file: str, name: str, value: Any) -> str:
        self._run("property:set", {"name": name, "value": value, "file": _noext(file)})
        return _noext(file) + ".md"


def _noext(name: str) -> str:
    return name.removesuffix(".md")


# ---------------------------------------------------------------- MCP server

def _sdk_hint(exc: ImportError) -> str:
    """Tell the two failures apart: no SDK at all, versus an SDK we cannot drive.

    The v1 SDK has no `mcp.server.mcpserver` — the server class was called FastMCP and lived
    elsewhere — so the import raises ModuleNotFoundError, an ImportError subclass. Reporting that
    as "the SDK is missing" sends people off to install something they already had.
    """
    try:
        import importlib.metadata as md
        version = md.version("mcp")
    except Exception:  # noqa: BLE001 — no SDK at all
        return ("the MCP python SDK is missing: pip install 'mcp>=2,<3'   (or run with --selftest)")
    return (f"the installed MCP python SDK ({version}) is not supported by this bridge: {exc}\n"
            f"this code targets the 2.x API; install a compatible one with:  pip install 'mcp>=2,<3'\n"
            f"or with uv, which reads the pin from the script header:       uv run {Path(__file__).name} ...")


def _announced(backend, payload: str) -> str:
    """A whole-vault result, plus a line saying how much of the vault it could not see.

    The CLI warns the user in their own terminal; a model has no terminal, so a tool that just
    listed "everything" has to say when everything was short. Without it the model reads a
    complete answer and tells the user the note does not exist.

    The count, not the paths: an undecodable note is hidden from the model exactly because we
    cannot read its frontmatter to see whether it is confidential, and that includes its name.
    `kit.py validate` names them for the user.
    """
    skipped = backend.skipped_files() if hasattr(backend, "skipped_files") else []
    if not skipped:
        return payload
    return (payload + f"\n\nnote: {len(skipped)} file(s) in this vault are not valid UTF-8 and are invisible to "
            "every tool, so this result is incomplete — tell the user to run `kit.py validate` to see which, "
            "and to re-save them as UTF-8.")


# What the backend raises to say "that call was wrong, or not allowed" — as opposed to crashing.
# PolicyError and BridgeError are the bridge's own refusals; ValueError and sqlite3.Error are how
# `graph_query` refuses a query, and it is the one tool whose argument the model writes freehand.
REFUSALS = (BridgeError, bridge_policy.PolicyError, ValueError, sqlite3.Error)


class _Refusals:
    """The backend as the tools see it, with every refusal raised as the SDK's `ToolError`.

    A refusal is an answer: "marked confidential", "writes to `05-people` are denied by policy",
    "only SELECT / WITH queries are allowed" are written for the model to read and act on. The
    SDK forwards a `ToolError`'s message to the model and withholds every other exception's, so
    a refusal has to arrive as one — and a crash still does not, which is what keeps a
    traceback's absolute paths out of the model's context.
    """

    def __init__(self, backend, tool_error):
        self._backend, self._tool_error = backend, tool_error

    def __getattr__(self, name: str):
        attr = getattr(self._backend, name)
        if not callable(attr):
            return attr

        @functools.wraps(attr)
        def call(*args, **kwargs):
            try:
                return attr(*args, **kwargs)
            except REFUSALS as exc:
                raise self._tool_error(str(exc)) from exc

        return call


def build_server(backend, read_only: bool = False):
    # The bind address is not the server's business in 2.x: it belongs to the transport, so both
    # --http paths below pass it at run time instead.
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    backend = _Refusals(backend, ToolError)
    mcp = MCPServer("obsidian-vault", version=kitlib.KIT_VERSION, instructions=(
        "Read and write the user's Obsidian vault. Notes are Markdown with YAML frontmatter (OKF). "
        "Search first, then read specific notes by path. Prefer append over overwrite. "
        "Tasks are written as `- [ ] verb — owner, due YYYY-MM-DD`."))

    @mcp.tool(name="obsidian_search")
    def obsidian_search(query: str, limit: int = 10) -> str:
        """Keyword search across the vault. Returns matching note paths with a short snippet. Use before reading."""
        return _announced(backend, json.dumps(backend.search(query, limit), ensure_ascii=False, indent=1))

    @mcp.tool(name="obsidian_read_note")
    def obsidian_read_note(file: str) -> str:
        """Return the full text of one note. `file` is a vault-relative path (with or without .md) or a unique note name."""
        return backend.read_note(file)

    @mcp.tool(name="obsidian_list_files")
    def obsidian_list_files(folder: str = "") -> str:
        """List markdown files, optionally under a folder prefix such as `03-projects`."""
        return _announced(backend, "\n".join(backend.list_files(folder)))

    @mcp.tool(name="obsidian_backlinks")
    def obsidian_backlinks(file: str) -> str:
        """Notes that link to the given note — the dossier view for a project, person or decision."""
        return "\n".join(backend.backlinks(file))

    @mcp.tool(name="graph_context")
    def graph_context(note: str, depth: int = 1) -> str:
        """Structured facts about a note: type, state/health/status, derived trust/staleness, and its relations (owner, project, people, decisions, citations). Use for who/what/when questions instead of guessing from prose."""
        return backend.graph_context(note, depth)

    @mcp.tool(name="graph_neighbors")
    def graph_neighbors(note: str, depth: int = 1, predicates: str = "") -> str:
        """Edges around a note as JSON rows {src, pred, dst, depth}. `predicates` is a comma-separated filter, e.g. `owns,involved_in` or `links_to,cites`."""
        preds = [p.strip() for p in predicates.split(",") if p.strip()] or None
        return json.dumps(backend.graph_neighbors(note, depth, preds), ensure_ascii=False)

    @mcp.tool(name="graph_path")
    def graph_path(a: str, b: str) -> str:
        """Shortest chain of relations between two notes (provenance edges skipped). Empty when unconnected."""
        return json.dumps(backend.graph_path(a, b), ensure_ascii=False)

    @mcp.tool(name="graph_query")
    def graph_query(sql: str, limit: int = 100) -> str:
        """Read-only SQL over the vault graph: tables nodes(id,kind,type,title,description,path,props), edges(src,pred,dst,origin), props(node,key,value), derived(node,key,value); views v_trust, v_stale, v_at_risk. Recursive CTEs work."""
        return json.dumps(backend.graph_query(sql, limit), ensure_ascii=False)

    @mcp.tool(name="vault_todos")
    def vault_todos(owner: str = "", overdue_only: bool = False) -> str:
        """Every open task in the vault with file, line, owner and due date, plus done/open conflicts across notes. Filter by owner or overdue."""
        return _announced(backend, json.dumps(backend.todos(owner, overdue_only), ensure_ascii=False))

    if read_only:
        return mcp

    @mcp.tool(name="obsidian_create_note")
    def obsidian_create_note(name: str, content: str = "", template: str = "", overwrite: bool = False) -> str:
        """Create a note at a vault-relative path (e.g. `02-meetings/2026-09-14-sync`). Optional template name from 90-templates (e.g. `meeting`). Fails if the note exists unless overwrite=true."""
        return backend.create_note(name, content, template or None, overwrite)

    @mcp.tool(name="obsidian_append_note")
    def obsidian_append_note(file: str, content: str) -> str:
        """Append text to the end of an existing note. Safe, non-destructive."""
        return backend.append_note(file, content)

    @mcp.tool(name="obsidian_daily_append")
    def obsidian_daily_append(content: str) -> str:
        """Append a line to today's daily note (created from the daily template if missing)."""
        return backend.daily_append(content)

    @mcp.tool(name="obsidian_set_property")
    def obsidian_set_property(file: str, name: str, value: str) -> str:
        """Set one frontmatter property on a note, e.g. state=done, health=green, description=..."""
        return backend.set_property(file, name, value)

    @mcp.tool(name="file_meeting_minutes")
    def file_meeting_minutes(text: str, title: str, date: str = "", project: str = "", people: str = "", kind: str = "project") -> str:
        """File a pasted recap/transcript as a meeting note in 02-meetings: people and project resolved against the vault, actions (`Action:`, `TODO:`, `- [ ]`) and decisions (`Decision:`, `Agreed:`) extracted, recap kept verbatim, entry added to log.md and today's daily note. `people` is comma-separated names; date defaults to today."""
        return json.dumps(backend.file_minutes(text, title, date, project, people, kind), ensure_ascii=False)

    return mcp


def make_backend(args):
    if args.backend == "fs":
        if not args.vault:
            sys.exit("--vault is required for the fs backend")
        return FsBackend(Path(args.vault))
    return CliBackend(args.vault_name, vault_path=args.vault)


def selftest(backend) -> int:
    print("files:", len(backend.list_files()))
    hits = backend.search("hybrid search", 3)
    print("search:", [h["file"] for h in hits])
    if hits:
        print("read:", backend.read_note(hits[0]["file"])[:120].replace("\n", " "), "...")
        print("graph:", len(backend.graph_neighbors(hits[0]["file"], 1)), "edges around it")
    print("todos:", backend.todos()["open"], "open")
    print("selftest OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["fs", "cli"], default="fs")
    ap.add_argument("--vault", help="vault path (fs backend)")
    ap.add_argument("--vault-name", help="Obsidian vault name (cli backend)")
    ap.add_argument("--selftest", action="store_true", help="exercise the backend without MCP")
    ap.add_argument("--http", action="store_true", help="serve MCP over streamable HTTP (for headless/service use) instead of stdio")
    ap.add_argument("--host", default="127.0.0.1", help="bind address for --http; keep it local unless you pass --token (off the loopback, whoever reaches it edits the vault)")
    ap.add_argument("--port", type=int, default=8765, help="port for --http; clients use http://HOST:PORT/mcp")
    ap.add_argument("--token", default=os.environ.get("KIT_BRIDGE_TOKEN", ""),
                    help="bearer token required on --http; prefer KIT_BRIDGE_TOKEN or `--token new` — a token in argv is visible to every user on the machine (ps)")
    pol = ap.add_argument_group("access control")
    pol.add_argument("--read-only", action="store_true", help="register only read tools")
    pol.add_argument("--deny-write", help="comma-separated folder prefixes model-chosen writes may never touch (default: 05-people,99-system,90-templates,09-archive,.obsidian,.kit,index.md,log.md,context.jsonld); "
                                          "file_meeting_minutes still appends its one-line provenance entry to log.md, which is how a vault records that a tool wrote something")
    pol.add_argument("--allow-write", help="comma-separated folder prefixes that are the only writable ones")
    pol.add_argument("--show-confidential", action="store_true", help="expose notes with sensitivity: confidential (hidden by default)")
    pol.add_argument("--max-write-bytes", type=int, default=20_000); pol.add_argument("--max-writes-per-minute", type=int, default=20)
    pol.add_argument("--allow-overwrite", action="store_true", help="let the model pass overwrite=true on obsidian_create_note (denied by default: it blanks an existing note)")
    pol.add_argument("--propose", action="store_true", help="writes become proposals in .kit/proposals/ (kit.py proposals apply)")
    pol.add_argument("--audit", help="JSONL audit log path (default <vault>/.kit/bridge-audit.jsonl)"); pol.add_argument("--no-audit", action="store_true")
    args = ap.parse_args(argv)
    kitlib.use_utf8_io(stdout=args.selftest or args.http)  # stdio transport owns stdout
    raw = make_backend(args)
    vault = Path(args.vault).expanduser().resolve() if args.vault else None
    policy = bridge_policy.Policy.from_args(args, vault)
    if policy.hide_confidential and vault is None:
        # Without the vault we cannot read any note's frontmatter, so "confidential hidden" would
        # be a banner over a control that is not running. Say so instead of serving the notes.
        sys.exit("confidential hiding needs the vault: add --vault <path> (or pass --show-confidential to run without it)")
    backend = bridge_policy.Guarded(raw, policy, vault)
    if args.selftest:
        print("policy:", policy.describe())
        return selftest(backend)
    try:
        server = build_server(backend, read_only=policy.read_only)
    except ImportError as exc:
        sys.exit(_sdk_hint(exc))
    print(f"policy: {policy.describe()}", file=sys.stderr)
    if args.http:
        token = bridge_policy.new_token() if args.token == "new" else args.token
        if token:
            import uvicorn
            app = bridge_policy.BearerAuth(server.streamable_http_app(host=args.host), token)
            print(f"obsidian-vault MCP bridge on http://{args.host}:{args.port}/mcp — bearer token required"
                  + (f": {token}" if args.token == "new" else ""), file=sys.stderr)
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        else:
            if args.host not in ("127.0.0.1", "localhost", "::1"):
                print("WARNING: HTTP bridge without --token on a non-loopback address — anyone who can reach it can edit the vault", file=sys.stderr)
            print(f"obsidian-vault MCP bridge on http://{args.host}:{args.port}/mcp (streamable HTTP, no auth)", file=sys.stderr)
            server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        server.run()  # stdio transport
    return 0


if __name__ == "__main__":
    sys.exit(main())
