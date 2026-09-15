"""bridge_policy — access control for the vault MCP bridge.

The model is untrusted input (notes it reads can carry instructions). This module puts a policy layer between
the MCP tools and the backend:

  * read-only mode          write tools are not even registered
  * write allow/deny lists  vault-relative folder prefixes, checked against the path the backend resolves to
                            (case-insensitively); people notes and system folders are denied by default
  * confidential hiding     notes with `sensitivity: confidential` are invisible to reads, searches, listings and every
                            graph tool, and agents may not write them or set the `sensitivity` property
  * size and rate limits    per write call and per minute
  * propose mode            writes are recorded as proposals under .kit/proposals/ and applied by a human
                            (`kit.py proposals list|apply|reject`)
  * audit log               two JSON lines per write in .kit/bridge-audit.jsonl (attempt, then outcome) and one per read;
                            a write whose attempt line cannot be written does not happen
  * bearer token            for the HTTP transport (ASGI middleware, no SDK dependency in the check itself)

Standard library + PyYAML (through kitlib).
"""
from __future__ import annotations

import datetime as dt
import json
import posixpath
import re
import secrets
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import kitgraph
import kitlib

DEFAULT_DENY_WRITE = ("05-people", "99-system", "90-templates", "09-archive", ".obsidian", ".kit", "index.md", "log.md", "context.jsonld")
READ_TOOLS = ("read_note", "list_files", "search", "backlinks", "graph_neighbors", "graph_path", "graph_query", "graph_context", "todos")
WRITE_TOOLS = ("create_note", "append_note", "daily_append", "set_property", "file_minutes")


class PolicyError(RuntimeError):
    pass


@dataclass
class Policy:
    read_only: bool = False
    deny_write: tuple[str, ...] = DEFAULT_DENY_WRITE
    allow_write: tuple[str, ...] = ()          # when set, only these prefixes are writable (deny still applies)
    hide_confidential: bool = True
    max_write_bytes: int = 20_000
    max_writes_per_minute: int = 20
    propose: bool = False
    audit_path: Path | None = None             # None = no audit log

    @staticmethod
    def from_args(args, vault: Path | None) -> "Policy":
        audit = None
        if not getattr(args, "no_audit", False) and vault is not None:
            audit = Path(args.audit) if getattr(args, "audit", None) else vault / ".kit" / "bridge-audit.jsonl"
        deny = tuple(p.strip("/ ") for p in (args.deny_write.split(",") if getattr(args, "deny_write", None) else DEFAULT_DENY_WRITE) if p.strip("/ "))
        allow = tuple(p.strip("/ ") for p in (args.allow_write.split(",") if getattr(args, "allow_write", None) else ()) if p.strip("/ "))
        return Policy(read_only=bool(getattr(args, "read_only", False)), deny_write=deny, allow_write=allow,
                      hide_confidential=not getattr(args, "show_confidential", False),
                      max_write_bytes=int(getattr(args, "max_write_bytes", 20_000)), max_writes_per_minute=int(getattr(args, "max_writes_per_minute", 20)),
                      propose=bool(getattr(args, "propose", False)), audit_path=audit)

    def describe(self) -> str:
        parts = ["read-only" if self.read_only else "read-write",
                 "propose" if self.propose else "direct writes",
                 "confidential hidden" if self.hide_confidential else "confidential visible",
                 f"deny={','.join(self.deny_write) or '-'}", f"allow={','.join(self.allow_write) or '*'}",
                 f"max {self.max_write_bytes} B/write, {self.max_writes_per_minute}/min",
                 f"audit={self.audit_path or 'off'}"]
        return "; ".join(parts)


_UNRESOLVED = "\x00unresolved"   # sentinel: a reference we could not map into the vault


def _norm_rel(path: str) -> str:
    """One spelling per note: forward slashes, no `.md`, `.` and `..` collapsed.

    Without the collapse the policy compares a spelling the filesystem never sees —
    `03-projects/../05-people/x` misses the `05-people` prefix and lands there anyway.
    """
    p = path.strip().replace("\\", "/")
    p = p[:-3] if p.endswith(".md") else p
    p = posixpath.normpath(p) if p.strip("/") else ""
    return "" if p == "." else p.strip("/")


def _escapes(rel: str) -> bool:
    """A normalised path that still starts with `..` points outside the vault."""
    return rel == ".." or rel.startswith("../")


def _prefix_hit(rel: str, prefixes: tuple[str, ...]) -> str | None:
    """The deny/allow prefix a vault-relative path falls under, compared case-insensitively.

    macOS and Windows open `05-People/x` and `05-people/x` as the same file, so a case-sensitive
    comparison is a bypass there; on case-sensitive Linux folding can only ever deny more.
    """
    low = rel.casefold()
    low_md = low + ".md"
    name = Path(rel).name.casefold()
    for pre in prefixes:
        pre_n = pre.rstrip("/").casefold()
        if low == pre_n or low_md == pre_n or low.startswith(pre_n + "/") or name == pre_n:
            return pre
    return None


def _unparsed_frontmatter(body: str) -> bool:
    """True when a note kept its `---` block but parse_note handed back no frontmatter.

    A UTF-8 BOM (PowerShell 5.1, legacy Notepad) defeats the `\\A---` match, so the note reads as
    "no sensitivity set". The block is there; we simply could not read it — which is not the same
    as "not confidential".
    """
    return body.lstrip("\ufeff \t\r\n").startswith("---")


class Guarded:
    """Wraps a backend (FsBackend or CliBackend) and enforces the policy on every call."""

    def __init__(self, backend, policy: Policy, vault: Path | None):
        self.backend = backend
        self.policy = policy
        # resolve(): FsBackend canonicalises its own vault path. If these two disagree —
        # a symlinked home, `--vault .`, /tmp on macOS, a junction on Windows — then
        # _resolve_rel() below cannot map a note back into the vault, and confidential
        # hiding silently stops working.
        self.vault = Path(vault).resolve() if vault else None
        self._writes: list[float] = []
        self.proposals = ProposalStore(self.vault / ".kit" / "proposals") if (self.vault and policy.propose) else None

    # ------------------------------------------------------------ helpers
    def _audit(self, tool: str, args: dict[str, Any], ok: bool | None, detail: str = "", phase: str = "result") -> None:
        if not self.policy.audit_path:
            return
        rec = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "tool": tool, "phase": phase, "ok": ok,
               "args": {k: (v if not isinstance(v, str) or len(v) <= 200 else v[:200] + f"…(+{len(v) - 200})") for k, v in args.items()},
               "detail": detail[:300]}
        self.policy.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.policy.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _audit_attempt(self, tool: str, args: dict[str, Any]) -> None:
        """Record a write before it happens. A write we cannot audit does not happen.

        Auditing only the outcome leaves the one gap that matters: the note is already on disk
        when the audit append fails (read-only .kit, full disk, a bad --audit path), and the tool
        reports an error the model is free to retry.
        """
        try:
            self._audit(tool, args, None, "attempt", phase="attempt")
        except OSError as exc:
            raise PolicyError(f"{tool}: the audit log cannot be written ({exc.__class__.__name__}); refusing the write") from exc

    def _confidential(self, rel: str) -> bool:
        """Must this reference be hidden from the model?

        Every uncertainty answers yes. The control is only worth its name if "I could not tell"
        and "it is not confidential" are different answers: an unresolvable reference, a vault we
        cannot look into, a file we cannot decode (UTF-16 from PowerShell) and frontmatter we
        cannot parse (a leading BOM) all count as confidential.
        """
        if not self.policy.hide_confidential:
            return False
        if rel == _UNRESOLVED or not self.vault:
            return True
        p = self.vault / (rel if rel.endswith(".md") else rel + ".md")
        if not p.exists():
            return False
        try:
            note = kitlib.parse_note(p, self.vault)
        except Exception:  # noqa: BLE001 — undecodable or unreadable: we cannot prove it is safe
            return True
        if note.frontmatter is None:
            return bool(note.fm_error) or _unparsed_frontmatter(note.body)
        return str(note.frontmatter.get("sensitivity", "")).lower() == "confidential"

    def _resolve_rel(self, file: str, must_exist: bool = True) -> str:
        """Vault-relative path of a note reference, using the backend's resolver when it has one.

        Returns `_UNRESOLVED` when the reference cannot be mapped into the vault. Callers must
        treat that as "assume the worst": a blanket `except: pass` here used to turn a path
        mismatch into a silent downgrade of confidential hiding.
        """
        resolve = getattr(self.backend, "_resolve", None)
        if resolve and self.vault:
            try:
                return Path(resolve(file, must_exist)).resolve().relative_to(self.vault).as_posix()
            except FileNotFoundError:
                pass                                # no such note: fall through to the literal spelling
            except ValueError:
                return _UNRESOLVED                  # resolved outside the vault
            except Exception:  # noqa: BLE001 — backend-specific failure (e.g. the Obsidian CLI)
                return _UNRESOLVED
        rel = _norm_rel(file)
        return _UNRESOLVED if (not rel or _escapes(rel)) else rel + ".md"

    def _check_write(self, tool: str, target: str, payload: str = "", prop: str = "", template: str | None = None) -> None:
        """Every model-controlled input of a write tool, checked against the policy in one place.

        `target` is the path the backend resolved to, not the spelling the model sent: the two
        differ for short names and for `..`, and the policy has to see the file the write hits.
        """
        if self.policy.read_only:
            raise PolicyError(f"{tool}: the bridge runs read-only")
        if target == _UNRESOLVED:
            raise PolicyError(f"{tool}: cannot tell which note this writes; refusing (policy)")
        rel = _norm_rel(target)
        if not rel or _escapes(rel):
            raise PolicyError(f"{tool}: `{target}` is not a path inside the vault")
        hit = _prefix_hit(rel, self.policy.deny_write)
        if hit:
            raise PolicyError(f"{tool}: writes to `{hit}` are denied by policy (edit it yourself in Obsidian)")
        if self.policy.allow_write and not _prefix_hit(rel, self.policy.allow_write):
            raise PolicyError(f"{tool}: `{rel}` is outside the writable folders {list(self.policy.allow_write)}")
        if self._confidential(rel):
            raise PolicyError(f"{tool}: `{rel}` is marked confidential; agents do not change it (policy)")
        if prop and self.policy.hide_confidential and prop.strip().lower() == "sensitivity":
            raise PolicyError(f"{tool}: `sensitivity` is the line this policy enforces; change it yourself in Obsidian")
        if template:
            tpl = str(template).strip().replace("\\", "/")
            if posixpath.isabs(tpl) or re.match(r"^[A-Za-z]:", tpl) or ".." in tpl.split("/"):
                raise PolicyError(f"{tool}: template `{template}` must be a name under 90-templates")
        if len(payload.encode("utf-8")) > self.policy.max_write_bytes:
            raise PolicyError(f"{tool}: payload of {len(payload.encode('utf-8'))} bytes exceeds the {self.policy.max_write_bytes}-byte limit; split it")
        now = time.monotonic()
        self._writes = [t for t in self._writes if now - t < 60]
        if len(self._writes) >= self.policy.max_writes_per_minute:
            raise PolicyError(f"{tool}: write rate limit ({self.policy.max_writes_per_minute}/min) reached; wait a minute")
        self._writes.append(now)

    def _guard(self, tool: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        try:
            out = fn()
        except Exception as exc:
            try:
                self._audit(tool, args, False, f"{exc.__class__.__name__}: {exc}")
            except OSError:
                pass   # the caller needs the original failure; writes already have their attempt line
            raise
        self._audit(tool, args, True, (out if isinstance(out, str) else json.dumps(out, default=str))[:120])
        return out

    # ------------------------------------------------------------ read side
    def read_note(self, file: str) -> str:
        def run():
            rel = self._resolve_rel(file)
            if rel == _UNRESOLVED:
                raise PolicyError(f"`{file}` does not resolve to one note in the vault; use obsidian_search for the path (policy)")
            if self._confidential(rel):
                raise PolicyError(f"`{rel}` is marked confidential and is not available to agents (policy)")
            return self.backend.read_note(file)
        return self._guard("read_note", {"file": file}, run)

    def list_files(self, folder: str = "") -> list[str]:
        return self._guard("list_files", {"folder": folder}, lambda: [f for f in self.backend.list_files(folder) if not self._confidential(f)])

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self._guard("search", {"query": query, "limit": limit},
                           lambda: [h for h in self.backend.search(query, limit) if not self._confidential(str(h.get("file", "")))])

    def backlinks(self, file: str) -> list[str]:
        return self._guard("backlinks", {"file": file}, lambda: [f for f in self.backend.backlinks(file) if not self._confidential(f)])

    def graph_neighbors(self, node: str, depth: int = 1, preds=None):
        def run():
            g = self._graph_view()
            if not kitgraph.resolve_id(g, node):
                raise PolicyError(f"no visible note matches {node!r}; use obsidian_search to find its path")
            return [{"src": s, "pred": p, "dst": d, "depth": dd} for s, p, d, dd in kitgraph.neighbors(g, node, depth, preds)]
        return self._guard("graph_neighbors", {"node": node, "depth": depth}, run)

    def graph_path(self, a: str, b: str):
        return self._guard("graph_path", {"a": a, "b": b},
                           lambda: [{"src": s, "pred": p, "dst": d} for s, p, d in kitgraph.shortest_path(self._graph_view(), a, b)])

    def graph_query(self, sql: str, limit: int = 100):
        def run():
            con = kitgraph.to_sqlite(self._graph_view(), ":memory:")
            try:
                cols, rows = kitgraph.query_sqlite(con, sql, limit)
            finally:
                con.close()
            return {"columns": cols, "rows": [list(r) for r in rows]}
        return self._guard("graph_query", {"sql": sql}, run)

    def graph_context(self, node: str, depth: int = 1) -> str:
        def run():
            if self._confidential(self._resolve_rel(node)):
                raise PolicyError(f"`{node}` is confidential; no context pack for agents (policy)")
            return kitgraph.context_pack(self._graph_view(), node, depth)
        return self._guard("graph_context", {"node": node, "depth": depth}, run)

    def todos(self, owner: str = "", overdue_only: bool = False):
        def run():
            rep = self.backend.todos(owner, overdue_only)
            if self.policy.hide_confidential:
                rep["tasks"] = [t for t in rep["tasks"] if not self._confidential(t["file"])]
                rep["conflicts"] = [g for g in rep["conflicts"] if not any(self._confidential(t["file"]) for t in g)]
            return rep
        return self._guard("todos", {"owner": owner, "overdue_only": overdue_only}, run)

    def graph(self):
        return self._graph_view()

    _graph_redacted: tuple[Any, Any] | None = None

    def _graph_view(self):
        """The graph every graph_* tool sees.

        Filtering the tools' output one by one is not enough: graph_query hands the model raw SQL
        over the whole graph, whose nodes table carries each note's title, description and full
        frontmatter. So the redaction happens once, on the graph itself, and every tool reads the
        copy. Cached against the backend's graph object, which FsBackend rebuilds when a note changes.
        """
        g = self.backend.graph()
        if not self.policy.hide_confidential:
            return g
        cached = self._graph_redacted
        if cached and cached[0] is g:
            return cached[1]
        red = self._redact_graph(g)
        self._graph_redacted = (g, red)
        return red

    def _redact_graph(self, g):
        hidden = {nid for nid, n in g.nodes.items() if self._node_confidential(nid, n)}
        red = kitgraph.Graph(built_at=g.built_at)
        red.nodes = {nid: n for nid, n in g.nodes.items() if nid not in hidden}
        for src, pred, dst, origin in g.edges:
            if src not in hidden and dst not in hidden:
                red.add_edge(src, pred, dst, origin)
        red.findings = [f for f in g.findings if f.node not in hidden]
        return red

    def _node_confidential(self, nid: str, node) -> bool:
        if getattr(node, "kind", "") != "note":
            return False       # actors, sources and virtual nodes carry no note frontmatter
        if str(node.props.get("sensitivity", "")).lower() == "confidential":
            return True
        return self._confidential(node.path or nid)

    # ------------------------------------------------------------ write side
    def _write(self, tool: str, args: dict[str, Any], target: str, payload: str, fn: Callable[[], Any],
               prop: str = "", template: str | None = None) -> Any:
        def run():
            self._check_write(tool, target, payload, prop, template)
            self._audit_attempt(tool, args)
            if self.proposals:
                pid = self.proposals.add(tool, args)
                return f"proposed:{pid} — recorded, not written. A human applies it with `kit.py proposals apply {pid}`."
            return fn()
        return self._guard(tool, args, run)

    def _side_write_ok(self, rel: str) -> bool:
        """May a tool's own side-write (the daily note, the log entry) go ahead?

        Same deny/allow lists as a direct write, without the rate and size accounting: the model
        did not choose this path, it chose the tool.
        """
        if rel == _UNRESOLVED:
            return False
        rel = _norm_rel(rel)
        if not rel or _escapes(rel) or _prefix_hit(rel, self.policy.deny_write):
            return False
        return not (self.policy.allow_write and not _prefix_hit(rel, self.policy.allow_write))

    def create_note(self, name: str, content: str = "", template: str | None = None, overwrite: bool = False) -> str:
        target = self._resolve_rel(name, must_exist=False)
        args = {"name": name if target == _UNRESOLVED else target, "content": content, "template": template or "", "overwrite": overwrite}
        return self._write("create_note", args, target, content, lambda: self.backend.create_note(name, content, template, overwrite),
                           template=template)

    def append_note(self, file: str, content: str) -> str:
        target = self._resolve_rel(file)
        args = {"file": file if target == _UNRESOLVED else target, "content": content}
        return self._write("append_note", args, target, content, lambda: self.backend.append_note(file, content))

    def daily_append(self, content: str, day: dt.date | None = None) -> str:
        target = kitlib.daily_note_path(self.vault, day or dt.date.today()).relative_to(self.vault).as_posix() if self.vault else "01-journal/daily"
        return self._write("daily_append", {"content": content}, target, content, lambda: self.backend.daily_append(content, day))

    def set_property(self, file: str, name: str, value: Any) -> str:
        target = self._resolve_rel(file)
        args = {"file": file if target == _UNRESOLVED else target, "name": name, "value": value}
        return self._write("set_property", args, target, str(value),
                           lambda: self.backend.set_property(file, name, value), prop=name)

    def file_minutes(self, text: str, title: str, date: str = "", project: str = "", people: str = "", kind: str = "project"):
        args = {"text": text, "title": title, "date": date, "project": project, "people": people, "kind": kind}
        target = f"02-meetings/{(date or dt.date.today().isoformat())}-{kitlib.slugify(title)}"

        def run():
            daily_rel = self._daily_rel(date)
            daily_ok = bool(daily_rel) and self._side_write_ok(daily_rel)
            out = self.backend.file_minutes(text, title, date, project, people, kind, daily=daily_ok)
            if daily_rel and not daily_ok:
                out["skipped"] = [f"{daily_rel} (write denied by policy)"]
            return out
        return self._write("file_minutes", args, target, text, run)

    def _daily_rel(self, date: str) -> str:
        """Vault-relative path of the daily note a dated tool would touch ("" when we cannot tell)."""
        if not self.vault:
            return ""
        try:
            day = dt.date.fromisoformat(date) if date else dt.date.today()
        except ValueError:
            return ""      # the backend reports the bad date; there is nothing to gate yet
        return kitlib.daily_note_path(self.vault, day).relative_to(self.vault).as_posix()


# ---------------------------------------------------------------- proposals

def _reguard(backend, policy: "Policy | None" = None) -> Guarded:
    """A policy-checking view of a backend, whatever the caller handed us.

    `kit.py proposals apply` builds a bare FsBackend, so without this the apply path is the one
    write into the vault that no policy ever sees.
    """
    if isinstance(backend, Guarded):
        raw, pol, vault = backend.backend, policy or backend.policy, backend.vault
    else:
        raw, pol, vault = backend, policy or Policy(), getattr(backend, "vault", None)
    return Guarded(raw, replace(pol, propose=False), vault)


class ProposalStore:
    """Pending writes as JSON files; humans apply or reject them with kit.py proposals."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def add(self, tool: str, args: dict[str, Any]) -> str:
        pid = f"{dt.datetime.now():%Y%m%dT%H%M%S}-{tool}-{secrets.token_hex(2)}"
        (self.root / f"{pid}.json").write_text(json.dumps({"id": pid, "tool": tool, "args": args, "status": "pending",
                                                            "created": dt.datetime.now().astimezone().isoformat(timespec="seconds")}, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        return pid

    def pending(self) -> list[dict[str, Any]]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("*.json")) if not p.name.endswith((".applied.json", ".rejected.json"))]

    def get(self, pid: str) -> tuple[Path, dict[str, Any]]:
        p = self.root / f"{pid}.json"
        if not p.exists():
            raise PolicyError(f"no pending proposal {pid}")
        return p, json.loads(p.read_text(encoding="utf-8"))

    def apply(self, pid: str, backend, policy: "Policy | None" = None) -> str:
        """Run a proposal through the policy a second time.

        A proposal is a request, not a licence: it was recorded minutes or days ago, the vault has
        moved on, and the human approved the line the listing showed. Re-resolving and re-checking
        is what keeps the approved target and the written file the same file.
        """
        p, rec = self.get(pid)
        tool, a = rec["tool"], rec["args"]
        guard = _reguard(backend, policy)
        if tool == "create_note":
            out = guard.create_note(a["name"], a.get("content", ""), a.get("template") or None, bool(a.get("overwrite")))
        elif tool == "append_note":
            out = guard.append_note(a["file"], a["content"])
        elif tool == "daily_append":
            out = guard.daily_append(a["content"])
        elif tool == "set_property":
            out = guard.set_property(a["file"], a["name"], a["value"])
        elif tool == "file_minutes":
            out = guard.file_minutes(a["text"], a["title"], a.get("date", ""), a.get("project", ""), a.get("people", ""), a.get("kind", "project"))
        else:
            raise PolicyError(f"unknown tool in proposal: {tool}")
        rec["status"] = "applied"; rec["applied"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        p.with_name(f"{pid}.applied.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n"); p.unlink()
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)

    def reject(self, pid: str, reason: str = "") -> None:
        p, rec = self.get(pid)
        rec["status"] = "rejected"; rec["reason"] = reason
        p.with_name(f"{pid}.rejected.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n"); p.unlink()


# ---------------------------------------------------------------- HTTP bearer auth (ASGI middleware)

def token_ok(header_value: str | bytes | None, token: str) -> bool:
    if not token or header_value is None:
        return False
    value = header_value.decode() if isinstance(header_value, bytes) else header_value
    m = re.match(r"^\s*Bearer\s+(\S+)\s*$", value)
    return bool(m) and secrets.compare_digest(m.group(1), token)


class BearerAuth:
    """Rejects HTTP requests without `Authorization: Bearer <token>`. Wraps any ASGI app (the MCP streamable-HTTP app)."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
            if not token_ok(headers.get(b"authorization"), self.token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def new_token() -> str:
    return secrets.token_urlsafe(32)
