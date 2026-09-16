# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""testkit - run the documented path end to end and write one report to send back.

Every step is recorded, not asserted: a step that cannot run on this machine is SKIP with a
reason, never a failure. A tester behind a blocked registry still returns a useful report.

The report carries the same guarantee as `kit.py doctor --report`: no note content, no paths
from inside a vault, no username. Everything written goes through redact().

    uv run testkit/testkit.py                 # full run, report next to the kit
    uv run testkit/testkit.py --quick         # skip the model and index legs
    uv run testkit/testkit.py --out report.md
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


# --------------------------------------------------------------------------- redaction
def _secrets() -> list[str]:
    """Tokens that must never reach the report, longest first so substrings do not survive."""
    out = {str(Path.home()), os.environ.get("USERPROFILE", ""), os.environ.get("HOME", "")}
    with contextlib.suppress(Exception):   # no account name to read is not a reason to fail
        out.add(getpass.getuser())
    for var in ("USER", "USERNAME", "LOGNAME"):
        out.add(os.environ.get(var, ""))
    return sorted((s for s in out if s and len(s) > 2), key=len, reverse=True)


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_URL = re.compile(r"\b([a-z][a-z0-9+.-]*)://(?:[^/\s@]*@)?([^/\s:]+)(:\d+)?", re.I)
_WINPATH = re.compile(r"[A-Za-z]:\\[^\s\"']+")
_POSIXPATH = re.compile(r"/(?:Users|home)/[^\s/\"']+")


def redact(text: str) -> str:
    """Strip usernames, home directories, and URL credentials/hosts from a fragment."""
    if not text:
        return text
    out = str(text)
    # URL userinfo and host go first: a hostname often embeds the username.
    out = _URL.sub(lambda m: f"{m.group(1)}://<host>{m.group(3) or ''}", out)
    out = _WINPATH.sub("<path>", out)
    out = _POSIXPATH.sub("<path>", out)
    for secret in _secrets():
        out = re.sub(re.escape(secret), "<redacted>", out, flags=re.I)
    return out


# --------------------------------------------------------------------------- runner
class Report:
    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.started = time.time()

    def add(self, name: str, status: str, detail: str = "", seconds: float = 0.0) -> None:
        self.steps.append({"name": name, "status": status,
                           "detail": redact(detail).strip(), "seconds": round(seconds, 1)})
        mark = {PASS: "ok", FAIL: "FAIL", SKIP: "skip"}[status]
        line = f"  {mark:<5} {name}"
        if status != PASS and detail:
            line += f"  --  {redact(detail).strip().splitlines()[0][:110]}"
        print(line, flush=True)

    def counts(self) -> dict[str, int]:
        return {s: sum(1 for x in self.steps if x["status"] == s) for s in (PASS, FAIL, SKIP)}


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 300,
        env: dict | None = None) -> tuple[int, str]:
    """Run a command, never raise. Returns (returncode, combined output)."""
    full = dict(os.environ)
    full.setdefault("PYTHONIOENCODING", "utf-8")
    if env:
        full.update(env)
    try:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=full, check=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except Exception as exc:                                    # noqa: BLE001 - report, never crash
        return 1, f"{type(exc).__name__}: {exc}"


def step(rep: Report, name: str, cmd: list[str], *, cwd: Path | None = None,
         timeout: int = 300, env: dict | None = None, expect: int = 0,
         skip_if: str | None = None) -> tuple[int, str]:
    if skip_if:
        rep.add(name, SKIP, skip_if)
        return -1, ""
    t0 = time.time()
    rc, out = run(cmd, cwd=cwd, timeout=timeout, env=env)
    rep.add(name, PASS if rc == expect else FAIL,
            "" if rc == expect else f"exit {rc}\n{out[-1500:]}", time.time() - t0)
    return rc, out


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _version_of(tool: str, flag: str) -> str:
    """First line that looks like a version. Some CLIs answer with an ANSI banner."""
    out = _ANSI.sub("", run([tool, flag], timeout=30)[1])
    for line in out.splitlines():
        line = line.strip()
        if re.search(r"\d+\.\d+", line):
            return line[:60]
    return (out.strip().splitlines() or ["(no version output)"])[0][:60]


# --------------------------------------------------------------------------- the path
def main() -> int:
    ap = argparse.ArgumentParser(description="run the documented okf-vault-kit path end to end")
    ap.add_argument("--out", default="testkit-report.md", help="where to write the report")
    ap.add_argument("--quick", action="store_true", help="skip the qmd index and model legs")
    ap.add_argument("--keep", action="store_true", help="keep the scratch vault for inspection")
    args = ap.parse_args()

    rep = Report()
    scratch = Path(tempfile.mkdtemp(prefix="okf-testkit-"))
    vault = scratch / "vault"
    # qmd's index is global; redirect it so a test run never touches the tester's real one.
    qmd_env = {"XDG_CONFIG_HOME": str(scratch / "cfg"), "XDG_CACHE_HOME": str(scratch / "cache")}

    print(f"okf-vault-kit test kit -- scratch at {scratch}\n")

    # 1. environment ---------------------------------------------------------
    env_rows = [("os", f"{platform.system()} {platform.release()} ({platform.machine()})"),
                ("python", platform.python_version())]
    for tool, flag in (("uv", "--version"), ("bun", "--version"), ("qmd", "--version"),
                       ("lms", "version"), ("obsidian", "version"), ("node", "--version")):
        env_rows.append((tool, _version_of(tool, flag) if have(tool) else "not installed"))
    rep.add("environment", PASS, "")

    # 2. doctor --------------------------------------------------------------
    rc, doctor_out = run([PY, str(ROOT / "kit.py"), "doctor"], cwd=ROOT, timeout=180)
    rep.add("kit.py doctor", PASS if rc in (0, 1) else FAIL,
            "" if rc in (0, 1) else f"exit {rc}\n{doctor_out[-1500:]}")

    # 3. the offline suite, on the pinned SDK --------------------------------
    step(rep, "offline test suite (mcp 2.x)",
         ["uv", "run", "--with", "mcp>=2,<3", str(ROOT / "kit.py"), "test"],
         cwd=ROOT, timeout=900, skip_if=None if have("uv") else "uv not installed")

    # 4. init ----------------------------------------------------------------
    rc, _ = step(rep, "kit.py init", [PY, str(ROOT / "kit.py"), "init",
                                      "--target", str(vault), "--actor", "human:testkit"],
                 cwd=ROOT, timeout=180)
    vault_ok = rc == 0 and vault.exists()

    gate = None if vault_ok else "init did not produce a vault"

    # 5. the vault is conformant --------------------------------------------
    step(rep, "validate --strict", [PY, str(ROOT / "kit.py"), "validate",
                                    "--vault", str(vault), "--strict"],
         cwd=ROOT, timeout=180, skip_if=gate)

    # 6. the graph -----------------------------------------------------------
    step(rep, "graph build", [PY, str(ROOT / "kit.py"), "graph", "build", "--vault", str(vault)],
         cwd=ROOT, timeout=300, skip_if=gate)
    step(rep, "graph query", [PY, str(ROOT / "kit.py"), "graph", "query", "--vault", str(vault),
                              "SELECT id FROM nodes LIMIT 3"],
         cwd=ROOT, timeout=180, skip_if=gate)

    # 7. reconciliation (dry run: exit 2 means 'something to apply', not a failure)
    t0 = time.time()
    if gate:
        rep.add("reconcile (dry run)", SKIP, gate)
    else:
        rc, out = run([PY, str(ROOT / "kit.py"), "reconcile", "--vault", str(vault)],
                      cwd=ROOT, timeout=300)
        rep.add("reconcile (dry run)", PASS if rc in (0, 2) else FAIL,
                "" if rc in (0, 2) else f"exit {rc}\n{out[-1200:]}", time.time() - t0)
    step(rep, "todos", [PY, str(ROOT / "kit.py"), "todos", "--vault", str(vault)],
         cwd=ROOT, timeout=180, skip_if=gate)

    # 8. minutes: the one write path a tester exercises by hand ---------------
    if gate:
        rep.add("minutes", SKIP, gate)
    else:
        recap = scratch / "recap.txt"
        # Deliberately non-ASCII: this is the lane that broke on Windows before.
        recap.write_text("Kurzprotokoll vom Rollout-Sync — Teilnehmer: Alex Example\n"
                         "Action: Benchmark für Dienstag vorbereiten — Alex, 2026-09-16\n"
                         "Decision: qmd bleibt die Suchmaschine\n", encoding="utf-8")
        step(rep, "minutes (non-ASCII recap)",
             [PY, str(ROOT / "kit.py"), "minutes", str(recap),
              "--title", "Rollout Sync", "--vault", str(vault)], cwd=ROOT, timeout=180)

    # 9. mcp-config (print only: never touch the tester's client config) ------
    step(rep, "mcp-config --client lmstudio (print)",
         [PY, str(ROOT / "kit.py"), "mcp-config", "--client", "lmstudio", "--vault", str(vault)],
         cwd=ROOT, timeout=120, skip_if=gate)

    # 10. qmd ----------------------------------------------------------------
    qmd_gate = gate or ("qmd not installed" if not have("qmd") else
                        ("--quick" if args.quick else None))
    step(rep, "qmd collection add", ["qmd", "collection", "add", str(vault),
                                     "--name", "testkit", "--mask", "**/*.md"],
         timeout=300, env=qmd_env, skip_if=qmd_gate)
    step(rep, "qmd embed", ["qmd", "embed"], timeout=1800, env=qmd_env, skip_if=qmd_gate)
    step(rep, "qmd query", ["qmd", "query", "hybrid search"], timeout=300,
         env=qmd_env, skip_if=qmd_gate)

    # 11. the local model ----------------------------------------------------
    llm_gate = gate or ("--quick" if args.quick else None)
    step(rep, "llm smoke", [PY, str(ROOT / "kit.py"), "llm", "smoke"],
         cwd=ROOT, timeout=300, skip_if=llm_gate)
    step(rep, "llm ask --graph", [PY, str(ROOT / "kit.py"), "llm", "ask",
                                  "who owns the search relaunch", "--graph",
                                  "--vault", str(vault)],
         cwd=ROOT, timeout=600, skip_if=llm_gate)

    # 12. a real MCP session -------------------------------------------------
    # Run from the scratch directory, not the repo: the repo's own `mcp/` folder would shadow
    # the `mcp` package on sys.path[0]. --no-project keeps uv from building a .venv here.
    step(rep, "MCP stdio session (14 tools)",
         ["uv", "run", "--no-project", "--with", "mcp>=2,<3", "python", "-c", MCP_PROBE,
          str(ROOT), str(vault)],
         cwd=scratch, timeout=300, skip_if=gate or (None if have("uv") else "uv not installed"))

    # ------------------------------------------------------------------ write
    out_path = Path(args.out).resolve()
    out_path.write_text(render(rep, env_rows), encoding="utf-8")
    if not args.keep:
        shutil.rmtree(scratch, ignore_errors=True)
    else:
        print(f"\nscratch kept at {scratch}")

    c = rep.counts()
    print(f"\n{c[PASS]} passed, {c[FAIL]} failed, {c[SKIP]} skipped")
    print(f"report: {out_path}")
    print("\nSend that file back. It contains no note content, no vault paths and no username.")
    return 1 if c[FAIL] else 0


MCP_PROBE = """
import asyncio, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
root, vault = sys.argv[1], sys.argv[2]

async def go():
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(Path(root) / "mcp" / "obsidian_bridge.py"),
                                         "--backend", "fs", "--vault", vault])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print(f"{len(tools)} tools: {', '.join(tools)}")
            assert len(tools) >= 14, f"expected 14 tools, got {len(tools)}"

asyncio.run(go())
"""


def render(rep: Report, env_rows: list[tuple[str, str]]) -> str:
    c = rep.counts()
    lines = [
        "# okf-vault-kit test kit report", "",
        (f"{c[PASS]} passed / {c[FAIL]} failed / {c[SKIP]} skipped"
         f"  --  {round(time.time() - rep.started)}s total"), "",
        "No note content, no vault paths and no username appear below.", "",
        "## Environment", "", "| | |", "|---|---|",
    ]
    lines += [f"| {k} | {redact(v)} |" for k, v in env_rows]
    lines += ["", "## Steps", "", "| step | result | seconds |", "|---|---|---|"]
    lines += [f"| {s['name']} | {s['status']} | {s['seconds']} |" for s in rep.steps]

    problems = [s for s in rep.steps if s["status"] == FAIL]
    if problems:
        lines += ["", "## Failures", ""]
        for s in problems:
            lines += [f"### {s['name']}", "", "```", s["detail"][:3000], "```", ""]
    skipped = [s for s in rep.steps if s["status"] == SKIP and s["detail"]]
    if skipped:
        lines += ["", "## Skipped, and why", ""]
        lines += [f"- **{s['name']}** - {s['detail']}" for s in skipped]
    lines += ["", "## Anything else", "",
              "Replace this line with what you noticed: what was confusing, what you expected",
              "to be there and was not, and whether you would keep using it.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
