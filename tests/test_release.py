"""Packaging guards: what the tooling leaves behind, what `make release` refuses, what CI enforces.

These are the checks that only bite at release time, which is the worst moment to discover them.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tests.helpers import ROOT, kitlib

MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)


def make_target(name: str) -> str:
    """The recipe lines of one Makefile target."""
    m = re.search(rf"(?ms)^{re.escape(name)}:.*?(?=\n\w|\n\Z|\Z)", MAKEFILE)
    assert m, f"no target {name} in the Makefile"
    return m.group(0)


class Housekeeping(unittest.TestCase):
    """Running the tooling must not dirty the checkout, and `clean` must remove what it does."""

    GENERATED = ["vault/.kit/graph.json", ".kit/graph.sqlite", ".venv/pyvenv.cfg",
                 "build/lib/kit.py", "dist/okf-vault-kit.zip", ".ruff_cache/CACHEDIR.TAG"]

    def test_generated_paths_are_ignored(self):
        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            self.skipTest("not a git checkout")
        for path in self.GENERATED:
            with self.subTest(path=path):
                matched = git("check-ignore", "-v", path)
                self.assertEqual(matched.returncode, 0,
                                 f"{path} is not gitignored; the quickstart writes it into the checkout")
                # the repo's own rule, not a .gitignore a tool happened to drop into the directory
                self.assertTrue(matched.stdout.startswith(".gitignore:"),
                                f"{path} is only ignored by {matched.stdout.split(':')[0]}")

    def test_clean_removes_the_vault_graph_it_generates(self):
        recipe = make_target("clean")
        self.assertIn("vault/.kit", recipe,
                      "graph build writes vault/.kit when no vault is given; clean must remove it")
        self.assertIn("build", recipe, "a PEP 517 build leaves build/ behind")

    def test_clean_removes_the_linter_cache(self):
        self.assertIn(".ruff_cache", make_target("clean"), "`make lint` writes it into the checkout")


class ReleaseGuards(unittest.TestCase):
    """`make release` archives HEAD, so it must refuse whenever HEAD is not what was checked."""

    def test_release_refuses_a_dirty_or_staged_tree(self):
        recipe = make_target("release")
        self.assertIn("git diff --quiet", recipe)
        self.assertIn("git diff --cached --quiet", recipe,
                      "staged changes pass `git diff --quiet` but are not in the archived HEAD")

    def test_release_refuses_an_untagged_or_mistagged_head(self):
        recipe = make_target("release")
        self.assertIn("git describe --exact-match --tags HEAD", recipe)
        self.assertIn('"v$(VERSION)"', recipe, "the tag must be compared against VERSION")

    def test_release_depends_on_the_gate(self):
        self.assertRegex(MAKEFILE, r"(?m)^release:\s*check\b")

    def test_the_tag_on_head_matches_version(self):
        described = git("describe", "--exact-match", "--tags", "HEAD")
        if described.returncode != 0:
            self.skipTest("HEAD is not tagged")
        self.assertEqual(described.stdout.strip(), f"v{kitlib.KIT_VERSION}",
                         "the tag on HEAD and VERSION disagree")


class Workflow(unittest.TestCase):
    """The single required check is only as good as the list of jobs it waits for."""

    def setUp(self):
        self.ci = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.jobs = self.ci["jobs"]

    def test_every_job_is_aggregated(self):
        needed = set(self.jobs["ci-required"]["needs"])
        others = set(self.jobs) - {"ci-required"}
        self.assertEqual(others - needed, set(),
                         "a job missing from ci-required's needs is a job nobody enforces")

    def test_no_job_is_advisory_as_a_whole(self):
        advisory = [name for name, job in self.jobs.items() if "continue-on-error" in job]
        self.assertEqual(advisory, [],
                         "job-level continue-on-error hides the job from ci-required; "
                         "put it on the step that may fail")

    def test_the_aggregate_fails_on_any_non_success(self):
        run = "".join(step.get("run", "") for step in self.jobs["ci-required"]["steps"])
        self.assertIn('.value.result != "success"', run)
        self.assertIn("exit 1", run)

    def test_ci_calls_the_makefile(self):
        targets = set(re.findall(r"(?m)^([a-z-]+):", MAKEFILE))
        called = set()
        for job in self.jobs.values():
            for step in job.get("steps", []):
                called |= set(re.findall(r"\bmake ([a-z-]+)", step.get("run", "")))
        self.assertTrue(called <= targets, f"CI calls targets the Makefile lacks: {called - targets}")
        self.assertIn("check", called, "the Makefile claims CI runs `check`; it must")


class LintGate(unittest.TestCase):
    """Ruff is the Python gate, and it hangs off `make lint` — so CI picks it up with no job of its own."""

    PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    def test_lint_runs_ruff(self):
        self.assertIn("ruff", make_target("lint"),
                      "`make lint` is the only place CI would pick the linter up")

    def test_the_linter_version_is_pinned(self):
        self.assertRegex(MAKEFILE, r"(?m)^RUFF\s*\?=\s*ruff@\d+\.\d+\.\d+$",
                         "an unpinned linter turns an unrelated CI run red on a day nobody changed code")

    def test_the_rule_set_is_named_not_inherited(self):
        self.assertIn("[tool.ruff.lint]", self.PYPROJECT)
        self.assertRegex(self.PYPROJECT, r"(?m)^select = \[",
                         "ruff's default set moves between releases; the gate has to name its rules")

    def test_every_suppressed_rule_says_why(self):
        block = re.search(r"(?ms)^ignore = \[(.*?)^\]", self.PYPROJECT)
        self.assertIsNotNone(block, "a rule set that turns nothing off still has to say so")
        lines = [line for line in block.group(1).splitlines() if line.strip()]
        self.assertTrue(lines, "no suppressed rules to check")
        for line in lines:
            with self.subTest(rule=line.strip()):
                self.assertRegex(line, r'^\s+"[A-Z]+\d*",\s+# \S',
                                 "a rule turned off without a reason is one nobody can review")


class SuiteBootstrap(unittest.TestCase):
    """The bridge is importable only once `mcp/` is on sys.path, and each module must do that itself.

    Leaning on whichever module imported the bridge first makes the suite depend on discovery
    order: the full run stays green while `python -m unittest tests.<module>` fails on its own.
    Sorting imports is enough to reorder it, which is how this was found.
    """

    BRIDGE_IMPORT = re.compile(r"(?m)^import (?:obsidian_bridge|bridge_policy)\b")
    PATH_INSERT = re.compile(r'(?m)^sys\.path\.insert\(0, str\(ROOT / "mcp"\)\)')

    def test_bridge_importers_put_mcp_on_the_path_first(self):
        checked = 0
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            src = path.read_text(encoding="utf-8")
            imported = self.BRIDGE_IMPORT.search(src)
            if not imported:
                continue
            checked += 1
            with self.subTest(module=path.name):
                inserted = self.PATH_INSERT.search(src)
                self.assertIsNotNone(inserted, f"{path.name} imports the bridge without adding mcp/ to sys.path")
                self.assertLess(inserted.start(), imported.start(),
                                f"{path.name} imports the bridge before the sys.path insert that makes it importable")
        self.assertTrue(checked, "no test module imports the bridge; this guard has gone stale")


class WheelLane(unittest.TestCase):
    """The VERSION file is a checkout's; an installed wheel has only its metadata."""

    def load_without_version_file(self):
        with tempfile.TemporaryDirectory(prefix="okf-wheel-") as tmp:
            copied = Path(tmp) / "kitlib.py"
            shutil.copy(ROOT / "kitlib.py", copied)
            self.assertFalse((Path(tmp) / "VERSION").exists())
            spec = importlib.util.spec_from_file_location("kitlib_no_version_file", copied)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module  # dataclasses resolves annotations through sys.modules
            try:
                spec.loader.exec_module(module)
            finally:
                sys.modules.pop(spec.name, None)
            return module

    def test_version_falls_back_to_package_metadata(self):
        with mock.patch("importlib.metadata.version", return_value="9.9.9") as version:
            module = self.load_without_version_file()
        self.assertEqual(module.KIT_VERSION, "9.9.9")
        version.assert_called_once_with("okf-vault-kit")

    def test_import_survives_without_version_file_or_metadata(self):
        with mock.patch("importlib.metadata.version",
                        side_effect=importlib.metadata.PackageNotFoundError("okf-vault-kit")):
            module = self.load_without_version_file()
        self.assertEqual(module.KIT_VERSION, "0+unknown")

    def test_pyproject_still_reads_the_version_file(self):
        self.assertIn('version = { file = "VERSION" }',
                      (ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class Inventory(unittest.TestCase):
    """THIRD-PARTY.md answers 'what else comes with it', so the installers must not out-run it."""

    COMPONENTS = ["ripgrep", "fd", "jq", "sqlite", "obsidian-cli", "qmd", "bun", "uv",
                  "Obsidian", "LM Studio"]

    def test_every_installed_component_is_listed(self):
        text = (ROOT / "THIRD-PARTY.md").read_text(encoding="utf-8")
        for name in self.COMPONENTS:
            with self.subTest(component=name):
                self.assertIn(name, text, f"THIRD-PARTY.md does not mention {name}")

    def test_the_third_party_tap_is_named(self):
        text = (ROOT / "THIRD-PARTY.md").read_text(encoding="utf-8")
        self.assertIn("yakitrak", text, "obsidian-cli arrives from a third-party tap; say so")


class MacInstaller(unittest.TestCase):
    """set -e plus an unguarded cask is how a managed machine's install dies half-way."""

    def setUp(self):
        self.script = (ROOT / "scripts" / "install-macos.sh").read_text(encoding="utf-8")

    def test_casks_are_guarded_by_app_presence(self):
        calls = [l.strip() for l in self.script.splitlines() if re.match(r"^(cask|app) ", l)]
        self.assertEqual([c for c in calls if c.startswith("cask ")], [],
                         "IT-deployed apps have no brew record; guard on /Applications instead")
        self.assertIn("Obsidian.app", self.script)
        self.assertIn("LM Studio.app", self.script)

    def test_the_guard_still_installs_a_missing_app(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("no bash")
        defs = "\n".join(l for l in self.script.splitlines() if re.match(r"^(cask|app)\(\)", l))
        probe = f'{defs}\ncask() {{ echo "install:$1"; }}\napp obsidian "No Such Vendor App.app"\n'
        r = subprocess.run([bash, "-c", probe], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "install:obsidian")

    def test_the_script_parses(self):
        bash = shutil.which("bash")
        if bash is None or sys.platform == "win32":
            self.skipTest("no bash")
        for name in ("install-macos.sh", "install-wsl.sh"):
            r = subprocess.run([bash, "-n", str(ROOT / "scripts" / name)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", check=False)
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
