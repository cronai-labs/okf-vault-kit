"""`kit.py agent-settings claude-code` in allow-open mode (#126).

With `open_folders` declared, the generator denies the vault (`./`) to the sandbox, re-opens the open
folders with `allowRead`, denies what is confidential inside them one by one, forbids edits to
`.claude/`, and registers a PreToolUse guard inline in settings.json. The guard is the only control
over the Read, Grep and Glob tools in this mode, so its tests run the generated command itself, as
Claude Code does (`sh -c`), with hook JSON on stdin.
"""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

import kitagent
from tests.helpers import kitlib
from tests.test_agent_settings import MANIFEST, MARKED, SETTINGS, _Vault, agent, read_settings, run
from tests.test_confidential_floor import (
    CONFIG,
    CSV_OPEN_REL,
    CSV_REL,
    HARRIER_REL,
    LABELLED_REL,
    NO_FM_REL,
    OPEN_CONFIG,
    OPEN_REL,
    RAISED_REL,
    set_config,
    symlink,
    write,
)

HAS_SH = os.name != "nt" and shutil.which("python3") is not None and Path("/bin/sh").exists()
def sorted_lists(obj):
    if isinstance(obj, dict):
        return {k: sorted_lists(v) for k, v in obj.items()}
    if isinstance(obj, list) and all(isinstance(x, str) for x in obj):
        return sorted(obj)
    return obj


USER_HOOK = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}


class _Open(_Vault):
    def setUp(self):
        super().setUp()
        set_config(self.v, OPEN_CONFIG)

    def plan(self, allow_git: bool = False) -> kitagent.Plan:
        return kitagent.claude_code_plan(self.v, self.export(), allow_git=allow_git)

    def guards(self) -> list:
        return [e for e in read_settings(self.v).get("hooks", {}).get("PreToolUse", []) if kitagent.is_guard(e)]


@unittest.skipUnless(HAS_SH, "the guard runs under a POSIX sh with python3 on PATH; not on this machine")
class Guard(_Open):
    """The generated command, fed hook JSON on stdin: exit 0 lets the call through, 2 refuses it."""

    def call(self, tool: str, tool_input, cwd: Path | None = None, plan: kitagent.Plan | None = None,
             stdin: str | None = None) -> subprocess.CompletedProcess:
        plan = plan or self.plan()
        command = plan.hook["hooks"][0]["command"]
        if stdin is None:
            stdin = json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                                "cwd": str(cwd or self.v)})
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.v)}
        return subprocess.run(["/bin/sh", "-c", command], input=stdin, capture_output=True, text=True,
                               encoding="utf-8", env=env, cwd=self.v, check=False, timeout=30)

    def code(self, tool: str, tool_input, **kw) -> int:
        return self.call(tool, tool_input, **kw).returncode

    def test_the_hook_entry(self):
        hook = self.plan().hook
        self.assertEqual(hook["matcher"], "Read|Grep|Glob|NotebookRead|Edit|Write|MultiEdit|NotebookEdit")
        (inner,) = hook["hooks"]
        self.assertEqual((inner["type"], inner["timeout"], inner["onFailure"]), ("command", 10, "block"))
        self.assertTrue(inner["command"].startswith("python3 -I -c '"), inner["command"][:40])
        self.assertIn(kitagent.GUARD_MARKER, inner["command"])

    def test_read_inside_and_outside_an_open_folder(self):
        self.assertEqual(self.code("Read", {"file_path": str(self.v / OPEN_REL)}), 0)
        self.assertEqual(self.code("Read", {"file_path": str(self.v / CSV_OPEN_REL)}), 0)
        for rel in ("index.md", "log.md", "05-people/alex-example.md", kitlib.CONFIG_REL, "04-areas-old/x.md"):
            r = self.call("Read", {"file_path": str(self.v / rel)})
            self.assertEqual(r.returncode, 2, rel)
            self.assertIn("okf-vault-kit guard", r.stderr)

    def test_a_nested_deny_a_label_and_a_path_rule_inside_an_open_folder(self):
        for rel in (NO_FM_REL, LABELLED_REL, "04-areas/board", RAISED_REL, HARRIER_REL, CSV_REL):
            self.assertEqual(self.code("Read", {"file_path": str(self.v / rel)}), 2, rel)
        self.assertEqual(self.code("Edit", {"file_path": str(self.v / RAISED_REL), "old_string": "a", "new_string": "b"}), 2)
        self.assertEqual(self.code("Grep", {"pattern": "x", "path": str(self.v / "04-areas/board")}), 2)

    def test_a_symlink_out_of_the_open_folders(self):
        # Made after the settings: the export would otherwise name each link one by one, and the
        # guard must refuse a link it was never told about by where it lands.
        plan = self.plan()
        symlink(self, self.v / "04-areas/link-in-vault.md", "../log.md")
        outside = self.v.parent / "outside.md"
        outside.write_text("x\n", encoding="utf-8")
        symlink(self, self.v / "04-areas/link-out.md", outside)
        symlink(self, self.v / "04-areas/link-to-board", "board")        # onto a denied folder inside an open one
        for rel in ("04-areas/link-in-vault.md", "04-areas/link-out.md", "04-areas/link-to-board/kestrel-plan.md"):
            self.assertEqual(self.code("Read", {"file_path": str(self.v / rel)}, plan=plan), 2, rel)
        self.assertEqual(self.code("Read", {"file_path": str(self.v / OPEN_REL)}, plan=plan), 0)

    def test_a_path_that_climbs_and_an_absolute_path(self):
        for path in (f"{self.v}/04-areas/../log.md", "04-areas/../04-areas/plover.md", "/etc/hosts",
                     str(self.v.parent / "elsewhere.md"), "~/notes.md", str(self.v)):
            self.assertEqual(self.code("Read", {"file_path": path}), 2, path)

    def test_a_relative_path_must_pass_from_the_cwd_and_the_project_dir(self):
        # Which base the tool resolves a relative path against was not observed, so both must pass.
        self.assertEqual(self.code("Read", {"file_path": OPEN_REL}), 0)
        self.assertEqual(self.code("Read", {"file_path": "plover.md"}, cwd=self.v / "04-areas"), 2)
        self.assertEqual(self.code("Read", {"file_path": "plover.md"}), 2, "no plover.md at the root")
        self.assertEqual(self.code("Read", {"file_path": OPEN_REL}, cwd=self.v / "05-people"), 2)

    def test_case_variants(self):
        self.assertEqual(self.code("Read", {"file_path": "04-AREAS/Plover.md"}), 0)
        for rel in ("04-AREAS/BOARD/kestrel-plan.md", "04-Areas/Heron.md", "08-resources/EXPORTS/payroll.CSV"):
            self.assertEqual(self.code("Read", {"file_path": rel}), 2, rel)

    def test_grep_and_glob(self):
        self.assertEqual(self.code("Grep", {"pattern": "kestrel", "path": str(self.v / "04-areas")}), 0)
        self.assertEqual(self.code("Grep", {"pattern": "kestrel"}), 2, "no path: the project root")
        self.assertEqual(self.code("Glob", {"pattern": "**/*.md"}), 2, "no path: the project root")
        # No path is refused wherever the session stands: the tool's own default is not observed.
        self.assertEqual(self.code("Grep", {"pattern": "kestrel"}, cwd=self.v / "04-areas"), 2)
        self.assertEqual(self.code("Glob", {"pattern": "*.md"}, cwd=self.v / "04-areas"), 2)
        self.assertEqual(self.code("Glob", {"pattern": "**/*.md", "path": str(self.v / "07-knowledge")}), 0)
        for tool, args in (("Glob", {"pattern": "../**/*.md", "path": str(self.v / "04-areas")}),
                           ("Glob", {"pattern": f"{self.v}/**/*.md", "path": str(self.v / "04-areas")}),
                           ("Grep", {"pattern": "x", "glob": "../*.md", "path": str(self.v / "04-areas")}),
                           ("Grep", {"pattern": "x", "glob": "/etc/*", "path": str(self.v / "04-areas")})):
            self.assertEqual(self.code(tool, args), 2, args)

    def test_unknown_shapes_and_broken_input_refuse(self):
        self.assertEqual(self.code("Bash", {"command": "cat log.md"}), 2, "a tool the guard does not know")
        self.assertEqual(self.code("Read", ["04-areas/plover.md"]), 2, "tool_input not an object")
        self.assertEqual(self.code("Read", "04-areas/plover.md"), 2)
        self.assertEqual(self.code("Read", {}), 2, "no path")
        self.assertEqual(self.code("Read", {"file_path": 7}), 2)
        self.assertEqual(self.code("Read", {"file_path": ""}), 2)
        self.assertEqual(self.code("Read", {"file_path": "04-areas/plover.md\x00"}), 2)
        self.assertEqual(self.code("Glob", {"pattern": 3, "path": str(self.v / "04-areas")}), 2)
        for stdin in ("{", "", "[]", "null", '{"tool_name": "Read"}'):
            r = self.call("Read", {}, stdin=stdin)
            self.assertEqual(r.returncode, 2, repr(stdin))
            self.assertIn("okf-vault-kit guard", r.stderr)

    def test_special_folders_stay_denied_whatever_is_open(self):
        set_config(self.v, CONFIG + '  open_folders: ["04-areas", ".claude", ".git"]\n')
        write(self.v, SETTINGS, "{}\n")
        write(self.v, ".git/HEAD", "ref: refs/heads/main\n")
        plan = self.plan()
        self.assertEqual(self.code("Read", {"file_path": SETTINGS}, plan=plan), 2)
        self.assertEqual(self.code("Read", {"file_path": ".git/HEAD"}, plan=plan), 2)
        self.assertIn("Read(./.claude/**)", plan.deny)
        self.assertIn("Read(./.git/**)", plan.deny)
        self.assertEqual(plan.allow_read, ["./04-areas", "./.kit/config.yml"], "never re-opened to the shell")
        self.assertIn("./.git", self.plan(allow_git=True).allow_read)
        self.assertNotIn("./.claude", self.plan(allow_git=True).allow_read)
        self.assertEqual(self.plan(allow_git=True).allow_read, ["./.git", "./04-areas", "./.kit/config.yml"],
                         "a declared-open .git is listed once")

    def test_allow_git_lets_git_through(self):
        write(self.v, ".git/HEAD", "ref: refs/heads/main\n")
        self.assertEqual(self.code("Read", {"file_path": ".git/HEAD"}), 2)
        plan = self.plan(allow_git=True)
        self.assertEqual(self.code("Read", {"file_path": ".git/HEAD"}, plan=plan), 0)
        self.assertIn("./.git", plan.allow_read)
        self.assertTrue(any("readable through the history" in n for n in plan.notes))
        self.assertNotIn("./.git", self.plan().allow_read)

    def test_allow_git_opens_git_to_the_read_tools_only(self):
        # A hook written into .git/hooks runs outside the sandbox at the next git command (#126 review).
        write(self.v, ".git/HEAD", "ref: refs/heads/main\n")
        write(self.v, ".git/config", "[core]\n")
        for config in (OPEN_CONFIG, CONFIG + '  open_folders: ["04-areas", ".git"]\n'):
            set_config(self.v, config)
            plan = self.plan(allow_git=True)
            # Made after the settings, so the guard alone judges it by where it lands.
            symlink(self, self.v / "04-areas/git-link", "../.git")
            self.assertEqual(self.code("Read", {"file_path": ".git/HEAD"}, plan=plan), 0)
            self.assertEqual(self.code("Grep", {"pattern": "x", "path": str(self.v / ".git")}, plan=plan), 0)
            for tool, args in (("Write", {"file_path": ".git/hooks/post-commit", "content": "x"}),
                               ("Edit", {"file_path": ".git/config", "old_string": "a", "new_string": "b"}),
                               ("MultiEdit", {"file_path": str(self.v / ".git/config"), "edits": []}),
                               ("Write", {"file_path": "04-areas/git-link/hooks/pre-commit", "content": "x"}),
                               ("NotebookEdit", {"notebook_path": ".git/x.ipynb", "new_source": "x"})):
                self.assertEqual(self.code(tool, args, plan=plan), 2, (config, tool, args))
            (self.v / "04-areas/git-link").unlink()
        self.assertEqual(self.code("Write", {"file_path": "04-areas/new.md", "content": "x"}, plan=plan), 0)

    def test_a_pattern_with_braces_is_refused(self):
        # Whether Glob or Grep expand braces was not observed; `{..,x}` would climb out if they do.
        path = str(self.v / "04-areas")
        for tool, key in (("Glob", "pattern"), ("Grep", "glob")):
            for pat in ("{..,x}/**/*.md", "{/etc,x}/*", "{.,x}./y.md", "{,}../y.md", "{,}/etc/*", "*.{md,txt}"):
                args = {"pattern": "x", "path": path} if tool == "Grep" else {"path": path}
                args[key] = pat
                self.assertEqual(self.code(tool, args), 2, (tool, pat))
        self.assertEqual(self.code("Glob", {"pattern": "**/*.md", "path": path}), 0)

    def test_a_folder_name_the_shell_would_expand(self):
        name = "it's \"$(touch PWNED)\" `touch PWNED2` $HOME ü"
        write(self.v, f"{name}/note.md", "# note\n")
        set_config(self.v, CONFIG + "  open_folders: ['04-areas', '" + name.replace("'", "''") + "']\n")
        self.assertIsNone(kitlib.confidentiality_config_error(self.v))
        plan = self.plan()
        self.assertEqual(self.code("Read", {"file_path": f"{name}/note.md"}, plan=plan), 0)
        self.assertEqual(self.code("Read", {"file_path": "log.md"}, plan=plan), 2)
        self.assertFalse((self.v / "PWNED").exists() or (self.v / "PWNED2").exists())
        self.assertIn(f"./{name}", plan.allow_read)

    def test_parity_with_the_predicate(self):
        # Read is let through exactly where `kitlib.confidentiality` says False.
        write(self.v, "04-areas/sub/n#.md", MARKED)
        # The deny rules climb to 04-areas/sub for n#.md; the guard names it literally and leaves its
        # unlabelled neighbour open, as the predicate does (#126 review).
        write(self.v, "04-areas/sub/plain.md", "# open\n")
        # A confidential folder whose name a rule cannot match: the guard must name it itself.
        set_config(self.v, OPEN_CONFIG.replace('["04-Areas/Board"]', '["04-Areas/Board", "04-areas/team/h#r"]'))
        write(self.v, "04-areas/team/h#r/plain.md", "# hidden by its folder\n")
        write(self.v, "04-areas/team/plain.md", "# open\n")
        write(self.v, "07-knowledge/Überblick ä.md", "# open\n")
        plan = self.plan()
        self.assertIs(kitlib.confidentiality(self.v, "04-areas/sub/plain.md"), False)
        self.assertIs(kitlib.confidentiality(self.v, "04-areas/team/plain.md"), False)
        self.assertIs(kitlib.confidentiality(self.v, "04-areas/team/h#r/plain.md"), True)
        for dirpath, dirnames, filenames in os.walk(self.v):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                rel = Path(dirpath, name).relative_to(self.v).as_posix()
                expected = 0 if kitlib.confidentiality(self.v, rel) is False else 2
                self.assertEqual(self.code("Read", {"file_path": rel}, plan=plan), expected, rel)


class AllowOpenPlan(_Open):
    def test_the_config_picks_the_mode(self):
        plan = self.plan()
        self.assertEqual(plan.mode, kitagent.ALLOW_OPEN)
        self.assertEqual(plan.deny_read[0], "./")
        self.assertEqual(plan.deny[0], "Edit(./.claude/**)")
        self.assertEqual(plan.allow_read, ["./04-areas", "./07-knowledge", "./08-resources", "./.kit/config.yml"])
        set_config(self.v, CONFIG)
        plan = self.plan()
        self.assertEqual((plan.mode, plan.allow_read, plan.hook), (kitagent.DENY_LIST, [], None))

    def test_only_what_is_confidential_inside_the_open_folders_is_listed(self):
        plan = self.plan()
        for perm, sbx in (("Read(./04-areas/board/**)", "./04-areas/board"),
                          ("Read(./08-resources/exports/*.csv)", "./08-resources/exports/*.csv"),
                          ("Read(./07-knowledge/**/harrier-*.md)", "./07-knowledge/**/harrier-*.md"),
                          (f"Read(./{RAISED_REL})", f"./{RAISED_REL}")):
            self.assertIn(perm, plan.deny)
            self.assertIn(sbx, plan.deny_read)
        for entry in plan.deny + plan.deny_read:            # `./` covers everything outside
            for outside in ("index.md", "log.md", "05-people", ".trash", "_drafts", ".kit", "00-home"):
                self.assertNotIn(outside, entry)
        self.assertFalse(any("not denied: .kit/config.yml" in n for n in plan.notes))

    def test_a_path_rule_that_cannot_reach_an_open_folder_is_left_to_the_root(self):
        set_config(self.v, CONFIG + '  open_folders: ["04-areas"]\n')
        plan = self.plan()
        self.assertNotIn("Read(./08-resources/exports/*.csv)", plan.deny)
        self.assertNotIn("Read(./07-knowledge/**/harrier-*.md)", plan.deny)
        self.assertTrue(kitagent._may_reach("**/*.csv", ("04-areas",)))
        self.assertTrue(kitagent._may_reach("04-Areas/sub/x.md", ("04-areas",)))
        self.assertTrue(kitagent._may_reach("0*/x.md", ("04-areas",)))
        self.assertFalse(kitagent._may_reach("04-areas", ("04-areas",)), "names the folder itself, as a file")
        self.assertFalse(kitagent._may_reach("05-people/*.md", ("04-areas",)))

    def test_the_kits_own_files_inside_a_declared_open_folder_are_denied(self):
        set_config(self.v, CONFIG + '  open_folders: ["04-areas", ".kit"]\n')
        plan = self.plan()
        for entry in ("Read(./.kit/agent-settings/**)", "Read(./.kit/graph.*)", "Read(./.kit/bridge-audit.jsonl)",
                      "Read(./.kit/proposals/**)"):
            self.assertIn(entry, plan.deny)

    def test_a_name_a_rule_cannot_match_climbs_inside_the_open_folder_only(self):
        write(self.v, "04-areas/sub/n#.md", MARKED)
        plan = self.plan()
        self.assertIn("Read(./04-areas/sub/**)", plan.deny)
        self.assertIn("04-areas/sub/n#.md", plan.guard["files"], "the guard names it literally")
        write(self.v, "04-areas/plan #2.md", MARKED)
        with self.assertRaises(kitagent.AgentSettingsError) as ctx:
            self.plan()
        self.assertIn("inside the open folders", str(ctx.exception))
        self.assertNotIn("plan #2", str(ctx.exception))
        # Outside the open folders such a name is covered by `./` and needs nothing.
        (self.v / "04-areas/plan #2.md").unlink()
        write(self.v, "plan [draft].md", MARKED)
        self.assertEqual(self.plan().mode, kitagent.ALLOW_OPEN)

    def test_the_guard_embeds_no_quote_that_ends_the_shell_word(self):
        command = self.plan().hook["hooks"][0]["command"]
        self.assertEqual(command.count("'"), 2, "the program holds no single quote of its own")


class AllowOpenMerge(_Open):
    FOREIGN = {
        "model": "some-model",
        "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(rm:*)"]},
        "sandbox": {"enabled": False, "filesystem": {"allowWrite": ["./tmp"], "denyRead": ["~/.ssh"]}},
        "hooks": {"PreToolUse": []},
    }

    def put(self, data: dict) -> None:
        write(self.v, SETTINGS, json.dumps(data, indent=4) + "\n")

    def test_apply_writes_the_mode_and_a_second_run_changes_nothing(self):
        r = agent(self.v)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("allow-open", r.stdout)
        self.assertIn('"onFailure": "block"', r.stdout)
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        s = read_settings(self.v)
        self.assertEqual(s["sandbox"]["filesystem"]["denyRead"][0], "./")
        self.assertIn("./04-areas", s["sandbox"]["filesystem"]["allowRead"])
        self.assertIn("Edit(./.claude/**)", s["permissions"]["deny"])
        self.assertEqual(len(self.guards()), 1)
        m = json.loads((self.v / MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(m["mode"], "allow-open")
        self.assertEqual(m["hooks.PreToolUse"], self.guards())
        before = (self.v / SETTINGS).read_bytes(), (self.v / MANIFEST).read_bytes()
        r = agent(self.v, "--apply")
        self.assertIn("nothing to do", r.stdout)
        self.assertEqual(((self.v / SETTINGS).read_bytes(), (self.v / MANIFEST).read_bytes()), before)
        self.assertEqual(agent(self.v, "--check").returncode, 0)
        self.assertEqual(kitagent.doctor_row(self.v)[1], "ok")

    def test_a_config_change_is_drift_and_replaces_the_guard(self):
        agent(self.v, "--apply")
        old = self.guards()
        set_config(self.v, CONFIG + '  open_folders: ["04-Areas", "07-knowledge"]\n')
        r = agent(self.v, "--check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(kitagent.doctor_row(self.v)[1].startswith("drift:"))
        agent(self.v, "--apply")
        self.assertEqual(len(self.guards()), 1)
        self.assertNotEqual(self.guards(), old)
        self.assertNotIn("./08-resources", read_settings(self.v)["sandbox"]["filesystem"]["allowRead"])
        self.assertEqual(agent(self.v, "--check").returncode, 0)

    def test_switching_modes_both_ways_replaces_the_owned_entries(self):
        set_config(self.v, CONFIG)
        self.put(self.FOREIGN)
        agent(self.v, "--apply")
        deny_list = read_settings(self.v)
        deny_manifest = json.loads((self.v / MANIFEST).read_text(encoding="utf-8"))
        set_config(self.v, OPEN_CONFIG)
        self.assertEqual(agent(self.v, "--check").returncode, 1)
        agent(self.v, "--apply")
        s = read_settings(self.v)
        self.assertNotIn("Read(./.kit/agent-settings/**)", s["permissions"]["deny"], "`./` covers it now")
        self.assertNotIn("Read(./05-people/**)", s["permissions"]["deny"])
        self.assertIn("Bash(rm:*)", s["permissions"]["deny"])
        self.assertEqual(len(self.guards()), 1)
        set_config(self.v, CONFIG)
        self.assertEqual(agent(self.v, "--check").returncode, 1)
        agent(self.v, "--apply")
        # The same entries; the ones added again come after those that stayed, so order aside.
        self.assertEqual(sorted_lists(read_settings(self.v)), sorted_lists(deny_list))
        self.assertEqual(sorted_lists(json.loads((self.v / MANIFEST).read_text(encoding="utf-8"))),
                         sorted_lists(deny_manifest))
        self.assertEqual(read_settings(self.v)["hooks"], {"PreToolUse": []})

    def test_remove_takes_out_the_guard_and_leaves_other_hooks(self):
        foreign = json.loads(json.dumps(self.FOREIGN))
        foreign["hooks"]["PreToolUse"].append(USER_HOOK)
        self.put(foreign)
        agent(self.v, "--apply")
        self.assertEqual(read_settings(self.v)["hooks"]["PreToolUse"][0], USER_HOOK)
        r = agent(self.v, "--remove")
        self.assertIn("dry run", r.stdout)
        self.assertEqual(agent(self.v, "--remove", "--apply").returncode, 0)
        self.assertEqual(read_settings(self.v), foreign)

    def test_remove_keeps_an_empty_list_that_was_there(self):
        self.put(self.FOREIGN)
        agent(self.v, "--apply")
        self.assertEqual(agent(self.v, "--remove", "--apply").returncode, 0)
        self.assertEqual(read_settings(self.v), self.FOREIGN)

    def test_remove_from_a_vault_that_had_no_settings(self):
        agent(self.v, "--apply")
        self.assertEqual(agent(self.v, "--remove", "--apply").returncode, 0)
        self.assertEqual(read_settings(self.v), {})
        self.assertFalse((self.v / MANIFEST).exists())

    def test_wrong_hook_shapes_are_refused(self):
        for bad in ({"hooks": []}, {"hooks": {"PreToolUse": {}}}, {"sandbox": {"filesystem": {"allowRead": "./x"}}}):
            self.put(bad)
            r = agent(self.v, "--apply")
            self.assertEqual(r.returncode, 2, bad)
            self.assertEqual(read_settings(self.v), bad)

    def test_a_manifest_from_before_the_mode_reads_as_the_deny_list(self):
        set_config(self.v, CONFIG)
        agent(self.v, "--apply")
        m = json.loads((self.v / MANIFEST).read_text(encoding="utf-8"))
        del m["mode"]
        write(self.v, MANIFEST, json.dumps(m))
        self.assertEqual(agent(self.v, "--check").returncode, 1, "manifest out of date")
        set_config(self.v, OPEN_CONFIG)
        self.assertEqual(agent(self.v, "--apply").returncode, 0)
        self.assertNotIn("Read(./05-people/**)", read_settings(self.v)["permissions"]["deny"])
        self.assertEqual(agent(self.v, "--remove", "--apply").returncode, 0)


class AllowOpenDoctor(_Open):
    def row(self) -> str:
        return kitagent.doctor_row(self.v)[1]

    def edit(self, change) -> None:
        s = read_settings(self.v)
        change(s)
        write(self.v, SETTINGS, json.dumps(s))

    def test_an_altered_guard(self):
        agent(self.v, "--apply")
        self.edit(lambda s: s["hooks"]["PreToolUse"][0]["hooks"][0].update(
            command=s["hooks"]["PreToolUse"][0]["hooks"][0]["command"].replace("sys.exit(2)", "sys.exit(0)")))
        self.assertIn("open-folder guard altered", self.row())

    def test_an_altered_guard_left_beside_a_new_one(self):
        agent(self.v, "--apply")
        self.edit(lambda s: s["hooks"]["PreToolUse"][0]["hooks"][0].pop("onFailure"))
        agent(self.v, "--apply")                  # writes a correct guard; the altered one is not the kit's any more
        self.assertEqual(len(self.guards()), 2)
        status = self.row()
        self.assertIn("open-folder guard altered", status)
        self.assertIn("without onFailure", status)

    def test_a_missing_guard_and_a_missing_on_failure(self):
        agent(self.v, "--apply")
        self.edit(lambda s: s["hooks"]["PreToolUse"][0]["hooks"][0].pop("onFailure"))
        status = self.row()
        self.assertIn("open-folder guard without onFailure: block", status)
        self.assertIn("open-folder guard altered", status)
        self.edit(lambda s: s["hooks"].update(PreToolUse=[]))
        self.assertIn("open-folder guard missing", self.row())

    def test_disable_all_hooks_in_either_file(self):
        agent(self.v, "--apply")
        self.edit(lambda s: s.update(disableAllHooks=True))
        self.assertIn("settings.json sets disableAllHooks", self.row())
        self.edit(lambda s: s.update(disableAllHooks=False))
        self.assertEqual(self.row(), "ok")
        write(self.v, kitagent.LOCAL_SETTINGS_REL, json.dumps({"disableAllHooks": 1}))
        self.assertIn("settings.local.json sets disableAllHooks", self.row())
        r = agent(self.v, "--check")
        self.assertIn("disableAllHooks", r.stderr)

    def test_the_kits_allow_read_is_not_a_reopening_but_another_is(self):
        agent(self.v, "--apply")
        self.assertEqual(self.row(), "ok")
        write(self.v, kitagent.LOCAL_SETTINGS_REL, json.dumps({"sandbox": {"filesystem": {"allowRead": ["./05-people"]}}}))
        self.assertIn("1 allowRead entry re-open denied paths", self.row())

    def test_doctor_names_no_vault_path(self):
        agent(self.v, "--apply")
        self.edit(lambda s: s["hooks"]["PreToolUse"][0]["hooks"][0].pop("onFailure"))
        write(self.v, "04-areas/new-hidden.md", MARKED)
        report = self.v.parent / "doctor.txt"
        r = run("doctor", "--vault", str(self.v), "--report", str(report), "--base-url", "http://127.0.0.1:9/v1")
        line = next(x for x in r.stdout.splitlines() if x.startswith("agent settings (claude-code)"))
        self.assertIn("drift:", line)
        self.assertIn("guard", line)
        text = report.read_text(encoding="utf-8")
        for name in ("new-hidden", "harrier", "payroll", "heron", "04-areas", "board", "07-knowledge"):
            self.assertNotIn(name, line)
            self.assertNotIn(name, text)
        r = agent(self.v, "--check")
        for name in ("new-hidden", "04-areas", "python3"):
            self.assertNotIn(name, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
