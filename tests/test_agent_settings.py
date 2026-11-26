"""`kit.py policy export` and `kit.py agent-settings claude-code` (#120).

The export is a walk that asks the shared predicate about every file, so the parity test runs both
ways: a file is confidential exactly when an exported folder covers it or `confidential_files`
names it. The generator is checked as a merge: what the user wrote survives, what the kit added and
no longer wants goes, a second run changes nothing, and doctor reports drift without naming paths.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import kitagent
from tests.helpers import ROOT, kitlib
from tests.test_confidential_floor import (
    CSV_OPEN_REL,
    CSV_REL,
    HARRIER_REL,
    LABELLED_REL,
    NO_FM_REL,
    OPEN_REL,
    RAISED_REL,
    floor_vault,
    set_config,
    symlink,
    write,
)

KIT = [sys.executable, str(ROOT / "kit.py")]
MARKED = "---\ntitle: Marked\nsensitivity: confidential\n---\nbody\n"
TRASH_REL = ".trash/old-plan.md"
PRIVATE_REL = "_drafts/quiet.md"
SETTINGS = kitagent.SETTINGS_REL
MANIFEST = kitagent.MANIFEST_REL


def run(*args):
    return subprocess.run(KIT + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=False, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def agent(vault: Path, *flags: str):
    return run("agent-settings", "claude-code", "--vault", str(vault), *flags)


def read_settings(vault: Path) -> dict:
    return json.loads((vault / SETTINGS).read_text(encoding="utf-8"))


class _Vault(unittest.TestCase):
    def setUp(self):
        self.v = floor_vault()
        write(self.v, TRASH_REL, MARKED)            # Obsidian's trash: an agent reads it like any folder
        write(self.v, PRIVATE_REL, MARKED)          # `_`-prefixed: the kit never reads it, an agent does
        self.addCleanup(shutil.rmtree, self.v.parent, ignore_errors=True)

    def export(self) -> dict:
        return kitagent.policy_export(self.v)


class PolicyExport(_Vault):
    def test_lists_folders_as_the_vault_spells_them_and_each_other_confidential_file(self):
        e = self.export()
        self.assertEqual(e["version"], 1)
        self.assertEqual(e["source"], ".kit/config.yml")
        # The config says 04-Areas/Board; the folder on disk is 04-areas/board, and a deny list
        # matches literally, so the disk's spelling is what goes out.
        self.assertEqual(e["confidential_folders"], ["04-areas/board"])
        self.assertEqual(e["confidential_paths"], ["07-knowledge/**/harrier-*.md", "08-resources/exports/*.csv"])
        files = e["confidential_files"]
        for rel in (HARRIER_REL, RAISED_REL, CSV_REL, TRASH_REL, PRIVATE_REL):
            self.assertIn(rel, files)
        for rel in (OPEN_REL, CSV_OPEN_REL, NO_FM_REL, LABELLED_REL):   # open, or covered by the folder
            self.assertNotIn(rel, files)
        self.assertEqual(files, sorted(files))
        self.assertEqual(e["unreadable"], [])

    def test_a_folder_not_on_disk_yet_keeps_the_declared_spelling(self):
        set_config(self.v, 'confidentiality:\n  confidential_folders: ["Finance/Payroll"]\n')
        self.assertEqual(self.export()["confidential_folders"], ["Finance/Payroll"])

    def test_cli_output_is_deterministic_and_equals_the_function(self):
        a, b = run("policy", "export", "--vault", str(self.v)), run("policy", "export", "--vault", str(self.v))
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(a.stdout, b.stdout)
        self.assertEqual(json.loads(a.stdout), self.export())

    def test_out_writes_the_file_and_nothing_to_stdout(self):
        out = self.v.parent / "export.json"
        r = run("policy", "export", "--vault", str(self.v), "--out", str(out))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), self.export())

    def test_unreadable_and_unparsable_notes_are_listed_fail_closed(self):
        (self.v / "07-knowledge/utf16.md").write_bytes("---\ntitle: W\n---\nbody\n".encode("utf-16"))
        write(self.v, "07-knowledge/broken-yaml.md", "---\ntitle: [unclosed\n---\nbody\n")
        e = self.export()
        for rel in ("07-knowledge/utf16.md", "07-knowledge/broken-yaml.md"):
            self.assertIn(rel, e["confidential_files"])
            self.assertIn(rel, e["unreadable"])
        self.assertTrue(set(e["unreadable"]) <= set(e["confidential_files"]))

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "POSIX permissions, not root")
    def test_a_folder_the_walk_cannot_list_is_listed_as_unreadable(self):
        locked = self.v / "07-knowledge" / "locked"
        locked.mkdir()
        (locked / "inside.md").write_text("x\n", encoding="utf-8")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        e = self.export()
        self.assertIn("07-knowledge/locked/", e["confidential_files"])
        self.assertIn("07-knowledge/locked/", e["unreadable"])
        plan = kitagent.claude_code_plan(self.v, e)
        self.assertIn("Read(./07-knowledge/locked/**)", plan.deny)

    def test_parity_with_the_predicate_both_ways(self):
        e = self.export()
        policy = kitlib.confidentiality_policy(self.v)
        listed = set(e["confidential_files"])
        folders = kitlib.ConfidentialityPolicy.build(e["confidential_folders"])
        seen = 0
        for dirpath, dirnames, filenames in os.walk(self.v):
            rel_dir = Path(dirpath).relative_to(self.v).as_posix()
            if rel_dir == ".":
                dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                rel = (Path(rel_dir) / name).as_posix() if rel_dir != "." else name
                seen += 1
                exported = folders.in_folder(rel) or rel in listed
                self.assertEqual(kitlib.is_confidential(self.v, rel), exported, rel)
                if exported and not folders.in_folder(rel):
                    self.assertFalse(policy.in_folder(rel), rel)
        self.assertGreater(seen, 50)

    def test_symlinked_files_and_folders_are_followed(self):
        outside = self.v.parent / "elsewhere"
        write(outside, "x.md", MARKED)                                  # marked, in a folder outside the vault
        symlink(self, self.v / "03-projects/shared", outside)
        symlink(self, self.v / "03-projects/board", Path("../04-areas/board"))     # into the declared folder
        symlink(self, self.v / "03-projects/kestrel.md", Path("../04-areas/board/kestrel-plan.md"))
        symlink(self, self.v / "03-projects/loop", Path(".."))         # a cycle: not followed again
        e = self.export()
        self.assertIn("03-projects/shared/x.md", e["confidential_files"])
        self.assertIn("03-projects/board/", e["confidential_files"], "a link into a floor is denied whole")
        self.assertIn("03-projects/kestrel.md", e["confidential_files"])
        # A link back up its own path is not walked again, but it is an alias for everything above
        # it: whether Claude Code resolves it before matching a deny rule was not tested, so it goes whole.
        self.assertEqual([f for f in e["confidential_files"] if f.startswith("03-projects/loop/")], ["03-projects/loop/"])
        plan = kitagent.claude_code_plan(self.v, e)
        self.assertIn("Read(./03-projects/board/**)", plan.deny)
        self.assertIn("Read(./03-projects/shared/x.md)", plan.deny)

    def test_refuses_a_broken_config_with_nothing_on_stdout(self):
        set_config(self.v, "confidentiality:\n  confidential_folder: [x]\n")
        r = run("policy", "export", "--vault", str(self.v))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("unknown key", r.stderr)
        r = agent(self.v)
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn(".kit/config.yml", r.stderr)


class Plan(_Vault):
    def test_entries_for_folders_paths_files_and_the_manifest(self):
        plan = kitagent.claude_code_plan(self.v, self.export())
        for perm, sbx in (("Read(./.kit/agent-settings/**)", "./.kit/agent-settings"),
                          ("Read(./04-areas/board/**)", "./04-areas/board"),
                          ("Read(./08-resources/exports/*.csv)", "./08-resources/exports/*.csv"),
                          (f"Read(./{RAISED_REL})", f"./{RAISED_REL}"),
                          (f"Read(./{CSV_REL})", f"./{CSV_REL}")):
            self.assertIn(perm, plan.deny)
            self.assertIn(sbx, plan.deny_read)
        self.assertEqual(len(plan.deny), len(plan.deny_read))
        self.assertNotIn("Read(./.kit/config.yml)", plan.deny)   # the agent's own kit runs need it

    def test_files_the_kit_derives_are_always_denied(self):
        # The graph files, the bridge's audit log and pending proposals are written before a floor
        # is declared and can hold a confidential note's title, frontmatter or text.
        set_config(self.v, "providers:\n  search: naive\n")
        write(self.v, ".kit/graph.json", "{}")
        plan = kitagent.claude_code_plan(self.v, self.export())
        for perm, sbx in (("Read(./.kit/graph.*)", "./.kit/graph.*"),
                          ("Read(./.kit/bridge-audit.jsonl)", "./.kit/bridge-audit.jsonl"),
                          ("Read(./.kit/proposals/**)", "./.kit/proposals")):
            self.assertIn(perm, plan.deny)
            self.assertIn(sbx, plan.deny_read)
        set_config(self.v, 'confidentiality:\n  confidential_paths: ["**/*.json"]\n')
        plan = kitagent.claude_code_plan(self.v, self.export())
        self.assertNotIn("Read(./.kit/graph.json)", plan.deny, "covered by the pattern already")

    def test_a_path_rule_over_the_config_does_not_deny_it(self):
        write(self.v, "99-system/extra.yml", "a: 1\n")
        set_config(self.v, 'confidentiality:\n  confidential_paths: ["**/*.yml"]\n')
        plan = kitagent.claude_code_plan(self.v, self.export())
        for entry in plan.deny_read:                 # neither as a file nor through the pattern
            rel = entry.removeprefix("./")
            denies = kitlib.ConfidentialityPolicy.build(paths=[rel]).covers(kitlib.CONFIG_REL) or \
                kitlib.ConfidentialityPolicy.build(folders=[rel]).in_folder(kitlib.CONFIG_REL)
            self.assertFalse(denies, entry)
        self.assertNotIn("Read(./**/*.yml)", plan.deny)
        self.assertIn("Read(./99-system/extra.yml)", plan.deny)       # what it matches now, one by one
        self.assertTrue(any(".kit/config.yml" in n and "not carried over" in n for n in plan.notes))

    def test_a_path_rule_without_an_extension_goes_out_in_both_spellings(self):
        # The kit hides `notes/plan.md` for the rule `notes/plan`; a deny rule `Read(./notes/plan)`
        # does not name that file, so a plan.md made after the run would stay readable to the agent.
        set_config(self.v, 'confidentiality:\n  confidential_paths: ["notes/plan"]\n')
        plan = kitagent.claude_code_plan(self.v, self.export())
        for spelling in ("notes/plan", "notes/plan.md"):
            self.assertIn(f"Read(./{spelling})", plan.deny)
            self.assertIn(f"./{spelling}", plan.deny_read)

    def test_a_path_rule_with_a_dot_in_its_last_name_goes_out_in_both_spellings(self):
        # No file matches yet: the spellings come from the rule, not from the files listed one by one.
        set_config(self.v, 'confidentiality:\n  confidential_paths: ["daily/2024.11.26", "07-knowledge/Salary Review v1.2"]\n')
        plan = kitagent.claude_code_plan(self.v, self.export())
        for spelling in ("daily/2024.11.26", "daily/2024.11.26.md",
                         "07-knowledge/Salary Review v1.2", "07-knowledge/Salary Review v1.2.md"):
            self.assertIn(f"Read(./{spelling})", plan.deny)
            self.assertIn(f"./{spelling}", plan.deny_read)

    def test_a_name_a_rule_cannot_match_literally_denies_its_parent_folder(self):
        for name in ("odd [x]/n[1].md", "spaced/ lead.md", "bang/!x.md", "hash/#x.md"):
            write(self.v, f"07-knowledge/{name}", MARKED)
        plan = kitagent.claude_code_plan(self.v, self.export())
        # `odd [x]` itself cannot be named either, so the denial climbs to 07-knowledge, which
        # then covers the other three as well.
        self.assertIn("Read(./07-knowledge/**)", plan.deny)
        self.assertFalse([d for d in plan.deny if d.startswith("Read(./07-knowledge/") and d != "Read(./07-knowledge/**)"
                          and "harrier-*" not in d])     # the declared path rule stays, as written
        self.assertTrue(any("07-knowledge/" in n and "cannot match literally" in n for n in plan.notes))

    def test_a_safe_parent_is_denied_on_its_own(self):
        write(self.v, "07-knowledge/sub/n#.md", MARKED)     # * and ? cannot be file names on Windows
        plan = kitagent.claude_code_plan(self.v, self.export())
        self.assertIn("Read(./07-knowledge/sub/**)", plan.deny)
        self.assertNotIn("Read(./07-knowledge/**)", plan.deny)

    def test_at_the_vault_root_it_refuses_and_names_only_a_count(self):
        write(self.v, "plan [draft].md", MARKED)
        write(self.v, " leading.md", MARKED)
        with self.assertRaises(kitagent.AgentSettingsError) as ctx:
            kitagent.claude_code_plan(self.v, self.export())
        self.assertIn("2 confidential", str(ctx.exception))
        r = agent(self.v)
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("2 confidential", r.stderr)
        self.assertNotIn("draft", r.stderr)
        self.assertFalse((self.v / SETTINGS).exists())

    def test_literal_safe(self):
        for rel in ("a/b.md", "05-people/Jörg Müller (CFO).md"):
            self.assertTrue(kitagent.literal_safe(rel), rel)
        for rel in ("a/[b].md", "a/b*.md", "a/b?.md", "a\\b.md", "a/!b.md", "a/#b.md", " a/b.md", "a/b.md ", "a/b\n.md"):
            self.assertFalse(kitagent.literal_safe(rel), rel)

    def test_git_is_denied_by_default_and_kept_with_allow_git(self):
        (self.v / ".git").mkdir()
        e = self.export()
        self.assertIn("Read(./.git/**)", kitagent.claude_code_plan(self.v, e).deny)
        self.assertIn("./.git", kitagent.claude_code_plan(self.v, e).deny_read)
        plan = kitagent.claude_code_plan(self.v, e, allow_git=True)
        self.assertNotIn("Read(./.git/**)", plan.deny)
        self.assertTrue(any("readable through the history" in n for n in plan.notes))
        r = agent(self.v, "--allow-git")
        self.assertIn("readable through the history", r.stdout)

    def test_no_git_entry_without_a_repository(self):
        self.assertNotIn("Read(./.git/**)", kitagent.claude_code_plan(self.v, self.export()).deny)

    def test_a_git_file_names_history_outside_the_vault(self):
        (self.v / ".git").write_text("gitdir: /elsewhere/.git/worktrees/v\n", encoding="utf-8")
        plan = kitagent.claude_code_plan(self.v, self.export())
        self.assertIn("git history outside the vault not denied", plan.flags)
        self.assertFalse(any("cannot be read through the history" in n for n in plan.notes))

    def test_allow_git_is_recorded_and_check_and_doctor_follow_it(self):
        (self.v / ".git").mkdir()
        self.assertEqual(agent(self.v, "--apply", "--allow-git").returncode, 0)
        self.assertNotIn("Read(./.git/**)", read_settings(self.v)["permissions"]["deny"])
        self.assertTrue(json.loads((self.v / MANIFEST).read_text(encoding="utf-8"))["allow_git"])
        self.assertEqual(agent(self.v, "--check").returncode, 0)
        self.assertEqual(kitagent.doctor_row(self.v)[1], "ok")
        r = agent(self.v, "--apply")                 # without the flag: back to the default
        self.assertIn("denies `.git` again", r.stdout)
        self.assertIn("Read(./.git/**)", read_settings(self.v)["permissions"]["deny"])
        self.assertEqual(agent(self.v, "--check").returncode, 0)


class Merge(_Vault):
    FOREIGN = {
        "model": "some-model",
        "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(rm:*)", f"Read(./{RAISED_REL})"]},
        "sandbox": {"enabled": False, "filesystem": {"allowWrite": ["./tmp"], "denyRead": ["~/.ssh"]}},
        "hooks": {"PreToolUse": []},
    }

    def put(self, data: dict) -> None:
        write(self.v, SETTINGS, json.dumps(data, indent=4) + "\n")

    def test_dry_run_prints_a_diff_and_writes_nothing(self):
        r = agent(self.v)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("+++ b/.claude/settings.json", r.stdout)
        self.assertIn("dry run", r.stdout)
        self.assertFalse((self.v / SETTINGS).exists())
        self.assertFalse((self.v / MANIFEST).exists())

    def test_apply_keeps_every_foreign_key_and_entry_and_reports_the_override(self):
        self.put(self.FOREIGN)
        r = agent(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("overrides your value: sandbox.enabled: false -> true", r.stdout)
        s = read_settings(self.v)
        self.assertEqual(s["model"], "some-model")
        self.assertEqual(s["hooks"], {"PreToolUse": []})
        self.assertEqual(s["permissions"]["allow"], ["Bash(ls:*)"])
        self.assertEqual(s["permissions"]["deny"][:2], ["Bash(rm:*)", f"Read(./{RAISED_REL})"])
        self.assertEqual(s["sandbox"]["filesystem"]["allowWrite"], ["./tmp"])
        self.assertEqual(s["sandbox"]["filesystem"]["denyRead"][0], "~/.ssh")
        self.assertEqual({k: s["sandbox"][k] for k in kitagent.SANDBOX_FLAGS}, kitagent.SANDBOX_FLAGS)
        self.assertEqual(s["permissions"]["deny"].count(f"Read(./{RAISED_REL})"), 1)
        manifest = json.loads((self.v / MANIFEST).read_text(encoding="utf-8"))
        # The user wrote that entry before the kit did: it is theirs, not the kit's.
        self.assertNotIn(f"Read(./{RAISED_REL})", manifest["permissions.deny"])
        self.assertIn("Read(./.kit/agent-settings/**)", s["permissions"]["deny"])
        self.assertIn("./.kit/agent-settings", s["sandbox"]["filesystem"]["denyRead"])

    def test_dropping_a_mark_removes_the_kits_entry_but_never_the_users(self):
        self.put(self.FOREIGN)
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        write(self.v, RAISED_REL, "---\ntitle: Heron\nsensitivity: internal\n---\nopen now\n")
        (self.v / CSV_REL).unlink()
        r = agent(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = read_settings(self.v)
        self.assertIn(f"Read(./{RAISED_REL})", s["permissions"]["deny"])      # the user's, kept
        self.assertNotIn(f"Read(./{CSV_REL})", s["permissions"]["deny"])      # the kit's, stale, gone
        self.assertNotIn(f"./{CSV_REL}", s["sandbox"]["filesystem"]["denyRead"])
        self.assertIn("Bash(rm:*)", s["permissions"]["deny"])

    def test_second_apply_is_byte_identical_and_says_so(self):
        self.put(self.FOREIGN)
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        before = ((self.v / SETTINGS).read_bytes(), (self.v / MANIFEST).read_bytes())
        r = agent(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to do", r.stdout)
        self.assertEqual(((self.v / SETTINGS).read_bytes(), (self.v / MANIFEST).read_bytes()), before)

    def test_second_apply_is_a_no_op_with_a_rule_that_matches_the_settings_themselves(self):
        # `**/*.json` covers .claude/settings.json and the manifest, which the first --apply creates:
        # the next export listed both, so the run only settled the third time.
        set_config(self.v, 'confidentiality:\n  confidential_paths: ["**/*.json"]\n')
        write(self.v, kitagent.LOCAL_SETTINGS_REL, "{}\n")
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        before = ((self.v / SETTINGS).read_bytes(), (self.v / MANIFEST).read_bytes())
        r = agent(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to do", r.stdout)
        self.assertEqual(((self.v / SETTINGS).read_bytes(), (self.v / MANIFEST).read_bytes()), before)
        self.assertEqual(agent(self.v, "--check").returncode, 0)
        self.assertEqual(kitagent.doctor_row(self.v)[1], "ok")
        files = self.export()["confidential_files"]
        for own in (SETTINGS, kitagent.LOCAL_SETTINGS_REL, MANIFEST):
            self.assertNotIn(own, files)

    def test_sandbox_switches_must_be_json_booleans(self):
        # 1 == True in Python, so integers passed --check and doctor and stayed in the file.
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        s = read_settings(self.v)
        s["sandbox"].update({"enabled": 1, "allowUnsandboxedCommands": 0, "failIfUnavailable": 1})
        write(self.v, SETTINGS, json.dumps(s, indent=2) + "\n")
        self.assertEqual(agent(self.v, "--check").returncode, 1)
        self.assertNotEqual(kitagent.doctor_row(self.v)[1], "ok")
        r = agent(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("overrides your value: sandbox.enabled: 1 -> true", r.stdout)
        raw = (self.v / SETTINGS).read_text(encoding="utf-8")
        self.assertIn('"enabled": true', raw)
        self.assertIn('"allowUnsandboxedCommands": false', raw)
        self.assertIn('"failIfUnavailable": true', raw)
        self.assertEqual(agent(self.v, "--check").returncode, 0)

    def test_check_exit_codes_and_writes_nothing(self):
        r = agent(self.v, "--check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("drift:", r.stdout)
        self.assertFalse((self.v / SETTINGS).exists())
        agent(self.v, "--apply")
        self.assertEqual(agent(self.v, "--check").returncode, 0)
        write(self.v, "07-knowledge/new-hidden.md", MARKED)
        before = (self.v / SETTINGS).read_bytes()
        r = agent(self.v, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("2 list entries to add", r.stdout)
        self.assertNotIn("new-hidden", r.stdout)
        self.assertEqual((self.v / SETTINGS).read_bytes(), before)

    def test_reformatting_by_hand_is_not_drift(self):
        agent(self.v, "--apply")
        self.put(read_settings(self.v))           # same content, four-space indent
        self.assertEqual(agent(self.v, "--check").returncode, 0)

    def test_apply_and_check_together_is_a_usage_error(self):
        self.assertEqual(agent(self.v, "--apply", "--check").returncode, 2)

    def test_shortened_flags_are_refused(self):
        self.assertEqual(agent(self.v, "--app").returncode, 2)
        self.assertFalse((self.v / SETTINGS).exists())

    def test_invalid_json_or_wrong_shapes_are_refused(self):
        write(self.v, SETTINGS, "{ not json")
        r = agent(self.v, "--apply")
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("not valid JSON", r.stderr)
        self.assertEqual((self.v / SETTINGS).read_text(encoding="utf-8"), "{ not json")
        for bad in ([], {"permissions": []}, {"permissions": {"deny": "Read(x)"}}, {"sandbox": {"filesystem": []}},
                    {"sandbox": {"filesystem": {"denyRead": [1]}}}, {"permissions": None}, {"sandbox": None},
                    {"sandbox": {"filesystem": None}}):
            self.put(bad)
            self.assertEqual(agent(self.v, "--apply").returncode, 2, bad)

    def test_a_byte_order_mark_is_accepted_and_kept(self):
        # Some Windows editors write one; the config loader already accepted it, this file did not.
        (self.v / SETTINGS).parent.mkdir(parents=True, exist_ok=True)
        (self.v / SETTINGS).write_bytes(b"\xef\xbb\xbf" + json.dumps(self.FOREIGN, indent=2).encode() + b"\n")
        r = agent(self.v)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("-\ufeff", r.stdout, "the mark is not shown as a change")
        r = agent(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        raw = (self.v / SETTINGS).read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf{"), raw[:8])
        self.assertEqual(json.loads(raw.decode("utf-8-sig"))["model"], "some-model")
        self.assertIn("nothing to do", agent(self.v, "--apply").stdout)
        self.assertEqual(agent(self.v, "--check").returncode, 0)
        self.assertEqual(kitagent.doctor_row(self.v)[1], "ok")

    def test_check_names_the_manifest_and_prints_the_plans_flags(self):
        (self.v / ".git").write_text("gitdir: /elsewhere/.git/worktrees/v\n", encoding="utf-8")
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        r = agent(self.v, "--check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("git history outside the vault not denied", r.stderr)
        r = run("agent-settings", "claude-code", "--help")
        self.assertRegex(" ".join(r.stdout.split()), r"settings\.json or the manifest differs")

    def test_a_broken_manifest_owns_nothing_and_is_rewritten(self):
        agent(self.v, "--apply")
        write(self.v, MANIFEST, "garbage")
        (self.v / CSV_REL).unlink()
        r = agent(self.v, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not a manifest this kit wrote", r.stderr)
        # Over-deny, never under-deny: with no record of what the kit added, the stale entry stays.
        self.assertIn(f"Read(./{CSV_REL})", read_settings(self.v)["permissions"]["deny"])
        json.loads((self.v / MANIFEST).read_text(encoding="utf-8"))

    def test_the_kits_sample_vault_is_never_written(self):
        r = run("agent-settings", "claude-code", "--vault", str(ROOT / "vault"), "--apply")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sample", r.stderr)
        self.assertFalse((ROOT / "vault" / ".claude").exists())

    def test_merge_list_ownership(self):
        new, owned, added, removed = kitagent._merge_list(["user", "kit-old", "shared"], {"kit-old"}, ["shared", "kit-new"])
        self.assertEqual(new, ["user", "shared", "kit-new"])
        self.assertEqual(owned, ["kit-new"])
        self.assertEqual((added, removed), (["kit-new"], ["kit-old"]))


class Reverse(_Vault):
    """--apply records what it replaced, refuses to replace a symlink, and --remove undoes it."""

    FOREIGN = Merge.FOREIGN
    put = Merge.put

    def test_a_symlinked_settings_file_is_not_replaced(self):
        real = self.v.parent / "dotfiles-settings.json"
        real.write_text('{"model": "x"}\n', encoding="utf-8")
        symlink(self, self.v / SETTINGS, real)
        r = agent(self.v, "--apply")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("symlink", r.stderr)
        self.assertTrue((self.v / SETTINGS).is_symlink())
        self.assertEqual(real.read_text(encoding="utf-8"), '{"model": "x"}\n')
        self.assertFalse((self.v / MANIFEST).exists())
        self.assertEqual(agent(self.v).returncode, 0, "the dry run still previews")
        write(self.v, MANIFEST, json.dumps({"permissions.deny": [], "sandbox.filesystem.denyRead": []}))
        r = agent(self.v, "--remove", "--apply")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("symlink", r.stderr)
        self.assertTrue((self.v / SETTINGS).is_symlink())

    def test_the_manifest_records_what_apply_replaced_and_keeps_it(self):
        self.put(self.FOREIGN)
        agent(self.v, "--apply")
        m = json.loads((self.v / MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(m["sandbox.prior"], {"enabled": False})
        self.assertEqual(m["sandbox.added"], ["allowUnsandboxedCommands", "failIfUnavailable"])
        agent(self.v, "--apply")
        self.assertEqual(json.loads((self.v / MANIFEST).read_text(encoding="utf-8")), m, "the kit's own values are not 'prior'")

    def test_remove_restores_the_settings_and_is_idempotent(self):
        self.put(self.FOREIGN)
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        applied = (self.v / SETTINGS).read_bytes()
        r = agent(self.v, "--remove")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry run", r.stdout)
        self.assertEqual((self.v / SETTINGS).read_bytes(), applied)
        self.assertTrue((self.v / MANIFEST).exists())
        set_config(self.v, "confidentiality: [")          # removing needs no policy
        r = agent(self.v, "--remove", "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(read_settings(self.v), self.FOREIGN)
        self.assertFalse((self.v / MANIFEST).exists())
        before = (self.v / SETTINGS).read_bytes()
        r = agent(self.v, "--remove", "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to remove", r.stdout)
        self.assertEqual((self.v / SETTINGS).read_bytes(), before)

    def test_remove_from_a_vault_that_had_no_settings(self):
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        self.assertEqual(agent(self.v, "--remove", "--apply").returncode, 0)
        self.assertEqual(read_settings(self.v), {})
        self.assertFalse((self.v / MANIFEST).exists())

    def test_remove_leaves_a_value_changed_since_and_the_users_entries(self):
        self.put(self.FOREIGN)
        agent(self.v, "--apply")
        s = read_settings(self.v)
        s["sandbox"]["failIfUnavailable"] = False            # changed by hand after the kit set it
        s["permissions"]["deny"].append("Read(./mine.md)")
        write(self.v, SETTINGS, json.dumps(s))
        self.assertEqual(agent(self.v, "--remove", "--apply").returncode, 0)
        s = read_settings(self.v)
        self.assertIs(s["sandbox"]["failIfUnavailable"], False)
        self.assertIs(s["sandbox"]["enabled"], False)
        self.assertNotIn("allowUnsandboxedCommands", s["sandbox"])
        self.assertEqual(s["permissions"]["deny"], ["Bash(rm:*)", f"Read(./{RAISED_REL})", "Read(./mine.md)"])

    def test_remove_refuses_a_foreign_manifest_and_check(self):
        agent(self.v, "--apply")
        write(self.v, MANIFEST, "[]\n")
        r = agent(self.v, "--remove", "--apply")
        self.assertEqual(r.returncode, 2)
        self.assertIn("by hand", r.stderr)
        self.assertEqual(agent(self.v, "--remove", "--check").returncode, 2)


class Doctor(_Vault):
    def row(self) -> tuple[str, str]:
        return kitagent.doctor_row(self.v)

    def test_dash_when_nothing_is_set_up(self):
        self.assertEqual(self.row(), ("-", "-"))

    def test_ok_after_apply(self):
        agent(self.v, "--apply")
        self.assertEqual(self.row()[1], "ok")

    def test_drift_counts_without_paths(self):
        agent(self.v, "--apply")
        write(self.v, "07-knowledge/new-hidden.md", MARKED)
        (self.v / CSV_REL).unlink()
        status = self.row()[1]
        self.assertTrue(status.startswith("drift: 2 to add, 2 stale"), status)
        self.assertIn("--apply", status)
        for name in ("new-hidden", "payroll", "05-people", "04-areas"):
            self.assertNotIn(name, status)

    def test_local_settings_that_switch_the_sandbox_off_are_drift(self):
        agent(self.v, "--apply")
        write(self.v, kitagent.LOCAL_SETTINGS_REL, json.dumps({"sandbox": {"enabled": False, "allowUnsandboxedCommands": True}}))
        status = self.row()[1]
        self.assertIn("settings.local.json turns the sandbox off", status)
        self.assertIn("settings.local.json allows unsandboxed commands", status)
        self.assertNotEqual(status, "ok")
        r = agent(self.v, "--check")
        self.assertIn("turns the sandbox off", r.stderr)

    def test_local_settings_with_false_ish_values_are_flagged(self):
        agent(self.v, "--apply")
        write(self.v, kitagent.LOCAL_SETTINGS_REL,
              json.dumps({"sandbox": {"enabled": 0, "allowUnsandboxedCommands": 1, "failIfUnavailable": "no"}}))
        status = self.row()[1]
        for flag in ("turns the sandbox off", "allows unsandboxed commands", "lets commands run unsandboxed"):
            self.assertIn(flag, status)

    def test_an_allow_read_inside_a_denied_folder_is_flagged(self):
        agent(self.v, "--apply")
        write(self.v, kitagent.LOCAL_SETTINGS_REL, json.dumps({"sandbox": {"filesystem": {"allowRead": ["./04-areas/board/x"]}}}))
        self.assertIn("1 allowRead entry re-open denied paths", self.row()[1])
        write(self.v, kitagent.LOCAL_SETTINGS_REL, json.dumps({"sandbox": {"filesystem": {"allowRead": ["./"]}}}))
        self.assertEqual(self.row()[1], "ok")     # a wider allow does not beat a narrower deny

    def test_reopens_is_conservative_about_globs_case_and_symlinked_prefixes(self):
        root = self.v.resolve()
        denied = ["./05-people", "./08-resources/exports/*.csv"]
        for allow in ("./0*/alice.md", "./05-peo*/**", "./05-PEOPLE/x.md", "./**", "./08-resources/exports/a.csv",
                      f"{root}/05-people/x.md"):
            self.assertTrue(kitagent._reopens(root, allow, denied), allow)
        alias = self.v.parent / "alias"
        symlink(self, alias, self.v)
        self.assertTrue(kitagent._reopens(root, f"{alias}/05-people/x.md", denied), "an absolute path through a link")
        for allow in ("./", "./04-areas/**", "./05", "./06-*/x.md"):
            self.assertFalse(kitagent._reopens(root, allow, denied), allow)

    def test_a_negation_after_the_kits_rules_is_flagged(self):
        agent(self.v, "--apply")
        s = read_settings(self.v)
        s["permissions"]["deny"].append("Read(!04-areas/board/agenda.md)")
        write(self.v, SETTINGS, json.dumps(s))
        self.assertIn("1 Read(!…) negation(s) after the kit's rules", self.row()[1])
        s["permissions"]["deny"].insert(0, s["permissions"]["deny"].pop())     # listed first, it carves nothing
        write(self.v, SETTINGS, json.dumps(s))
        self.assertEqual(self.row()[1], "ok")

    def test_a_valid_settings_file_of_the_wrong_shape_is_not_called_invalid_json(self):
        agent(self.v, "--apply")
        for bad, reason in (([], "top level is not a JSON object"), ({"permissions": []}, "`permissions` has the wrong type"),
                            ("{", "not valid JSON")):
            write(self.v, SETTINGS, bad if isinstance(bad, str) else json.dumps(bad))
            status = self.row()[1]
            self.assertIn(f"cannot check: {reason}", status)

    def test_broken_inputs_never_raise(self):
        agent(self.v, "--apply")
        write(self.v, SETTINGS, "{")
        self.assertIn("cannot check", self.row()[1])
        (self.v / SETTINGS).unlink()
        agent(self.v, "--apply")
        set_config(self.v, "confidentiality: [")
        self.assertIn("cannot check", self.row()[1])

    def test_doctor_row_and_report_name_no_confidential_path(self):
        agent(self.v, "--apply")
        write(self.v, "07-knowledge/new-hidden.md", MARKED)
        report = self.v.parent / "doctor.txt"
        r = run("doctor", "--vault", str(self.v), "--report", str(report), "--base-url", "http://127.0.0.1:9/v1")
        line = next(x for x in r.stdout.splitlines() if x.startswith("agent settings (claude-code)"))
        self.assertIn("drift: 2 to add", line)
        text = report.read_text(encoding="utf-8")
        self.assertIn("agent settings (claude-code)", text)
        for name in ("new-hidden", "harrier", "payroll", "heron"):
            self.assertNotIn(name, line)
            self.assertNotIn(name, text)


if __name__ == "__main__":
    unittest.main()
