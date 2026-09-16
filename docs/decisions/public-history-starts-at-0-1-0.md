---
type: decision
title: The public version history starts at 0.1.0, not at the number the private history reached
description: This repository's first tag is v0.1.0 because no earlier version was ever published from it; revisit only if the archived private history is ever replayed here.
tags: [release, versioning, changelog]
sources:
  - { name: "no tag and no release existed when this was decided: `git tag -l` and `gh release list` both empty", credibility: verified-firsthand }
  - { name: "issue #38", credibility: verified-firsthand }
verified: true
verified_on: 2026-09-16
stale_after: 2027-09-16
status: active
---

# The public version history starts at 0.1.0

## Decision

`VERSION` is `0.1.0`, `cliff.toml`'s `initial_tag` is `v0.1.0`, and the first tag pushed from this
repository will be `v0.1.0`.

The private repository that preceded this one reached `0.4.0-alpha.2`. That number came across in
`VERSION` when the code was re-initialised here as a single commit, and it is not carried forward.

## Why

Nothing was ever published from this repository under any version. No tag existed, `gh release
list` was empty, and the three minor versions and two alphas the old number implies happened in a
repository nobody outside CronAI can open. Keeping it asks a reader to account for history they
cannot see, and makes the first Releases page entry look like a continuation of something absent.

`0.1.0` says what is true: this is the first release of this repository.

## Consequences

- **The GitHub release will not carry the *Pre-release* badge.** `release.yml` sets
  `--prerelease` only for a version containing a hyphen, and `0.1.0` has none. `0.x` already
  signals pre-1.0 and `ALPHA.md` states the rest, so the badge is redundant rather than missing —
  but it is a change from what `0.4.0-alpha.2` would have produced. A future prerelease can still
  use the `-alpha.N` form; `tag_pattern` still matches it.
- **The changelog does not list the import.** The commit that seeded this repository is the
  baseline `0.1.0` describes, not a change within it, and its subject names the old version. It is
  skipped by an explicit `commit_parsers` entry matching that exact subject, so `0.1.0`'s notes are
  the changes actually made here. What the baseline *is* belongs in the README and `ALPHA.md`.
- Anyone holding a build labelled `0.4.0-alpha.x` has something that predates the public
  repository. There is no upgrade path to describe, because nothing was distributed under it.

## Re-check triggers

- The archived private history is ever replayed into this repository, which would make its version
  numbers visible and this decision wrong.
- A build labelled `0.4.0-alpha.x` turns out to have been distributed outside CronAI, which would
  make `0.1.0` a version that appears to go backwards for that holder.
