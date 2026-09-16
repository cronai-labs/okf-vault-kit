# Contributing

## The gate

```bash
make check      # build + lint + test, exactly what CI runs
```

`make lint` runs [ruff](https://docs.astral.sh/ruff/) over every Python file in the repo. The rule
set is named rule by rule in `pyproject.toml`, and every rule that is turned off carries its reason
on the same line — argue with the config, not with a `# noqa`. If you do need one, give it a code
and a reason, the way the existing ones do. Why that set, and what it deliberately does not check:
[docs/decisions/python-linting-ruff.md](docs/decisions/python-linting-ruff.md). There is no
formatter, and `make fmt` says so rather than pretending.

`make test` runs on **Python 3.11**, the minimum supported version — not whatever `uv` picks. That
is deliberate: a 3.13-only API once passed locally and failed the matrix.

## Workflow

Issue first, with `file:line` evidence. Branch from the issue so it is linked
(`gh issue develop <n> --checkout`). PR to `main`, opened as a draft while in progress, referencing
`Closes #<n>`. Squash merge. Never work on `main`.

Rebase onto `main`; never merge `main` into a branch. `git fetch origin && git rebase origin/main`,
then `git push --force-with-lease`.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/). Only `feat`, `fix`, `perf` and
`revert` are user-visible; `docs`, `test`, `refactor`, `ci`, `build` and `chore` are not. The commit
body is where the reasoning goes — say what was wrong and why the fix is the right shape, not what
the diff already shows.

## Claims

Do not write that something passes without having run it, and say so plainly when a check could not
be run in the environment you had. The same applies to documentation: if a flag name was read back
from `--help` on a specific version, say which version; if it was not verified, mark it as such.
`docs/platforms/proxy.md` does this and it is the standard to match.

## Releasing

**Pushing the tag is the whole release.** `.github/workflows/release.yml` builds the archive,
publishes the GitHub release and verifies the checksum of what it published. Do not create the
release or attach assets by hand — the workflow's `gh release create` then fails with
`already_exists`, and the release that exists is the one nothing verified.

```bash
make next-version                 # what the commits since the last tag would bump to
# bump VERSION, run `make changelog`, open a PR, merge it
git tag v$(cat VERSION) && git push origin v$(cat VERSION)
```

`VERSION` is the single source of truth — `pyproject.toml` reads it, `kit.py --version` prints it,
and tests fail on drift. The release notes come from the newest second-level section of `CHANGELOG.md`,
so that file is what a reader sees on the Releases page; the workflow refuses to publish if that
section renders to almost nothing.

`make release` builds `dist/okf-vault-kit-<VERSION>.zip` and its `.sha256` locally from a clean,
tagged tree. It is what CI runs, and is useful for checking the artifact before tagging.
