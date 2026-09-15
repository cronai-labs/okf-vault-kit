"""End-to-end: qmd indexes the template vault and finds the right notes.

Runs automatically when `qmd` is on PATH. qmd's config and index are redirected into a temp directory
via XDG_CONFIG_HOME/XDG_CACHE_HOME, so the user's own collections and index database are never touched.
The GGUF models are the one thing we keep sharing — symlinked from the real cache, because re-downloading
~2 GB per test run is not a trade anyone would make.

  KIT_E2E_EMBED=1   also embed and run a vector + hybrid query (downloads ~300 MB + ~1.7 GB of models once)
  KIT_E2E_BENCH=1   also run `qmd bench` with tests/fixtures/qmd-bench.json (needs all three models)
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT, env_flag, has_tool, temp_vault

FIXTURE = ROOT / "tests/fixtures/qmd-bench.json"


REAL_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "qmd" / "index.yml"
REAL_MODELS = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "qmd" / "models"
_ISOLATED: dict[str, str] = {}


def qmd(*args, cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    env = dict(os.environ, NO_COLOR="1", QMD_TRUST_LOCAL_CONFIG="1", **_ISOLATED)
    return subprocess.run(["qmd", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)


@unittest.skipUnless(has_tool("qmd"), "qmd not installed (npm install -g @tobilu/qmd)")
class QmdEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vault = temp_vault()
        cls.xdg = Path(tempfile.mkdtemp(prefix="okf-qmd-xdg-"))
        cache = cls.xdg / "cache" / "qmd"
        cache.mkdir(parents=True)
        if REAL_MODELS.is_dir():
            try:
                (cache / "models").symlink_to(REAL_MODELS, target_is_directory=True)
            except (OSError, NotImplementedError):
                pass  # no symlinks (Windows without developer mode): models download into the temp cache
        _ISOLATED.update(XDG_CONFIG_HOME=str(cls.xdg / "config"), XDG_CACHE_HOME=str(cls.xdg / "cache"))
        cls.real_config_before = REAL_CONFIG.read_text(encoding="utf-8") if REAL_CONFIG.exists() else None
        try:
            r = qmd("collection", "add", ".", "--name", "kit-e2e", "--mask", "**/*.md", cwd=cls.vault)
            assert r.returncode == 0, r.stdout + r.stderr
            qmd("context", "add", "qmd://kit-e2e", "OKF vault kit template: journal, meetings, projects, decisions, knowledge", cwd=cls.vault)
            r = qmd("update", cwd=cls.vault)
            assert r.returncode == 0, r.stdout + r.stderr
        except Exception:
            cls.tearDownClass()   # a failed setUp must not leave the next run to trip over it
            raise

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.vault.parent, ignore_errors=True)
        shutil.rmtree(cls.xdg, ignore_errors=True)   # does not follow the models symlink
        _ISOLATED.clear()

    def test_the_users_own_qmd_config_is_untouched(self):
        """The suite is told to run in the quickstart. It must not appear in anyone's real index."""
        after = REAL_CONFIG.read_text(encoding="utf-8") if REAL_CONFIG.exists() else None
        self.assertEqual(after, self.real_config_before, f"{REAL_CONFIG} was modified by the test run")
        self.assertNotIn("kit-e2e", after or "")

    def test_status_reports_collection(self):
        r = qmd("status", cwd=self.vault)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("kit-e2e", r.stdout)

    def test_keyword_search_finds_concept_note(self):
        r = qmd("search", "hybrid search reciprocal rank fusion", "--format", "json", "-n", "5", "-c", "kit-e2e", cwd=self.vault)
        self.assertEqual(r.returncode, 0, r.stderr)
        hits = json.loads(r.stdout)
        files = [h.get("file", "") for h in hits]
        self.assertTrue(any(f.endswith("07-knowledge/hybrid-search.md") for f in files), files)

    def test_get_by_path(self):
        r = qmd("get", "07-knowledge/reindex-qmd.md", "--no-line-numbers", cwd=self.vault)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("qmd embed", r.stdout)

    @unittest.skipUnless(env_flag("KIT_E2E_EMBED"), "set KIT_E2E_EMBED=1 to embed and run vector/hybrid queries")
    def test_vector_and_hybrid_query(self):
        r = qmd("embed", cwd=self.vault, timeout=3600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = qmd("vsearch", "how do we combine keyword and semantic results", "--format", "json", "-n", "3", "-c", "kit-e2e", cwd=self.vault)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any(h.get("file", "").endswith("hybrid-search.md") for h in json.loads(r.stdout)))
        r = qmd("query", "why did the team pick qmd over a hosted vector database", "--format", "json", "-n", "3", "-c", "kit-e2e", cwd=self.vault, timeout=3600)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any("adopt-qmd" in h.get("file", "") for h in json.loads(r.stdout)))

    @unittest.skipUnless(env_flag("KIT_E2E_BENCH"), "set KIT_E2E_BENCH=1 to run the retrieval benchmark")
    def test_bench_fixture(self):
        qmd("embed", cwd=self.vault, timeout=3600)
        r = qmd("bench", str(FIXTURE), "-c", "kit-e2e", "--json", cwd=self.vault, timeout=3600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("hybrid", r.stdout.lower())
