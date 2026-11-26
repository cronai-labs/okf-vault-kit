"""kitagent — what is confidential, as data, and a coding agent's own read restrictions built from it (#120).

Two layers, both pure functions of the vault so the CLI, `--check` and `doctor` cannot disagree:

  policy_export   every path the shared predicate (`kitlib.confidentiality`) hides, in the vault's own
                  spelling: the declared folders, the declared path rules, and each other file judged
                  confidential (marked in its frontmatter, matched by a path rule, or unreadable). Nothing
                  here decides confidentiality; it only walks the tree and asks the predicate.
  claude_code_*   Claude Code's `permissions.deny` `Read(...)` rules and `sandbox.filesystem.denyRead`
                  entries for that export, merged into the vault's `.claude/settings.json`. Which entries
                  the kit added is recorded in a manifest beside the policy, not in settings.json, so an
                  entry the user wrote is never removed.

Standard library only, apart from what kitlib needs.
"""
from __future__ import annotations

import contextlib
import copy
import difflib
import json
import os
import posixpath
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
# What --apply sets in `sandbox`, whatever was there: without these the deny lists are best effort.
SANDBOX_FLAGS = {"enabled": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True}
# A permission rule is a gitignore pattern with no documented escape: a file name holding one of
# these, or edge whitespace, or a line break, may not be matched literally. Such a file is denied
# through the nearest folder above it whose path holds none of them.
UNSAFE_CHARS = frozenset("*?[]\\!#")
PATTERN_UNSAFE = frozenset("[]\\!#")      # a path rule keeps its * and ?, which mean the same there
APPLY_HINT = f"run `{GENERATOR} --apply` with the same --vault"


class AgentSettingsError(ValueError):
    """The settings cannot be generated or merged; the message says why and what to do."""


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

    `confidential_files` holds every file under the vault, `.git` aside, that `kitlib.confidentiality`
    does not clear and that no listed folder already covers: a note's own `sensitivity`, a path rule,
    or a file it could not read (fail closed; also in `unreadable`). Not `iter_markdown`: an agent's
    tools read attachments, `.trash` and `_`-prefixed folders like any other file. A folder the walk
    cannot list is listed with a trailing `/`, in both lists: what is in it cannot be told.
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

    for dirpath, dirnames, filenames in os.walk(root, onerror=unlisted):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in sorted(dirnames):
            rel = posixpath.join(rel_dir, d) if rel_dir else d
            if (not rel_dir and d == ".git") or policy.in_folder(rel):
                continue           # .git: its own entry; a declared folder: covered whole
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = posixpath.join(rel_dir, name) if rel_dir else name
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


@dataclass
class Plan:
    deny: list[str] = field(default_factory=list)          # permissions.deny entries the kit wants
    deny_read: list[str] = field(default_factory=list)     # sandbox.filesystem.denyRead entries
    notes: list[str] = field(default_factory=list)         # preview lines: escalations, skips, git
    flags: list[str] = field(default_factory=list)         # short doctor-safe flags, no vault paths
    allow_git: bool = False


def claude_code_plan(root: Path, export: dict[str, Any], allow_git: bool = False) -> Plan:
    """The entries for an export. Raises AgentSettingsError when a file would need the whole vault denied."""
    root = Path(root).resolve()
    plan = Plan(allow_git=allow_git)
    wanted: list[tuple[str, str]] = [("folder", MANIFEST_DIR)]
    if (root / ".git").exists():
        if allow_git:
            plan.notes.append("--allow-git: `.git` is not denied, so confidential notes tracked in git stay readable "
                              "through the history (`git show`, `git grep HEAD`)")
        else:
            wanted.append(("folder", ".git"))
            plan.notes.append("`.git` is denied, so confidential notes tracked in git cannot be read through the "
                              "history; the agent cannot use git in this vault either (--allow-git keeps git)")
    outside = git_outside(root)
    if outside is not None:
        plan.notes.append(f"the vault's git history lives outside it ({outside}); the kit denies nothing outside the "
                          "vault, so confidential notes tracked there stay readable through git")
        plan.flags.append("git history outside the vault not denied")
    root_hits = 0
    escalated: dict[str, int] = {}
    folders = []
    for rel in export["confidential_folders"]:
        if literal_safe(rel):
            folders.append(rel)
            continue
        anc = _safe_ancestor(rel)
        if not anc:
            root_hits += 1
        else:
            escalated[anc] = escalated.get(anc, 0) + 1
    files = []
    for rel in export["confidential_files"]:
        is_dir = rel.endswith("/")
        rel = rel.rstrip("/")
        if rel == kitlib.CONFIG_REL:
            plan.notes.append(f"not denied: {kitlib.CONFIG_REL} (a path rule covers it, but the agent's own kit runs "
                              "need it, and a policy they cannot read hides everything from them)")
            continue
        if literal_safe(rel):
            (folders if is_dir else files).append(rel)
            continue
        anc = _safe_ancestor(rel)
        if not anc:
            root_hits += 1
        else:
            escalated[anc] = escalated.get(anc, 0) + 1
    if root_hits:
        raise AgentSettingsError(
            f"{root_hits} confidential file(s) or folder(s) at the vault root have names a permission rule cannot "
            "match literally (one of * ? [ ] \\ ! #, a leading or trailing space, or a line break), and the only "
            "folder that would cover them is the vault itself. Rename them, or move them into a confidential "
            "folder, and run again")
    for anc, n in sorted(escalated.items()):
        folders.append(anc)
        plan.notes.append(f"denies the whole folder {anc}/: {n} confidential name(s) in it hold a character a "
                          "permission rule cannot match literally")
    patterns = []
    for pat in export["confidential_paths"]:
        if literal_safe(pat, PATTERN_UNSAFE):
            patterns.append(pat)
        else:
            plan.notes.append(f"path rule {pat!r} is not carried over (it holds [ ] \\ ! # or edge spaces, which mean "
                              "something else in a permission rule); the files it matches now are denied one by one, "
                              "a file it matches later only after the next run")
    chosen = sorted(set(folders))
    # A folder inside another chosen folder, and a file inside any, adds nothing.
    chosen = [f for f in chosen if not any(_under(f, g) for g in chosen)]
    wanted += [("folder", f) for f in chosen]
    wanted += [("path", p) for p in patterns]
    wanted += [("file", f) for f in sorted(set(files)) if not any(_under(f, g) for g in chosen)]
    seen: set[str] = set()
    for kind, rel in wanted:
        perm, sbx = entries(kind, rel)
        if perm in seen:
            continue
        seen.add(perm)
        plan.deny.append(perm)
        plan.deny_read.append(sbx)
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
            raise AgentSettingsError(f"{where}: `{'.'.join(path[:i])}` must be an object — fix it by hand first")
        cur = cur.get(key)
        if cur is None:
            return []
    if not isinstance(cur, list) or not all(isinstance(x, str) for x in cur):
        raise AgentSettingsError(f"{where}: `{'.'.join(path)}` must be a list of strings — fix it by hand first")
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


def merge_settings(existing: Any, manifest: Any, plan: Plan) -> Merge:
    """The settings.json and manifest `--apply` would write, from what is there now. Pure."""
    warnings: list[str] = []
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise AgentSettingsError(f"{SETTINGS_REL}: the top level must be a JSON object — fix it by hand first")
    current_deny = _list_at(existing, ("permissions", "deny"), SETTINGS_REL)
    current_read = _list_at(existing, ("sandbox", "filesystem", "denyRead"), SETTINGS_REL)
    owned_deny = _owned(manifest, "permissions.deny")
    owned_read = _owned(manifest, "sandbox.filesystem.denyRead")
    if manifest is not None and (owned_deny is None or owned_read is None):
        warnings.append(f"{MANIFEST_REL} is not a manifest this kit wrote; treating every existing entry as yours "
                        "(entries the kit added before stay until you remove them)")
        owned_deny = owned_read = None
    deny, own_deny, add_d, rm_d = _merge_list(current_deny, owned_deny or set(), plan.deny)
    read, own_read, add_r, rm_r = _merge_list(current_read, owned_read or set(), plan.deny_read)
    settings = copy.deepcopy(existing)
    settings.setdefault("permissions", {})["deny"] = deny
    sandbox = settings.setdefault("sandbox", {})
    overrides, flags_set = [], []
    for key, value in SANDBOX_FLAGS.items():
        if sandbox.get(key, None) != value or key not in sandbox:
            flags_set.append(f"sandbox.{key}")
        if key in sandbox and sandbox[key] != value:
            overrides.append(f"sandbox.{key}: {json.dumps(sandbox[key])} -> {json.dumps(value)}")
        sandbox[key] = value
    sandbox.setdefault("filesystem", {})["denyRead"] = read
    new_manifest = {"version": 1, "generated_by": GENERATOR, "allow_git": plan.allow_git,
                    "permissions.deny": own_deny, "sandbox.filesystem.denyRead": own_read}
    changed = settings != existing or new_manifest != manifest
    return Merge(settings, new_manifest, add_d + add_r, rm_d + rm_r, flags_set, overrides, warnings, changed)


def settings_text(settings: dict[str, Any]) -> str:
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- files

def _read_json(path: Path, rel: str) -> tuple[Any, str | None]:
    """(parsed, raw text); (None, None) when the file is not there. Raises AgentSettingsError otherwise."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        raise AgentSettingsError(f"{rel}: cannot be read ({exc.__class__.__name__})") from exc
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError as exc:
        raise AgentSettingsError(f"{rel}: not valid JSON (line {exc.lineno}) — fix or move it first") from exc


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
    local_flags: list[str]


def claude_code_state(root: Path, allow_git: bool | None = None) -> State:
    """Read the vault and its settings; raises PolicyConfigError or AgentSettingsError.

    `allow_git=None` takes the choice the manifest recorded (doctor), else the default: deny `.git`.
    """
    root = Path(root).resolve()
    export = policy_export(root)
    manifest = read_manifest(root)
    if allow_git is None:
        allow_git = bool(isinstance(manifest, dict) and manifest.get("allow_git") is True)
    plan = claude_code_plan(root, export, allow_git=allow_git)
    existing, raw = _read_json(root / SETTINGS_REL, SETTINGS_REL)
    merge = merge_settings(existing, manifest, plan)
    local = local_override_flags(root, plan, merge.settings)
    return State(plan, merge, raw, local)


def diff(state: State) -> str:
    old = (state.raw_settings or "").splitlines(keepends=True)
    new = settings_text(state.merge.settings).splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, f"a/{SETTINGS_REL}", f"b/{SETTINGS_REL}"))


def apply(root: Path, state: State) -> bool:
    """Write settings.json, then the manifest. False when there was nothing to do.

    In that order: a run stopped between the two leaves entries the kit added recorded as the
    user's, so they stay (over-denied, never under-denied) until removed by hand.
    """
    if not state.merge.changed:
        return False
    root = Path(root)
    existing, _ = _read_json(root / SETTINGS_REL, SETTINGS_REL)
    if existing != state.merge.settings:
        write_atomic(root / SETTINGS_REL, settings_text(state.merge.settings))
    write_atomic(root / MANIFEST_REL, settings_text(state.merge.manifest))
    return True


# ---------------------------------------------------------------- overrides elsewhere

_LOCAL_WEAKENING = {
    "enabled": (False, "settings.local.json turns the sandbox off"),
    "allowUnsandboxedCommands": (True, "settings.local.json allows unsandboxed commands"),
    "failIfUnavailable": (False, "settings.local.json lets commands run unsandboxed when the sandbox cannot start"),
}


def _resolve_sandbox_path(root: Path, entry: str) -> Path | None:
    """A sandbox path as Claude Code resolves it in project settings (`./x` and `x` from the project root)."""
    if entry.startswith("//"):
        return Path(entry[1:])
    if entry.startswith("/"):
        return Path(entry)
    if entry.startswith("~/") or entry == "~":
        return Path(entry).expanduser()
    return Path(root) / entry


def _literal_prefix(entry: str) -> str:
    cut = min((i for i, c in enumerate(entry) if c in "*?["), default=len(entry))
    return entry[:cut].rstrip("/") or "/"


def _reopens(root: Path, allow: str, denied: list[str]) -> bool:
    """Could this `allowRead` entry re-open part of a denied path? (A narrower allow wins.)"""
    a = _resolve_sandbox_path(root, _literal_prefix(allow))
    has_glob = _literal_prefix(allow) != allow.rstrip("/")
    if a is None:
        return True
    a_form = os.path.normpath(str(a)).casefold()
    for d in denied:
        dp = _resolve_sandbox_path(root, _literal_prefix(d))
        if dp is None:
            continue
        d_form = os.path.normpath(str(dp)).casefold()
        inside = a_form == d_form or a_form.startswith(d_form.rstrip(os.sep) + os.sep)
        if inside or (has_glob and d_form.startswith(a_form.rstrip(os.sep) + os.sep)):
            return True
    return False


def local_override_flags(root: Path, plan: Plan, settings: dict[str, Any]) -> list[str]:
    """What weakens the generated settings from elsewhere: the local settings file, or an `allowRead`
    entry that re-opens a denied path. Short, and naming no vault path, so doctor can print them."""
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
            for key, (bad, flag) in _LOCAL_WEAKENING.items():
                if sandbox.get(key) is bad:
                    flags.append(flag)
        fs = sandbox.get("filesystem")
        if isinstance(fs, dict) and isinstance(fs.get("allowRead"), list):
            allow += [x for x in fs["allowRead"] if isinstance(x, str)]
    reopening = sum(1 for a in allow if _reopens(root, a, plan.deny_read))
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
            reason = "not valid JSON" if "JSON" in str(exc) else "cannot be generated for this vault"
            return SETTINGS_REL, f"cannot check: {reason} — run `{GENERATOR}` with the same --vault"
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
