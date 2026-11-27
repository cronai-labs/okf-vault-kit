"""Packaging guards: what the tooling leaves behind, what `make release` refuses, what CI enforces.

These are the checks that only bite at release time, which is the worst moment to discover them.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
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
# `make release` strips `.github/` from the archive on purpose, and the zip is the one place a
# tester might run the suite without a clone. These assertions are about CI configuration, which
# is not part of what is distributed, so they skip there instead of erroring.
HAVE_WORKFLOW = WORKFLOW.is_file()


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


class ReleaseNotes(unittest.TestCase):
    """The Releases page is the first thing a visitor opens, so what it publishes is a gate."""

    def _notes_step(self) -> str:
        if not HAVE_WORKFLOW:
            self.skipTest("no .github/ — running from the release archive, which strips it")
        release = ROOT / ".github" / "workflows" / "release.yml"
        wf = yaml.safe_load(release.read_text(encoding="utf-8"))
        for job in wf["jobs"].values():
            for step in job.get("steps", []):
                if "CHANGELOG" in str(step.get("name", "")):
                    return step["run"]
        self.fail("no release-notes step found in release.yml")

    def test_the_changelog_target_names_the_version(self):
        """`make changelog` must pass `--tag v$(VERSION)`.

        Without it git-cliff sees an untagged tree and writes `[Unreleased]`. The tag is created
        *after* the commit that carries the changelog, so the released file would describe itself
        as unreleased forever — and the tag-aware guard in test_docs would then fail every
        release instead of catching a stale one. That is how v0.1.0 shipped. (#43)
        """
        recipe = make_target("changelog")
        self.assertIn("--tag v$(VERSION)", recipe,
                      "`make changelog` must name the version it is generating for")
        self.assertIn("refs/tags/v$(VERSION)", recipe,
                      "and must refuse once that version is tagged: git-cliff would then label "
                      "the commits since the tag with it too, emitting a duplicate section")

    def test_the_published_body_carries_no_section_heading(self):
        """The release body must never carry a `## ` heading.

        `gh release create --title "$REF"` already names the release after the tag, so a heading
        in the body is redundant — and redundant is the only way for it to be wrong. (#41, #43)
        """
        bash = shutil.which("bash")
        if bash is None or sys.platform == "win32":
            # Same guard as test_the_script_parses below, for the same reason and one more: the
            # step is `runs-on: ubuntu-latest` and calls `python3`, which a Windows shell does not
            # have. A bare "bash" is worse still -- Windows resolves it to System32\bash.exe, the
            # WSL stub, which answers "no installed distributions" in UTF-16.
            self.skipTest("the release job runs on ubuntu")
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(ROOT / "CHANGELOG.md", Path(tmp) / "CHANGELOG.md")
            r = subprocess.run([bash, "-c", self._notes_step()], cwd=tmp,
                               capture_output=True, text=True, check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            notes = (Path(tmp) / "NOTES.md").read_text(encoding="utf-8")

        self.assertNotIn("[Unreleased]", notes, "the release would be titled Unreleased")
        self.assertFalse(any(l.startswith("## ") for l in notes.splitlines()),
                         f"a section heading reached the release body:\n{notes[:200]}")
        self.assertTrue(any(l.startswith("### ") for l in notes.splitlines()),
                        "the grouped sections are the body and must survive")

    def _run_notes_step(self, changelog: str) -> subprocess.CompletedProcess:
        bash = shutil.which("bash")
        if bash is None or sys.platform == "win32":
            self.skipTest("the release job runs on ubuntu")   # same guard as above, same reasons
        step = self._notes_step()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
            return subprocess.run([bash, "-c", step], cwd=tmp, capture_output=True, text=True, check=False)

    def test_the_step_refuses_a_section_without_entries(self):
        """An empty or unparsed section is what the gate exists for: caught before the tag is spent."""
        r = self._run_notes_step("# Changelog\n\n## [9.9.9] - 2026-01-01\n\n### Bug Fixes\n\n## [9.9.8] - 2025-12-01\n\n- old\n")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_release_with_one_short_entry_passes(self):
        """One fix is a release. A byte floor rejected exactly this for 0.1.2 (#84)."""
        r = self._run_notes_step("# Changelog\n\n## [9.9.9] - 2026-01-01\n\n### Bug Fixes\n\n- **x:** Fix one thing (#1)\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 entries", r.stdout)


class NextVersion(unittest.TestCase):
    """`make next-version` is what a maintainer reads before cutting a release."""

    RECIPE = next(block for block in MAKEFILE.split("\n\n") if block.startswith("next-version:"))

    def test_the_first_release_is_not_reported_as_nothing_to_release(self):
        """Before any tag exists git-cliff falls back to initial_tag, which equals VERSION.

        Treating that as "nothing releasable landed" told the maintainer the opposite of the truth
        at the one moment it mattered — ten releasable commits were on main. The recipe has to ask
        whether a tag exists *before* it compares to VERSION. (#36)
        """
        tag_check = self.RECIPE.find("git tag -l")
        version_compare = self.RECIPE.find('= "$(VERSION)"')
        self.assertNotEqual(tag_check, -1,
                            "next-version must distinguish 'no tag yet' from 'nothing to release'")
        self.assertNotEqual(version_compare, -1, "the nothing-to-release branch is still wanted")
        self.assertLess(tag_check, version_compare,
                        "the tag check has to come first, or the first release is misreported")

    def test_both_outcomes_are_still_explained(self):
        self.assertIn("initial_tag", self.RECIPE)
        self.assertIn("no releasable changes", self.RECIPE)


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
        if not HAVE_WORKFLOW:
            self.skipTest("no .github/ — running from the release archive, which strips it")
        self.ci = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.jobs = self.ci["jobs"]

    def test_draft_ci_and_ready_for_review_stay_paired(self):
        """One of these two must be true, or a PR can reach `ready` with no run at all.

        Either drafts run CI — in which case `ready_for_review` is redundant and re-runs the whole
        matrix on a commit already tested — or drafts are skipped, in which case
        `ready_for_review` is the only thing that ever starts a run for a PR opened as a draft, and
        removing it leaves a required check that can never go green. (#34)
        """
        # `on` parses as the boolean True: YAML 1.1, and this file is read by a YAML 1.1 loader.
        on = self.ci.get("on", self.ci.get(True))
        types = on["pull_request"]["types"]
        skips_drafts = any("draft" in str(job.get("if", "")) for job in self.jobs.values())

        if skips_drafts:
            self.assertIn("ready_for_review", types,
                          "a job skips drafts, so a PR opened as a draft never runs until it is "
                          "marked ready — `ready_for_review` has to be in the types list")
        else:
            self.assertNotIn("ready_for_review", types,
                             "nothing skips drafts, so a draft is already fully tested and this "
                             "trigger only re-runs the matrix on the identical commit")

    def test_edited_survives_because_the_policy_job_reads_the_title(self):
        """Dropping `edited` strands a corrected title on its old, failing result."""
        on = self.ci.get("on", self.ci.get(True))
        self.assertIn("edited", on["pull_request"]["types"])

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

    def test_the_secret_scan_fails_closed(self):
        """gitleaks exits 0 when git cannot read the history, and a shallow checkout hides it. (#53)

        Its exit code covers leaks only, so the step has to check the rest itself: a full checkout,
        a positive commit count and no `ERR` line, with the exit code surviving the `tee`.
        """
        steps = self.jobs["security"]["steps"]
        checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
        self.assertEqual((checkout.get("with") or {}).get("fetch-depth"), 0,
                         "a shallow checkout scans only the fetched commits")
        install = next((s for s in steps if "gitleaks" in str(s.get("name", "")).lower()
                        and "install" in str(s.get("name", "")).lower()), None)
        self.assertIsNotNone(install, "the security job no longer installs gitleaks")
        self.assertIn("sha256sum -c", install["run"], "the gitleaks archive is no longer checksum-verified")
        scans = [s for s in steps if re.search(r'gitleaks"?\s+git\b', s.get("run", ""))]
        self.assertEqual(len(scans), 1, "exactly one step runs `gitleaks git`")
        scan, run = scans[0], scans[0]["run"]
        self.assertNotRegex(run, r'gitleaks"?\s+detect\b', "`detect` is deprecated; use `gitleaks git`")
        self.assertTrue(scan.get("shell") == "bash" or "pipefail" in run,
                        "without pipefail the `tee` swallows gitleaks' exit code, and a leak passes")
        self.assertIn("tee", run, "the scan's log is what the count and ERR checks read")
        self.assertIn("--no-color", run, "ANSI codes hide the count and the ERR level from the checks")
        self.assertIn("--is-shallow-repository", run, "the step must refuse a shallow checkout")
        self.assertIn("commits scanned", run, "the step must require a positive commit count")
        self.assertRegex(run, r"-gt 0", "the step must require a positive commit count")
        self.assertIn(" ERR ", run, "the step must fail on any ERR line")
        self.assertRegex(run, r'\[ "\$shallow" = false \]\s*\\?\s*\|\|\s*\{[^}]*::error::[^}]*exit 1; \}',
                         "the step must refuse a shallow checkout, not just ask git whether it is one")
        self.assertGreaterEqual(run.count("::error::"), 5, "each failure names its cause in an ::error::")

    # A stand-in for gitleaks: prints $STUB_OUT and exits 0, or, with STUB_LEAK set, exits with
    # the code the step passed in --exit-code, as gitleaks does when it finds a leak.
    STUB_GITLEAKS = ('#!/bin/sh\ncode=0\nwhile [ $# -gt 0 ]; do [ "$1" = --exit-code ] && code=$2; shift; done\n'
                     'printf \'%s\\n\' "$STUB_OUT"\n[ -n "$STUB_LEAK" ] && exit "$code"\nexit 0\n')

    def run_secret_scan(self, repo: Path, runner_temp: Path, out: str, leak: bool = False):
        """Run the scan step's own script the way Actions runs `shell: bash`, against the stub."""
        step = next(s for s in self.jobs["security"]["steps"] if re.search(r'gitleaks"?\s+git\b', s.get("run", "")))
        script = runner_temp / "step.sh"
        script.write_text(step["run"], encoding="utf-8")
        env = {**os.environ, "RUNNER_TEMP": str(runner_temp), "STUB_OUT": out,
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CEILING_DIRECTORIES": str(runner_temp.parent)}  # no repository above the temp dir counts
        env.pop("STUB_LEAK", None)
        if leak:
            env["STUB_LEAK"] = "1"
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=repo,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                              timeout=60, check=False)

    @unittest.skipIf(os.name == "nt" or not (shutil.which("bash") and shutil.which("git")),
                     "needs bash and git, as the ubuntu runner has; native Windows' bash may be WSL's")
    def test_the_secret_scan_fails_closed_when_run(self):
        """The step's script, run: each way gitleaks can pass without scanning the history fails it. (#53)

        String checks on the step miss a check that is present but disarmed (an `exit 0` at the end,
        a dropped `fail=1`, `--exit-code 0`); running the script does not.
        """
        with tempfile.TemporaryDirectory(prefix="okf-gitleaks-") as tmp:
            tmp = Path(tmp)
            temp, full, shallow, bare = tmp / "runner", tmp / "full", tmp / "shallow", tmp / "not-a-repo"
            for d in (temp, full, bare):
                d.mkdir()
            stub = temp / "gitleaks"
            stub.write_text(self.STUB_GITLEAKS, encoding="utf-8")
            stub.chmod(0o755)
            git_env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

            def git(*args, cwd=full):
                subprocess.run(["git", *args], cwd=cwd, env=git_env, check=True, capture_output=True)
            git("init", "-q")
            for n in (1, 2):
                (full / "note.md").write_text(f"{n}\n", encoding="utf-8")
                git("add", "note.md")
                git("commit", "-q", "-m", str(n))
            git("clone", "-q", "--depth", "1", full.resolve().as_uri(), str(shallow), cwd=tmp)

            clean = "4:50PM INF 2 commits scanned.\n4:50PM INF no leaks found"
            r = self.run_secret_scan(full, temp, clean)
            self.assertEqual(r.returncode, 0, f"a clean full scan must pass:\n{r.stdout}{r.stderr}")
            failing = {
                "a leak": (full, clean.replace("no leaks found", "leaks found: 1"), True, "exited non-zero"),
                "an ERR line": (full, "4:50PM ERR failed to read a commit\n4:50PM INF 1 commits scanned.",
                                False, "logged an error"),
                "0 commits scanned": (full, "4:50PM INF 0 commits scanned.", False, "scanned 0 commits"),
                "no count at all": (full, "4:50PM INF no leaks found", False, "scanned no commits"),
                "a shallow checkout": (shallow, clean, False, "shallow checkout"),
                "no repository": (bare, clean, False, "git cannot read"),
            }
            for case, (repo, out, leak, why) in failing.items():
                with self.subTest(case):
                    r = self.run_secret_scan(repo, temp, out, leak)
                    self.assertNotEqual(r.returncode, 0, f"{case} must fail the step:\n{r.stdout}{r.stderr}")
                    self.assertIn("::error::", r.stdout)
                    self.assertIn(why, r.stdout, f"the ::error:: line must say why ({case})")

    def test_qmd_is_pinned_and_the_pin_is_documented(self):
        """An unpinned qmd turns the e2e lane red or green with no change here. (#91)"""
        install = next((s for s in self.jobs["qmd-e2e"]["steps"] if "@tobilu/qmd" in s.get("run", "")), None)
        self.assertIsNotNone(install, "the qmd-e2e job no longer installs qmd")
        pin = re.search(r"bun install -g @tobilu/qmd@(\d+\.\d+\.\d+)(?=\s|$)", install["run"])
        self.assertIsNotNone(pin, "qmd must be installed at an exact version, bumped in its own PR")
        testing = (ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
        self.assertIn(f"@tobilu/qmd@{pin.group(1)}", testing, "docs/testing.md must name the version CI pins")


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

    BRIDGE_IMPORT = re.compile(r"(?m)^import (?:vault_bridge|bridge_policy)\b")
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


class SuiteTimeout(unittest.TestCase):
    """A hung test fails the run and names itself, rather than holding CI until the job times out. (#94)

    `make check` runs the suite through `kit.py test`, which is unittest, so pytest-timeout does not
    reach it; the runner arms its own limit, for each test and for the class and module fixtures.
    """

    HANGS = ("import time\nimport unittest\n\n\nclass Hangs(unittest.TestCase):\n"
             "    def test_blocks_forever(self):\n        time.sleep(600)\n")

    def test_a_hung_test_is_stopped_with_a_traceback_that_names_it(self):
        import kit
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "test_hangs.py").write_text(self.HANGS, encoding="utf-8")
            env = {**os.environ, "KIT_TEST_TIMEOUT": "2", "PYTHONIOENCODING": "utf-8"}
            try:
                r = subprocess.run(kit.test_command(Path(tmp), Path(tmp)), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", env=env, timeout=90, check=False)
            except subprocess.TimeoutExpired:
                self.fail("the runner let a hung test run past its limit")
        self.assertNotEqual(r.returncode, 0, "a hung test must fail the run")
        self.assertIn("test_blocks_forever", r.stderr, "the output must name the test that hung")
        self.assertIn("Timeout", r.stderr, "the traceback dump says the limit was hit")

    # unittest runs class and module fixtures outside startTest/stopTest, so a limit armed only
    # there let a hung setUpClass (where test_e2e_qmd.py indexes with qmd) run unbounded.
    FIXTURE_HANGS = {
        "setUpModule": "def setUpModule():\n    time.sleep(8)\n\n\n",
        "setUpClass": "",
        "tearDownClass": "",
    }

    def fixture_module(self, where: str) -> str:
        body = "import time\nimport unittest\n\n\n" + self.FIXTURE_HANGS[where]
        body += "class Fixture(unittest.TestCase):\n"
        if where in ("setUpClass", "tearDownClass"):
            body += f"    @classmethod\n    def {where}(cls):\n        time.sleep(8)\n\n"
        return body + "    def test_passes(self):\n        pass\n"

    def test_a_hung_class_or_module_fixture_is_stopped_too(self):
        import kit
        for where in self.FIXTURE_HANGS:
            with self.subTest(fixture=where), tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "test_fixture_hangs.py").write_text(self.fixture_module(where), encoding="utf-8")
                env = {**os.environ, "KIT_TEST_TIMEOUT": "1", "PYTHONIOENCODING": "utf-8"}
                r = subprocess.run(kit.test_command(Path(tmp), Path(tmp)), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", env=env, timeout=90, check=False)
                self.assertNotEqual(r.returncode, 0, f"a hung {where} must fail the run:\n{r.stderr}")
                self.assertIn("Timeout", r.stderr, "the traceback dump says the limit was hit")
                self.assertIn(where, r.stderr, "the traceback names the fixture that hung")

    def test_a_limit_the_runner_cannot_arm_is_refused(self):
        """`nan` switched the limit off without a word; `inf` and 1e10 crashed the first test. (#94)"""
        import kit
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "test_quick.py").write_text(
                "import unittest\n\n\nclass Quick(unittest.TestCase):\n    def test_passes(self):\n        pass\n",
                encoding="utf-8")
            for value in ("nan", "inf", "-inf", "1e10", "-1", "soon"):
                with self.subTest(KIT_TEST_TIMEOUT=value):
                    env = {**os.environ, "KIT_TEST_TIMEOUT": value, "PYTHONIOENCODING": "utf-8"}
                    r = subprocess.run(kit.test_command(Path(tmp), Path(tmp)), capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", env=env, timeout=90, check=False)
                    self.assertNotEqual(r.returncode, 0, f"KIT_TEST_TIMEOUT={value} must be refused")
                    self.assertIn("KIT_TEST_TIMEOUT", r.stderr, "the refusal names the setting")
                    self.assertNotIn("Traceback", r.stderr, "a refusal, not a crash")

    def test_the_limit_can_be_switched_off(self):
        import kit
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "test_quick.py").write_text(
                "import unittest\n\n\nclass Quick(unittest.TestCase):\n    def test_passes(self):\n        pass\n",
                encoding="utf-8")
            env = {**os.environ, "KIT_TEST_TIMEOUT": "0", "PYTHONIOENCODING": "utf-8"}
            r = subprocess.run(kit.test_command(Path(tmp), Path(tmp)), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env, timeout=90, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ran 1 test", r.stderr)

    def test_pytest_runs_under_the_same_limit(self):
        """`python -m pytest` is the documented alternative; it gets pytest-timeout at the runner's limit."""
        import tomllib

        from tests import runner
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        extra = project["project"]["optional-dependencies"]["test"]
        self.assertTrue(any(re.match(r"pytest-timeout\b", dep) for dep in extra),
                        "pytest-timeout belongs in the test extra, or the ini timeout below means nothing")
        self.assertEqual(float(project["tool"]["pytest"]["ini_options"]["timeout"]), runner.DEFAULT_LIMIT)


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

    @staticmethod
    def kit_modules() -> set[str]:
        """Every top-level module of this repository that `import kit` loads, kit included."""
        import ast
        local = {p.stem for p in ROOT.glob("*.py")}
        seen, todo = set(), ["kit"]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            tree = ast.parse((ROOT / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
                todo += [n.split(".")[0] for n in names if n.split(".")[0] in local]
        return seen

    def test_every_module_kit_imports_is_in_the_wheel(self):
        """`kitredact` was missing from py-modules, so an installed wheel could not `import kit` (#114)."""
        import tomllib
        listed = set(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["py-modules"])
        needed = self.kit_modules()
        self.assertIn("kitredact", needed, "the import walk has gone stale")
        self.assertEqual(needed - listed, set(), "pyproject.toml [tool.setuptools] py-modules")

    def test_every_module_is_byte_compiled_and_scanned(self):
        """`make lint` byte-compiles on the floor Python and CI runs bandit; both name the modules one by one."""
        for name in sorted(self.kit_modules()):
            with self.subTest(module=name):
                self.assertIn(f" {name}.py", make_target("lint"), "Makefile `lint` compileall line")
        if not HAVE_WORKFLOW:
            self.skipTest("no .github/ — running from the release archive, which strips it")
        bandit = next(l for l in WORKFLOW.read_text(encoding="utf-8").splitlines() if "bandit" in l and " -r " in l)
        for name in sorted(self.kit_modules()):
            with self.subTest(module=name):
                self.assertIn(f" {name}.py", bandit, "ci.yml bandit step")

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


class TheSuiteRunsFromTheReleaseArchive(unittest.TestCase):
    """`make release` strips `.github/`, and the zip is where a tester runs the suite without a clone."""

    def test_no_test_reads_dot_github_without_a_skip_guard(self):
        stripped = ".github"
        for module in sorted((ROOT / "tests").glob("test_*.py")):
            text = module.read_text(encoding="utf-8")
            if stripped not in text:
                continue
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if stripped not in line or line.lstrip().startswith("#"):
                    continue
                # Constructing the Path is harmless; only a READ errors when the file is absent.
                if not any(op in line for op in ("read_text", "read_bytes", "open(", "glob(",
                                                 "safe_load", "iterdir")):
                    continue
                window = "\n".join(lines[max(0, i - 8):i + 3])
                self.assertTrue(
                    "skipTest" in window or "is_file()" in window or "HAVE_WORKFLOW" in window,
                    f"{module.name}:{i + 1} reads {stripped} with no skip guard — the suite "
                    f"then errors when run from the release archive, which strips it:\n  {line.strip()}")
