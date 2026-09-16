"""Documentation hygiene: links resolve, every doc has a title, README indexes the docs, CLI commands mentioned exist."""
import re
import subprocess
import sys
import unittest
import urllib.parse

from tests.helpers import DOCS, ROOT, kitlib

LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
MCP_INSTALL = re.compile(r"""(?:--with|pip install)\s+['"]?(mcp[^'"`\s]*)""")


class Docs(unittest.TestCase):
    def docs(self):
        return sorted(list(DOCS.rglob("*.md")) + [ROOT / "README.md"])

    def test_docs_exist(self):
        for name in ["quickstart.md", "vault-guide.md", "tools.md", "local-llm.md", "models.md", "plugins.md", "okf.md", "examples.md", "testing.md", "faq.md", "knowledge-graph.md", "reconciliation.md", "security.md",
                     "platforms/macos.md", "platforms/windows.md", "platforms/linux.md"]:
            self.assertTrue((DOCS / name).exists(), name)

    def test_relative_links_resolve(self):
        for doc in self.docs():
            text = re.sub(r"```.*?```", "", doc.read_text(encoding="utf-8"), flags=re.DOTALL)
            for m in LINK.finditer(text):
                target = m.group(1)
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                path = (doc.parent / urllib.parse.unquote(target.split("#")[0])).resolve()
                self.assertTrue(path.exists(), f"{doc.relative_to(ROOT)}: broken link {target}")

    def test_every_doc_has_h1_and_no_todo(self):
        for doc in self.docs():
            text = doc.read_text(encoding="utf-8")
            # A decision record opens with OKF frontmatter, by design -- that block is what makes
            # every claim in the repo greppable for its re-check date. Look for the H1 after it.
            body = re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", "", text, count=1, flags=re.DOTALL)
            self.assertTrue(body.lstrip().startswith("# "), f"{doc.name} must have an H1")
            self.assertFalse(re.search(r"^\s*(TODO|TBD|FIXME)\b", body, re.M), f"{doc.name} has a placeholder marker")

    def test_readme_links_all_docs(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for doc in DOCS.glob("*.md"):
            self.assertIn(f"docs/{doc.name}", readme, f"README should link docs/{doc.name}")

    def test_kit_commands_in_docs_exist(self):
        help_text = subprocess.run([sys.executable, str(ROOT / "kit.py"), "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False).stdout
        mentioned = set()
        for doc in self.docs():
            mentioned |= set(re.findall(r"kit\.py (doctor|init|validate|index|mcp-config|llm|log|test|graph|reconcile|todos|minutes|proposals)\b", doc.read_text(encoding="utf-8")))
        self.assertTrue(mentioned)
        for cmd in mentioned:
            self.assertIn(cmd, help_text)

    def mcp_spec(self) -> str:
        """The one place the SDK version is decided. Every other copy is checked against it."""
        extra = re.search(r'(?m)^mcp = \["([^"]+)"\]',
                          (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIsNotNone(extra, "pyproject must declare the mcp extra")
        return extra.group(1)

    def test_documented_mcp_installs_carry_the_pin(self):
        """Every `--with mcp` / `pip install mcp` a reader copy-pastes must be pyproject's spec.

        Both bounds, not one: the bridge speaks a single major of the SDK, and the other majors
        do not merely lack a feature — the server class is not there under the name it imports.
        """
        spec = self.mcp_spec()
        found = []
        for path in self.docs() + [ROOT / "ALPHA.md", ROOT / "kit.py", ROOT / "mcp/obsidian_bridge.py"]:
            for m in MCP_INSTALL.finditer(path.read_text(encoding="utf-8")):
                found.append((path.relative_to(ROOT), m.group(1)))
        self.assertTrue(found, "no documented mcp install command found — did the wording change?")
        for where, documented in found:
            self.assertEqual(documented, spec, f"{where}: `{documented}` must be pyproject's `{spec}`")

    def test_every_declared_mcp_pin_matches_pyproject(self):
        """The declarations, as opposed to the prose: each is an install path of its own.

        `pip install -r`, `make test`, `uv run` reading the bridge's PEP 723 header, the test kit
        a tester runs — a copy left behind hands somebody an SDK the bridge cannot drive, on a
        lane nobody re-reads. Dependabot is the fifth, and the only one that can move on its own.
        """
        spec = self.mcp_spec()
        sites = {
            "requirements.txt": r"(?m)^#\s*(mcp\S+)\s*$",
            "Makefile": r"(?m)^MCP\s*\?=\s*(\S+)\s*$",
            "mcp/obsidian_bridge.py": r'(?m)^# dependencies = \[.*?"(mcp[^"]+)"',
            "testkit/testkit.py": r'"--with", "(mcp[^"]+)"',
        }
        for name, pattern in sites.items():
            declared = re.findall(pattern, (ROOT / name).read_text(encoding="utf-8"))
            self.assertTrue(declared, f"{name}: no mcp pin found — did the wording change?")
            for one in declared:
                self.assertEqual(one, spec, f"{name} pins `{one}`, pyproject pins `{spec}`")

        upper = re.search(r"<(\d+)", spec)
        self.assertIsNotNone(upper, f"`{spec}` needs an upper bound, or nothing holds the bot back")
        # `make release` strips `.github/`, and the zip is the one place a tester might run the
        # suite without a clone. The dependabot assertion is about CI config, not about what is
        # distributed, so it skips there rather than erroring.
        dependabot = ROOT / ".github/dependabot.yml"
        if not dependabot.is_file():
            self.skipTest("no .github/ — running from the release archive, which strips it")
        self.assertIn(f'versions: [">={upper.group(1)}"]',
                      dependabot.read_text(encoding="utf-8"),
                      "dependabot must ignore the major pyproject excludes")

    def test_tools_doc_lists_every_bridge_tool(self):
        names = re.findall(r'@mcp\.tool\(name="([^"]+)"\)',
                           (ROOT / "mcp/obsidian_bridge.py").read_text(encoding="utf-8"))
        self.assertGreater(len(names), 8, "no tool registrations found in the bridge")
        tools = (DOCS / "tools.md").read_text(encoding="utf-8")
        for name in names:
            self.assertIn(f"`{name}`", tools, f"docs/tools.md omits the bridge tool {name}")


class Versioning(unittest.TestCase):
    """One version, one place. An alpha tester's bug report is useless without it."""

    def test_version_has_a_single_source(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
        self.assertEqual(kitlib.KIT_VERSION, version)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = { file = "VERSION" }', pyproject,
                      "pyproject must read VERSION rather than restate it")
        self.assertNotRegex(pyproject, r'(?m)^version\s*=\s*"', "a literal version in pyproject will drift")

    def test_changelog_leads_with_unreleased_or_the_current_version(self):
        """CHANGELOG.md is generated by git-cliff, so what belongs at the top depends on whether
        this commit is a release. Untagged, `[Unreleased]` is the honest heading. Tagged, the
        top section must be this VERSION -- that is the drift this guards against.

        This used to return green on `[Unreleased]` without ever asking whether HEAD was tagged,
        so it passed at the one moment it was written to fail: v0.1.0 was cut from a tagged commit
        whose changelog said `[Unreleased]`, and shipped notes generated on a branch. (#43)
        """
        first = next(l for l in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
                     if l.startswith("## "))
        described = subprocess.run(["git", "describe", "--exact-match", "--tags", "HEAD"], cwd=ROOT,
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", check=False)
        tagged = described.stdout.strip() if described.returncode == 0 else ""
        if first.startswith("## [Unreleased]"):
            self.assertFalse(tagged,
                             f"HEAD is tagged {tagged} but CHANGELOG.md leads with [Unreleased]; "
                             "run `make changelog` before tagging")
            return                      # development tree; nothing is being released from here
        # git-cliff renders `## [0.1.0] - 2026-09-16`, brackets included.
        self.assertTrue(first.startswith(f"## [{kitlib.KIT_VERSION}]"),
                        f"CHANGELOG starts with {first!r}, expected [{kitlib.KIT_VERSION}]")

    def test_cli_reports_the_version(self):
        r = subprocess.run([sys.executable, str(ROOT / "kit.py"), "--version"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(kitlib.KIT_VERSION, r.stdout)


class Shipping(unittest.TestCase):
    """What a tester unzips. Build residue in a release is how you ship someone your machine."""

    IGNORED = ("egg-info", "__pycache__", ".pytest_cache", ".venv", "dist/", ".DS_Store", ".turbo")

    def test_no_build_residue_is_tracked(self):
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", check=False)
        if tracked.returncode != 0:
            self.skipTest("not a git checkout")
        offenders = [f for f in tracked.stdout.split("\n") if any(bad in f for bad in self.IGNORED)]
        self.assertEqual(offenders, [], f"build residue is tracked: {offenders}")

    def test_the_files_a_tester_needs_are_present(self):
        for name in ("ALPHA.md", "FEEDBACK.md", "README.md", "LICENSE", "THIRD-PARTY.md",
                     "VERSION", "Makefile", "docs/it-review.md", "docs/platforms/proxy.md",
                     "docs/platforms/wsl2.md"):
            self.assertTrue((ROOT / name).exists(), f"missing from the release: {name}")

    def test_alpha_md_names_the_shipped_version(self):
        self.assertIn(kitlib.KIT_VERSION, (ROOT / "ALPHA.md").read_text(encoding="utf-8"),
                      "ALPHA.md must state the build it describes")
