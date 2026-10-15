---
type: decision
title: The kit ships as a release zip first, then PyPI, MCP and an agent package, in that order
description: The checksum-verified release zip stays the baseline, PyPI/uvx comes next and carries MCP and the agent package, and Homebrew is not planned. Revisit when a channel's platform changes or someone asks for Homebrew.
tags: [distribution, packaging, pypi, mcp, agents, release]
sources:
  - { name: "kit.py:51 (TEMPLATE_VAULT = ROOT / \"vault\") and pyproject.toml (no [project.scripts], flat py-modules), at 3519817", credibility: verified-firsthand }
  - { name: "MCP Registry, official docs (preview notice, metadata only, namespaces)", url: "https://modelcontextprotocol.io/registry/about", credibility: primary }
  - { name: "Vercel Docs, Agent Skills (npx skills add, 18+ agents), last_updated 2026-09-15", url: "https://vercel.com/docs/agent-resources/skills", credibility: primary }
  - { name: "Vercel Docs, Vercel Plugin (npx plugins add; skills, agents, commands, hooks; Node and Bun), last_updated 2026-07-29", url: "https://vercel.com/docs/agent-resources/vercel-plugin", credibility: primary }
  - { name: "issues #74 (tracking), #23 (okf-lint extraction)", credibility: verified-firsthand }
verified: true
verified_on: 2026-10-15
stale_after: 2027-04-15
status: active
---

# Distribution channels and their order

## Decision

The kit reaches people through four channels, added in this order:

1. **The release zip** with its `.sha256`, as today. It stays the baseline.
2. **PyPI**, so `uvx okf-vault-kit …` and `uv tool install okf-vault-kit` work. Planned together
   with the okf-lint extraction (#23), because both decide the package boundary.
3. **MCP**: the bridge starts via `uvx` from the PyPI package, and the server is listed in the
   official MCP Registry as soon as it is on PyPI.
4. **An agent package**: skills for the kit's main jobs, the MCP server config and a few commands,
   installable into coding agents in one step.

Homebrew is not planned.

## Why

**The zip stays first** because it needs nothing but GitHub. People who use a vault kit for work
are often on managed machines where package registries are blocked or proxied. A checksum-verified
archive run with `uv run` is how the kit already runs on such a machine, and no later channel may
take that away.

**PyPI comes next** because the kit is Python, its users already need `uv`, and every later channel
builds on an installable package: the MCP server command and the agent package both want
`uvx okf-vault-kit`, not a checkout path. It is not possible today: `pyproject.toml` declares no
`[project.scripts]`, and `kit init` copies the template from `ROOT / "vault"` (`kit.py:51`), which a
wheel built from the flat `py-modules` list does not contain.

**The MCP Registry listing comes early, although the registry is a preview.** Its docs say breaking
changes or data resets may occur before general availability. What the kit publishes is stable, so
time to market wins: a reset costs a re-publish, not a redesign. The registry stores only metadata
that points at the PyPI package, so listing adds no second artifact to maintain.

**The agent package comes last** because it bundles the other two: skills are only useful if the
tool they describe installs in one command, and the MCP config inside it needs a portable command.
Two installers exist: `npx skills add <owner/repo>` (skills, 18+ agents) and `npx plugins add
<owner/repo>` (skills, agents, commands and hooks for about eleven tools, as Vercel ships its own
plugin). Both need Node, and Vercel's plugin route also needs Bun. That is extra runtime on a
managed machine, which is why this channel adds to the zip and PyPI rather than replacing them.

**Homebrew is not planned** because a Python formula vendors every dependency as a resource and
has to be kept in step with each release, and PyPI already reaches anyone with `uv`.

## Consequences

- The release workflow publishes to PyPI next to the zip, from the same tag.
- CI builds and installs the wheel on every pull request, so the PyPI route cannot break silently
  between releases.
- `kit.py mcp-config` learns to write a `uvx` command when the kit is installed from PyPI.
- The okf-lint extraction (#23) and the packaging work are sequenced together: the linter becomes a
  dependency of the published kit rather than code carried inside it.

## Re-check triggers

- Someone asks for a Homebrew install, or a user without `uv` cannot get the kit any other way.
- The MCP Registry leaves preview, or changes how it verifies PyPI ownership.
- `npx skills` or `npx plugins` changes its package layout, or one installer clearly wins.
- A managed machine turns out to allow PyPI but not GitHub release downloads, which would reorder
  the first two channels.
