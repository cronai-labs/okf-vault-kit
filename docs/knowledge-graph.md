# The vault as a knowledge graph — semantics, ontology and inference without a platform

**All four levels below ship.** They are written as levels because that is how the capability
layers — each one produces plain files the next reads, and you can stop at any of them — not
because the later ones are planned. Level 1 is `99-system/ontology.yml` and `context.jsonld`;
Level 2 is `kit.py graph build` and its four exports; Level 3 is the derived facts and integrity
findings in `kit.py validate`; Level 4 is the four `graph_*` MCP tools and `kit.py llm ask
--graph`. The engines section is genuinely optional, and the short list of open work is at the
end. The reconciliation layer built on top is [reconciliation.md](reconciliation.md).

The vault is already a graph: typed nodes (every note has a `type`), typed edges in frontmatter (`owner`, `project`, `area`, `people`, `sources`, `verified.by`), untyped edges in markdown links. Moving it into knowledge-engineering territory means making that graph explicit, giving the types and edges public meaning, and running a handful of rules over it — in four steps, each of which produces plain files any tool can read. A database enters only when a query needs one.

Dependency stance for every step: Python standard library + PyYAML (already required). RDF is written by hand (N-Triples is one line per fact), SQLite is built into Python, JSON-LD is JSON. No rdflib, owlready, networkx or graph server until a concrete question cannot be answered without them.

## Level 0 — the graph a vault already is

| Graph concept | In the vault |
|---|---|
| Node | one `.md` file; IRI = vault path |
| Node class | `type` |
| Typed edge | frontmatter property whose value is a markdown link or a name (`owner`, `project`, `area`, `people`) |
| Provenance edge | `generated.by`, `verified[].by`, `sources[].resource` |
| Untyped edge | markdown link in the body |
| Node attributes | every other property (`description`, `state`, `health`, `status`, `stale_after`, …) |

`kit.py validate` already checks the OKF layer; nothing checks that `owner` points at a `person` note. That is the first gap.

## Level 1 — vocabulary: an ontology file and a JSON-LD context

One file in the vault, `99-system/ontology.yml`, declares the classes (note types), their properties with **range** and **cardinality**, and the edge names — the schema the vault has been following informally:

```yaml
classes:
  person:   { extends: node, props: { role: text, team: text, next_1_1: date } }
  project:  { extends: node, props: { owner: {range: person, required: true}, area: {range: area}, people: {range: person, many: true}, health: {enum: [green, yellow, red]}, due: date } }
  decision: { extends: node, props: { project: {range: project}, owner: {range: person}, supersedes: {range: decision} } }
  concept:  { extends: knowledge }
  runbook:  { extends: knowledge }
  spec:     { extends: knowledge, props: { project: {range: project} } }
  knowledge: { extends: node, props: { status: {enum: [draft, stable, deprecated]}, stale_after: datetime, sources: {range: source, many: true} } }
edges:
  owner: { inverse: owns }
  project: { inverse: has_decision }
  supersedes: { transitive: true, inverse: superseded_by }
```

A second file at the vault root, `context.jsonld`, maps the property names to public vocabularies so the same frontmatter reads as linked data with no change to the notes:

- `title`, `description`, `date` → Dublin Core (`dcterms:title`, `dcterms:description`, `dcterms:date`)
- `person` → `schema:Person`, `team` → `schema:Organization`, `project` → `schema:Project`, `meeting` → `schema:Event`
- `generated.by` → `prov:wasAttributedTo`, `generated.at` → `prov:generatedAtTime`, `verified` → a `prov:Activity` of type validation, `sources` → `prov:wasDerivedFrom` / `schema:citation`
- `concept`, `tags` → SKOS (`skos:Concept`, `skos:subject`)

OKF v0.2 is already a provenance vocabulary in disguise; the context just makes that formal. Because OKF consumers tolerate unknown keys, `@id`/`@type` never need to appear in the files — the context supplies them from `type` and the path.

Deliverable: `kit.py validate --ontology` reports range and cardinality violations ("`owner` of search-relaunch.md must be a person note", "decision without a project") alongside the OKF checks.

## Level 2 — extraction: the graph as files and as SQLite

`kit.py graph` walks the vault once and writes deterministic artifacts:

- `graph.json` — `{nodes: [{id, type, title, props}], edges: [{src, pred, dst}]}`; the interchange format for anything else (D3 page, Gephi, a script)
- `graph.nt` — N-Triples, one `<subject> <predicate> <object> .` per line, IRIs from a base you choose plus the vault path; loadable into any RDF store or SPARQL engine without conversion
- `.kit/graph.sqlite` — three tables (`nodes`, `edges`, `props`) plus views for **trust tier** (unverified / machine-confirmed / human-reviewed, from `verified`) and **freshness** (`stale_after` vs. now)

Recursive CTEs make SQLite a perfectly good graph database at vault scale (thousands of notes): "every note that depends, transitively, on concept X", "decisions that touch project P through any edge", "people connected to more than three at-risk projects". `kit.py graph query "<sql>"` exposes it; `kit.py graph neighbors <note> --depth 2` is the shortcut.

## Level 3 — semantics: a dozen rules, not OWL

The rules live next to the schema in `ontology.yml` and run as SQL or Python passes after extraction:

- **inverse edges** (`owner` → `owns`) and **transitivity** (`supersedes`)
- **inheritance** (`runbook` is a `knowledge` node, inherits `stale_after` semantics)
- **derived facts**: trust tier; `at_risk` = knowledge that cites a source or concept that is stale/deprecated; `superseded` state on decisions that something supersedes; project health "yellow" when any open decision is past its date
- **integrity**: every project has an owner; no decision without a project or area; no `verified` on a note whose `generated.by` is not human unless a human verified it

Findings go into the validate report and, optionally, into `log.md`. This is the honest scope of "ontology" for a personal or team vault: a schema plus derived facts you can read in one screen. Full OWL reasoning brings a reasoner, a licence discussion and a class of bugs nobody here needs.

## Level 4 — serving it to the model (GraphRAG-lite)

This is the level that changes what a small model can do, and it is the reason the three below it
are worth the effort.

The bridge registers four graph tools — `graph_context(note)`, `graph_neighbors(note, depth,
edge_types)`, `graph_path(a, b)` and `graph_query(sql)` — and `kit.py llm ask --graph` assembles
the same facts into the prompt. Instead of retrieving prose and guessing at relationships, the
model is handed them. A context pack, which is literally what it receives:

```text
# Search relaunch (example)
- type: project        - state: active       - health: yellow      - due: 2026-09-18
- at_risk (derived): False    - stale (derived): False    - trust_tier (derived): unverified

## Relations
- area: Platform engineering (04-areas/platform-engineering.md) [area, state=active]
- has_item: Adopt qmd for local search (06-decisions/…) [decision, state=decided];
            Search relaunch — pilot spec (07-knowledge/…) [spec, status=draft]
- links_to: Hybrid search (07-knowledge/hybrid-search.md) [concept, status=stable]
```

Every relation arrives typed and with the other note's own state attached, so "who owns this and
what is at risk" is a lookup rather than an inference. That matters most exactly where a 2B model
is weakest: owners, dates, states and the difference between a decided decision and a draft spec.

`kit.py graph pack <note>` prints a pack; `kit.py llm ask --graph --dry-run` prints the whole
prompt, facts included, without calling the model. The generated OKF `index.md` is a rendered view
of the same graph.

## When to bring in an engine

| Need | Tool | Why it fits the "simple, transparent" bar |
|---|---|---|
| SPARQL over the RDF export | Oxigraph | single Rust binary, embedded or server, loads `graph.nt` directly |
| Cypher, embedded | Kuzu | pip wheel, in-process, no server |
| Datalog rules at scale | Cozo | single binary, rules as data |
| Multi-user team graph | Neo4j / Fuseki | only when several people query concurrently |
| Pictures | Obsidian graph view, or `graph.json` → a self-contained D3 page, or Gephi | nothing to install for the first two |

None of them are prerequisites; all of them consume the Level 2 files unchanged.

## What the implementation does today

```bash
uv run kit.py validate                       # + ontology section: ranges, enums, required, unresolved/ambiguous refs, aliases
uv run kit.py graph build                    # .kit/graph.json, graph.nt, graph.jsonld, graph.sqlite (+ findings)
uv run kit.py graph query "select id from v_at_risk"
uv run kit.py graph query "with recursive dep(id) as (select '07-knowledge/hybrid-search.md' union select e.src from edges e join dep on e.dst=dep.id where e.pred in ('links_to','cites','project')) select id from dep"
uv run kit.py graph neighbors alex-example --pred owns,involved_in
uv run kit.py graph path hybrid-search alex-example
uv run kit.py graph pack search-relaunch     # the context pack a model gets
uv run kit.py graph export --format jsonld --out vault.jsonld
```

Nodes: every note (`kind: note`), the vault owner (`self`, a virtual person that `me` and your `human:` actor resolve to), other actors from `generated.by`/`verified.by`, and sources from `sources[].resource` (shared across notes that cite the same URL). Edges: one per `ref` property (owner, people, project, area, team, supersedes), `links_to` from body links, `has_source` and `cites`, `generated_by`, `verified_by`, plus the inverses from `ontology.yml` and the transitive closure of `supersedes`. Derived per note: `trust_tier`, `stale`, `status_effective`, `at_risk`, `superseded_by`. Findings: `missing_required`, `enum_violation`, `type_violation`, `range_violation`, `cardinality`, `unresolved_ref` (with fuzzy suggestions), `ambiguous_ref`, `property_alias`, `unknown_type`, `unused_source`, `at_risk`, `project_at_risk`, `state_conflict`, `decision_without_context`.

Resolution order for a reference: markdown link path → wikilink stem → exact title/alias (unique first names of people count as aliases) → range preference when several notes share a title → fuzzy suggestion (difflib) when nothing matches. `me`, `self` and your own actor id resolve to the virtual `self` node.

## Order of work

1. ~~`ontology.yml` + `--ontology` validation~~ done.
2. ~~`kit.py graph` with JSON + N-Triples + JSON-LD + SQLite~~ done, tested on the sample vault.
3. ~~Rules and derived facts, surfaced in validate~~ done (a dozen, toggled in `ontology.yml`).
4. ~~Bridge tools and context packs~~ done — four tools plus `llm ask --graph`. Still open: measure with the qmd bench whether grounded answers actually improve, rather than assuming they do.
5. `context.jsonld` exists; open: a SPARQL smoke test through Oxigraph over `graph.nt` — the point where the vault is a first-class linked-data artifact.
6. Open: entity resolution across vaults (two people's vaults naming the same project), and a `graph diff` between two builds for change review.

If the aim is also to present this somewhere: "an OKF bundle that is simultaneously a PROV/JSON-LD graph, maintained by a local 2B model through MCP" is a concrete, reproducible artifact — the test suite and the retrieval bench are the evidence a knowledge-engineering audience (SEMANTiCS, ISWC workshops, the Knowledge Graph Conference) expects.
