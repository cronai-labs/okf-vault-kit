"""The kit config at the vault root, `.okf-vault-kit.yaml`, and the move from `.kit/config.yml` (#135).

One resolver (`kitlib.config_rel`) says which file is in use: the new one whenever anything is at its
path, else the old one with a warning, else none. A broken new file fails closed and is never passed
over for a good old one. `kit.py config migrate` moves the file and updates the .gitignore `init`
wrote; `agent-settings` names the new file and denies `.kit/` whole; the bridge denies writes to it.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import kitagent
import kitgraph
from tests.helpers import ROOT, VAULT, kitlib, kitproviders
from tests.test_agent_settings import MANIFEST, SETTINGS, agent, read_settings
from tests.test_confidential_floor import (
    BROKEN,
    CONFIG,
    HARRIER_REL,
    NO_FM_REL,
    OPEN_REL,
    floor_vault,
    symlink,
    write,
)

sys.path.insert(0, str(ROOT / "mcp"))
import bridge_policy as bp
import vault_bridge as ob

KIT = [sys.executable, str(ROOT / "kit.py")]
NEW, OLD = kitlib.CONFIG_REL, kitlib.LEGACY_CONFIG_REL
OPEN_CONFIG = CONFIG + '  open_folders: ["04-Areas", "07-knowledge", "08-RESOURCES"]\n'
# The .gitignore `init` wrote before #135 (#54), and the user's own lines around it.
OLD_INIT_BLOCK = ("# per-machine kit state: graph, bridge audit log, proposals, agent settings\n"
                  ".kit/*\n"
                  "# except the kit config: confidential folders, path rules and open folders must reach every clone\n"
                  "!.kit/config.yml\n")
NEW_INIT_BLOCK = "# per-machine kit state: graph, bridge audit log, proposals, agent settings\n.kit/\n"
WARNING = "warning: this vault's kit config is still at .kit/config.yml"


def run(*args, env=None):
    env = {k: v for k, v in (env or os.environ).items() if k != "KIT_VAULT"}
    return subprocess.run(KIT + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=False, env={**env, "PYTHONIOENCODING": "utf-8"})


def migrate(vault: Path, *flags: str):
    return run("config", "migrate", "--vault", str(vault), *flags)


def to_old_place(vault: Path) -> None:
    """Move the vault's config back to where a kit before #135 kept it."""
    (vault / ".kit").mkdir(exist_ok=True)
    (vault / NEW).replace(vault / OLD)


def every_file(vault: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".kit")]
        out += [Path(dirpath, n).relative_to(vault).as_posix() for n in filenames]
    return sorted(out)


class _Vault(unittest.TestCase):
    def setUp(self):
        self.v = floor_vault()                 # CONFIG at the new place
        self.addCleanup(shutil.rmtree, self.v.parent, ignore_errors=True)
        kitlib._LEGACY_WARNED.clear()
        self.addCleanup(kitlib._LEGACY_WARNED.clear)


class TheResolver(_Vault):
    def quiet(self, fn, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = fn(*args)
        return result, err.getvalue()

    def test_the_new_file_is_read(self):
        self.assertEqual(kitlib.config_rel(self.v), NEW)
        self.assertEqual(kitlib.confidentiality_policy(self.v).declared_folders, ("04-Areas/Board",))
        self.assertIs(kitlib.confidentiality(self.v, NO_FM_REL), True)
        write(self.v, NEW, CONFIG + 'collection: "notes-here"\n')
        self.assertEqual(kitproviders.read_config(self.v)["collection"], "notes-here")
        self.assertIsNone(kitlib.config_notice(self.v))

    def test_the_old_place_is_read_with_one_warning(self):
        to_old_place(self.v)
        rel, err = self.quiet(kitlib.config_rel, self.v)
        self.assertEqual(rel, OLD)
        self.assertIn(WARNING, err)
        self.assertIn("kit.py config migrate", err)
        (policy, err2) = self.quiet(kitlib.confidentiality_policy, self.v)
        self.assertEqual(policy.declared_folders, ("04-Areas/Board",))
        self.assertEqual(err2, "", "once per vault and process")
        self.assertIs(kitlib.confidentiality(self.v, NO_FM_REL), True)
        write(self.v, OLD, CONFIG + 'providers:\n  search: naive\n')
        self.assertEqual(kitproviders.load_config(self.v), {"search": "naive"}, "the lenient reader follows it too")
        self.assertIn("still at .kit/config.yml", kitlib.config_notice(self.v))

    def test_the_cli_warns_once_on_stderr_and_keeps_stdout_clean(self):
        to_old_place(self.v)
        r = run("policy", "export", "--vault", str(self.v))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.count(WARNING), 1, r.stderr)
        self.assertNotIn("warning", r.stdout)
        self.assertEqual(json.loads(r.stdout)["source"], OLD)

    def test_with_both_files_the_new_one_wins_and_validate_and_doctor_say_the_old_is_ignored(self):
        write(self.v, OLD, 'confidentiality:\n  confidential_folders: ["03-projects"]\n')
        self.assertEqual(kitlib.config_rel(self.v), NEW)
        self.assertIs(kitlib.confidentiality(self.v, NO_FM_REL), True)
        self.assertIs(kitlib.confidentiality(self.v, "03-projects/search-relaunch.md"), False, "the old file is not read")
        r = run("validate", "--vault", str(self.v))
        self.assertIn(".kit/config.yml is ignored, since .okf-vault-kit.yaml exists and wins", r.stdout)
        self.assertNotIn(WARNING, r.stderr)
        r = run("doctor", "--vault", str(self.v), "--base-url", "http://127.0.0.1:9")
        row = next(ln for ln in r.stdout.splitlines() if ln.startswith("kit config"))
        self.assertIn("warning: .kit/config.yml is ignored", row)

    def test_doctor_names_the_file_in_use(self):
        def row() -> str:
            r = run("doctor", "--vault", str(self.v), "--base-url", "http://127.0.0.1:9")
            return next(ln for ln in r.stdout.splitlines() if ln.startswith("kit config"))
        self.assertIn("ok — .okf-vault-kit.yaml", row())
        write(self.v, NEW, BROKEN["yaml"])
        broken = row()
        self.assertIn("WARNING: .okf-vault-kit.yaml is broken", broken, "never `ok` while every reader hides everything")
        self.assertNotIn("unclosed", broken, "the reason stays out of a report that leaves the machine")
        write(self.v, NEW, CONFIG)
        to_old_place(self.v)
        self.assertIn("warning: the config is still at .kit/config.yml", row())
        write(self.v, OLD, BROKEN["yaml"])
        self.assertIn("WARNING: .kit/config.yml is broken", row())
        (self.v / OLD).unlink()
        self.assertIn("none — no .okf-vault-kit.yaml", row())

    def test_a_broken_new_file_beside_a_good_old_one_fails_closed(self):
        write(self.v, OLD, CONFIG)
        for name, text in BROKEN.items():
            with self.subTest(name):
                write(self.v, NEW, text + "#" * len(name))     # a new size each time: the cache keys on it
                with self.assertRaisesRegex(kitlib.PolicyConfigError, r"^\.okf-vault-kit\.yaml: "):
                    kitlib.confidentiality_policy(self.v)
                self.assertIsNone(kitlib.confidentiality(self.v, OPEN_REL), "every path, the open ones too")

    def test_a_broken_old_file_alone_fails_closed_too(self):
        to_old_place(self.v)
        for name, text in BROKEN.items():
            with self.subTest(name):
                write(self.v, OLD, text + "#" * len(name))
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(kitlib.PolicyConfigError, r"^\.kit/config\.yml: "):
                        kitlib.confidentiality_policy(self.v)
                    self.assertIsNone(kitlib.confidentiality(self.v, OPEN_REL))

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "needs POSIX modes that bind the user")
    def test_an_old_config_in_a_folder_that_cannot_be_read_is_no_crash(self):
        """agent-settings denies `.kit/` whole now, so a vault still on `.kit/config.yml` meets a
        folder its own kit runs may not look into. Before Python 3.14 `Path.exists()` raised there,
        outside the reader's guard, and the CLI stopped with a traceback; the floors fail closed."""
        to_old_place(self.v)
        os.chmod(self.v / ".kit", 0)
        self.addCleanup(os.chmod, self.v / ".kit", 0o755)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(kitproviders.read_config(self.v), {})
            self.assertEqual(kitproviders.load_config(self.v), {})
            self.assertIsNone(kitlib.confidentiality(self.v, OPEN_REL), "the floors fail closed")

    def test_a_new_path_that_is_no_file_is_never_passed_over(self):
        write(self.v, OLD, CONFIG)
        (self.v / NEW).unlink()
        (self.v / NEW).mkdir()
        self.assertEqual(kitlib.config_rel(self.v), NEW)
        self.assertIsNone(kitlib.confidentiality(self.v, OPEN_REL), "a folder")
        (self.v / NEW).rmdir()
        symlink(self, self.v / NEW, "nowhere.yaml")
        self.assertEqual(kitlib.config_rel(self.v), NEW)
        with self.assertRaisesRegex(kitlib.PolicyConfigError, "link to nothing"):
            kitlib.confidentiality_policy(self.v)
        self.assertIsNone(kitlib.confidentiality(self.v, OPEN_REL), "a link to nothing")

    def test_parity_the_decision_is_the_same_from_either_place(self):
        for config in (CONFIG, OPEN_CONFIG):
            with self.subTest(open_folders="open_folders" in config):
                write(self.v, NEW, config)
                write(self.v, "08-resources/hidden-ledger.csv", "a,b\n")
                rels = every_file(self.v) + ["no/such/note.md", "04-areas/board", ".trash/" + HARRIER_REL]
                at_new = {rel: kitlib.confidentiality(self.v, rel) for rel in rels}
                to_old_place(self.v)
                with contextlib.redirect_stderr(io.StringIO()):
                    at_old = {rel: kitlib.confidentiality(self.v, rel) for rel in rels}
                self.assertEqual(at_new, at_old)
                self.assertIn(True, at_new.values())
                self.assertIn(False, at_new.values())
                (self.v / OLD).replace(self.v / NEW)

    def test_moving_the_file_makes_the_signature_stale(self):
        before = kitgraph.note_set_signature(self.v)
        self.assertIn((NEW, kitlib.config_fingerprint(self.v)), before)
        to_old_place(self.v)
        with contextlib.redirect_stderr(io.StringIO()):
            moved = kitgraph.note_set_signature(self.v)
            self.assertEqual(moved, ob.FsBackend(self.v)._note_set_signature(), "one definition")
        self.assertNotEqual(before, moved, "same bytes, other place: a graph built before must be rebuilt")
        (self.v / OLD).replace(self.v / NEW)
        self.assertEqual(kitgraph.note_set_signature(self.v), before)


class Migrate(_Vault):
    def setUp(self):
        super().setUp()
        self.bytes = b"# my floors\r\n" + CONFIG.encode() + b'collection: "caf\xc3\xa9"\r\n'
        (self.v / NEW).unlink()
        (self.v / ".kit").mkdir(exist_ok=True)
        (self.v / OLD).write_bytes(self.bytes)
        self.gitignore = "# mine\r\n*.pdf\r\n" + OLD_INIT_BLOCK + ".obsidian/cache\nprivate/"
        (self.v / ".gitignore").write_text(self.gitignore, encoding="utf-8", newline="")

    def state(self) -> tuple:
        def read(rel):
            p = self.v / rel
            return p.read_bytes() if p.exists() else None
        return read(NEW), read(OLD), read(OLD + ".migrated"), read(".gitignore")

    def test_dry_run_apply_and_a_second_run(self):
        before = self.state()
        r = migrate(self.v)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would move .kit/config.yml to .okf-vault-kit.yaml", r.stdout)
        self.assertIn("would update .gitignore", r.stdout)
        self.assertIn("dry run: nothing written", r.stdout)
        self.assertEqual(self.state(), before, "a dry run writes nothing")
        r = migrate(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("moved .kit/config.yml to .okf-vault-kit.yaml", r.stdout)
        self.assertIn("updated .gitignore", r.stdout)
        new, old, aside, gi = self.state()
        self.assertEqual((new, old, aside), (self.bytes, None, None), "moved, bytes and all")
        self.assertEqual(gi.decode(), "# mine\r\n*.pdf\r\n" + NEW_INIT_BLOCK + ".obsidian/cache\nprivate/",
                         "only init's block changes; the rest, line endings included, stays")
        self.assertEqual(sorted(p.name for p in self.v.iterdir() if p.name.startswith(".okf")), [NEW], "no temp file left")
        self.assertEqual(kitlib.confidentiality_policy(self.v).declared_folders, ("04-Areas/Board",))
        after = self.state()
        r = migrate(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to do", r.stdout)
        self.assertEqual(self.state(), after)

    def test_a_gitignore_without_the_block_is_left_alone(self):
        mine = b"# my own rules\r\n.kit/\r\n*.pdf"
        (self.v / ".gitignore").write_bytes(mine)
        self.assertEqual(migrate(self.v, "--apply").returncode, 0)
        self.assertEqual((self.v / ".gitignore").read_bytes(), mine)
        (self.v / ".gitignore").unlink()
        self.assertIn("nothing to do", migrate(self.v).stdout, "no .gitignore at all")

    def test_only_the_gitignore_left_is_finished_too(self):
        (self.v / OLD).replace(self.v / NEW)          # moved by hand, or a run stopped after the move
        r = migrate(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("moved", r.stdout)
        self.assertIn("updated .gitignore", r.stdout)
        self.assertIn("nothing to do", migrate(self.v).stdout)

    def test_both_files_are_refused_without_force(self):
        write(self.v, NEW, "collection: other\n")
        before = self.state()
        for flags in ((), ("--apply",)):
            r = migrate(self.v, *flags)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("refused: both .okf-vault-kit.yaml and .kit/config.yml exist", r.stderr)
            self.assertIn("--force", r.stderr)
            self.assertEqual(self.state(), before)

    def test_force_sets_the_old_file_aside_and_says_so(self):
        write(self.v, NEW, "collection: other\n")
        r = migrate(self.v, "--force")
        self.assertIn("would set aside .kit/config.yml as .kit/config.yml.migrated", r.stdout)
        r = migrate(self.v, "--force", "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("set aside .kit/config.yml as .kit/config.yml.migrated, not deleted", r.stdout)
        new, old, aside, _ = self.state()
        self.assertEqual((new, old, aside), (b"collection: other\n", None, self.bytes))
        self.assertIn("nothing to do", migrate(self.v, "--force", "--apply").stdout)

    def test_force_never_writes_over_a_file_set_aside_before(self):
        write(self.v, NEW, "collection: other\n")
        (self.v / (OLD + ".migrated")).write_bytes(b"earlier\n")
        before = self.state()
        r = migrate(self.v, "--force", "--apply")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn(".kit/config.yml.migrated exists already", r.stderr)
        self.assertEqual(self.state(), before)

    def test_an_old_copy_with_the_same_bytes_is_what_a_stopped_run_leaves_and_is_removed(self):
        (self.v / NEW).write_bytes(self.bytes)
        r = migrate(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("removed .kit/config.yml: it holds the same bytes", r.stdout)
        self.assertEqual(self.state()[:3], (self.bytes, None, None))

    def test_a_gitignore_rule_it_cannot_rewrite_is_named_not_called_gone(self):
        """Only init's exact block is rewritten. A keep rule in any other shape, or a .gitignore that
        is a link, is left for the user, and the output says so rather than "no rule is left"."""
        (self.v / OLD).replace(self.v / NEW)
        for keep in ("!.kit/config.yml", "!/.kit/config.yml"):
            rules = f"/.kit/*\n{keep}\n"
            with self.subTest(keep=keep):
                (self.v / ".gitignore").write_text(rules, encoding="utf-8", newline="")
                for flags in ((), ("--apply",)):
                    r = migrate(self.v, *flags)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertNotIn("rule for it is left", r.stdout)
                    self.assertIn("by hand", r.stdout)
                    self.assertIn(f"`{keep}`", r.stdout)
                self.assertEqual((self.v / ".gitignore").read_text(encoding="utf-8"), rules)
        (self.v / ".gitignore").unlink()
        (self.v / "elsewhere").write_text(self.gitignore, encoding="utf-8", newline="")
        symlink(self, self.v / ".gitignore", "elsewhere")
        r = migrate(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("rule for it is left", r.stdout)
        self.assertIn("it is a link", r.stdout)
        self.assertEqual((self.v / "elsewhere").read_bytes(), self.gitignore.encode())

    def test_it_says_to_upgrade_every_machine_before_the_old_file_goes(self):
        """A kit before #135 reads only `.kit/config.yml`, and a missing one means no floors: a
        bridge started before the upgrade, or a clone on an older kit, would apply none."""
        for flags in ((), ("--apply",)):
            r = migrate(self.v, *flags)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("upgrade the kit on every machine and restart any running bridge", r.stdout)
        self.assertNotIn("restart any running bridge", migrate(self.v).stdout, "nothing to do: nothing to say")

    def test_the_sample_vault_is_refused_without_vault(self):
        r = run("config", "migrate", "--apply")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("refused: no vault given", r.stderr)
        r = run("config", "migrate", "--apply", "--force")
        self.assertEqual(r.returncode, 2, r.stdout)
        r = run("config", "migrate")
        self.assertEqual(r.returncode, 0, "a dry run reads the sample, as other dry runs do")
        self.assertIn("note: reading the kit's sample vault", r.stderr)
        self.assertFalse((VAULT / NEW).exists())

    def test_a_shortened_flag_is_refused(self):
        r = migrate(self.v, "--app")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.state()[1], self.bytes)


def to_old_settings(vault: Path, open_mode: bool) -> None:
    """Rewrite settings.json and the manifest into what a kit before #135 wrote: the config at
    `.kit/config.yml`, and the files in `.kit/` denied one by one rather than the folder whole."""
    old_reads = ["Read(./.kit/agent-settings/**)", "Read(./.kit/graph.*)", "Read(./.kit/bridge-audit.jsonl)",
                 "Read(./.kit/proposals/**)"]
    old_sbx = ["./.kit/agent-settings", "./.kit/graph.*", "./.kit/bridge-audit.jsonl", "./.kit/proposals"]

    def swap(entries: list, new_old: dict) -> list:
        out = []
        for e in entries:
            out += new_old.get(e, [e])
        return out
    deny = {"Edit(./.okf-vault-kit.yaml)": ["Edit(./.kit/config.yml)"]}
    sbx = {"./.okf-vault-kit.yaml": ["./.kit/config.yml"]}
    if not open_mode:          # in allow-open mode `./` covered .kit, and only `.kit` inside an open folder was listed
        deny["Read(./.kit/**)"] = old_reads
        sbx["./.kit"] = old_sbx
    s = read_settings(vault)
    m = json.loads((vault / MANIFEST).read_text(encoding="utf-8"))
    fs = s["sandbox"]["filesystem"]
    s["permissions"]["deny"] = swap(s["permissions"]["deny"], deny)
    m["permissions.deny"] = swap(m["permissions.deny"], deny)
    for key in ("denyRead", "denyWrite", "allowRead"):
        if key in fs:
            fs[key] = swap(fs[key], sbx)
            m[f"sandbox.filesystem.{key}"] = swap(m[f"sandbox.filesystem.{key}"], sbx)
    write(vault, SETTINGS, json.dumps(s, indent=2) + "\n")
    write(vault, MANIFEST, json.dumps(m, indent=2) + "\n")


class AgentSettings(_Vault):
    def plan(self):
        return kitagent.claude_code_plan(self.v, kitagent.policy_export(self.v))

    def test_both_modes_name_the_new_file_and_deny_kit_whole(self):
        for config in (CONFIG, OPEN_CONFIG):
            with self.subTest(open_folders="open_folders" in config):
                write(self.v, NEW, config)
                plan = self.plan()
                self.assertIn("Edit(./.okf-vault-kit.yaml)", plan.deny)
                self.assertIn("./.okf-vault-kit.yaml", plan.deny_write)
                self.assertIn("./.kit/agent-settings", plan.deny_write, "the manifest stays write-protected")
                self.assertNotIn("Read(./.okf-vault-kit.yaml)", plan.deny, "the agent's own kit runs read it")
                self.assertEqual([e for e in plan.deny + plan.deny_write + plan.deny_read + plan.allow_read
                                  if ".kit/config.yml" in e], [])
                if "open_folders" in config:
                    self.assertEqual(plan.allow_read[-1], "./.okf-vault-kit.yaml")
                    self.assertIn(".kit", plan.guard["folders"])
                    self.assertNotIn("./.kit", plan.deny_read, "`./` covers it outside the open folders")
                else:
                    self.assertIn("Read(./.kit/**)", plan.deny)
                    self.assertIn("./.kit", plan.deny_read)
                    self.assertEqual([e for e in plan.deny + plan.deny_read if e.startswith(("Read(./.kit/", "./.kit/"))],
                                     ["Read(./.kit/**)"], "one entry for the folder, none per file")

    def test_settings_from_before_drift_once_then_apply_replaces_them_and_says_so(self):
        for config in (CONFIG, OPEN_CONFIG):
            with self.subTest(open_folders="open_folders" in config):
                write(self.v, NEW, config)
                self.assertEqual(agent(self.v, "--apply").returncode, 0)
                to_old_settings(self.v, "open_folders" in config)
                self.assertEqual(agent(self.v, "--check").returncode, 1, "drift")
                self.assertTrue(kitagent.doctor_row(self.v)[1].startswith("drift:"))
                r = agent(self.v, "--apply")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("are replaced once", r.stdout)
                s = read_settings(self.v)
                text = json.dumps(s)
                for gone in ("Edit(./.kit/config.yml)", "./.kit/config.yml", "./.kit/graph.*", "./.kit/proposals"):
                    self.assertNotIn(f'"{gone}"', text)
                self.assertIn("Edit(./.okf-vault-kit.yaml)", s["permissions"]["deny"])
                r = agent(self.v, "--apply")
                self.assertIn("nothing to do", r.stdout)
                self.assertNotIn("replaced once", r.stdout)
                self.assertEqual(agent(self.v, "--check").returncode, 0)
                self.assertEqual(kitagent.doctor_row(self.v)[1], "ok")

    def test_a_config_still_at_the_old_place_stays_write_protected_and_is_flagged(self):
        to_old_place(self.v)
        with contextlib.redirect_stderr(io.StringIO()):
            plan = self.plan()
        self.assertIn("Edit(./.kit/config.yml)", plan.deny)
        self.assertIn("./.kit/config.yml", plan.deny_write)
        self.assertIn("Edit(./.okf-vault-kit.yaml)", plan.deny, "the new name is protected before it exists")
        self.assertTrue(any("kit.py config migrate --apply" in n for n in plan.notes))
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        with contextlib.redirect_stderr(io.StringIO()):
            row = kitagent.doctor_row(self.v)[1]
        self.assertIn("config still at .kit/config.yml", row)
        self.assertEqual(migrate(self.v, "--apply").returncode, 0)
        self.assertTrue(kitagent.doctor_row(self.v)[1].startswith("drift:"), "the old file's entries are stale now")
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        self.assertEqual(kitagent.doctor_row(self.v)[1], "ok")
        self.assertNotIn("./.kit/config.yml", json.dumps(read_settings(self.v)))


    def test_a_config_at_the_old_place_stays_write_protected_in_allow_open_mode_too(self):
        write(self.v, NEW, OPEN_CONFIG)
        to_old_place(self.v)
        with contextlib.redirect_stderr(io.StringIO()):
            plan = self.plan()
        self.assertIn("Edit(./.kit/config.yml)", plan.deny)
        self.assertIn("./.kit/config.yml", plan.deny_write)
        self.assertIn("Edit(./.okf-vault-kit.yaml)", plan.deny)
        self.assertIn("./.okf-vault-kit.yaml", plan.deny_write)
        self.assertTrue(any("kit.py config migrate --apply" in n for n in plan.notes))


class TheBridge(_Vault):
    def setUp(self):
        super().setUp()
        write(self.v, NEW, "collection: mine\n")      # no floors, no open folders: the deny list decides

    def test_writes_to_the_config_are_refused_by_default(self):
        self.assertIn(NEW, bp.DEFAULT_DENY_WRITE)
        g = bp.Guarded(ob.FsBackend(self.v), bp.Policy(audit_path=None), self.v)
        with self.assertRaisesRegex(bp.PolicyError, r"writes to `\.okf-vault-kit\.yaml` are denied"):
            g._check_write("create_note", NEW)
        # The tools write notes, so the name gets `.md`: that look-alike is refused as well.
        for call in (lambda: g.create_note(NEW, "confidentiality: {}\n", overwrite=False),
                     lambda: g.create_note(".OKF-Vault-Kit.yaml", "x")):
            with self.assertRaisesRegex(bp.PolicyError, "denied by policy"):
                call()
        self.assertEqual((self.v / NEW).read_text(encoding="utf-8"), "collection: mine\n")
        self.assertFalse((self.v / (NEW + ".md")).exists())
        g.create_note("03-projects/allowed", "fine")         # the deny list is not a blanket refusal
        self.assertTrue((self.v / "03-projects/allowed.md").exists())

    def test_help_names_the_default_list(self):
        r = subprocess.run([sys.executable, str(ROOT / "mcp" / "vault_bridge.py"), "--help"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", check=False)
        if r.returncode != 0:
            self.skipTest("the bridge needs the MCP SDK to print its help")
        self.assertIn(",".join(bp.DEFAULT_DENY_WRITE), "".join(r.stdout.split()), "argparse wraps at a hyphen")


if __name__ == "__main__":
    unittest.main()
