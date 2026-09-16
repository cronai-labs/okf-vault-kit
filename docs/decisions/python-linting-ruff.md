---
type: decision
title: Ruff lints the Python, with a named rule set and no formatter
description: Adopted ruff behind `make lint` with an explicit select/ignore list in pyproject.toml; revisit when a pinned ruff upgrade changes what the named rules report, or when a bug class the set does not cover reaches main.
tags: [ci, python, linting]
sources:
  - { name: "issue #4: measured on this tree with ruff 0.16.7", credibility: verified-firsthand }
  - { name: "`make check` green after the one-pass fix, 271 tests", credibility: verified-firsthand }
verified: true
verified_on: 2026-09-16
stale_after: 2027-03-16
status: active
---

# Ruff lints the Python

## The decision

`make lint` runs `ruff check .` before it byte-compiles and validates the template vault. The
rule set is named rule by rule in `pyproject.toml` under `[tool.ruff.lint]`, every suppressed
rule carries its reason on the same line, and no formatter is configured.

The ruff version is pinned in the `Makefile` (`RUFF ?= ruff@0.16.7`). `tests/test_release.py`
fails if `make lint` stops naming ruff, if the pin goes away, if the rule set stops being
explicit, or if a rule is turned off without a reason beside it.

## What the tree looked like before

Measured 2026-09-16 with ruff 0.16.7, before any fix:

| selection | findings |
|---|---|
| ruff's own defaults | 140 |
| `--select E,F --ignore E501` | 282 |
| the set adopted here | 109 |

All 109 were fixed in one pass. The largest groups were 22 `subprocess.run` calls with no explicit
`check=`, 12 unused imports, 10 unsorted import blocks, 9 quoted annotations, 7 unused loop
variables, 7 `# noqa` directives that no longer suppressed anything, 6 hand-rolled `removesuffix`
slices, a duplicated import block in `tests/test_vault_structure.py`, and one duplicated member in
the bridge's stopword set.

## Why these rules

The kit is stdlib plus PyYAML, and what it actually does is spawn processes, walk paths and
decode text. The set follows that shape rather than a general style opinion:

- **`PLW1514`** — `open` / `read_text` / `write_text` without `encoding=`. The repo's rule is that
  every one of them is explicit, and this is the only mechanical check of it. It found nothing,
  which is the point: it is a guard against the next one, not a cleanup. It is a preview rule, so
  `preview` is on together with `explicit-preview-rules`, which keeps every *other* preview rule
  out.
- **`PLW1510`** — `subprocess.run` without `check=`. A subprocess whose exit code nobody looks at
  is a real bug class here; the rule forces the author to say which it is.
- **`E4` and `BLE`** — the codebase already carried `# noqa: E402` and `# noqa: BLE001` comments
  written against a linter it did not yet run. Selecting those rules is what makes those
  directives mean something again; without them ruff reports each one as dead.
- **`F`, `B`, `RUF`, `SIM`, `UP`, `ISC`, `FURB`, `I`, `W`, `E7`, `E9`** — unused and shadowed
  names, mutable default arguments, bare `except:`, the missing-comma shape inside collections,
  and the modern spellings the 3.11 floor allows.

## What was left out, and why

The reasons live beside the rules in `pyproject.toml`, so that a reader who wonders why a
finding does not appear reads the answer where they are already looking. In summary: `E501` and
the semicolon rules would reflow code that is dense on purpose, `DTZ` would fight the fact that a
daily note is named for the user's *local* date, `S` duplicates the bandit job CI already runs,
and `PERF`'s loop-to-comprehension rewrites cost more readability than they buy.

**No formatter.** `ruff format` would reflow prose, comments and docstrings, and those are
reviewed text in this repo. `make fmt` says so rather than pretending otherwise.

## Re-check trigger

Not the date alone. Revisit when **any** of these happens:

- the pinned ruff version is bumped and the named rules report something new
- a defect class reaches `main` that this set could have caught — that is an argument for a rule,
  with the commit as its evidence
- a suppressed rule's reason stops being true, most likely `E741` (single-letter comprehension
  variables) or `FURB171` (single-item exclusion tuples), both of which describe how the code is
  written today rather than a property of the language
