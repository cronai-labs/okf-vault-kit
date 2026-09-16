"""kitgraph — the vault as an explicit graph.

Ontology (vault/99-system/ontology.yml) → validation of ranges, enums, required props, references.
Graph build: notes, actors, sources as nodes; ref-properties, body links, citations, provenance as edges.
Derived facts: inverse edges, transitive closure, trust tier, staleness, at-risk, superseded.
Exports: JSON, N-Triples, JSON-LD (via vault/context.jsonld), SQLite (stdlib sqlite3).
Queries: neighbours, shortest path, SQL passthrough, context packs for models.

Standard library + PyYAML only.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import difflib
import json
import os
import re
import sqlite3
import urllib.parse
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

import kitlib

ONTOLOGY_REL = "99-system/ontology.yml"
DEFAULT_ONTOLOGY = Path(__file__).resolve().parent / "config" / "ontology.yml"
KIT_DIR = ".kit"
SELF_ID = "self"
MD_LINK_VALUE = re.compile(r"^\s*\[([^\]]*)\]\(([^)\s]+)\)\s*$")
WIKI_VALUE = re.compile(r"^\s*\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]\s*$")
FOOTNOTE_REF = re.compile(r"\[\^([^\]]+)\](?!:)")
KNOWLEDGE_ROOT = "knowledge"


# ---------------------------------------------------------------- ontology

@dataclass
class PropSpec:
    name: str
    type: str = "text"
    values: list[str] = field(default_factory=list)
    range: list[str] = field(default_factory=list)
    many: bool = False
    soft: bool = False
    required: bool = False


class Ontology:
    def __init__(self, data: dict[str, Any], source: Path | None = None):
        self.data = data or {}
        self.source = source
        self.base_iri: str = self.data.get("base_iri", "urn:okf-vault-kit:")
        self.context_file: str = self.data.get("context_file", "context.jsonld")
        self.classes: dict[str, dict] = self.data.get("classes", {}) or {}
        self.edges: dict[str, dict] = self.data.get("edges", {}) or {}
        self.property_aliases: dict[str, str] = self.data.get("property_aliases", {}) or {}
        self.value_aliases: dict[str, dict] = self.data.get("value_aliases", {}) or {}
        self.rules: dict[str, bool] = self.data.get("rules", {}) or {}
        self._props_cache: dict[str, dict[str, PropSpec]] = {}

    # -- classes
    def has_class(self, cls: str) -> bool:
        return cls in self.classes

    def ancestors(self, cls: str) -> list[str]:
        out, seen = [], set()
        while cls and cls not in seen and cls in self.classes:
            out.append(cls); seen.add(cls)
            cls = (self.classes[cls] or {}).get("extends")
        return out

    def is_a(self, cls: str, ancestor: str) -> bool:
        return ancestor in self.ancestors(cls)

    def props(self, cls: str) -> dict[str, PropSpec]:
        """Merged property specs through the extends chain (child overrides parent)."""
        if cls in self._props_cache:
            return self._props_cache[cls]
        merged: dict[str, PropSpec] = {}
        chain = self.ancestors(cls) or ["node"]
        for c in reversed(chain):
            for name, raw in ((self.classes.get(c) or {}).get("props") or {}).items():
                merged[name] = _prop_spec(name, raw)
        self._props_cache[cls] = merged
        return merged

    def ref_props(self, cls: str) -> dict[str, PropSpec]:
        return {k: v for k, v in self.props(cls).items() if v.type == "ref"}

    # -- edges
    def inverse(self, pred: str) -> str | None:
        return (self.edges.get(pred) or {}).get("inverse")

    def transitive(self, pred: str) -> bool:
        return bool((self.edges.get(pred) or {}).get("transitive"))

    # -- mapping
    def canonical_key(self, key: str) -> str:
        return self.property_aliases.get(key, key)

    def canonical_value(self, key: str, value: Any) -> Any:
        table = self.value_aliases.get(key) or {}
        if isinstance(value, str) and value in table:
            return table[value]
        return value

    def normalise(self, fm: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Apply property/value aliases. Returns (canonical frontmatter, notes about what was mapped)."""
        out: dict[str, Any] = {}
        notes: list[str] = []
        for k, v in fm.items():
            ck = self.canonical_key(k)
            if ck != k:
                notes.append(f"`{k}` mapped to canonical `{ck}`")
                if ck in fm:  # canonical present too — canonical wins
                    continue
            cv = self.canonical_value(ck, v)
            if cv != v:
                notes.append(f"`{ck}: {v}` mapped to `{cv}`")
            out[ck] = cv
        return out, notes

    def rule(self, name: str) -> bool:
        return bool(self.rules.get(name, True))


def _prop_spec(name: str, raw: Any) -> PropSpec:
    if isinstance(raw, str):
        return PropSpec(name=name, type=raw)
    raw = raw or {}
    rng = raw.get("range") or []
    if isinstance(rng, str):
        rng = [rng]
    return PropSpec(name=name, type=raw.get("type", "text"), values=[str(v) for v in raw.get("values", [])],
                    range=list(rng), many=bool(raw.get("many")), soft=bool(raw.get("soft")), required=bool(raw.get("required")))


def ontology_path(vault: Path) -> Path | None:
    p = Path(vault) / ONTOLOGY_REL
    if p.exists():
        return p
    return DEFAULT_ONTOLOGY if DEFAULT_ONTOLOGY.exists() else None


def load_ontology(vault: Path) -> Ontology:
    """The vault's ontology, falling back to the kit's own.

    A vault ontology we cannot decode is the same situation as a vault without one: the fallback
    is a graph built on the shipped classes, not no graph at all. `validate` names the file.
    """
    for p in (Path(vault) / ONTOLOGY_REL, DEFAULT_ONTOLOGY):
        text = kitlib.read_text(p)      # None for a file that is missing as well as one we cannot decode
        if text is not None:
            return Ontology(yaml.safe_load(text) or {}, p)
    return Ontology({})


def load_context(vault: Path, onto: Ontology) -> dict[str, Any]:
    text = kitlib.read_text(Path(vault) / onto.context_file)
    if text is None:
        return {}                      # absent or undecodable: the exports fall back to the base IRI
    try:
        return json.loads(text).get("@context", {})
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------- graph model

@dataclass
class Node:
    id: str
    kind: str                      # note | actor | source | virtual
    type: str                      # ontology class (note type) or actor/source
    title: str
    description: str = ""
    path: str = ""
    mtime: float = 0.0
    props: dict[str, Any] = field(default_factory=dict)
    derived: dict[str, Any] = field(default_factory=dict)


@dataclass
class Finding:
    level: str            # error | warning | info
    code: str
    node: str
    message: str
    suggestion: str = ""

    def render(self) -> str:
        s = f"{self.level:7} {self.code:26} {self.node}: {self.message}"
        return s + (f"  → {self.suggestion}" if self.suggestion else "")


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[tuple[str, str, str, str]] = field(default_factory=list)   # src, pred, dst, origin
    findings: list[Finding] = field(default_factory=list)
    built_at: str = ""
    _edge_set: set[tuple[str, str, str]] = field(default_factory=set, repr=False)

    def add_edge(self, src: str, pred: str, dst: str, origin: str = "frontmatter") -> bool:
        key = (src, pred, dst)
        if key in self._edge_set or src == dst:
            return False
        self._edge_set.add(key)
        self.edges.append((src, pred, dst, origin))
        return True

    def out_edges(self, node: str) -> list[tuple[str, str, str, str]]:
        return [e for e in self.edges if e[0] == node]

    def in_edges(self, node: str) -> list[tuple[str, str, str, str]]:
        return [e for e in self.edges if e[2] == node]

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]


# ---------------------------------------------------------------- reference resolution

def _norm(s: str) -> str:
    s = re.sub(r"\(example\)", "", s, flags=re.I)
    s = re.sub(r"[^\w\s-]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


class Resolver:
    """Resolve property values and mentions to node ids: by path, stem, title, alias — then fuzzy suggestions."""

    def __init__(self, nodes: dict[str, Node], onto: Ontology, self_actor: str | None = None):
        self.nodes = nodes
        self.onto = onto
        self.self_actor = self_actor
        self.by_path: dict[str, str] = {}
        self.by_stem: dict[str, list[str]] = defaultdict(list)
        self.by_name: dict[str, list[str]] = defaultdict(list)
        for nid, n in nodes.items():
            if n.kind != "note":
                continue
            self.by_path[n.path] = nid
            self.by_path[n.path.removesuffix(".md")] = nid
            self.by_stem[Path(n.path).stem.lower()].append(nid)
            self.by_name[_norm(n.title)].append(nid)
            for alias in n.props.get("aliases") or []:
                if isinstance(alias, str) and alias.strip():
                    self.by_name[_norm(alias)].append(nid)
        # unique first names of people as implicit aliases
        first: dict[str, list[str]] = defaultdict(list)
        for nid, n in nodes.items():
            if n.kind == "note" and n.type == "person":
                fn = _norm(n.title).split(" ")[0] if n.title else ""
                if len(fn) >= 3:
                    first[fn].append(nid)
        for fn, ids in first.items():
            if len(ids) == 1 and fn not in self.by_name:
                self.by_name[fn] = ids

    def resolve(self, value: Any, ranges: list[str] | None = None, from_path: str = "") -> tuple[str | None, str, list[str]]:
        """Return (node_id or None, method, candidates)."""
        if value is None:
            return None, "empty", []
        if not isinstance(value, str):
            value = str(value)
        raw = value.strip()
        if not raw:
            return None, "empty", []
        if raw in ("me", "self", "myself") or (self.self_actor and raw == self.self_actor):
            return SELF_ID, "self", [SELF_ID]
        m = MD_LINK_VALUE.match(raw)
        if m:
            target = urllib.parse.unquote(m.group(2).split("#", 1)[0])
            if target.startswith(("http://", "https://")):
                return None, "external", []
            base = Path(from_path).parent if from_path else Path("")
            rel = (base / target) if not target.startswith("/") else Path(target.lstrip("/"))
            rel = Path(*[p for p in rel.as_posix().split("/") if p not in ("", ".")])
            # collapse ".."
            parts: list[str] = []
            for p in rel.parts:
                if p == "..":
                    if parts:
                        parts.pop()
                else:
                    parts.append(p)
            key = "/".join(parts)
            nid = self.by_path.get(key) or self.by_path.get(key + ".md")
            if nid:
                return nid, "link", [nid]
            # fall back to the link text
            raw = m.group(1)
        w = WIKI_VALUE.match(raw)
        if w:
            stem = Path(w.group(1).strip()).name.lower()
            stem = stem.removesuffix(".md")
            cands = self.by_stem.get(stem, [])
            cands = self._prefer_range(cands, ranges)
            if len(cands) == 1:
                return cands[0], "wikilink", cands
            return None, "ambiguous" if cands else "unresolved", cands
        cands = list(self.by_name.get(_norm(raw), [])) or list(self.by_stem.get(raw.lower(), []))
        cands = self._prefer_range(cands, ranges)
        if len(cands) == 1:
            return cands[0], "name", cands
        if len(cands) > 1:
            return None, "ambiguous", cands
        return None, "unresolved", self.suggest(raw, ranges)

    def _prefer_range(self, cands: list[str], ranges: list[str] | None) -> list[str]:
        if not ranges or len(cands) <= 1:
            return cands
        inr = [c for c in cands if any(self.onto.is_a(self.nodes[c].type, r) for r in ranges)]
        return inr or cands

    def suggest(self, value: str, ranges: list[str] | None = None, n: int = 3) -> list[str]:
        pool = {}
        for nid, node in self.nodes.items():
            if node.kind != "note":
                continue
            if ranges and not any(self.onto.is_a(node.type, r) for r in ranges):
                continue
            pool[_norm(node.title)] = nid
            pool[Path(node.path).stem.lower()] = nid
        matches = difflib.get_close_matches(_norm(value), list(pool), n=n, cutoff=0.6)
        out: list[str] = []
        for m in matches:
            if pool[m] not in out:
                out.append(pool[m])
        return out


# ---------------------------------------------------------------- build

def _self_actor(vault: Path) -> str | None:
    """The vault owner's actor id: the `human:` actor the templates stamp (kit init --actor)."""
    tdir = Path(vault) / "90-templates"
    counts: dict[str, int] = defaultdict(int)
    for p in tdir.glob("*.md") if tdir.exists() else []:
        text = kitlib.read_text(p)
        if text is None:
            continue          # same rule as kitlib.load_vault: skip it, `validate` reports it
        for m in re.finditer(r"by:\s*(human:[\w.-]+)", text):
            counts[m.group(1)] += 1
    if counts:
        return max(counts, key=counts.get)
    return "human:me"


def _actor_events(value: Any) -> list[dict]:
    if value is None:
        return []
    return [v for v in (value if isinstance(value, list) else [value]) if isinstance(v, dict)]


def _event_times(value: Any) -> list[Any]:
    """The `at` values of provenance events, raw: each exporter decides how to type the literal."""
    return [ev["at"] for ev in _actor_events(value) if ev.get("at") is not None]


def _iso(value: Any) -> str:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def build_graph(vault: Path, onto: Ontology | None = None, now: dt.datetime | None = None) -> Graph:
    vault = Path(vault)
    onto = onto or load_ontology(vault)
    now = now or dt.datetime.now(dt.UTC)
    g = Graph(built_at=now.isoformat())
    self_actor = _self_actor(vault)
    g.nodes[SELF_ID] = Node(id=SELF_ID, kind="virtual", type="person", title="me", description="The vault owner", props={"actor": self_actor})

    notes = [n for n in kitlib.load_vault(vault)
             if n.path.name not in kitlib.RESERVED_FILENAMES and not n.rel.startswith("90-templates/")]
    # pass 1: nodes
    for note in notes:
        fm = note.frontmatter or {}
        fm, mapped = onto.normalise(fm)
        ntype = str(fm.get("type") or "unknown")
        title = fm.get("title") if isinstance(fm.get("title"), str) and fm.get("title").strip() else note.path.stem
        nid = note.rel
        g.nodes[nid] = Node(id=nid, kind="note", type=ntype, title=title, description=str(fm.get("description") or "").strip(),
                            path=note.rel, mtime=note.path.stat().st_mtime, props=fm)
        for msg in mapped:
            g.findings.append(Finding("warning", "property_alias", nid, msg, "rename the property in the note so every tool sees the canonical name"))
        if ntype != "unknown" and not onto.has_class(ntype) and onto.classes:
            g.findings.append(Finding("info", "unknown_type", nid, f"type `{ntype}` is not declared in ontology.yml (tolerated)", "add a class or use an existing one"))
    resolver = Resolver(g.nodes, onto, self_actor)

    # pass 2: edges and per-note checks
    for note in notes:
        nid = note.rel
        node = g.nodes[nid]
        fm = node.props
        specs = onto.props(node.type) if onto.has_class(node.type) else onto.props("node")
        _check_props(g, node, specs, onto, resolver, now)
        # provenance
        for ev in _actor_events(fm.get("generated")):
            by = ev.get("by")
            if isinstance(by, str):
                g.add_edge(nid, "generated_by", _actor_node(g, by, self_actor), "frontmatter")
        for ev in _actor_events(fm.get("verified")):
            by = ev.get("by")
            if isinstance(by, str):
                g.add_edge(nid, "verified_by", _actor_node(g, by, self_actor), "frontmatter")
        # sources + citations
        declared: dict[str, str] = {}
        for s in fm.get("sources") or []:
            if isinstance(s, dict) and s.get("resource"):
                sid = _source_node(g, s)
                g.add_edge(nid, "has_source", sid, "frontmatter")
                if s.get("id"):
                    declared[str(s["id"])] = sid
        cited = set(FOOTNOTE_REF.findall(kitlib._strip_code(note.body)))
        for fid in cited:
            if fid in declared:
                g.add_edge(nid, "cites", declared[fid], "body")
        if onto.rule("unused_source"):
            for fid in declared:
                if fid not in cited:
                    g.findings.append(Finding("info", "unused_source", nid, f"source `{fid}` is declared but never cited with [^{fid}]", "cite it in the body or remove it"))
        # body links
        text = kitlib._strip_code(note.body)
        for m in kitlib.MD_LINK_RE.finditer(text):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#", "obsidian://")):
                continue
            tid, _method, _ = resolver.resolve(f"[x]({target})", None, note.rel)
            if tid and tid != nid:
                g.add_edge(nid, "links_to", tid, "body")
        for m in kitlib.WIKILINK_RE.finditer(text):
            tid, _method, _ = resolver.resolve(f"[[{m.group(1).strip()}]]", None, note.rel)
            if tid and tid != nid:
                g.add_edge(nid, "links_to", tid, "body")

    derive(g, onto, now)
    return g


def _actor_node(g: Graph, actor: str, self_actor: str | None) -> str:
    if self_actor and actor == self_actor:
        return SELF_ID
    aid = f"actor:{actor}"
    if aid not in g.nodes:
        kind = "human" if actor.startswith("human:") else ("process" if actor.startswith("process:") else "agent")
        g.nodes[aid] = Node(id=aid, kind="actor", type="actor", title=actor, description=f"{kind} actor", props={"actor_kind": kind})
    return aid


def _source_node(g: Graph, s: dict) -> str:
    res = str(s["resource"]).strip()
    sid = f"source:{res}"
    if sid not in g.nodes:
        g.nodes[sid] = Node(id=sid, kind="source", type="source", title=str(s.get("title") or res), description="",
                            props={k: _iso(v) for k, v in s.items() if k != "id"})
    return sid


def _check_props(g: Graph, node: Node, specs: dict[str, PropSpec], onto: Ontology, resolver: Resolver, now: dt.datetime) -> None:
    fm = node.props
    nid = node.id
    for name, spec in specs.items():
        val = fm.get(name)
        empty = val is None or val == "" or val == []
        if spec.required and empty:
            g.findings.append(Finding("error", "missing_required", nid, f"`{name}` is required for type `{node.type}`"))
            continue
        if empty:
            continue
        if spec.type == "enum" and str(val) not in spec.values:
            g.findings.append(Finding("error", "enum_violation", nid, f"`{name}: {val}` is not one of {spec.values}"))
        elif spec.type == "date":
            ok = isinstance(val, dt.date) or (isinstance(val, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", val))
            if not ok:
                g.findings.append(Finding("error", "type_violation", nid, f"`{name}` must be a date (YYYY-MM-DD); got {val!r}"))
        elif spec.type == "datetime":
            if not kitlib._is_iso(val):
                g.findings.append(Finding("error", "type_violation", nid, f"`{name}` must be an ISO-8601 instant with offset; got {val!r}"))
        elif spec.type == "list":
            if not isinstance(val, list):
                g.findings.append(Finding("warning", "type_violation", nid, f"`{name}` should be a list; got {type(val).__name__}"))
        elif spec.type == "ref":
            values = val if isinstance(val, list) else [val]
            if isinstance(val, list) and not spec.many:
                g.findings.append(Finding("warning", "cardinality", nid, f"`{name}` holds {len(values)} values but the ontology allows one"))
            for v in values:
                tid, method, cands = resolver.resolve(v, spec.range, node.path)
                if tid:
                    tnode = g.nodes[tid]
                    if spec.range and tid != SELF_ID and not any(onto.is_a(tnode.type, r) for r in spec.range):
                        g.findings.append(Finding("error", "range_violation", nid,
                                                  f"`{name}` points at {tid} of type `{tnode.type}`; expected {spec.range}"))
                    g.add_edge(nid, name, tid, "frontmatter")
                elif method == "ambiguous":
                    g.findings.append(Finding("warning", "ambiguous_ref", nid, f"`{name}: {v}` matches several notes: {cands}",
                                              "use a markdown link to the intended note"))
                elif method == "external":
                    continue
                else:
                    level = "info" if spec.soft else "warning"
                    sugg = f"did you mean {', '.join(cands)}?" if cands else (f"create a `{spec.range[0]}` note for it" if spec.range else "")
                    g.findings.append(Finding(level, "unresolved_ref", nid, f"`{name}: {v}` does not resolve to a note", sugg))
    # unknown props are tolerated (OKF); nothing to report


# ---------------------------------------------------------------- derived facts

def derive(g: Graph, onto: Ontology, now: dt.datetime) -> None:
    # inverse edges
    for src, pred, dst, _origin in list(g.edges):
        inv = onto.inverse(pred)
        if inv:
            g.add_edge(dst, inv, src, "derived")
    # transitive closure
    for pred in [p for p in onto.edges if onto.transitive(p)]:
        adj: dict[str, set[str]] = defaultdict(set)
        for src, p, dst, _ in g.edges:
            if p == pred:
                adj[src].add(dst)
        for start in list(adj):
            seen, stack = set(), list(adj[start])
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(adj.get(cur, ()))
            for reach in seen:
                if reach not in adj[start]:
                    g.add_edge(start, pred, reach, "derived")
                    inv = onto.inverse(pred)
                    if inv:
                        g.add_edge(reach, inv, start, "derived")
    # per-node derived props
    for node in g.nodes.values():
        if node.kind != "note":
            continue
        fm = node.props
        if onto.rule("trust_tier"):
            events = _actor_events(fm.get("verified"))
            bys = [str(e.get("by", "")) for e in events]
            node.derived["trust_tier"] = ("human-reviewed" if any(b.startswith("human:") for b in bys)
                                          else "machine-confirmed" if bys else "unverified")
        if onto.rule("stale"):
            sa = fm.get("stale_after")
            stale = False
            if isinstance(sa, dt.datetime):
                stale = (sa if sa.tzinfo else sa.replace(tzinfo=dt.UTC)) < now
            elif isinstance(sa, str) and kitlib._is_iso(sa):
                stale = dt.datetime.fromisoformat(sa) < now
            node.derived["stale"] = stale
        if onto.is_a(node.type, KNOWLEDGE_ROOT) or node.type == KNOWLEDGE_ROOT:
            node.derived["status_effective"] = fm.get("status") or "stable"
    knowledge_bad = {nid for nid, n in g.nodes.items()
                     if n.kind == "note" and (n.derived.get("stale") or n.derived.get("status_effective") == "deprecated")}
    if onto.rule("at_risk"):
        for nid, node in g.nodes.items():
            if node.kind != "note":
                continue
            if onto.is_a(node.type, KNOWLEDGE_ROOT):
                bad = [dst for src, p, dst, _ in g.out_edges(nid) if p in ("links_to", "cites") and dst in knowledge_bad]
                node.derived["at_risk"] = bool(bad)
                if bad:
                    g.findings.append(Finding("warning", "at_risk", nid, f"relies on stale/deprecated knowledge: {bad}", "re-verify the cited notes or update this one"))
        for nid, node in g.nodes.items():
            if node.kind == "note" and node.type == "project":
                dep = [src for src, p, dst, _ in g.in_edges(nid) if p == "project" and (g.nodes[src].derived.get("at_risk") or src in knowledge_bad)]
                node.derived["at_risk"] = bool(dep)
                if dep:
                    g.findings.append(Finding("warning", "project_at_risk", nid, f"depends on at-risk or stale notes: {dep}"))
    if onto.rule("superseded"):
        # direct edges only: the transitive closure above would otherwise point every node in a chain at the newest decision
        for src, pred, dst, origin in g.edges:
            if pred == "supersedes" and origin != "derived" and dst in g.nodes and g.nodes[dst].kind == "note":
                old = g.nodes[dst]
                old.derived["superseded_by"] = src
                if old.props.get("state") != "superseded":
                    g.findings.append(Finding("warning", "state_conflict", dst, f"is superseded by {src} but has state `{old.props.get('state')}`",
                                              "set state: superseded (kit.py reconcile --apply does this)"))
    if onto.rule("decision_needs_context"):
        for nid, node in g.nodes.items():
            if node.kind == "note" and node.type == "decision" and not any(p in ("project", "area") for _, p, _, _ in g.out_edges(nid)):
                g.findings.append(Finding("warning", "decision_without_context", nid, "decision has neither a project nor an area", "set `project:` or `area:`"))


# ---------------------------------------------------------------- queries

def neighbors(g: Graph, node: str, depth: int = 1, preds: Iterable[str] | None = None) -> list[tuple[str, str, str, int]]:
    """Breadth-first over outgoing edges (inverses are materialised, so this covers both directions)."""
    node = resolve_id(g, node)
    if not node:
        return []
    allowed = set(preds) if preds else None
    seen = {node}
    frontier = [node]
    out: list[tuple[str, str, str, int]] = []
    for d in range(1, depth + 1):
        nxt = []
        for cur in frontier:
            for src, p, dst, _ in g.out_edges(cur):
                if allowed and p not in allowed:
                    continue
                out.append((src, p, dst, d))
                if dst not in seen:
                    seen.add(dst); nxt.append(dst)
        frontier = nxt
    return out


PROVENANCE_PREDS = {"generated_by", "generated", "verified_by", "verified"}


def shortest_path(g: Graph, a: str, b: str, skip: Iterable[str] = PROVENANCE_PREDS) -> list[tuple[str, str, str]]:
    """Shortest path over materialised edges; provenance edges are skipped by default (everything meets at `self`)."""
    a, b = resolve_id(g, a), resolve_id(g, b)
    if not a or not b:
        return []
    skip = set(skip or ())
    prev: dict[str, tuple[str, str] | None] = {a: None}
    q = deque([a])
    while q:
        cur = q.popleft()
        if cur == b:
            break
        for _src, p, dst, _ in g.out_edges(cur):
            if p in skip:
                continue
            if dst not in prev:
                prev[dst] = (cur, p); q.append(dst)
    if b not in prev:
        return []
    path: list[tuple[str, str, str]] = []
    cur = b
    while prev[cur]:
        p_src, p_pred = prev[cur]
        path.append((p_src, p_pred, cur)); cur = p_src
    return list(reversed(path))


def resolve_id(g: Graph, ref: str) -> str | None:
    if ref in g.nodes:
        return ref
    ref2 = ref if ref.endswith(".md") else ref + ".md"
    if ref2 in g.nodes:
        return ref2
    low = ref.lower().strip()
    cands = [nid for nid, n in g.nodes.items() if n.kind == "note" and (Path(n.path).stem.lower() == low or _norm(n.title) == _norm(ref))]
    return cands[0] if len(cands) == 1 else None


def context_pack(g: Graph, node: str, depth: int = 1) -> str:
    """Markdown facts about a node and its neighbourhood — the structured context a small model should get."""
    nid = resolve_id(g, node)
    if not nid:
        return f"No node matches {node!r}."
    n = g.nodes[nid]
    lines = [f"# {n.title}", f"- id: {nid}", f"- type: {n.type}"]
    if n.description:
        lines.append(f"- description: {n.description}")
    for k in ("state", "health", "status", "due", "milestone", "date", "stale_after"):
        if n.props.get(k) not in (None, ""):
            lines.append(f"- {k}: {_iso(n.props[k])}")
    for k, v in sorted(n.derived.items()):
        lines.append(f"- {k} (derived): {v}")
    grouped: dict[str, list[str]] = defaultdict(list)
    for src, p, dst, _d in neighbors(g, nid, depth):
        if src == nid:
            grouped[p].append(_label(g, dst))
    if grouped:
        lines.append("")
        lines.append("## Relations")
        for p in sorted(grouped):
            lines.append(f"- {p}: " + "; ".join(sorted(set(grouped[p]))))
    return "\n".join(lines) + "\n"


def mentions(g: Graph, text: str, types_excluded: Iterable[str] = ("system",)) -> list[str]:
    """Note ids whose title or alias appears in free text (word boundary, case-insensitive), longest titles first."""
    out: list[tuple[int, str]] = []
    excluded = set(types_excluded)
    for nid, n in g.nodes.items():
        if n.kind != "note" or n.type in excluded:
            continue
        names = [n.title] + [a for a in (n.props.get("aliases") or []) if isinstance(a, str)]
        for name in names:
            clean = re.sub(r"\s*\(example\)", "", name, flags=re.I).strip()
            if len(clean) >= 4 and re.search(r"(?<![\w-])" + re.escape(clean) + r"(?![\w-])", text, re.I):
                out.append((len(clean), nid)); break
    return [nid for _, nid in sorted(out, reverse=True)]


def _label(g: Graph, nid: str) -> str:
    n = g.nodes.get(nid)
    if not n:
        return nid
    extra = []
    for k in ("state", "health", "status"):
        if n.props.get(k):
            extra.append(f"{k}={n.props[k]}")
    if n.derived.get("stale"):
        extra.append("stale")
    tag = f" [{n.type}{', ' + ', '.join(extra) if extra else ''}]"
    return f"{n.title} ({nid}){tag}"


# ---------------------------------------------------------------- exports

def _json_safe(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json_safe(x) for x in v]
    return v


def to_json(g: Graph) -> dict[str, Any]:
    return {
        "built_at": g.built_at,
        "nodes": [{"id": n.id, "kind": n.kind, "type": n.type, "title": n.title, "description": n.description, "path": n.path,
                   "props": _json_safe(n.props), "derived": _json_safe(n.derived)} for n in sorted(g.nodes.values(), key=lambda x: x.id)],
        "edges": [{"src": s, "pred": p, "dst": d, "origin": o} for s, p, d, o in sorted(g.edges)],
        "findings": [{"level": f.level, "code": f.code, "node": f.node, "message": f.message, "suggestion": f.suggestion}
                     for f in g.findings],
    }


def _iri(g_base: str, nid: str) -> str:
    return "<" + g_base + urllib.parse.quote(nid, safe=":/@") + ">"


def _iri_term(term: str) -> str:
    """Percent-encode a vault-supplied term before it becomes part of an IRI: N-Triples IRIREFs forbid spaces and <>"{}|^`."""
    return urllib.parse.quote(term, safe=":/@#")


def _expand(term: str, context: dict[str, Any], base: str) -> str:
    """Expand a context term to a full IRI (compact IRIs with declared prefixes only)."""
    entry = context.get(term)
    iri = entry.get("@id") if isinstance(entry, dict) else entry
    if not isinstance(iri, str):
        vocab = context.get("@vocab", base)
        return vocab + _iri_term(term)
    if ":" in iri:
        prefix, rest = iri.split(":", 1)
        pv = context.get(prefix)
        if isinstance(pv, str) and not rest.startswith("//"):
            return pv + rest
    return iri


def _nt_literal(value: Any, context_entry: Any = None) -> str:
    if isinstance(value, bool):
        return f'"{str(value).lower()}"^^<http://www.w3.org/2001/XMLSchema#boolean>'
    if isinstance(value, dt.datetime):
        return f'"{value.isoformat()}"^^<http://www.w3.org/2001/XMLSchema#dateTime>'
    if isinstance(value, dt.date):
        return f'"{value.isoformat()}"^^<http://www.w3.org/2001/XMLSchema#date>'
    if isinstance(value, (int, float)):
        return f'"{value}"^^<http://www.w3.org/2001/XMLSchema#{"integer" if isinstance(value, int) else "double"}>'
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{s}"'


def to_ntriples(g: Graph, context: dict[str, Any], base: str) -> list[str]:
    rdf_type = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
    lines: list[str] = []
    for n in sorted(g.nodes.values(), key=lambda x: x.id):
        s = _iri(base, n.id)
        lines.append(f"{s} {rdf_type} <{_expand(n.type.capitalize(), context, base)}> .")
        lines.append(f"{s} <{_expand('title', context, base)}> {_nt_literal(n.title)} .")
        if n.description:
            lines.append(f"{s} <{_expand('description', context, base)}> {_nt_literal(n.description)} .")
        edge_preds = {p for s, p, _, _ in g.edges if s == n.id}
        for k, v in sorted(n.props.items()):
            if k in ("type", "title", "description", "generated", "verified", "sources") or v in (None, "", []) or k in edge_preds:
                continue
            if isinstance(v, dict):
                continue
            vals = v if isinstance(v, list) else [v]
            for x in vals:
                if isinstance(x, (dict, list)):
                    continue
                # a link-shaped value that resolved is already an edge (k in edge_preds); what is left here never resolved, so keep it as a literal
                lines.append(f"{s} <{_expand(k, context, base)}> {_nt_literal(x)} .")
        for term, prop in (("generated_at", "generated"), ("verified_at", "verified")):
            for at in _event_times(n.props.get(prop)):
                lines.append(f"{s} <{_expand(term, context, base)}> {_nt_literal(at)} .")
        for k, v in sorted(n.derived.items()):
            lines.append(f"{s} <{base}derived/{_iri_term(k)}> {_nt_literal(v)} .")
    for src, p, dst, origin in sorted(g.edges):
        if origin == "derived":
            continue
        lines.append(f"{_iri(base, src)} <{_expand(p, context, base)}> {_iri(base, dst)} .")
    return lines


def _jsonld_value(x: Any) -> Any:
    """Wrap a link-shaped value that produced no edge: a bare string under an `@type: @id` term would be read as an IRI, not as text."""
    if isinstance(x, str) and (MD_LINK_VALUE.match(x) or WIKI_VALUE.match(x)):
        return {"@value": x}
    return _json_safe(x)


def to_jsonld(g: Graph, context: dict[str, Any], base: str) -> dict[str, Any]:
    graph = []
    for n in sorted(g.nodes.values(), key=lambda x: x.id):
        obj: dict[str, Any] = {"@id": base + urllib.parse.quote(n.id, safe=":/@"), "@type": n.type.capitalize(), "title": n.title}
        if n.description:
            obj["description"] = n.description
        edge_preds = {p for s, p, _, _ in g.edges if s == n.id}
        for k, v in sorted(n.props.items()):
            if k in ("type", "title", "description", "generated", "verified", "sources") or v in (None, "", []) or isinstance(v, dict) or k in edge_preds:
                continue
            vals = [_jsonld_value(x) for x in (v if isinstance(v, list) else [v])]
            obj[k] = vals if isinstance(v, list) else vals[0]
        for term, prop in (("generated_at", "generated"), ("verified_at", "verified")):
            times = [_iso(at) for at in _event_times(n.props.get(prop))]
            if times:
                obj[term] = times[0] if len(times) == 1 else times
        for k, v in sorted(n.derived.items()):
            obj[f"derived_{k}"] = v
        for src, p, dst, origin in g.edges:
            if src == n.id and origin != "derived":
                obj.setdefault(p, []).append({"@id": base + urllib.parse.quote(dst, safe=":/@")})
        graph.append(obj)
    return {"@context": context or {"@vocab": base}, "@graph": graph}


# ---------------------------------------------------------------- sqlite

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT, type TEXT, title TEXT, description TEXT, path TEXT, mtime REAL, props TEXT);
CREATE TABLE edges (src TEXT, pred TEXT, dst TEXT, origin TEXT, PRIMARY KEY (src, pred, dst));
CREATE TABLE props (node TEXT, key TEXT, value TEXT);
CREATE TABLE derived (node TEXT, key TEXT, value TEXT);
CREATE TABLE findings (level TEXT, code TEXT, node TEXT, message TEXT, suggestion TEXT);
CREATE INDEX edges_dst ON edges(dst);
CREATE INDEX props_key ON props(key);
CREATE VIEW v_trust AS SELECT node AS id, value AS trust_tier FROM derived WHERE key='trust_tier';
CREATE VIEW v_stale AS SELECT node AS id FROM derived WHERE key='stale' AND value='true';
CREATE VIEW v_at_risk AS SELECT node AS id FROM derived WHERE key='at_risk' AND value='true';
"""


def to_sqlite(g: Graph, path: str | Path = ":memory:") -> sqlite3.Connection:
    """Build the database. A file path is built beside its target and moved into place, so a build
    that fails — or a sync client holding the old file open — never leaves a half-written graph."""
    if path == ":memory:":
        con = sqlite3.connect(":memory:")
        _fill_sqlite(con, g)
        return con
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        con = sqlite3.connect(str(tmp))
        try:
            _fill_sqlite(con, g)
        finally:
            con.close()
        os.replace(tmp, p)
        return sqlite3.connect(str(p))
    except PermissionError as exc:
        raise SystemExit(f"cannot write {p}: another process holds it (a sync client such as OneDrive, or an open viewer) "
                         "— pause syncing or close it, then run the build again") from exc
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()


def _fill_sqlite(con: sqlite3.Connection, g: Graph) -> None:
    con.executescript(SCHEMA)
    con.execute("INSERT INTO meta VALUES ('built_at', ?)", (g.built_at,))
    con.execute("INSERT INTO meta VALUES ('nodes', ?)", (str(len(g.nodes)),))
    con.execute("INSERT INTO meta VALUES ('edges', ?)", (str(len(g.edges)),))
    for n in g.nodes.values():
        con.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)",
                    (n.id, n.kind, n.type, n.title, n.description, n.path, n.mtime, json.dumps(_json_safe(n.props), ensure_ascii=False)))
        for k, v in n.props.items():
            if isinstance(v, (dict,)):
                continue
            for x in (v if isinstance(v, list) else [v]):
                if x in (None, "") or isinstance(x, (dict, list)):
                    continue
                con.execute("INSERT INTO props VALUES (?,?,?)", (n.id, k, _iso(x)))
        for k, v in n.derived.items():
            con.execute("INSERT INTO derived VALUES (?,?,?)", (n.id, k, str(v).lower() if isinstance(v, bool) else str(v)))
    con.executemany("INSERT OR IGNORE INTO edges VALUES (?,?,?,?)", g.edges)
    con.executemany("INSERT INTO findings VALUES (?,?,?,?,?)", [(f.level, f.code, f.node, f.message, f.suggestion) for f in g.findings])
    con.commit()


def query_sqlite(con: sqlite3.Connection, sql: str, limit: int = 200) -> tuple[list[str], list[tuple]]:
    """Run one read-only statement.

    The prefix check is a friendly error, not the guard: SQLite accepts a WITH-prefixed
    INSERT/UPDATE/DELETE, so `WITH x AS (SELECT 1) DELETE FROM nodes` reads as a SELECT and
    deletes the table. `query_only` is what the read-only promise rests on, and it holds for
    whatever connection a caller passes in.
    """
    if not re.match(r"^\s*(select|with)\b", sql, re.I):
        raise ValueError("only SELECT / WITH queries are allowed")
    was_query_only = con.execute("PRAGMA query_only").fetchone()[0]
    con.execute("PRAGMA query_only = ON")
    try:
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description or []]
        return cols, cur.fetchmany(limit)
    finally:
        con.execute(f"PRAGMA query_only = {'ON' if was_query_only else 'OFF'}")


# ---------------------------------------------------------------- artifacts

def write_artifacts(vault: Path, g: Graph, onto: Ontology, out_dir: Path | None = None) -> dict[str, Path]:
    vault = Path(vault)
    out = Path(out_dir) if out_dir else vault / KIT_DIR
    out.mkdir(parents=True, exist_ok=True)
    context = load_context(vault, onto)
    base = onto.base_iri
    paths = {
        "json": out / "graph.json",
        "nt": out / "graph.nt",
        "jsonld": out / "graph.jsonld",
        "sqlite": out / "graph.sqlite",
    }
    paths["json"].write_text(json.dumps(to_json(g), indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    paths["nt"].write_text("\n".join(to_ntriples(g, context, base)) + "\n", encoding="utf-8", newline="\n")
    paths["jsonld"].write_text(json.dumps(to_jsonld(g, context, base), indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    to_sqlite(g, paths["sqlite"]).close()
    return paths


def ontology_report(g: Graph) -> kitlib.Report:
    rep = kitlib.Report(checked=sum(1 for n in g.nodes.values() if n.kind == "note"))
    for f in g.findings:
        if f.level == "error":
            rep.errors.append(f.render())
        elif f.level == "warning":
            rep.warnings.append(f.render())
    return rep
