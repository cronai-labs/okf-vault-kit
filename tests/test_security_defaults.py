"""Defaults that contradicted docs/security.md and docs/it-review.md (#9).

Each test is the attack the docs promised was refused. They are defaults tests, not feature
tests: the point is what happens when nobody passes a flag.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import ROOT, VAULT, temp_vault

sys.path.insert(0, str(ROOT / "mcp"))
import bridge_policy as bp
import kit
import obsidian_bridge as ob

KIT = [sys.executable, str(ROOT / "kit.py")]


def run(*args, **kw):
    return subprocess.run(KIT + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, **kw)


def guarded(vault: Path, **kw) -> bp.Guarded:
    return bp.Guarded(ob.FsBackend(vault), bp.Policy(**kw), vault)


class QmdIsNotASecondDoor(unittest.TestCase):
    """qmd's own MCP server has no policy layer and indexes confidential notes."""

    def _config(self, *extra):
        r = run("mcp-config", "--client", "lmstudio", "--vault", str(VAULT), *extra,
                env=dict(os.environ, KIT_NO_UV="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.split("\n# would be written")[0]), r.stdout

    def test_qmd_is_not_registered_by_default(self):
        cfg, out = self._config()
        self.assertNotIn("qmd", cfg["mcpServers"],
                         "a second, unguarded door into the notes the bridge hides")
        self.assertIn("obsidian-vault", cfg["mcpServers"], "search must still be available")
        self.assertIn("--with-qmd-mcp", out, "the omission has to be explained, not silent")

    def test_opting_in_says_what_it_costs(self):
        cfg, out = self._config("--with-qmd-mcp")
        self.assertIn("qmd", cfg["mcpServers"])
        self.assertIn("NOT behind this kit's policy layer", out)


class OverwriteIsDeniedByDefault(unittest.TestCase):
    """`overwrite` was the one write argument no policy check inspected."""

    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, ignore_errors=True)
        self.note = "04-knowledge/overwrite-probe.md"
        (self.vault / self.note).parent.mkdir(parents=True, exist_ok=True)
        (self.vault / self.note).write_text(
            "---\ntype: concept\ntitle: Probe\n---\n\nthe original body\n", encoding="utf-8")

    def test_a_model_cannot_blank_an_existing_note(self):
        g = guarded(self.vault)
        with self.assertRaises(bp.PolicyError) as caught:
            g.create_note(self.note, "", overwrite=True)
        self.assertIn("overwrit", str(caught.exception).lower())
        self.assertIn("the original body", (self.vault / self.note).read_text(encoding="utf-8"))

    def test_the_flag_loosens_it(self):
        g = guarded(self.vault, allow_overwrite=True)
        g.create_note(self.note, "replaced", overwrite=True)
        self.assertIn("replaced", (self.vault / self.note).read_text(encoding="utf-8"))

    def test_creating_a_new_note_is_unaffected(self):
        g = guarded(self.vault)
        g.create_note("04-knowledge/brand-new-probe.md", "hello")
        self.assertTrue((self.vault / "04-knowledge/brand-new-probe.md").exists())

    def test_the_policy_description_says_so(self):
        self.assertIn("overwrite denied", bp.Policy().describe())
        self.assertIn("overwrite allowed", bp.Policy(allow_overwrite=True).describe())


class LlmAskFailsClosed(unittest.TestCase):
    """`llm ask` placed a confidential note's body into the prompt when its YAML would not parse."""

    def setUp(self):
        self.vault = temp_vault()
        self.addCleanup(shutil.rmtree, self.vault.parent, ignore_errors=True)
        self.dir = self.vault / "04-knowledge"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _write(self, name, text, encoding="utf-8"):
        (self.dir / name).write_text(text, encoding=encoding)
        return f"04-knowledge/{name}"

    def test_broken_yaml_counts_as_confidential(self):
        rel = self._write("broken.md",
                          "---\nsensitivity: confidential\ntitle: [unclosed\n---\n\nthe secret\n")
        self.assertTrue(kit._confidential(self.vault, rel),
                        "unparseable frontmatter must not read as 'no sensitivity set'")

    def test_a_bom_hidden_block_counts_as_confidential(self):
        rel = self._write("bom.md",
                          "---\nsensitivity: confidential\ntitle: Hidden\n---\n\nthe secret\n",
                          encoding="utf-8-sig")
        self.assertTrue(kit._confidential(self.vault, rel))

    def test_a_plain_note_is_still_visible(self):
        rel = self._write("plain.md", "---\ntype: concept\ntitle: Fine\n---\n\nbody\n")
        self.assertFalse(kit._confidential(self.vault, rel))

    def test_a_confidential_note_is_hidden(self):
        rel = self._write("secret.md",
                          "---\ntype: concept\nsensitivity: confidential\n---\n\nbody\n")
        self.assertTrue(kit._confidential(self.vault, rel))

    def test_a_path_that_does_not_resolve_is_not_withheld(self):
        """The bridge answers False here too; mirroring it wrongly withholds notes that are fine."""
        self.assertFalse(kit._confidential(self.vault, "04-knowledge/not-a-real-note"))

    def test_the_cli_and_the_bridge_agree(self):
        """They diverged once. One helper now backs both, and this is the input that split them."""
        rel = self._write("agree.md",
                          "---\nsensitivity: confidential\ntitle: [unclosed\n---\n\nsecret\n")
        g = guarded(self.vault)
        self.assertEqual(kit._confidential(self.vault, rel), g._confidential(rel))


if __name__ == "__main__":
    unittest.main()
