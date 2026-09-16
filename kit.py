#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""kit — the OKF Vault Kit command line (macOS, Windows, Linux; Python 3.11+).

  python kit.py doctor                      environment and tooling check
  python kit.py init --target ~/Notes/vault --actor human:you
  python kit.py validate [--vault DIR]      OKF conformance + link check
  python kit.py index [--vault DIR]         regenerate the OKF root index.md
  python kit.py mcp-config --client lmstudio --vault ~/Notes/vault [--write]
  python kit.py llm smoke                   ping the local model endpoint
  python kit.py llm ask "question"          qmd search -> local LLM, with citations
  python kit.py log "message"               append to the vault's OKF log.md
  python kit.py test                        run the end-to-end test suite
  python kit.py graph build|export|query|neighbors|path|pack   the vault as an explicit graph (.kit/graph.*)
  python kit.py reconcile [--apply]         hub tables vs. notes, derived-state conflicts
  python kit.py todos [--digest] [--sync-done]   surface and reconcile every open task
  python kit.py minutes recap.txt --title "Sync"  file a recap as a meeting note
  python kit.py proposals list|show|apply|reject   human-in-the-loop writes from a --propose bridge (docs/security.md)

Every command also runs as `uv run kit.py ...` with no manual dependency setup (inline script metadata above).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import kitgraph  # noqa: E402
import kitlib  # noqa: E402
import kitproviders  # noqa: E402
import kitrecon  # noqa: E402
import kitredact  # noqa: E402

TEMPLATE_VAULT = ROOT / "vault"
QMD_MASK = "{*.md,!(_*)/**/*.md}"   # every markdown file except the tool-private `_`-prefixed folders
DEFAULT_BASE_URL = os.environ.get("KIT_LLM_BASE_URL", "http://localhost:1234/v1")
DEFAULT_MODEL = os.environ.get("KIT_LLM_MODEL", "")
IS_WINDOWS = platform.system() == "Windows"


def _vault(arg: str | None) -> Path:
    p = Path(arg or os.environ.get("KIT_VAULT") or TEMPLATE_VAULT).expanduser().resolve()
    if not p.exists():
        sys.exit(f"vault not found: {p}")
    _warn_undecodable(p)
    return p


def _warn_undecodable(vault: Path) -> None:
    """Name the files every reader skips, once, before the command runs.

    `kitlib.load_vault` drops a note it cannot decode so that one file cannot take out the whole
    command; that is only safe because this line says it happened. Every vault command comes
    through `_vault`, so there is no path where a file goes missing unannounced. Paths are fine
    here — this is the user's own terminal, not the doctor report, which carries none.
    """
    skipped = kitlib.undecodable(vault)
    if not skipped:
        return
    more = f" (+{len(skipped) - 1} more)" if len(skipped) > 1 else ""
    print(f"warning: skipping {len(skipped)} file(s) that are not valid UTF-8: {skipped[0]}{more}"
          " — re-save as UTF-8 (PowerShell: `Set-Content -Encoding utf8`)", file=sys.stderr)


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, **kw)


def _qmd() -> str | None:
    """Absolute path to qmd, or None.

    Always spawn the resolved path: on Windows `CreateProcess` only appends `.exe`, so a
    `qmd.cmd` shim — which is exactly what `npm install -g` produces — is not found when the
    bare name is passed to subprocess.
    """
    return shutil.which("qmd")


# ---------------------------------------------------------------- doctor

def cmd_doctor(args) -> int:
    rows: list[tuple[str, str, str]] = []
    rows.append(("kit", kitlib.KIT_VERSION, "ok"))
    py_ok = sys.version_info >= (3, 11)
    rows.append(("python", sys.version.split()[0], "ok" if py_ok else "need 3.11+"))
    for tool, hint in [
        ("bun", "runtime for qmd: brew install oven-sh/bun/bun | winget install Oven-sh.Bun"),
        ("uv", "runs kit.py and the bridge with managed deps: brew install uv | winget install astral-sh.uv"),
        ("qmd", "bun install -g @tobilu/qmd   (npm install -g @tobilu/qmd also works)"),
        ("node", "optional — only if you install qmd through npm instead of bun"),
        ("obsidian", "official CLI: Obsidian 1.12+ → Settings → General → Command line interface → Register"),
        ("obsidian-cli", "yakitrak/obsidian-cli (brew tap yakitrak/yakitrak | scoop)"),
        ("lms", "LM Studio CLI — run LM Studio once, then `lms --help`"),
        ("unsloth", "Unsloth Desktop/Studio CLI (optional)"),
        ("ollama", "optional alternative endpoint"),
        ("rg", "ripgrep — fast grep for scripts"),
        ("fd", "fd — fast find for scripts"),
        ("jq", "jq — JSON in shell pipelines"),
    ]:
        path = _which(tool)
        rows.append((tool, path or "-", "ok" if path else hint))
    try:
        import yaml  # noqa: F401
        rows.append(("pyyaml", "installed", "ok"))
    except ImportError:
        rows.append(("pyyaml", "-", "pip install pyyaml"))
    # Ask the package database, not the import system. This repository has a directory named
    # `mcp/`, so from the repo root `import mcp` succeeds as a namespace package and reports the
    # SDK present on exactly the machines where it is absent.
    try:
        _mcp_version = importlib.metadata.version("mcp")
        if _mcp_version.split(".")[0] == "2":
            rows.append(("mcp (python sdk)", _mcp_version, "ok"))
        else:
            rows.append(("mcp (python sdk)", _mcp_version,
                         "the bridge targets the 2.x API — pip install 'mcp>=2,<3'"))
    except importlib.metadata.PackageNotFoundError:
        rows.append(("mcp (python sdk)", "-", "pip install 'mcp>=2,<3' — needed only for the Obsidian MCP bridge"))
    base = args.base_url or DEFAULT_BASE_URL
    proxies = kitproviders.proxy_vars()
    if proxies:
        loopback = kitproviders.is_loopback(base)
        rows.append(("proxy", ", ".join(proxies),
                     "set — the kit calls the endpoint directly (loopback bypasses it)" if loopback else
                     f"set — {base} is not loopback, so the kit's model calls go through it; add the host to NO_PROXY to keep them local"))
    try:
        with kitproviders.urlopen(f"{base}/models", timeout=3) as r:
            data = json.load(r)
            ids = [m.get("id") for m in data.get("data", [])]
            rows.append(("llm endpoint", base, f"ok — {len(ids)} model(s): {', '.join(ids[:3])}"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("llm endpoint", base, f"not reachable ({exc.__class__.__name__}) — start LM Studio's server or set KIT_LLM_BASE_URL"))
    vault_for_cfg = Path(args.vault or os.environ.get("KIT_VAULT") or TEMPLATE_VAULT).expanduser()
    try:
        provider_rows = kitproviders.status(vault_for_cfg if vault_for_cfg.exists() else None, base)
    except SystemExit as exc:
        # A provider slot naming something we do not have is a diagnosis, not a reason to stop
        # diagnosing: doctor exists for exactly this misconfiguration, and the report is how a
        # tester reports it.
        provider_rows = [("search", "-", f"provider misconfigured: {exc}")]
    for slot, name, state in provider_rows:
        rows.append((f"  {slot}" if slot else "   ", name, state))
    vault = vault_for_cfg
    if vault.exists():
        rep = kitlib.validate_okf(vault)
        # Say when this is the shipped sample. Without it the row is identical for every tester
        # and cannot answer the one question it exists for: is my own vault healthy?
        sample = " — the kit's sample; pass --vault or set KIT_VAULT to describe yours" if vault == TEMPLATE_VAULT.resolve() else ""
        rows.append(("vault", str(vault), f"{rep.checked} notes, {len(rep.errors)} OKF error(s), {len(rep.warnings)} warning(s){sample}"))
    else:
        rows.append(("vault", str(vault), "not found — run `kit.py init`"))
    w = max(len(r[0]) for r in rows)
    lines = [f"{name.ljust(w)}  {status}" + (f"  [{where}]" if where not in ("-", "installed") else "")
             for name, where, status in rows]
    print("\n".join(lines))
    if args.report:
        out = Path(args.report).expanduser()
        out.write_text(_report(rows, vault), encoding="utf-8", newline="\n")
        print(f"\nreport written: {out}  — no note content in it; safe to send")
    return 0 if py_ok else 1


# The redactor lives in kitredact so `testkit/` can share it without importing this module or
# PyYAML — see kitredact's docstring. The private names stay as aliases: they are what the
# report code and its tests already call.
_SAFE_HOSTS = kitredact._SAFE_HOSTS
_URL_OR_PATH = kitredact._URL_OR_PATH
_TRAILING_PUNCT = kitredact._TRAILING_PUNCT
_basename = kitredact._basename
_redact_url = kitredact._redact_url
_redact_user = kitredact._redact_user
_scrub = kitredact.scrub


def _report(rows: list[tuple[str, str, str]], vault: Path) -> str:
    """A diagnosis someone can email us.

    Deliberately boring: versions, what is installed, which providers are active, whether the
    endpoint answers, and how many notes and errors. No note titles, no note content, and no paths
    from inside the vault — only its name, so we can tell "empty" from "1,200 notes". Every line
    goes through _scrub, the header included: the vault folder is often named after its owner.
    """
    redacted = [(name.strip(), "installed" if (where not in ("-", "installed", "") and Path(where).is_absolute()) else where, status)
                for name, where, status in rows]
    w = max(len(r[0]) for r in redacted)
    head = [
        f"okf-vault-kit {kitlib.KIT_VERSION} — doctor report",
        f"platform  {platform.system()} {platform.release()} ({platform.machine()})",
        f"python    {sys.version.split()[0]}",
        f"vault     {vault.name if vault.exists() else '(none)'}",
        f"proxy     {'set' if any(os.environ.get(v) for v in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy')) else 'not set'}",
        "",
    ]
    return _scrub("\n".join(head + [f"{n.ljust(w)}  {s}" + (f"  [{d}]" if d not in ("-", "installed", "") else "")
                                    for n, d, s in redacted])) + "\n"


# ---------------------------------------------------------------- init

def cmd_init(args) -> int:
    target = Path(args.target).expanduser().resolve()
    if target.exists() and any(target.iterdir()) and not args.force:
        sys.exit(f"{target} exists and is not empty (use --force to overwrite files)")
    shutil.copytree(TEMPLATE_VAULT, target, dirs_exist_ok=True)
    actor = args.actor
    if actor:
        if not kitlib.ACTOR.match(actor) or not actor.startswith("human:"):
            sys.exit("actor must look like human:<handle>")
        n = skipped = 0
        for p in kitlib.iter_markdown(target):
            text = kitlib.read_text(p)
            if text is None:
                # `--force` runs over a directory that already has files in it, so one of them can
                # be UTF-16. Rewriting bytes we cannot decode would corrupt the note; say so and
                # leave it alone. `kit.py validate` names it again, in full.
                skipped += 1
                continue
            # `by: human:me` — the frontmatter actor, not every occurrence of the token. A blind
            # replace rewrote the prose of 99-system/getting-started.md, whose whole job is to
            # tell the reader to replace `human:me`, into an instruction contradicting itself —
            # and would do the same to the user's own notes on a later `init --force`.
            if "by: human:me" in text:
                p.write_text(text.replace("by: human:me", f"by: {actor}"), encoding="utf-8", newline="\n")
                n += 1
        print(f"personalised {n} file(s) with {actor}"
              + (f" — {skipped} file(s) left untouched: not valid UTF-8" if skipped else ""))
    kitlib.write_index(target)
    collection = args.collection or kitproviders.suggest_collection_name(target)
    _write_kit_config(target, collection)
    print(f"vault ready: {target}")
    qmd = None if args.no_qmd else _qmd()
    if qmd:
        name = collection
        r = _run([qmd, "collection", "add", str(target), "--name", name, "--mask", QMD_MASK])
        said = r.stdout.strip() or r.stderr.strip()
        if said:
            print(said)
        if r.returncode != 0:
            print(f"qmd collection '{name}' NOT registered (qmd exited {r.returncode}) — search falls back to plain "
                  f'term frequency until you run: qmd collection add "{target}" --name {name} --mask "{QMD_MASK}"')
        else:
            c = _run([qmd, "context", "add", f"qmd://{name}", "Personal knowledge vault: journal, meetings, projects, people, decisions, knowledge notes (OKF frontmatter)."])
            if c.returncode != 0:
                print(f"qmd context for '{name}' not set (qmd exited {c.returncode}): {c.stderr.strip() or c.stdout.strip()}")
            print(f"qmd collection '{name}' registered — run `qmd embed` to enable semantic search")
            print("  the whole vault is indexed, templates and archive included — `qmd collection add` takes no "
                  "ignore list. To match config/qmd/index.example.yml, put this under the collection in qmd's "
                  "index.yml: ignore: [\".obsidian/**\", \"09-archive/**\", \"90-templates/**\"]")
    else:
        print(f'next: install qmd (docs/tools.md) and run `qmd collection add "{target}" '
              f'--name {collection} --mask "{QMD_MASK}"`')
    print("open the folder as a vault in Obsidian and read 99-system/getting-started.md")
    return 0


def _write_kit_config(vault: Path, collection: str) -> None:
    """Record what `init` chose, so later commands need not guess.

    The qmd collection name is the one that matters: without it `llm ask` queries the whole qmd
    index and answers from collections that have nothing to do with this vault.
    """
    path = vault / kitproviders.CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if "collection" not in kitproviders.read_config(vault):
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(f"collection: {json.dumps(collection)}\n")
        return
    path.write_text(f"# written by `kit.py init` — hand edits are fine (docs/tools.md)\n"
                    f"collection: {json.dumps(collection)}   # the qmd collection `kit.py llm ask` searches\n",
                    encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- index / validate / log

def cmd_index(args) -> int:
    out = kitlib.write_index(_vault(args.vault))
    print(f"wrote {out}")
    return 0


def cmd_validate(args) -> int:
    vault = _vault(args.vault)
    okf = kitlib.validate_okf(vault)
    links = kitlib.check_links(vault)
    print("== OKF conformance ==")
    print(okf.render())
    print("== Links, wikilinks, Bases embeds ==")
    print(links.render())
    hygiene = [links]
    use_onto = not args.no_ontology and (args.ontology or kitgraph.ontology_path(vault) is not None)
    if use_onto:
        g = kitgraph.build_graph(vault)
        onto = kitgraph.ontology_report(g)
        print("== Ontology: ranges, enums, required, references, derived facts ==")
        print(onto.render())
        hygiene.append(onto)

    # OKF v0.2 §11 lists what a consumer MUST NOT reject a bundle for, and broken cross-links and
    # a missing index.md are both on that list. So conformance alone decides the default exit
    # code: a bundle can be conformant and still be a mess, and only the first is ours to assert.
    # Our own rules are gated by --strict, which is what `make lint` and CI run.
    conformance_failed = bool(okf.errors)
    hygiene_failed = any(r.errors or r.warnings for r in hygiene) or bool(okf.warnings)
    if hygiene_failed and not args.strict:
        print("\nnote: the findings above outside `OKF conformance` are this kit's rules, not the "
              "spec's — OKF v0.2 §11 forbids rejecting a bundle for them, so they do not set the "
              "exit code. Run `validate --strict` to fail on them too (this is what CI runs).")
    return 1 if conformance_failed or (args.strict and hygiene_failed) else 0


def cmd_log(args) -> int:
    vault = _vault(args.vault)
    if kitlib.append_log(vault, args.message, args.kind) is None:
        print("log.md is not valid UTF-8 — nothing was written; re-save it as UTF-8 and log again")
        return 1
    print(f"logged under {dt.date.today().isoformat()}: * **{args.kind}**: {args.message}")
    return 0


# ---------------------------------------------------------------- mcp-config

def _mcp_config_path(client: str) -> Path:
    home = Path.home()
    if client == "lmstudio":
        return home / ".lmstudio" / "mcp.json"
    if client == "claude-desktop":
        if platform.system() == "Darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        if IS_WINDOWS:
            return Path(os.environ.get("APPDATA", home)) / "Claude" / "claude_desktop_config.json"
        return home / ".config" / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    raise ValueError(client)


def _is_wsl() -> bool:
    """True inside WSL, where `Path.home()` is the Linux home and the Windows client cannot read it."""
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False


def cmd_mcp_config(args) -> int:
    vault = _vault(args.vault)
    if vault == TEMPLATE_VAULT.resolve():
        if args.write:
            sys.exit("that vault is the kit's own sample, not yours — the client would search and write the copy "
                     "inside this checkout. Pass your own: --vault ~/Notes/vault")
        template_warning = (f"# WARNING: this config serves the kit's own sample vault ({vault}) — the client "
                            "would answer from the sample notes and write into this checkout. Point it at yours: "
                            "--vault ~/Notes/vault.")
    else:
        template_warning = ""
    bridge = ROOT / "mcp" / "obsidian_bridge.py"
    notes: list[str] = []
    servers: dict = {}
    # qmd's own MCP server has no policy layer: it indexes the whole vault, confidential notes
    # included, and answers `query`/`get` straight from that index. Registering it alongside the
    # bridge would hand the model a second door into exactly what the bridge hides, and
    # `qmd collection add` takes no ignore list, so there is no mask that fixes it. The bridge's
    # own `obsidian_search` serves search and does honour the policy. Opt in knowingly or not at all.
    if args.with_qmd_mcp:
        if args.qmd_http:
            servers["qmd"] = {"url": "http://localhost:8181/mcp"}
        else:
            qmd = _qmd()              # resolved path: the client spawns this, and a .cmd shim needs the full name
            if not qmd:
                notes.append("# qmd is not on PATH, so its entry says \"qmd\" — an MCP client on Windows cannot spawn "
                             "that (the installer writes qmd.cmd and CreateProcess only appends .exe). Install qmd and "
                             "re-run this command, or put the full path to qmd(.cmd) in the entry by hand.")
            servers["qmd"] = {"command": qmd or "qmd", "args": ["mcp"]}
        notes.append("# WARNING: the qmd server below is NOT behind this kit's policy layer. It indexes every note, "
                     "including sensitivity: confidential, and answers from that index — so docs/security.md's "
                     "confidential-hiding does not hold for the tools it exposes. Drop --with-qmd-mcp to remove it.")
    else:
        notes.append("# qmd's MCP server is not registered: it has no policy layer and would expose the confidential "
                     "notes the bridge hides (docs/security.md). Search still works — the bridge's obsidian_search "
                     "uses the same qmd index and applies the policy. Pass --with-qmd-mcp to register it anyway.")
    bridge_args = [str(bridge), "--backend", args.backend, "--vault", str(vault)]
    uv = None if (args.no_uv or os.environ.get("KIT_NO_UV")) else _which("uv")
    if uv:
        # uv provisions pyyaml + mcp from the bridge's inline metadata; nothing to pip-install
        servers["obsidian-vault"] = {"command": uv, "args": ["run", *bridge_args]}
    else:
        servers["obsidian-vault"] = {"command": sys.executable, "args": bridge_args}
    if args.backend == "cli" and args.vault_name:
        servers["obsidian-vault"]["args"] += ["--vault-name", args.vault_name]
    if args.bridge_http:
        servers["obsidian-vault"] = {"url": f"http://127.0.0.1:{args.bridge_port}/mcp"}
        if args.bridge_token:
            servers["obsidian-vault"]["headers"] = {"Authorization": f"Bearer {args.bridge_token}"}
    if args.bridge_flags:
        servers["obsidian-vault"].setdefault("args", []).extend(args.bridge_flags.split())
    cfg = {"mcpServers": servers}
    text = json.dumps(cfg, indent=2)
    path = _mcp_config_path(args.client)
    wsl_lmstudio = args.client == "lmstudio" and _is_wsl()
    if wsl_lmstudio:
        notes.append(f"# WSL detected: {path} is inside WSL, and LM Studio running on Windows never reads it. "
                     "Paste this JSON into C:\\Users\\<you>\\.lmstudio\\mcp.json instead, merging it with the "
                     "servers already there. Nothing here can write that file for you.")
    if not args.write:
        print(text)
        print(f"\n# would be written to: {path}  (add --write)")
        for note in notes:
            print(note)
        if template_warning:
            print(template_warning)
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sys.exit(f"{path} is not valid JSON — fix or move it first")
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        print(f"backup: {backup}")
    existing.setdefault("mcpServers", {}).update(servers)
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8", newline="\n")
    print(f"wrote {path} — restart the client so it picks up the servers")
    print(f"  vault served: {vault}")
    for note in notes:
        print(note)
    if wsl_lmstudio:
        print(text)
    return 0


# ---------------------------------------------------------------- graph

def _graph(vault: Path) -> kitgraph.Graph:
    return kitgraph.build_graph(vault)


def cmd_graph_build(args) -> int:
    vault = _vault(args.vault)
    onto = kitgraph.load_ontology(vault)
    g = kitgraph.build_graph(vault, onto)
    paths = kitgraph.write_artifacts(vault, g, onto, Path(args.out) if args.out else None)
    notes = sum(1 for n in g.nodes.values() if n.kind == "note")
    print(f"graph: {len(g.nodes)} nodes ({notes} notes), {len(g.edges)} edges, {len(g.findings)} finding(s)")
    for f in g.findings:
        print("  " + f.render())
    for k, p in paths.items():
        print(f"  {k:7} {p}")
    return 0


def cmd_graph_export(args) -> int:
    vault = _vault(args.vault)
    onto = kitgraph.load_ontology(vault)
    g = kitgraph.build_graph(vault, onto)
    ctx = kitgraph.load_context(vault, onto)
    if args.format == "json":
        text = json.dumps(kitgraph.to_json(g), indent=1, ensure_ascii=False)
    elif args.format == "nt":
        text = "\n".join(kitgraph.to_ntriples(g, ctx, onto.base_iri))
    else:
        text = json.dumps(kitgraph.to_jsonld(g, ctx, onto.base_iri), indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8", newline="\n"); print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_graph_query(args) -> int:
    vault = _vault(args.vault)
    db = vault / kitgraph.KIT_DIR / "graph.sqlite"
    if args.rebuild or not db.exists():
        onto = kitgraph.load_ontology(vault)
        kitgraph.write_artifacts(vault, kitgraph.build_graph(vault, onto), onto)
    import sqlite3
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)  # as_uri(): drive letters and spaces are not URI-safe
    try:
        cols, rows = kitgraph.query_sqlite(con, args.sql, args.limit)
    except (ValueError, sqlite3.Error) as exc:
        print(f"query error: {exc}"); return 1
    if args.json:
        print(json.dumps([dict(zip(cols, r, strict=True)) for r in rows], ensure_ascii=False, indent=1))
    else:
        print("\t".join(cols))
        for r in rows:
            print("\t".join("" if v is None else str(v) for v in r))
    return 0


def cmd_graph_neighbors(args) -> int:
    g = _graph(_vault(args.vault))
    preds = [p.strip() for p in args.pred.split(",")] if args.pred else None
    rows = kitgraph.neighbors(g, args.node, args.depth, preds)
    if not rows:
        print("no node or no neighbours"); return 1
    for src, p, dst, d in rows:
        print(f"{'  ' * (d - 1)}{src} —{p}→ {dst}")
    return 0


def cmd_graph_path(args) -> int:
    g = _graph(_vault(args.vault))
    path = kitgraph.shortest_path(g, args.a, args.b)
    if not path:
        print("no path"); return 1
    for src, p, dst in path:
        print(f"{src} —{p}→ {dst}")
    return 0


def cmd_graph_pack(args) -> int:
    g = _graph(_vault(args.vault))
    text = kitgraph.context_pack(g, args.node, args.depth)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n"); print(f"wrote {args.out}")
    else:
        print(text)
    return 0


# ---------------------------------------------------------------- reconcile / todos / minutes

def cmd_reconcile(args) -> int:
    vault = _vault(args.vault)
    changes = kitrecon.reconcile(vault, apply=args.apply)
    if args.json:
        print(json.dumps([c.__dict__ for c in changes], indent=1, ensure_ascii=False))
    elif not changes:
        print("hubs and notes agree; nothing to reconcile")
    else:
        for c in changes:
            print(c.render())
        if not args.apply and any(c.code in ("hub_drift", "hub_missing_row", "state_conflict") for c in changes):
            print("\nrun again with --apply to update hub rows and derived states (notes are the source of truth)")
    return 0 if args.apply or not changes else 2


def cmd_todos(args) -> int:
    vault = _vault(args.vault)
    rep = kitrecon.todo_report(vault, sync_done=args.sync_done)
    owner = args.owner.lower() if args.owner else ""

    def mine(t) -> bool:
        return not owner or (t.owner or "").lower() == owner

    tasks = [t for t in (rep.tasks if args.all else rep.open) if mine(t)]
    if args.json:
        print(json.dumps([t.__dict__ for t in tasks], indent=1, ensure_ascii=False))
    else:
        # the filter applies here too: a summary that ignores --owner reads as "nothing is assigned to you"
        overdue = [t for t in rep.overdue if mine(t)]
        due_soon = [t for t in rep.due_soon if mine(t)]
        duplicates = [grp for grp in rep.duplicates if any(mine(t) for t in grp)]
        conflicts = [grp for grp in rep.done_conflicts if any(mine(t) for t in grp)]
        open_tasks = [t for t in rep.open if mine(t)]
        all_tasks = [t for t in rep.tasks if mine(t)]
        scope = f" for owner {args.owner}" if owner else ""
        print(f"{len(open_tasks)} open / {len(all_tasks)} tasks{scope} — {len(overdue)} overdue, {len(due_soon)} due within 7 days, "
              f"{len(duplicates)} duplicated, {len(conflicts)} done/open conflict(s)")
        for label, items in (("OVERDUE", overdue), ("DUE SOON", due_soon)):
            for t in items:
                print(f"  {label:9} {t.due}  {t.text}  ({t.owner or '-'})  {t.file}:{t.line}")
        for grp in conflicts:
            print(f"  CONFLICT  {grp[0].text}  in " + ", ".join(f"{t.file}:{t.line}{'(done)' if t.done else ''}" for t in grp))
        if rep.synced:
            print(f"  synced {len(rep.synced)} task(s) to done")
        for skip in rep.skipped:
            print(f"  skipped: {skip}")
    if args.digest is not None:
        out = kitrecon.write_digest(vault, rep, args.digest or kitrecon.DIGEST)
        print(f"digest written: {out}")
    return 0


def _decode_recap(data: bytes, source: str) -> str:
    """Decode a recap as the field writes one: UTF-8 with or without a BOM, or UTF-16 from PowerShell.

    The bytes are decoded here rather than by the stream so that neither a Windows code page
    nor a leading BOM can reach the vault; anything else fails with a message, not a traceback.
    """
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        sys.exit(f"{source} is not UTF-8 or UTF-16 text (byte 0x{data[exc.start]:02x} at offset {exc.start}); re-save it as UTF-8")


def cmd_minutes(args) -> int:
    vault = _vault(args.vault)
    if args.file == "-":
        text = _decode_recap(sys.stdin.buffer.read(), "the recap on stdin")
    else:
        text = _decode_recap(Path(args.file).read_bytes(), args.file)
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    people = [p.strip() for p in args.people.split(",")] if args.people else None
    extra = None
    if args.llm:
        extra = _llm_structure(text, args.base_url or DEFAULT_BASE_URL, args.model or DEFAULT_MODEL)
    try:
        m = kitrecon.file_minutes(vault, text, args.title, date, args.project, people, args.kind,
                                  dry_run=args.dry_run, force=args.force, daily=args.daily, extra=extra)
    except FileExistsError as exc:
        print(exc); return 1
    print(f"{'would write' if args.dry_run else 'wrote'} {m.path}")
    print(f"  people: {m.people or '-'}   project: {m.project or '-'}   unresolved: {m.unresolved or '-'}")
    print(f"  {len(m.outcomes)} outcomes, {len(m.decisions)} decisions, {len(m.actions)} actions")
    for a in m.actions:
        print("  " + a)
    for skip in m.skipped:
        print(f"  skipped: {skip}")
    return 0


def _llm_structure(text: str, base: str, model: str) -> dict | None:
    prompt = ("Extract from this meeting recap. Reply with JSON only, no prose, shaped exactly as "
              '{"outcomes": [..], "decisions": [..], "actions": [{"text": "...", "owner": "...", "due": "YYYY-MM-DD or empty"}]}.\n\n' + text)
    try:
        raw = _chat(base, model, [{"role": "system", "content": "You extract structured minutes. JSON only."}, {"role": "user", "content": prompt}], max_tokens=800)
        data = json.loads(re.sub(r"```(?:json)?|```", "", raw).strip())
    except Exception as exc:  # noqa: BLE001
        print(f"  (LLM structuring skipped: {exc.__class__.__name__}: {exc})")
        return None
    actions = []
    for a in data.get("actions", []):
        if isinstance(a, dict) and a.get("text"):
            meta = ", ".join(x for x in (str(a.get("owner") or ""), f"due {a['due']}" if a.get("due") else "") if x)
            actions.append(f"- [ ] {a['text'].strip().rstrip('.')}" + (f" — {meta}" if meta else ""))
        elif isinstance(a, str):
            actions.append(f"- [ ] {a.strip()}")
    return {"outcomes": [str(x) for x in data.get("outcomes", [])], "decisions": [str(x) for x in data.get("decisions", [])], "actions": actions}


# ---------------------------------------------------------------- examples

def _sample_notes(vault: Path) -> list[kitlib.Note]:
    """Notes the vault ships to demonstrate itself, marked machine-readably.

    `example: true` in frontmatter is the boundary. The `(example)` suffix in a title is its
    human rendering and is deliberately not the marker: it was one of four inconsistent ways the
    set used to be signalled, and a person following "delete the (example) notes" left two
    fake colleagues behind and broke fifteen links.
    """
    return [n for n in kitlib.load_vault(vault)
            if isinstance(n.frontmatter, dict) and n.frontmatter.get("example") is True]


def _lines_referencing(path: Path, targets: set[Path], vault: Path) -> list[int]:
    """Indices of lines carrying a markdown link to any target, code fences and spans excluded.

    Whole-line removal is right for every shape these references take — a list item, a table row,
    a numbered priority — and a link inside a code span is a naming example, not a reference, so
    `99-system/conventions.md` keeps the filename it teaches with.
    """
    out: list[int] = []
    in_fence = False
    for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines()):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        bare = kitlib.CODE_SPAN_RE.sub("", line)
        for m in list(kitlib.MD_LINK_RE.finditer(bare)) + list(kitlib.MD_IMAGE_RE.finditer(bare)):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#", "obsidian://")):
                continue
            target = urllib.parse.unquote(target.split("#", 1)[0])
            if not target:
                continue
            resolved = (vault / target.lstrip("/")) if target.startswith("/") else (path.parent / target)
            with contextlib.suppress(OSError):
                if resolved.resolve() in targets:
                    out.append(i)
                    break
    return out


def _dead_names(samples: list[kitlib.Note]) -> set[str]:
    """Every spelling by which a frontmatter property can name a note that is about to go."""
    names: set[str] = set()
    for n in samples:
        fm = n.frontmatter or {}
        names.add(n.path.stem.lower())
        for key in ("title", "name"):
            if isinstance(fm.get(key), str):
                names.add(fm[key].lower())
        aliases = fm.get("aliases")
        if isinstance(aliases, list):
            names.update(str(a).lower() for a in aliases if str(a).strip())
    return {re.sub(r"\s*\(example\)\s*$", "", name).strip() for name in names if name.strip()}


def _frontmatter_repairs(note: kitlib.Note, dead: set[str]) -> dict[str, Any]:
    """List-valued properties with their dead entries dropped.

    The ontology resolves `people: [Alex Example]` by title, so deleting the note leaves the
    property pointing at nothing and `validate --strict` red — which is the whole failure this
    command exists to prevent. Only lists are pruned: a scalar reference cannot be removed
    without deciding whether the key is required, and none of the shipped sample set has one.
    """
    out: dict[str, Any] = {}
    for key, value in (note.frontmatter or {}).items():
        if not isinstance(value, list):
            continue
        kept = [v for v in value
                if not (isinstance(v, str) and re.sub(r"\s*\(example\)\s*$", "", v).strip().lower() in dead)]
        if len(kept) != len(value):
            out[key] = kept
    return out


def cmd_examples(args) -> int:
    vault = _vault(args.vault)
    if args.action != "remove":
        return 2
    samples = _sample_notes(vault)
    if not samples:
        print("no notes are marked `example: true` — nothing to remove")
        return 0
    targets = {n.path.resolve() for n in samples}

    dead = _dead_names(samples)
    repairs: dict[Path, list[int]] = {}
    fm_repairs: dict[Path, dict[str, Any]] = {}
    for note in kitlib.load_vault(vault):
        if note.path.resolve() in targets:
            continue
        hits = _lines_referencing(note.path, targets, vault)
        if hits:
            repairs[note.path] = hits
        props = _frontmatter_repairs(note, dead)
        if props:
            fm_repairs[note.path] = props

    print(f"{len(samples)} sample note(s) to delete:")
    for n in sorted(samples, key=lambda x: x.rel):
        print(f"  - {n.rel}")
    if repairs:
        total = sum(len(v) for v in repairs.values())
        print(f"{total} referring line(s) to remove, in {len(repairs)} file(s):")
        for path in sorted(repairs):
            for i in repairs[path]:
                line = path.read_text(encoding="utf-8-sig").splitlines()[i].strip()
                print(f"  - {path.relative_to(vault).as_posix()}:{i + 1}  {line[:90]}")
    if fm_repairs:
        print(f"{len(fm_repairs)} file(s) with frontmatter entries to prune:")
        for path in sorted(fm_repairs):
            for key, value in fm_repairs[path].items():
                print(f"  - {path.relative_to(vault).as_posix()}  {key}: -> {value}")
    print("index.md will be regenerated.")
    if args.dry_run:
        print("\ndry run — nothing was changed. Re-run without --dry-run to apply.")
        return 0

    for path, hits in repairs.items():
        drop = set(hits)
        text = path.read_text(encoding="utf-8-sig")
        kept = [ln for i, ln in enumerate(text.splitlines()) if i not in drop]
        path.write_text("\n".join(kept) + ("\n" if text.endswith("\n") else ""),
                        encoding="utf-8", newline="\n")
    for path, props in fm_repairs.items():
        kitlib.set_frontmatter(path, props)
    for n in samples:
        n.path.unlink()
    kitlib.write_index(vault)
    kitlib.append_log(vault, f"Removed {len(samples)} example note(s) and their references.", "Update")
    print(f"\nremoved {len(samples)} note(s); repaired {len(repairs) + len(fm_repairs)} file(s); index regenerated")
    print("run `kit.py validate --strict` to confirm")
    return 0


# ---------------------------------------------------------------- proposals (human-in-the-loop writes)

def _bridge_modules():
    sys.path.insert(0, str(ROOT / "mcp"))
    import bridge_policy
    import obsidian_bridge
    return bridge_policy, obsidian_bridge


def cmd_proposals(args) -> int:
    bridge_policy, obsidian_bridge = _bridge_modules()
    try:
        return _proposals(args, bridge_policy, obsidian_bridge)
    except bridge_policy.PolicyError as exc:
        # Refusing a proposal at apply time is what the policy layer is for; docs/security.md
        # advertises this path, so it reports rather than crashes.
        print(f"refused: {exc}", file=sys.stderr)
        return 1


def _proposals(args, bridge_policy, obsidian_bridge) -> int:
    vault = _vault(args.vault)
    store = bridge_policy.ProposalStore(vault / kitgraph.KIT_DIR / "proposals")
    if args.action == "list":
        items = store.pending()
        if not items:
            print("no pending proposals"); return 0
        for rec in items:
            a = rec["args"]; target = a.get("file") or a.get("name") or a.get("title") or "today's daily note"
            preview = str(a.get("content") or a.get("text") or a.get("value") or "").replace("\n", " ")[:80]
            print(f"{rec['id']}  {rec['tool']:13} → {target}  {preview!r}")
        return 0
    if args.action == "show":
        _, rec = store.get(args.id); print(json.dumps(rec, indent=1, ensure_ascii=False)); return 0
    if args.action == "apply":
        backend = obsidian_bridge.FsBackend(vault)
        out = store.apply(args.id, backend)
        kitlib.append_log(vault, f"Applied proposal {args.id} ({out})", "Update")
        print(f"applied {args.id}: {out}"); return 0
    if args.action == "reject":
        store.reject(args.id, getattr(args, "reason", None) or ""); print(f"rejected {args.id}"); return 0
    return 2


# ---------------------------------------------------------------- llm

SMOKE_TOKEN = "KIT-OK"
SMOKE_ANSWER_TOKENS = 64     # six characters of answer; kitproviders adds the thinking headroom


def _chat(base_url: str, model: str, messages: list[dict], temperature: float = 0.2,
          max_tokens: int = kitproviders.ANSWER_TOKENS) -> str:
    return kitproviders.llm_provider(base_url).chat(model, messages, temperature, max_tokens)


def _complete(base_url: str, model: str, messages: list[dict], temperature: float = 0.2,
              max_tokens: int = kitproviders.ANSWER_TOKENS) -> kitproviders.ChatResult:
    return kitproviders.llm_provider(base_url).complete(model, messages, temperature, max_tokens)


def _normalised(text: str) -> str:
    """Letters and digits only, folded to lower case — what a smoke answer is compared on."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _smoke_passed(answer: str) -> bool:
    """True when `answer` is the smoke token, allowing for a small model's phrasing.

    The question smoke asks is whether the endpoint completes, not whether a 2B model punctuates:
    `KIT`, `"KIT-OK."` and `kit ok` all prove the same thing. Case, punctuation and whitespace are
    dropped and the match runs both ways, so a truncated token still counts.
    """
    got, want = _normalised(answer), _normalised(SMOKE_TOKEN)
    return bool(got) and (want in got or got in want)


def _http_detail(exc: BaseException) -> str:
    """What the server said in an error response — LM Studio names the actual problem there."""
    if not isinstance(exc, urllib.error.HTTPError):
        return ""
    try:
        body = exc.read().decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001
        return ""
    return f": {body[:300]}" if body else ""


def cmd_llm_smoke(args) -> int:
    base = args.base_url or DEFAULT_BASE_URL
    try:
        with kitproviders.urlopen(f"{base}/models", timeout=5) as r:
            models = [m.get("id") for m in json.load(r).get("data", [])]
    except Exception as exc:  # noqa: BLE001
        print(f"endpoint {base} not reachable: {exc}")
        return 2
    print(f"models available: {models}")
    model = args.model or DEFAULT_MODEL or (models[0] if models else "")
    try:
        res = _complete(base, model, [
            {"role": "system", "content": "You are a terse assistant."},
            {"role": "user", "content": f"Reply with exactly the text {SMOKE_TOKEN} and nothing else."},
        ], max_tokens=SMOKE_ANSWER_TOKENS)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        # the state smoke exists to diagnose: the server answers /models and cannot complete
        print(f"endpoint {base} answered /models but not the chat call "
              f"({exc.__class__.__name__}: {exc}){_http_detail(exc)}")
        print("server up, no model loaded — load one in LM Studio (Developer tab), or pass --model" if not models
              else f"the endpoint lists {len(models)} model(s) but would not complete — load one in the app, "
                   f"or name it with --model")
        return 2
    answer = res.content.strip()
    thinking = f" reasoning={len(res.reasoning)} chars" if res.reasoning else ""
    print(f"model={model!r} answer={answer!r}{thinking}")
    if res.out_of_budget:
        print(res.budget_hint())
        return 1
    if _smoke_passed(answer):
        return 0
    print(f"the endpoint completes but did not answer {SMOKE_TOKEN} — it is reachable and the model "
          f"is not following a one-line instruction; try another model")
    return 1


def _confidential(vault: Path, rel: str) -> bool:
    """True when the note carries `sensitivity: confidential` — the line the MCP bridge enforces."""
    if not rel or rel.startswith(("http://", "https://")):
        return False
    path = vault / (rel if rel.endswith(".md") else rel + ".md")
    if not path.is_file():
        return False
    try:
        note = kitlib.parse_note(path, vault)
    except Exception:  # noqa: BLE001 — an unreadable note is not a reason to leak it
        return True
    if note.frontmatter is None:
        # Fail closed, like the bridge: a note whose YAML has any syntax error, or whose block a
        # BOM hid, would otherwise have its body placed verbatim into the prompt. An unresolvable
        # path stays False above — the bridge answers False there too, and mirroring it wrongly
        # would withhold notes that are fine.
        return bool(note.fm_error) or kitlib.unparsed_frontmatter(note.body)
    return str(note.frontmatter.get("sensitivity", "")).lower() == "confidential"


def _search_hits(vault: Path, question: str, n: int, collection: str | None,
                 provider: str | None = None) -> list[dict]:
    """Ask the configured search provider, then fall back.

    Two different fallbacks, both needed: `search_provider` falls back when the tool is not
    installed, and this one when it is installed but answers nothing — an unindexed vault, a
    collection that was never embedded, a qmd that errored. Without it, `llm ask` on a fresh
    vault says "no matching notes found" while the note sits right there.
    """
    p = kitproviders.search_provider(vault, provider)
    hits = p.query(vault, question, n, collection)
    if not hits and p.name != kitproviders.NaiveSearch.name:
        hits = kitproviders.NaiveSearch().query(vault, question, n, collection)
    return [h.as_dict() for h in hits]


def cmd_llm_ask(args) -> int:
    vault = _vault(args.vault)
    base = args.base_url or DEFAULT_BASE_URL
    collection = kitproviders.collection_name(vault, args.collection)
    hits = _search_hits(vault, args.question, args.n, collection, getattr(args, "search_provider", None))
    if not hits:
        print("no matching notes found")
        return 1
    withheld: set[str] = set()
    for h in hits:                       # the bridge hides confidential notes; the prompt must too
        rel = kitproviders.hit_rel(h["file"])
        if _confidential(vault, rel):
            h["snippet"] = "(withheld — sensitivity: confidential)"
            withheld.add(rel)
    context = "\n\n".join(f"[{i+1}] {h['file']} — {h['title']}\n{h['snippet']}" for i, h in enumerate(hits))
    facts = ""
    if args.graph:
        g = kitgraph.build_graph(vault)
        wanted: list[str] = []
        for nid in kitgraph.mentions(g, args.question):                  # notes named in the question come first
            if _confidential(vault, nid):
                withheld.add(nid)
            else:
                wanted.append(nid)
        for h in hits[: min(3, len(hits))]:
            nid = kitgraph.resolve_id(g, kitproviders.hit_rel(h["file"]))
            if nid and nid not in wanted and not _confidential(vault, nid):
                wanted.append(nid)
        packs = [kitgraph.context_pack(g, nid, 1) for nid in wanted[:4]]
        for nid in wanted[:2]:                                              # and their text joins the sources
            if nid in g.nodes and g.nodes[nid].kind == "note" and not any(kitproviders.hit_rel(h["file"]).endswith(nid) for h in hits):
                n = g.nodes[nid]
                hits.insert(0, {"file": nid, "title": n.title, "snippet": (vault / nid).read_text(encoding="utf-8")[:600], "score": None})
        context = "\n\n".join(f"[{i+1}] {h['file']} — {h['title']}\n{h['snippet']}" for i, h in enumerate(hits))
        if packs:
            facts = "\n\nFacts from the vault graph (authoritative for relations, states, dates):\n\n" + "\n".join(packs)
    if withheld:
        print(f"{len(withheld)} note(s) marked sensitivity: confidential — kept out of the prompt, "
              f"cited by title only")
    messages = [
        {"role": "system", "content": "You answer questions about the user's notes using ONLY the provided sources and facts. Cite sources as [n]. Prefer the graph facts for relationships, owners, states and dates. If the sources do not contain the answer, say so plainly."},
        {"role": "user", "content": f"Sources:\n\n{context}{facts}\n\nQuestion: {args.question}"},
    ]
    if args.dry_run:
        print("--- system ---"); print(messages[0]["content"]); print("--- user ---"); print(messages[1]["content"])
        return 0
    try:
        res = _complete(base, args.model or DEFAULT_MODEL, messages)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"endpoint {base} did not answer ({exc.__class__.__name__}: {exc}){_http_detail(exc)}. Sources found:")
        for i, h in enumerate(hits):
            print(f"  [{i+1}] {h['file']}")
        return 2
    if res.out_of_budget:
        print(f"{res.budget_hint()}. Sources found:")
        for i, h in enumerate(hits):
            print(f"  [{i+1}] {h['file']}")
        return 2
    print(res.content.strip())
    print("\nSources:")
    for i, h in enumerate(hits):
        print(f"  [{i+1}] {h['file']}")
    return 0


# ---------------------------------------------------------------- test

def cmd_test(args) -> int:
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-t", str(ROOT), "-v"]
    if args.pattern:
        cmd += ["-p", args.pattern]
    return subprocess.call(cmd, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    kitlib.use_utf8_io()
    ap = argparse.ArgumentParser(prog="kit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"okf-vault-kit {kitlib.KIT_VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="check tools, endpoint and vault"); p.add_argument("--vault"); p.add_argument("--base-url")
    p.add_argument("--report", metavar="FILE", help="also write a redacted report (no note content) to send with feedback"); p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("init", help="create a personal vault from the template")
    p.add_argument("--target", required=True); p.add_argument("--actor", help="human:<handle> — replaces human:me in the copied vault")
    p.add_argument("--collection", help="qmd collection name (default: derived from the vault path, so two vaults do not share one index)"); p.add_argument("--no-qmd", action="store_true"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("index", help="regenerate index.md"); p.add_argument("--vault"); p.set_defaults(fn=cmd_index)
    p = sub.add_parser("validate", help="OKF conformance, links, ontology"); p.add_argument("--vault"); p.add_argument("--strict", action="store_true", help="fail on this kit's own rules too — links, ontology and warnings — not only on OKF §11 conformance")
    p.add_argument("--ontology", action="store_true", help="force the ontology checks (default: on when 99-system/ontology.yml exists)"); p.add_argument("--no-ontology", action="store_true"); p.set_defaults(fn=cmd_validate)
    gp = sub.add_parser("graph", help="build, export and query the vault graph"); gsub = gp.add_subparsers(dest="graph_cmd", required=True)
    p = gsub.add_parser("build", help="write .kit/graph.{json,nt,jsonld,sqlite}"); p.add_argument("--vault"); p.add_argument("--out"); p.set_defaults(fn=cmd_graph_build)
    p = gsub.add_parser("export", help="print or write one format"); p.add_argument("--vault"); p.add_argument("--format", choices=["json", "nt", "jsonld"], default="json"); p.add_argument("--out"); p.set_defaults(fn=cmd_graph_export)
    p = gsub.add_parser("query", help="SQL over .kit/graph.sqlite (SELECT only)"); p.add_argument("sql"); p.add_argument("--vault"); p.add_argument("--json", action="store_true"); p.add_argument("--rebuild", action="store_true"); p.add_argument("--limit", type=int, default=200); p.set_defaults(fn=cmd_graph_query)
    p = gsub.add_parser("neighbors", help="edges around a note"); p.add_argument("node"); p.add_argument("--vault"); p.add_argument("--depth", type=int, default=1); p.add_argument("--pred", help="comma-separated predicates"); p.set_defaults(fn=cmd_graph_neighbors)
    p = gsub.add_parser("path", help="shortest path between two notes"); p.add_argument("a"); p.add_argument("b"); p.add_argument("--vault"); p.set_defaults(fn=cmd_graph_path)
    p = gsub.add_parser("pack", help="markdown context pack for a note (for models)"); p.add_argument("node"); p.add_argument("--vault"); p.add_argument("--depth", type=int, default=1); p.add_argument("--out"); p.set_defaults(fn=cmd_graph_pack)
    p = sub.add_parser("reconcile", help="hub tables vs notes; derived-state conflicts"); p.add_argument("--vault"); p.add_argument("--apply", action="store_true"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_reconcile)
    p = sub.add_parser("todos", help="surface and reconcile tasks"); p.add_argument("--vault"); p.add_argument("--json", action="store_true"); p.add_argument("--all", action="store_true", help="include done tasks in --json")
    p.add_argument("--owner", help="only tasks with this owner — filters the summary and --json alike"); p.add_argument("--digest", nargs="?", const="", help="write the digest page (default 00-home/todo-digest.md)"); p.add_argument("--sync-done", action="store_true", help="tick open copies of tasks already ticked in another note"); p.set_defaults(fn=cmd_todos)
    p = sub.add_parser("minutes", help="file a recap as a meeting note"); p.add_argument("file", help="text file or - for stdin"); p.add_argument("--title", required=True); p.add_argument("--vault"); p.add_argument("--date"); p.add_argument("--project"); p.add_argument("--people", help="comma-separated names")
    p.add_argument("--kind", default="project"); p.add_argument("--llm", action="store_true", help="also ask the local model to structure the recap"); p.add_argument("--base-url"); p.add_argument("--model"); p.add_argument("--daily", action="store_true", help="link the note from today's daily note")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_minutes)
    p = sub.add_parser("log", help="append an entry to log.md"); p.add_argument("message"); p.add_argument("--vault"); p.add_argument("--kind", default="Update", help="Update | Creation | Deprecation"); p.set_defaults(fn=cmd_log)
    p = sub.add_parser("mcp-config", help="print or write an mcp.json for qmd + the Obsidian bridge")
    p.add_argument("--client", choices=["lmstudio", "claude-desktop", "cursor"], default="lmstudio"); p.add_argument("--vault")
    p.add_argument("--backend", choices=["fs", "cli"], default="fs", help="bridge backend: fs (direct files, headless) or cli (official Obsidian CLI, app running)")
    p.add_argument("--vault-name", help="Obsidian vault name for the cli backend"); p.add_argument("--with-qmd-mcp", action="store_true", help="also register qmd's own MCP server — it has NO policy layer and exposes confidential notes (off by default)")
    p.add_argument("--qmd-http", action="store_true", help="when registering qmd (--with-qmd-mcp), use its HTTP transport on :8181 instead of stdio")
    p.add_argument("--no-uv", action="store_true", help="launch the bridge with this Python instead of `uv run` (also KIT_NO_UV=1)")
    p.add_argument("--bridge-http", action="store_true", help="point the client at an already running `obsidian_bridge.py --http` instead of spawning it"); p.add_argument("--bridge-port", type=int, default=8765)
    p.add_argument("--bridge-token", help="bearer token the running HTTP bridge expects (sent as an Authorization header)")
    p.add_argument("--bridge-flags", help="extra bridge flags for the spawned server, e.g. \"--read-only\" or \"--propose --allow-write 02-meetings,01-journal\"")
    p.add_argument("--write", action="store_true"); p.set_defaults(fn=cmd_mcp_config)
    llm = sub.add_parser("llm", help="talk to the local model endpoint"); lsub = llm.add_subparsers(dest="llm_cmd", required=True)
    p = lsub.add_parser("smoke", help="list models and run a one-line completion"); p.add_argument("--base-url"); p.add_argument("--model"); p.set_defaults(fn=cmd_llm_smoke)
    p = lsub.add_parser("ask", help="search the vault, then ask the model with citations"); p.add_argument("question"); p.add_argument("--vault"); p.add_argument("--base-url"); p.add_argument("--model")
    p.add_argument("--collection", help="qmd collection to search (default: the one `init` wrote into .kit/config.yml, else 'vault')"); p.add_argument("-n", type=int, default=5)
    p.add_argument("--search-provider", choices=["auto", "qmd", "naive"], help="override the configured search backend")
    p.add_argument("--graph", action="store_true", help="add graph facts (context packs) for the top hits"); p.add_argument("--dry-run", action="store_true", help="print the assembled prompt, do not call the model"); p.set_defaults(fn=cmd_llm_ask)
    p = sub.add_parser("test", help="run the end-to-end tests"); p.add_argument("-p", "--pattern", default=None); p.set_defaults(fn=cmd_test)
    p = sub.add_parser("examples", help="the notes this vault ships to demonstrate itself")
    esub = p.add_subparsers(dest="action", required=True)
    _e = esub.add_parser("remove", help="delete every note marked `example: true` and repair what referred to it")
    _e.add_argument("--vault"); _e.add_argument("--dry-run", action="store_true", help="print what would change and stop")
    _e.set_defaults(fn=cmd_examples)

    p = sub.add_parser("proposals", help="list/show/apply/reject writes recorded by a bridge running with --propose")
    psub = p.add_subparsers(dest="action", required=True)
    for _name, _help, _needs_id in (("list", "pending proposals, oldest first", False),
                                    ("show", "the full record of one proposal", True),
                                    ("apply", "write one proposal into the vault", True),
                                    ("reject", "discard one proposal", True)):
        _p = psub.add_parser(_name, help=_help)
        if _needs_id:
            _p.add_argument("id")
        _p.add_argument("--vault")
        if _name == "reject":
            _p.add_argument("--reason")
        _p.set_defaults(fn=cmd_proposals)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
