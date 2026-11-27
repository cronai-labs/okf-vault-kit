"""kitagent — what is confidential, as data, and a coding agent's own read restrictions built from it (#120).

Two layers, both pure functions of the vault so the CLI, `--check` and `doctor` cannot disagree:

  policy_export   every path the shared predicate (`kitlib.confidentiality`) hides, in the vault's own
                  spelling: the declared folders, the declared path rules, and each other file judged
                  confidential (marked in its frontmatter, matched by a path rule, or unreadable). Nothing
                  here decides confidentiality; it only walks the tree and asks the predicate.
  claude_code_*   Claude Code's `permissions.deny` `Read(...)` rules and `sandbox.filesystem.denyRead`
                  entries for that export, merged into the vault's `.claude/settings.json`. Which entries
                  the kit added is recorded in a manifest beside the policy, not in settings.json, so an
                  entry the user wrote is never removed. With `open_folders` declared (#126) the plan is
                  allow-open instead: `./` denied to the sandbox, the open folders in `allowRead`, what is
                  confidential inside them denied one by one, edits to `.claude/` denied, and a
                  PreToolUse guard (GUARD_CODE) inline in settings.json for the Read, Grep and Glob tools,
                  which the sandbox does not govern. In both modes the config and the manifest are
                  write-protected (`Edit(...)` deny rules and `sandbox.filesystem.denyWrite`): the
                  bridge reads the config live, so an agent that could edit it could widen the policy.

Standard library only, apart from what kitlib needs.
"""
from __future__ import annotations

import contextlib
import copy
import difflib
import json
import os
import posixpath
import shlex
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import kitlib

EXPORT_VERSION = 1
SETTINGS_REL = ".claude/settings.json"
LOCAL_SETTINGS_REL = ".claude/settings.local.json"
MANIFEST_DIR = ".kit/agent-settings"
MANIFEST_REL = f"{MANIFEST_DIR}/claude-code.json"
GENERATOR = "kit.py agent-settings claude-code"
# The files this generator writes or the agent's own harness reads: never listed as confidential,
# or a path rule such as `**/*.json` would make each run see the last one's output as new (#120).
AGENT_OWN = frozenset({SETTINGS_REL, LOCAL_SETTINGS_REL, MANIFEST_REL})
# Files the kit derives from the notes, denied whatever the policy says: written before a floor was
# declared (or by a graph built with --show-confidential), they still hold its notes' titles,
# frontmatter and, in the audit log, text a tool served. `kit.py graph build` rewrites the graph.
DERIVED = (("path", ".kit/graph.*"), ("file", ".kit/bridge-audit.jsonl"), ("folder", ".kit/proposals"))
# What --apply sets in `sandbox`, whatever was there: without these the deny lists are best effort.
SANDBOX_FLAGS = {"enabled": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True}
# A permission rule is a gitignore pattern with no documented escape: a file name holding one of
# these, or edge whitespace, or a line break, may not be matched literally. Such a file is denied
# through the nearest folder above it whose path holds none of them.
UNSAFE_CHARS = frozenset("*?[]\\!#")
PATTERN_UNSAFE = frozenset("[]\\!#")      # a path rule keeps its * and ?, which mean the same there
APPLY_HINT = f"run `{GENERATOR} --apply` with the same --vault"
DENY_LIST, ALLOW_OPEN = "deny-list", "allow-open"
# Allow-open mode (#126): the file tools a PreToolUse guard sees, and how long it may take. The
# guard is the only control over the Read, Grep and Glob tools there: the sandbox governs shell
# commands only, and a permission rule cannot re-open a folder under a denied root.
HOOK_MATCHER = "Read|Grep|Glob|NotebookRead|Edit|Write|MultiEdit|NotebookEdit"
HOOK_TIMEOUT = 10
GUARD_MARKER = "okf-vault-kit open-folder guard"
# Never readable or writable through the file tools in allow-open mode, whatever `open_folders`
# says: the agent's own settings (which hold the guard), and git's object store unless --allow-git.
GUARD_ALWAYS_DENIED = (".claude",)
EDIT_CLAUDE = "Edit(./.claude/**)"
# Write-protected in both modes, against the shell (sandbox denyWrite) and the file tools (Edit
# rules): the policy the bridge reads live, and the manifest that says which entries are the kit's.
# A canary on Claude Code 2.1.330 appended to the config from the agent's shell without them.
WRITE_PROTECTED = (kitlib.CONFIG_REL, MANIFEST_DIR)
EDIT_PROTECTED = (f"Edit(./{kitlib.CONFIG_REL})", f"Edit(./{MANIFEST_DIR}/**)")
DENY_WRITE = [f"./{rel}" for rel in WRITE_PROTECTED]
# The guard itself. Standard library only, and old-Python safe (no walrus, no removeprefix): it runs
# under whatever `python3` is on PATH. `__DATA__` becomes a string literal holding the JSON of the
# open and denied lists, so the guard reads no file at run time: a canary showed that a hook whose
# script file is missing does not run at all, even with `onFailure: "block"`.
GUARD_CODE = '''\
# okf-vault-kit open-folder guard, written by kit.py agent-settings claude-code (docs/security.md).
# Exit 2 refuses the tool call: unless every path it names is proven to lie inside an open folder
# and outside every denied entry, and on any error at all.
import json, os, re, sys, unicodedata
C = json.loads(__DATA__)
TOOLS = {"Read": "file_path", "Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
         "NotebookRead": "notebook_path", "NotebookEdit": "notebook_path", "Grep": "path", "Glob": "path"}
PATTERN = {"Grep": "glob", "Glob": "pattern"}
READS = ("Read", "Grep", "Glob", "NotebookRead")


def no(why):
    sys.stderr.write("okf-vault-kit guard: " + why + "; this vault allows its file tools only inside its open folders\\n")
    sys.exit(2)


def form(p):
    return unicodedata.normalize("NFC", p.replace(os.sep, "/")).casefold()


def under(rel, base):
    return rel == base or rel.startswith(base + "/")


def leaves(p):
    # A brace may expand to `..` or an absolute path (`{..,x}`, `{,}/etc`), and whether Grep or Glob
    # expand braces was not observed: refused unless proven.
    return p.startswith("~") or os.path.isabs(p) or ".." in re.split(r"[/\\\\]", p) or "{" in p or "}" in p


def rel_to(path, bases):
    f = form(path)
    for b in bases:
        b = form(b).rstrip("/")
        if f == b:
            return ""
        if f.startswith(b + "/"):
            return f[len(b) + 1:]
    return None


def allowed(rel, write):
    if not rel:
        return False
    if rel.startswith(".trash/") and not allowed(rel[len(".trash/"):], write):
        return False
    if write and any(under(rel, r) for r in C["read_only"]):
        return False
    if any(under(rel, d) for d in C["folders"]) or rel in C["files"]:
        return False
    if any(re.fullmatch(x, rel, re.DOTALL) for x in C["patterns"]):
        return False
    return any(under(rel, o) for o in C["open"])


def main():
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        no("unknown hook input")
    tool, args = data.get("tool_name"), data.get("tool_input")
    if tool not in TOOLS or not isinstance(args, dict):
        no("unknown tool or input shape")
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        cwd = project
    raw = args.get(TOOLS[tool])
    if not isinstance(raw, str) or not raw or "\\0" in raw:
        no(str(tool) + " names no usable path")
    if raw.startswith("~") or ".." in re.split(r"[/\\\\]", raw):
        no(tool + " with a path that climbs")
    pattern = args.get(PATTERN.get(tool, ""))
    if pattern is not None and (not isinstance(pattern, str) or leaves(pattern)):
        no(tool + " with a pattern that may leave its folder (absolute, `..`, `~` or a brace)")
    # A relative path is judged from the cwd of the call and from the project dir: which one the tool
    # uses was not observed, so both must pass.
    fulls = [raw] if os.path.isabs(raw) else [os.path.join(cwd, raw), os.path.join(project, raw)]
    bases = [os.path.normpath(os.path.abspath(project)), os.path.realpath(project)]
    for full in fulls:
        lexical = rel_to(os.path.normpath(full), bases)
        resolved = rel_to(os.path.realpath(full), bases[1:])
        if not (allowed(lexical, tool not in READS) and allowed(resolved, tool not in READS)):
            no(tool + " outside the open folders, or on a denied path")


try:
    main()
except SystemExit:
    raise
except BaseException as exc:
    no("could not check the call (" + exc.__class__.__name__ + ")")
'''


def guard_command(data: dict[str, Any]) -> str:
    """The hook command: `python3 -I -c '<GUARD_CODE>'` with `data` embedded as a JSON string literal.

    `-I` keeps the environment's PYTHON* variables, the user's site-packages and the current folder
    out of the interpreter. The JSON is ASCII (`json.loads` rebuilds any other character), and the
    whole program is quoted for `sh -c` by `shlex.quote`, so no folder name can end the quoting.
    """
    literal = json.dumps(json.dumps(data, sort_keys=True))
    return "python3 -I -c " + shlex.quote(GUARD_CODE.replace("__DATA__", literal, 1))


def guard_entry(data: dict[str, Any]) -> dict[str, Any]:
    """The `hooks.PreToolUse` entry: owned through the manifest by exactly this content."""
    return {"matcher": HOOK_MATCHER,
            "hooks": [{"type": "command", "command": guard_command(data), "timeout": HOOK_TIMEOUT, "onFailure": "block"}]}


def is_guard(entry: Any) -> bool:
    """Does this `hooks.PreToolUse` entry carry the kit's guard (as written, or altered since)?"""
    return isinstance(entry, dict) and isinstance(entry.get("hooks"), list) and any(
        isinstance(h, dict) and isinstance(h.get("command"), str) and GUARD_MARKER in h["command"] for h in entry["hooks"])


class AgentSettingsError(ValueError):
    """The settings cannot be generated or merged; the message says why and what to do.

    `doctor` is the short reason doctor prints: it names a settings key at most, never a vault path.
    """

    def __init__(self, message: str, doctor: str = "cannot be generated for this vault"):
        super().__init__(message)
        self.doctor = doctor


# ---------------------------------------------------------------- export

def _same(a: str, b: str) -> bool:
    return unicodedata.normalize("NFC", a).casefold() == unicodedata.normalize("NFC", b).casefold()


def _on_disk(root: Path, declared: str) -> list[str]:
    """Every existing spelling of a declared folder (matching ignores case and Unicode form, as the
    predicate does), or the declared spelling when none exists yet. An agent's deny lists match
    literally, so `04-Areas/Board` in the config must become the `04-areas/board` on disk."""
    found = [""]
    for seg in declared.split("/"):
        nxt = []
        for base in found:
            try:
                with os.scandir(root / base if base else root) as it:
                    names = sorted(e.name for e in it if e.is_dir(follow_symlinks=False) and _same(e.name, seg))
            except OSError:
                names = []
            nxt += [posixpath.join(base, n) if base else n for n in names]
        found = nxt
        if not found:
            return [declared]
    return found


def policy_export(root: Path) -> dict[str, Any]:
    """What is confidential in this vault, as the vault spells it. Raises kitlib.PolicyConfigError.

    `confidential_files` holds every file under the vault, `.git` and AGENT_OWN aside, that `kitlib.confidentiality`
    does not clear and that no listed folder already covers: a note's own `sensitivity`, a path rule,
    or a file it could not read (fail closed; also in `unreadable`). Not `iter_markdown`: an agent's
    tools read attachments, `.trash` and `_`-prefixed folders like any other file. A folder the walk
    cannot list is listed with a trailing `/`, in both lists: what is in it cannot be told.

    Symlinked folders are followed, since an agent reads through them; a folder that lands in a declared
    folder once links are resolved (a link into it, or a folder below a link to its parent) is listed
    whole with a trailing `/`, and so is a link back up its own path (a cycle), which is not followed again.
    """
    root = Path(root).resolve()
    policy = kitlib.confidentiality_policy(root)
    folders = sorted({f for d in policy.declared_folders for f in _on_disk(root, d)})
    files: list[str] = []
    unreadable: list[str] = []

    def unlisted(exc: OSError) -> None:
        name = getattr(exc, "filename", None)
        if not name:
            return
        rel = Path(os.path.relpath(name, root)).as_posix()
        if rel in ("", "."):
            raise exc
        files.append(rel + "/")
        unreadable.append(rel + "/")

    real_root = os.path.realpath(root)
    # The resolved folders on the way down to each folder: a link to one of them is a cycle.
    chains: dict[str, tuple[str, ...]] = {str(root): (real_root,)}
    for dirpath, dirnames, filenames in os.walk(root, onerror=unlisted, followlinks=True):
        chain = chains.pop(dirpath, (os.path.realpath(dirpath),))
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in sorted(dirnames):
            rel = posixpath.join(rel_dir, d) if rel_dir else d
            if (not rel_dir and d == ".git") or policy.in_folder(rel):
                continue           # .git: its own entry; a declared folder: covered whole
            full = os.path.join(dirpath, d)
            real = os.path.realpath(full)
            if real in chain:
                # A link back up its own path: not walked again, but an alias for everything above
                # it, and whether an agent resolves it before matching a deny rule was not tested.
                files.append(rel + "/")
                continue
            # Where it lands, for every folder and not only a link: below a link to the parent of a
            # declared folder (`alias -> 04-areas` for `04-areas/hr`), `alias/hr` is no link itself.
            try:
                inner = os.path.relpath(real, real_root).replace(os.sep, "/")
            except ValueError:
                inner = ".."                      # another drive (Windows): outside the vault
            if not inner.startswith("../") and inner != ".." and policy.in_folder(inner):
                files.append(rel + "/")           # reached through a link into a declared folder: denied whole
                continue
            chains[full] = (*chain, real)
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = posixpath.join(rel_dir, name) if rel_dir else name
            if rel in AGENT_OWN:
                continue
            verdict = kitlib.confidentiality(root, rel)
            if verdict is False:
                continue
            files.append(rel)
            if verdict is None:
                unreadable.append(rel)
    return {
        "version": EXPORT_VERSION,
        "source": kitlib.CONFIG_REL,
        "confidential_folders": folders,
        "confidential_paths": sorted(set(policy.declared_paths)),
        "confidential_files": sorted(set(files)),
        "unreadable": sorted(set(unreadable)),
    }


def export_json(export: dict[str, Any]) -> str:
    return json.dumps(export, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- plan

def literal_safe(rel: str, unsafe: frozenset[str] = UNSAFE_CHARS) -> bool:
    """Can a permission rule name this vault-relative path literally?"""
    if "\n" in rel or "\r" in rel:
        return False
    return all(seg == seg.strip() and not unsafe.intersection(seg) for seg in rel.split("/"))


def _safe_ancestor(rel: str) -> str:
    """The nearest folder above `rel` whose path a rule can name; "" is the vault root."""
    parent = posixpath.dirname(rel)
    while parent and not literal_safe(parent):
        parent = posixpath.dirname(parent)
    return parent


def entries(kind: str, rel: str) -> tuple[str, str]:
    """(permissions.deny entry, sandbox.filesystem.denyRead entry) for one folder, pattern or file."""
    if kind == "folder":
        return f"Read(./{rel}/**)", f"./{rel}"
    return f"Read(./{rel})", f"./{rel}"


def _under(rel: str, folder: str) -> bool:
    return rel.startswith(folder + "/")


def git_outside(root: Path) -> Path | None:
    """The repository that keeps this vault's history outside `./.git`, if any: a `.git` file (a
    worktree or submodule points elsewhere) or a `.git` in a folder above the vault."""
    root = Path(root).resolve()
    dot = root / ".git"
    if dot.is_file():
        return dot
    if dot.exists():
        return None
    for parent in root.parents:
        if (parent / ".git").exists():
            return parent / ".git"
    return None


def _derived(rel: str) -> bool:
    """Does one of the DERIVED entries deny this path as written? (Literal: no case folding, no `.trash`.)"""
    for kind, entry in DERIVED:
        if kind == "folder" and (rel == entry or _under(rel, entry)):
            return True
        if kind == "file" and rel == entry:
            return True
        if kind == "path" and posixpath.dirname(rel) == posixpath.dirname(entry) and \
                posixpath.basename(rel).startswith(posixpath.basename(entry).rstrip("*")):
            return True
    return False


@dataclass
class Plan:
    deny: list[str] = field(default_factory=list)          # permissions.deny entries the kit wants
    deny_read: list[str] = field(default_factory=list)     # sandbox.filesystem.denyRead entries
    deny_write: list[str] = field(default_factory=list)    # sandbox.filesystem.denyWrite entries
    notes: list[str] = field(default_factory=list)         # preview lines: escalations, skips, git
    flags: list[str] = field(default_factory=list)         # short doctor-safe flags, no vault paths
    allow_git: bool = False
    mode: str = DENY_LIST
    allow_read: list[str] = field(default_factory=list)    # sandbox.filesystem.allowRead (allow-open)
    hook: dict[str, Any] | None = None                     # the hooks.PreToolUse entry (allow-open)
    guard: dict[str, Any] | None = None                    # the lists embedded in the hook


def _git_notes(root: Path, plan: Plan, wanted: list[tuple[str, str]], how: str = "is denied") -> None:
    """The `.git` choice and the history-outside warning, for either mode."""
    if (root / ".git").is_dir():
        if plan.allow_git:
            plan.notes.append("--allow-git: `.git` is not denied, so confidential notes tracked in git stay readable "
                              "through the history (`git show`, `git grep HEAD`)")
        else:
            wanted.append(("folder", ".git"))
            plan.notes.append(f"`.git` {how}, so confidential notes tracked in git cannot be read through the "
                              "history; the agent cannot use git in this vault either (--allow-git keeps git)")
    outside = git_outside(root)
    if outside is not None:
        plan.notes.append(f"the vault's git history lives outside it ({outside}); the kit denies nothing outside the "
                          "vault, so confidential notes tracked there stay readable through git")
        plan.flags.append("git history outside the vault not denied")


def _in_open(rel: str, opens: tuple[str, ...]) -> bool:
    """Is this vault-relative path at or under an open folder (match forms, as the predicate compares)?"""
    form = kitlib._match_form(rel)
    return any(form == o or form.startswith(o + "/") for o in opens)


def _below_open(rel: str, opens: tuple[str, ...]) -> bool:
    """Strictly below an open folder: denying it leaves the open folder itself open."""
    form = kitlib._match_form(rel)
    return any(form.startswith(o + "/") for o in opens)


def _may_reach(pattern: str, opens: tuple[str, ...]) -> bool:
    """Could this path rule match a file inside an open folder? Conservative: a wildcard in a name
    before the pattern runs past the open folder counts as a match."""
    segs = kitlib._match_form(pattern).split("/")
    for o in opens:
        oseg = o.split("/")
        for i, seg in enumerate(segs):
            if "*" in seg or "?" in seg:
                return True
            if i >= len(oseg):
                return True               # literally below the open folder
            if seg != oseg[i]:
                break
    return False


def _confidential_entries(export: dict[str, Any], plan: Plan, keep, ancestor_ok, pattern_keep) -> list[tuple[str, str]]:
    """The (kind, rel) entries for the export's confidential folders, files and path rules.

    `keep` picks the paths this mode lists (all of them, or those inside the open folders);
    `ancestor_ok` says whether a name a rule cannot match literally may be denied through that folder;
    `pattern_keep` picks the path rules carried over as patterns. Raises AgentSettingsError when a
    name could be denied only through a folder `ancestor_ok` refuses.
    """
    refused = 0
    escalated: dict[str, int] = {}
    folders: list[str] = []
    files: list[str] = []

    def place(rel: str, is_dir: bool) -> None:
        nonlocal refused
        if literal_safe(rel):
            (folders if is_dir else files).append(rel)
            return
        anc = _safe_ancestor(rel)
        if not ancestor_ok(anc):
            refused += 1
        else:
            escalated[anc] = escalated.get(anc, 0) + 1

    for rel in export["confidential_folders"]:
        if keep(rel):
            place(rel, True)
    for rel in export["confidential_files"]:
        is_dir = rel.endswith("/")
        rel = rel.rstrip("/")
        if not keep(rel) or rel in AGENT_OWN or _under(rel, MANIFEST_DIR) or _derived(rel):
            continue           # outside this mode's reach, or the kit's own files: denied above, or never
        if rel == kitlib.CONFIG_REL:
            plan.notes.append(f"not denied: {kitlib.CONFIG_REL} (a path rule covers it, but the agent's own kit runs "
                              "need it, and a policy they cannot read hides everything from them)")
            continue
        place(rel, is_dir)
    if refused:
        where = ("inside the open folders" if plan.mode == ALLOW_OPEN else "at the vault root")
        cover = ("an open folder itself or a folder above it" if plan.mode == ALLOW_OPEN else "the vault itself")
        raise AgentSettingsError(
            f"{refused} confidential file(s) or folder(s) {where} have names a permission rule cannot "
            "match literally (one of * ? [ ] \\ ! #, a leading or trailing space, or a line break), and the only "
            f"folder that would cover them is {cover}. Rename them, or move them into a confidential "
            "folder, and run again")
    for anc, n in sorted(escalated.items()):
        folders.append(anc)
        plan.notes.append(f"denies the whole folder {anc}/: {n} confidential name(s) in it hold a character a "
                          "permission rule cannot match literally")
    patterns = []
    for pat in export["confidential_paths"]:
        if not pattern_keep(pat):
            continue
        if kitlib.ConfidentialityPolicy.build(paths=[pat]).covers(kitlib.CONFIG_REL):
            plan.notes.append(f"path rule {pat!r} is not carried over as a pattern, since it would deny "
                              f"{kitlib.CONFIG_REL}; the files it matches now are denied one by one, a file it "
                              "matches later only after the next run")
        elif literal_safe(pat, PATTERN_UNSAFE):
            # The spellings the kit matches: a rule without an extension covers the `.md` note too.
            patterns += kitlib.path_rule_spellings(pat)
        else:
            # The config refuses [ ] \\ ! # in a path rule; what is left is a name with edge spaces
            # or a line break inside the pattern.
            plan.notes.append(f"path rule {pat!r} is not carried over (a name in it has edge spaces or a line break, "
                              "which a permission rule may not match literally); the files it matches now are denied "
                              "one by one, a file it matches later only after the next run")
    chosen = sorted(set(folders))
    # A folder inside another chosen folder, and a file inside any, adds nothing.
    chosen = [f for f in chosen if not any(_under(f, g) for g in chosen)]
    if any(_under(kitlib.CONFIG_REL, g) for g in chosen):
        plan.notes.append(f"{kitlib.CONFIG_REL} is denied with the folder that holds it; the agent's own kit runs "
                          "then cannot read the policy and hide everything from it")
    out = [("folder", f) for f in chosen]
    out += [("path", p) for p in patterns]
    out += [("file", f) for f in sorted(set(files)) if not any(_under(f, g) for g in chosen)]
    return out


def _add_entries(plan: Plan, wanted: list[tuple[str, str]]) -> None:
    seen = set(plan.deny)
    for kind, rel in wanted:
        perm, sbx = entries(kind, rel)
        if perm in seen:
            continue
        seen.add(perm)
        plan.deny.append(perm)
        plan.deny_read.append(sbx)


def claude_code_plan(root: Path, export: dict[str, Any], allow_git: bool = False) -> Plan:
    """The entries for an export, in the mode the config picks: allow-open when `open_folders` is
    declared, the deny list otherwise. Raises AgentSettingsError when a file would need the whole
    vault (or, in allow-open mode, a whole open folder) denied."""
    root = Path(root).resolve()
    policy = kitlib.confidentiality_policy(root)
    if policy.open_folders:
        return _allow_open_plan(root, export, policy, allow_git)
    plan = Plan(allow_git=allow_git, deny=[EDIT_CLAUDE, *EDIT_PROTECTED], deny_write=list(DENY_WRITE))
    wanted: list[tuple[str, str]] = [("folder", MANIFEST_DIR), *DERIVED]
    _git_notes(root, plan, wanted)
    wanted += _confidential_entries(export, plan, keep=lambda rel: True, ancestor_ok=bool,
                                    pattern_keep=lambda pat: True)
    _add_entries(plan, wanted)
    return plan


def _allow_open_plan(root: Path, export: dict[str, Any], policy: kitlib.ConfidentialityPolicy,
                     allow_git: bool) -> Plan:
    """Allow-open (#126): deny the vault (`./`) to the shell, re-open the open folders, deny what is
    confidential inside them one by one, and guard the file tools with a PreToolUse hook.

    The export lists every file outside the open folders too; `./` covers those, so only what lies
    inside an open folder becomes an entry. The guard's lists are the same entries, literal (it needs
    no climb to a folder for a name a rule cannot match), plus `.claude` always and `.git` unless
    --allow-git, which opens `.git` to the read tools only.
    """
    opens = policy.open_folders
    plan = Plan(allow_git=allow_git, mode=ALLOW_OPEN, deny_write=list(DENY_WRITE))
    plan.notes.append("allow-open: the vault is denied to the agent and only the open folders "
                      f"({len(policy.declared_open_folders)}) are allowed; its file tools can read and write "
                      "only inside them, and a PreToolUse guard refuses every other path")
    special: list[tuple[str, str]] = [("folder", MANIFEST_DIR), *DERIVED, ("folder", ".claude")]
    git_wanted: list[tuple[str, str]] = []
    _git_notes(root, plan, git_wanted, how="is denied with the rest of the vault")
    special += git_wanted
    # The vault itself goes to the sandbox as `./`, and the agent may not edit its own settings,
    # which hold the guard: with that rule it could not switch the guard off by any tool in the canary.
    plan.deny, plan.deny_read = [EDIT_CLAUDE, *EDIT_PROTECTED], ["./"]
    wanted: list[tuple[str, str]] = []
    # The kit's own files are outside the open folders unless one is declared over them: then on the lists.
    wanted += [(k, r) for k, r in special if _in_open(r.rstrip("*").rstrip("."), opens)]
    found = _confidential_entries(export, plan, keep=lambda rel: _in_open(rel, opens),
                                  ancestor_ok=lambda anc: bool(anc) and _below_open(anc, opens),
                                  pattern_keep=lambda pat: _may_reach(pat, opens))
    wanted += found
    _add_entries(plan, wanted)
    # `.claude` is never re-opened to the shell, nor `.git` without --allow-git, even when declared open.
    closed = (".claude",) + (() if allow_git else (".git",))
    allow = sorted({f"./{f}" for d in policy.declared_open_folders for f in _on_disk(root, d)
                    if not _in_open(f, closed)})
    allow.append(f"./{kitlib.CONFIG_REL}")         # the agent's own kit runs load the policy, as in deny-list mode
    if allow_git and (root / ".git").is_dir() and "./.git" not in allow:
        allow.append("./.git")
    plan.allow_read = allow
    # The guard: literal names, matched as the predicate matches (case and Unicode form aside). Taken
    # from the export, not from `found`: that holds the folder the deny rules climb to for a name they
    # cannot match, and the guard would then refuse that name's neighbours, which the predicate leaves open.
    folders = {kitlib._match_form(r.rstrip("/")) for r in export["confidential_folders"] if _in_open(r.rstrip("/"), opens)}
    folders |= {kitlib._match_form(r.rstrip("/")) for r in export["confidential_files"]
                if r.endswith("/") and _in_open(r.rstrip("/"), opens)}
    folders |= {kitlib._match_form(r) for k, r in special if k == "folder"}
    folders |= set(GUARD_ALWAYS_DENIED)
    if not allow_git:
        folders.add(".git")
    files = {kitlib._match_form(r.rstrip("/")) for r in export["confidential_files"] if _in_open(r.rstrip("/"), opens)}
    files |= {kitlib._match_form(r) for k, r in special if k == "file"}
    # Every path rule, whether it reaches an open folder or not: the guard matches them all, so a
    # pattern the deny lists leave to `./` costs nothing here.
    pats = {kitlib._glob_regex(s).pattern for p in policy.paths for s in kitlib.path_rule_spellings(p)}
    pats |= {kitlib._glob_regex(kitlib._match_form(r)).pattern for k, r in special if k == "path"}
    # --allow-git opens .git to the read tools only: a hook written into .git/hooks would run outside
    # the sandbox at the next git command. Read-only whether or not .git is also declared open.
    opened = set(opens) | ({".git"} if allow_git else set())
    plan.guard = {"open": sorted(opened), "read_only": [".git"], "folders": sorted(folders),
                  "files": sorted(files), "patterns": sorted(pats)}
    plan.hook = guard_entry(plan.guard)
    return plan


# ---------------------------------------------------------------- merge

@dataclass
class Merge:
    settings: dict[str, Any]
    manifest: dict[str, Any]
    added: list[str]
    removed: list[str]
    flags_set: list[str]         # sandbox keys --apply sets because they are missing or differ
    overrides: list[str]         # of those, the ones that replace a value someone wrote
    warnings: list[str]
    changed: bool


def _list_at(obj: dict, path: tuple[str, ...], where: str) -> list:
    cur: Any = obj
    for i, key in enumerate(path):
        if not isinstance(cur, dict):
            raise AgentSettingsError(f"{where}: `{'.'.join(path[:i])}` must be an object — fix it by hand first",
                                     f"`{'.'.join(path[:i])}` has the wrong type")
        cur = cur.get(key)
        if cur is None:
            return []
    if not isinstance(cur, list) or not all(isinstance(x, str) for x in cur):
        raise AgentSettingsError(f"{where}: `{'.'.join(path)}` must be a list of strings — fix it by hand first",
                                 f"`{'.'.join(path)}` has the wrong type")
    return cur


def _owned(manifest: Any, key: str) -> set[str] | None:
    if not isinstance(manifest, dict):
        return None
    value = manifest.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        return None
    return set(value)


def _merge_list(current: list[str], owned: set[str], wanted: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    """(new list, entries now owned, added, removed). Only entries the kit added are owned: one the
    user wrote stays theirs even when the kit wants it too, so dropping a floor never removes it."""
    want = set(wanted)
    removed = [e for e in current if e in owned and e not in want]
    kept = [e for e in current if not (e in owned and e not in want)]
    present = set(kept)
    added = [w for w in wanted if w not in present]
    before = set(current)
    now_owned = [w for w in wanted if w in owned or w not in before]
    return kept + added, now_owned, added, removed


ALLOW_KEY = "sandbox.filesystem.allowRead"
DENY_WRITE_KEY = "sandbox.filesystem.denyWrite"
HOOKS_KEY = "hooks.PreToolUse"
# Settings keys the kit records as created when it adds them, so that --remove and a switch back to
# the deny list take out a key the kit made and keep one that was there before, empty or not.
CREATED_KEYS = (ALLOW_KEY, "hooks", HOOKS_KEY)
GUARD_LABEL = f"{HOOKS_KEY}: the open-folder guard"


def _owned_hooks(manifest: Any) -> list[dict] | None:
    if not isinstance(manifest, dict):
        return None
    value = manifest.get(HOOKS_KEY, [])
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        return None
    return value


def _created(manifest: Any) -> set[str]:
    value = manifest.get("created") if isinstance(manifest, dict) else None
    return {x for x in value if x in CREATED_KEYS} if isinstance(value, list) else set()


def _has(entries: list, entry: Any) -> bool:
    return any(same_json(e, entry) for e in entries)


def _hooks_list(settings: dict, where: str) -> list:
    """`hooks.PreToolUse` as it is ([] when absent); refused when `hooks` or the list has the wrong type."""
    hooks = settings.get("hooks")
    if hooks is None:
        return []
    if not isinstance(hooks, dict):
        raise AgentSettingsError(f"{where}: `hooks` must be an object — fix it by hand first", "`hooks` has the wrong type")
    pre = hooks.get("PreToolUse")
    if pre is None:
        return []
    if not isinstance(pre, list):
        raise AgentSettingsError(f"{where}: `{HOOKS_KEY}` must be a list — fix it by hand first",
                                 f"`{HOOKS_KEY}` has the wrong type")
    return pre


def _place(settings: dict, path: tuple[str, ...], value: list, created: set[str], prefix: str = "") -> set[str]:
    """Put `value` at `path` (two keys below `settings`, whose own key path is `prefix`), creating
    what is missing, or take an emptied list out where the kit created it. Returns which of
    CREATED_KEYS the kit has created and still stand."""
    parent_key, leaf = path
    parent_label = prefix + parent_key
    label = f"{parent_label}.{leaf}"
    parent = settings.get(parent_key)
    now = set(created)
    if value:
        if not isinstance(parent, dict):
            parent = settings[parent_key] = {}
            now.add(parent_label)
        if leaf not in parent:
            now.add(label)
        parent[leaf] = value
        return now & set(CREATED_KEYS)
    if isinstance(parent, dict) and leaf in parent:
        if label in created:
            del parent[leaf]
        else:
            parent[leaf] = value
    now.discard(label)
    if isinstance(parent, dict) and not parent and parent_label in created:
        del settings[parent_key]
    if parent_key not in settings:
        now.discard(parent_label)
    return now & set(CREATED_KEYS)


def _merge_hooks(current: list, owned: list[dict], wanted: list[dict]) -> tuple[list, list, list[str], list[str]]:
    """(new list, entries now owned, added, removed) for `hooks.PreToolUse`, matched by exact content."""
    stale = [e for e in current if _has(owned, e) and not _has(wanted, e)]
    kept = [e for e in current if not (_has(owned, e) and not _has(wanted, e))]
    added = [w for w in wanted if not _has(kept, w)]
    now_owned = [w for w in wanted if _has(owned, w) or not _has(current, w)]
    return kept + added, now_owned, [GUARD_LABEL] * len(added), [GUARD_LABEL] * len(stale)


def merge_settings(existing: Any, manifest: Any, plan: Plan) -> Merge:
    """The settings.json and manifest `--apply` would write, from what is there now. Pure."""
    warnings: list[str] = []
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise AgentSettingsError(f"{SETTINGS_REL}: the top level must be a JSON object — fix it by hand first",
                                 "top level is not a JSON object")
    for path in (("permissions",), ("sandbox",), ("sandbox", "filesystem")):
        parent = existing if len(path) == 1 else existing.get(path[0])
        if isinstance(parent, dict) and path[-1] in parent and not isinstance(parent[path[-1]], dict):
            raise AgentSettingsError(f"{SETTINGS_REL}: `{'.'.join(path)}` must be an object — fix it by hand first",
                                     f"`{'.'.join(path)}` has the wrong type")
    current_deny = _list_at(existing, ("permissions", "deny"), SETTINGS_REL)
    current_read = _list_at(existing, ("sandbox", "filesystem", "denyRead"), SETTINGS_REL)
    current_allow = _list_at(existing, ("sandbox", "filesystem", "allowRead"), SETTINGS_REL)
    current_write = _list_at(existing, ("sandbox", "filesystem", "denyWrite"), SETTINGS_REL)
    owned_deny = _owned(manifest, "permissions.deny")
    owned_read = _owned(manifest, "sandbox.filesystem.denyRead")
    owned_allow = _owned(manifest, ALLOW_KEY)
    owned_write = _owned(manifest, DENY_WRITE_KEY)
    owned_hooks = _owned_hooks(manifest)
    created = _created(manifest)
    if manifest is not None and None in (owned_deny, owned_read, owned_allow, owned_write, owned_hooks):
        warnings.append(f"{MANIFEST_REL} is not a manifest this kit wrote; treating every existing entry as yours "
                        "(entries the kit added before stay until you remove them)")
        owned_deny = owned_read = owned_allow = owned_write = None
        owned_hooks = None
        created = set()
    wanted_hooks = [plan.hook] if plan.hook is not None else []
    touch_hooks = bool(wanted_hooks or owned_hooks or created & {"hooks", HOOKS_KEY})
    current_pre = _hooks_list(existing, SETTINGS_REL) if touch_hooks else []
    deny, own_deny, add_d, rm_d = _merge_list(current_deny, owned_deny or set(), plan.deny)
    read, own_read, add_r, rm_r = _merge_list(current_read, owned_read or set(), plan.deny_read)
    allow, own_allow, add_a, rm_a = _merge_list(current_allow, owned_allow or set(), plan.allow_read)
    deny_w, own_write, add_w, rm_w = _merge_list(current_write, owned_write or set(), plan.deny_write)
    pre, own_hooks, add_h, rm_h = _merge_hooks(current_pre, owned_hooks or [], wanted_hooks)
    settings = copy.deepcopy(existing)
    settings.setdefault("permissions", {})["deny"] = deny
    sandbox = settings.setdefault("sandbox", {})
    prior, added_keys = _sandbox_record(sandbox, manifest)
    overrides, flags_set = [], []
    for key, value in SANDBOX_FLAGS.items():
        # `is`, not `==`: 1 == True in Python, and a switch must be a JSON boolean to count.
        if key not in sandbox or sandbox[key] is not value:
            flags_set.append(f"sandbox.{key}")
        if key in sandbox and sandbox[key] is not value:
            overrides.append(f"sandbox.{key}: {json.dumps(sandbox[key])} -> {json.dumps(value)}")
        sandbox[key] = value
    sandbox.setdefault("filesystem", {})["denyRead"] = read
    if deny_w or "denyWrite" in sandbox["filesystem"]:
        sandbox["filesystem"]["denyWrite"] = deny_w
    now_created = {k for k in _place(sandbox, ("filesystem", "allowRead"), allow, created, "sandbox.") if k == ALLOW_KEY}
    if touch_hooks:
        now_created |= {k for k in _place(settings, ("hooks", "PreToolUse"), pre, created) if k != ALLOW_KEY}
    new_manifest = {"version": 1, "generated_by": GENERATOR, "mode": plan.mode, "allow_git": plan.allow_git,
                    "permissions.deny": own_deny, "sandbox.filesystem.denyRead": own_read,
                    DENY_WRITE_KEY: own_write, "sandbox.prior": prior, "sandbox.added": added_keys}
    if own_allow:
        new_manifest[ALLOW_KEY] = own_allow
    if own_hooks:
        new_manifest[HOOKS_KEY] = own_hooks
    if now_created:
        new_manifest["created"] = sorted(now_created)
    changed = bool(flags_set) or not same_json(settings, existing) or not same_json(new_manifest, manifest)
    return Merge(settings, new_manifest, add_d + add_r + add_a + add_w + add_h, rm_d + rm_r + rm_a + rm_w + rm_h, flags_set,
                 overrides, warnings, changed)


def _sandbox_record(sandbox: dict, manifest: Any) -> tuple[dict[str, Any], list[str]]:
    """What --remove restores for the sandbox switches: (values the kit replaced, keys it added).

    Taken from the settings before this run. A switch that already holds the kit's value keeps what
    the manifest recorded (the kit set it, or the user had it so), never the kit's own value; one
    that holds anything else is recorded as it is now, since this run replaces it.
    """
    old_prior = manifest.get("sandbox.prior") if isinstance(manifest, dict) else None
    old_added = manifest.get("sandbox.added") if isinstance(manifest, dict) else None
    old_prior = old_prior if isinstance(old_prior, dict) else {}
    old_added = old_added if isinstance(old_added, list) else []
    prior: dict[str, Any] = {}
    added: list[str] = []
    for key, value in SANDBOX_FLAGS.items():
        if key not in sandbox:
            added.append(key)
        elif sandbox[key] is not value:
            prior[key] = sandbox[key]
        elif key in old_prior:
            prior[key] = old_prior[key]
        elif key in old_added:
            added.append(key)
    return prior, added


def same_json(a: Any, b: Any) -> bool:
    """Equal as JSON, key order aside: unlike `==`, `1` and `true` differ."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def settings_text(settings: dict[str, Any]) -> str:
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- files

BOM = "\ufeff"


def _read_json(path: Path, rel: str) -> tuple[Any, str | None]:
    """(parsed, raw text); (None, None) when the file is not there. Raises AgentSettingsError otherwise.

    A leading UTF-8 byte-order mark, which some Windows editors write, is accepted; the raw text
    keeps it, so a write can keep it too.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        raise AgentSettingsError(f"{rel}: cannot be read ({exc.__class__.__name__})", "cannot be read") from exc
    try:
        return json.loads(raw.removeprefix(BOM)), raw
    except json.JSONDecodeError as exc:
        raise AgentSettingsError(f"{rel}: not valid JSON (line {exc.lineno}) — fix or move it first",
                                 "not valid JSON") from exc


def has_bom(raw: str | None) -> bool:
    return bool(raw) and raw.startswith(BOM)


def read_manifest(root: Path) -> Any:
    """The manifest, or None when absent. One that cannot be read or parsed comes back as a string,
    which `merge_settings` reports and treats as owning nothing (over-deny, never under-deny)."""
    try:
        parsed, _ = _read_json(Path(root) / MANIFEST_REL, MANIFEST_REL)
    except AgentSettingsError:
        return "<unreadable>"
    return parsed


def write_atomic(path: Path, text: str) -> None:
    """Write beside the target and rename into place: a reader sees the old file or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@dataclass
class State:
    """Everything the CLI, `--check` and doctor need, computed once."""
    plan: Plan
    merge: Merge
    raw_settings: str | None
    local_flags: list[str]       # about settings.json as it is on disk (doctor, --check)
    after_flags: list[str]       # about what --apply would leave: what its own write does not fix


def recorded_allow_git(manifest: Any) -> bool:
    """The `--allow-git` choice the last `--apply` recorded."""
    return isinstance(manifest, dict) and manifest.get("allow_git") is True


def claude_code_state(root: Path, allow_git: bool | None = None) -> State:
    """Read the vault and its settings; raises PolicyConfigError or AgentSettingsError.

    `allow_git=None` takes the choice the manifest recorded (doctor, `--check`); `False` is the
    default of a run that writes: deny `.git`.
    """
    root = Path(root).resolve()
    export = policy_export(root)
    manifest = read_manifest(root)
    recorded = recorded_allow_git(manifest)
    plan = claude_code_plan(root, export, allow_git=recorded if allow_git is None else allow_git)
    if allow_git is False and recorded and (root / ".git").is_dir():
        plan.notes.append("the last --apply used --allow-git; this run denies `.git` again (pass --allow-git to keep it)")
    existing, raw = _read_json(root / SETTINGS_REL, SETTINGS_REL)
    merge = merge_settings(existing, manifest, plan)
    if isinstance(manifest, dict) and DENY_WRITE_KEY not in manifest and set(plan.deny_write) & set(merge.added):
        plan.notes.append(f"this kit version write-protects {kitlib.CONFIG_REL} and {MANIFEST_DIR}/ against the agent's "
                          "shell and file tools (sandbox denyWrite and Edit deny rules), so the agent cannot widen the "
                          "policy the bridge reads; the new entries are added once")
    local = local_override_flags(root, plan, merge.settings, existing)
    after = local_override_flags(root, plan, merge.settings, merge.settings)
    return State(plan, merge, raw, local, after)


def diff_text(raw: str | None, settings: dict[str, Any] | None) -> str:
    old = (raw or "").splitlines(keepends=True)
    new = ((BOM if has_bom(raw) else "") + settings_text(settings)).splitlines(keepends=True) if settings is not None else []
    return "".join(difflib.unified_diff(old, new, f"a/{SETTINGS_REL}", f"b/{SETTINGS_REL}"))


def diff(state: State) -> str:
    return diff_text(state.raw_settings, state.merge.settings)


def apply(root: Path, state: State) -> bool:
    """Write settings.json, then the manifest. False when there was nothing to do.

    In that order: a run stopped between the two leaves entries the kit added recorded as the
    user's, so they stay (over-denied, never under-denied) until removed by hand.
    """
    if not state.merge.changed:
        return False
    root = Path(root)
    if settings_differ(root, state.merge.settings):
        refuse_symlinked_settings(root)
        write_atomic(root / SETTINGS_REL, (BOM if has_bom(state.raw_settings) else "") + settings_text(state.merge.settings))
    write_atomic(root / MANIFEST_REL, settings_text(state.merge.manifest))
    return True


def settings_differ(root: Path, settings: dict[str, Any] | None) -> bool:
    """Would writing `settings` change settings.json as it is on disk? (None: nothing to write.)"""
    if settings is None:
        return False
    existing, _ = _read_json(Path(root) / SETTINGS_REL, SETTINGS_REL)
    return not same_json(existing, settings)


def refuse_symlinked_settings(root: Path) -> None:
    """--apply and --remove --apply rename a new file into place, which would turn a symlinked
    settings.json (one kept with dotfiles, say) into a regular file and leave its target as it was.
    Callers ask only when settings.json would change: the manifest beside it is the kit's own file,
    and writing it alone keeps a link that already matches from staying in drift forever."""
    if (Path(root) / SETTINGS_REL).is_symlink():
        raise AgentSettingsError(f"{SETTINGS_REL} is a symlink; writing would replace the link with a regular file. "
                                 "Merge the dry run's diff into its target by hand, or replace the link with a copy first")


@dataclass
class Removal:
    settings: dict[str, Any] | None      # None: there is no settings.json to change
    removed: list[str]
    restored: list[str]
    changed: bool


def _drop_owned(settings: dict, path: tuple[str, ...], owned: set[str], removed: list[str]) -> None:
    current = _list_at(settings, path, SETTINGS_REL)
    if not current:
        return
    keep = [e for e in current if e not in owned]
    removed += [e for e in current if e in owned]
    parent = settings
    for key in path[:-1]:
        parent = parent[key]
    if keep:
        parent[path[-1]] = keep
    else:
        del parent[path[-1]]               # emptied by the removal: gone, as before the kit added to it


def _prune_emptied(settings: dict, before: dict, path: tuple[str, ...]) -> None:
    """Remove an object at `path` that the removal emptied (one that was empty before stays)."""
    parent, old = settings, before
    for key in path[:-1]:
        parent, old = parent.get(key), (old.get(key) if isinstance(old, dict) else None)
        if not isinstance(parent, dict):
            return
    key = path[-1]
    if parent.get(key) == {} and isinstance(old, dict) and old.get(key) not in ({}, None):
        del parent[key]


def removal(existing: Any, manifest: Any) -> Removal:
    """What `--remove` leaves: settings.json without the entries the manifest owns, and the sandbox
    switches the kit set put back as recorded. Pure; raises AgentSettingsError when the manifest
    cannot say what the kit owns."""
    owned_deny = _owned(manifest, "permissions.deny")
    owned_read = _owned(manifest, "sandbox.filesystem.denyRead")
    owned_allow = _owned(manifest, ALLOW_KEY)
    owned_write = _owned(manifest, DENY_WRITE_KEY)
    owned_hooks = _owned_hooks(manifest)
    if not isinstance(manifest, dict) or None in (owned_deny, owned_read, owned_allow, owned_write, owned_hooks):
        raise AgentSettingsError(f"{MANIFEST_REL} is not a manifest this kit wrote, so which entries are the kit's "
                                 "cannot be told; remove them by hand, then delete the manifest")
    if existing is None:
        return Removal(None, [], [], True)
    if not isinstance(existing, dict):
        raise AgentSettingsError(f"{SETTINGS_REL}: the top level must be a JSON object — fix it by hand first",
                                 "top level is not a JSON object")
    settings = copy.deepcopy(existing)
    removed: list[str] = []
    _drop_owned(settings, ("permissions", "deny"), owned_deny, removed)
    _drop_owned(settings, ("sandbox", "filesystem", "denyRead"), owned_read, removed)
    _drop_owned(settings, ("sandbox", "filesystem", "denyWrite"), owned_write, removed)
    created = _created(manifest)
    allow = _list_at(settings, ("sandbox", "filesystem", "allowRead"), SETTINGS_REL)
    if allow:
        removed += [e for e in allow if e in owned_allow]
        _place(settings["sandbox"], ("filesystem", "allowRead"), [e for e in allow if e not in owned_allow],
               created, "sandbox.")
    if owned_hooks or created & {"hooks", HOOKS_KEY}:
        pre = _hooks_list(settings, SETTINGS_REL)
        removed += [GUARD_LABEL for e in pre if _has(owned_hooks, e)]
        if "hooks" in settings:
            _place(settings, ("hooks", "PreToolUse"), [e for e in pre if not _has(owned_hooks, e)], created)
    prior = manifest.get("sandbox.prior") if isinstance(manifest.get("sandbox.prior"), dict) else {}
    added = manifest.get("sandbox.added") if isinstance(manifest.get("sandbox.added"), list) else []
    restored: list[str] = []
    sandbox = settings.get("sandbox")
    if isinstance(sandbox, dict):
        for key, value in SANDBOX_FLAGS.items():
            if key not in sandbox or sandbox[key] is not value:
                continue                   # changed since the kit set it: whoever did, it is theirs
            if key in prior:
                sandbox[key] = prior[key]
                restored.append(f"sandbox.{key}: {json.dumps(value)} -> {json.dumps(prior[key])}")
            elif key in added:
                del sandbox[key]
                restored.append(f"sandbox.{key}: removed")
    for path in (("sandbox", "filesystem"), ("sandbox",), ("permissions",)):
        _prune_emptied(settings, existing, path)
    return Removal(settings, removed, restored, True)


def removal_state(root: Path) -> tuple[Removal | None, str | None]:
    """(what --remove would leave, settings.json as it is); (None, None) when there is no manifest."""
    root = Path(root).resolve()
    manifest, manifest_raw = _read_json(root / MANIFEST_REL, MANIFEST_REL)
    if manifest_raw is None:
        return None, None
    existing, raw = _read_json(root / SETTINGS_REL, SETTINGS_REL)
    return removal(existing, manifest), raw


def remove_apply(root: Path, result: Removal) -> None:
    """Write settings.json, then delete the manifest: a run stopped between the two leaves the
    manifest, and the next --remove finds nothing more to take out and deletes it."""
    root = Path(root)
    if settings_differ(root, result.settings):
        refuse_symlinked_settings(root)
        _, raw = _read_json(root / SETTINGS_REL, SETTINGS_REL)
        write_atomic(root / SETTINGS_REL, (BOM if has_bom(raw) else "") + settings_text(result.settings))
    with contextlib.suppress(FileNotFoundError):
        (root / MANIFEST_REL).unlink()


# ---------------------------------------------------------------- overrides elsewhere

# Any value other than the safe JSON boolean weakens the setting: `0`, `"false"` and `null` included.
_LOCAL_WEAKENING = {
    "enabled": (True, "settings.local.json turns the sandbox off"),
    "allowUnsandboxedCommands": (False, "settings.local.json allows unsandboxed commands"),
    "failIfUnavailable": (True, "settings.local.json lets commands run unsandboxed when the sandbox cannot start"),
}


def _resolve_sandbox_path(root: Path, entry: str) -> Path | None:
    """A sandbox path as Claude Code resolves it in project settings (`./x` and `x` from the project root).

    An absolute or home entry is resolved through symlinks too, as the vault root is, so `/tmp/v`
    and `/private/tmp/v` compare equal on macOS.
    """
    if entry.startswith("//"):
        return Path(os.path.realpath(entry[1:]))
    if entry.startswith("/"):
        return Path(os.path.realpath(entry))
    if entry.startswith("~/") or entry == "~":
        return Path(os.path.realpath(Path(entry).expanduser()))
    if os.path.isabs(entry):            # a drive path on Windows (`D:\v\x`): resolved like `/x`
        return Path(os.path.realpath(entry))
    return Path(root) / entry


_WILDCARDS = "*?["


def _literal_prefix(entry: str) -> str:
    cut = min((i for i, c in enumerate(entry) if c in _WILDCARDS), default=len(entry))
    return entry[:cut].rstrip("/") or "/"


def _form(root: Path, entry: str) -> str | None:
    """The literal part of an entry as an absolute, normalised, case-folded string."""
    p = _resolve_sandbox_path(root, _literal_prefix(entry))
    return None if p is None else os.path.normpath(str(p)).casefold()


def _at_or_under(form: str, base: str) -> bool:
    return form == base or form.startswith(base.rstrip(os.sep) + os.sep)


def _glob_reaches(glob: str, glob_form: str, form: str) -> bool:
    """Can the glob `glob` (literal start `glob_form`) match `form` or a path under it?

    When the literal start ends mid-name (`./05-peo*`), it is compared as a string, so it reaches
    `05-people`; when it ends at a `/` (`./05-people-public/**`), only paths at or under that folder
    count, so a sibling that merely starts the same (`05-people`) is not reached.
    """
    cut = min((i for i, c in enumerate(glob) if c in _WILDCARDS), default=len(glob))
    mid_name = cut > 0 and glob[cut - 1] not in "/\\"
    return _at_or_under(form, glob_form) or (mid_name and form.startswith(glob_form))


def _reopens(root: Path, allow: str, denied: list[str]) -> bool:
    """Could this `allowRead` entry re-open part of a denied path? (A narrower allow wins.)

    Conservative, since doctor's "ok" must mean ok: an entry at or under a denied path is flagged,
    and so is a glob whose literal start (the text before its first wildcard) is a prefix of a
    denied path or lies under one. A literal start that ends mid-name is compared as a string, so
    `./0*` reaches `./05-people`; one that ends at a `/` is compared by whole names, so
    `./05-people-public/**` does not. Case is ignored.
    """
    a_form = _form(root, allow)
    if a_form is None:
        return True
    a_glob = any(c in _WILDCARDS for c in allow)
    for d in denied:
        d_form = _form(root, d)
        if d_form is None:
            continue
        d_glob = any(c in _WILDCARDS for c in d)
        if _at_or_under(a_form, d_form):
            return True
        if a_glob and _glob_reaches(allow, a_form, d_form):
            return True
        if d_glob and _glob_reaches(d, d_form, a_form):
            return True
    return False


def local_override_flags(root: Path, plan: Plan, settings: dict[str, Any], current: Any = None) -> list[str]:
    """What weakens the generated settings from elsewhere: the local settings file, or an `allowRead`
    entry that re-opens a denied path; in allow-open mode also a guard that is missing or altered in
    `current` (settings.json as it is on disk), and `disableAllHooks`. Short, and naming no vault
    path, so doctor can print them."""
    root = Path(root)
    flags: list[str] = []
    allow: list[str] = []
    try:
        local, _ = _read_json(root / LOCAL_SETTINGS_REL, LOCAL_SETTINGS_REL)
    except AgentSettingsError:
        flags.append("settings.local.json is not valid JSON")
        local = None
    for source in (settings, local):
        if not isinstance(source, dict):
            continue
        sandbox = source.get("sandbox")
        if not isinstance(sandbox, dict):
            continue
        if source is local:
            for key, (safe, flag) in _LOCAL_WEAKENING.items():
                if key in sandbox and sandbox[key] is not safe:
                    flags.append(flag)
        fs = sandbox.get("filesystem")
        if isinstance(fs, dict) and isinstance(fs.get("allowRead"), list):
            allow += [x for x in fs["allowRead"] if isinstance(x, str)]
    # The kit's own allowRead entries re-open the open folders under `./` by design.
    own_allow = set(plan.allow_read)
    reopening = sum(1 for a in allow if a not in own_allow and _reopens(root, a, plan.deny_read))
    if reopening:
        flags.append(f"{reopening} allowRead entr{'y' if reopening == 1 else 'ies'} re-open denied paths")
    # A `Read(!…)` deny is a gitignore negation: it carves out of the rules before it in the same
    # file (permissions docs), so one after the kit's rules in settings.json can re-open them.
    deny = settings.get("permissions", {}).get("deny", [])
    wanted = set(plan.deny)
    first = next((i for i, e in enumerate(deny) if e in wanted), None)
    negations = 0 if first is None else sum(1 for e in deny[first:] if e.replace(" ", "").startswith("Read(!"))
    if negations:
        flags.append(f"{negations} Read(!…) negation(s) after the kit's rules in settings.json")
    if plan.hook is not None:
        flags += _guard_flags(plan, current, local)
    return flags


# What a flag about settings.json as it is becomes in a dry run, when --apply's own write fixes it.
WILL_FIX = {"open-folder guard missing": "the open-folder guard will be added",
            "open-folder guard altered": "the open-folder guard will be replaced"}


def will_fix(flag: str) -> str:
    return WILL_FIX.get(flag, f"{flag}: --apply fixes it")


def _guard_flags(plan: Plan, current: Any, local: Any) -> list[str]:
    """Allow-open: the guard as settings.json holds it, and the switch that turns every hook off."""
    flags: list[str] = []
    hooks = current.get("hooks") if isinstance(current, dict) else None
    pre = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    pre = pre if isinstance(pre, list) else []
    guards = [e for e in pre if is_guard(e)]
    if not guards:
        flags.append("open-folder guard missing")
    elif any(not same_json(e, plan.hook) for e in guards):
        # Also when a correct guard stands beside it: the altered one is no longer the kit's to remove.
        flags.append("open-folder guard altered")
    if any(isinstance(h, dict) and GUARD_MARKER in str(h.get("command")) and h.get("onFailure") != "block"
           for e in guards for h in e["hooks"]):
        flags.append("open-folder guard without onFailure: block")
    for source, name in ((current, "settings.json"), (local, "settings.local.json")):
        if isinstance(source, dict) and "disableAllHooks" in source and source["disableAllHooks"] is not False:
            flags.append(f"{name} sets disableAllHooks, which switches the guard off")
    return flags


# ---------------------------------------------------------------- doctor

def doctor_row(root: Path) -> tuple[str, str]:
    """(where, status) for doctor's "agent settings (claude-code)" row. Never raises, never names a vault path."""
    root = Path(root)
    try:
        if not (root / SETTINGS_REL).exists() and not (root / MANIFEST_REL).exists():
            return "-", "-"
        try:
            state = claude_code_state(root)
        except kitlib.PolicyConfigError:
            return SETTINGS_REL, f"cannot check: {kitlib.CONFIG_REL} is broken (`kit.py validate` says why)"
        except AgentSettingsError as exc:
            return SETTINGS_REL, f"cannot check: {exc.doctor} — run `{GENERATOR}` with the same --vault"
        m = state.merge
        flags = state.plan.flags + state.local_flags
        if not m.changed and not flags:
            return SETTINGS_REL, "ok"
        parts = []
        if m.changed:
            parts.append(f"drift: {len(m.added)} to add, {len(m.removed)} stale")
            if m.flags_set:
                parts.append(f"{len(m.flags_set)} sandbox flag(s) not set")
            if not (m.added or m.removed or m.flags_set):
                parts.append("manifest out of date")
        parts += flags
        hint = f" — {APPLY_HINT}" if m.changed else " — see docs/security.md §5"
        return SETTINGS_REL, ", ".join(parts) + hint
    except Exception as exc:  # noqa: BLE001 — doctor diagnoses; it must not stop at its own row
        return SETTINGS_REL, f"cannot check ({exc.__class__.__name__})"
