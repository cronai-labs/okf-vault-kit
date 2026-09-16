---
type: decision
title: The MCP bridge stays on the v1 SDK until the 2.x migration is deliberate work
description: Pinned `mcp>=1.2,<2` because 2.x renamed FastMCP to MCPServer; superseded on 2026-09-16 when the bridge was migrated to `mcp>=2,<3`.
tags: [mcp, dependencies, ci]
sources:
  - { name: "issue #13, and the failure PR #14 fixed", credibility: verified-firsthand }
  - { name: "reproduced on a clean machine 2026-09-15: unpinned resolves 2.x and the bridge does not start", credibility: verified-firsthand }
  - { name: "issue #2: the migration, verified with a real stdio session against mcp 2.2.0", credibility: verified-firsthand }
verified: true
verified_on: 2026-09-16
stale_after: 2027-03-16
status: superseded
---

# The MCP bridge stays on the v1 SDK

## The decision

`mcp` is pinned to `>=1.2,<2` everywhere it is declared, and Dependabot is configured not to
walk it past that bound.

## What was traded

The bridge targets the v1 API. In 2.x the server class was renamed — `FastMCP` became
`MCPServer` — so an unpinned install resolves a major the bridge cannot start against. We accept
being one major behind in exchange for a bridge that starts on a fresh machine today.

The cost is real and is not only "an old dependency":

- the pin is restated in several places (`pyproject` extras, the PEP 723 inline headers, the
  Makefile, `requirements.txt`) plus every documented install command, held together by a drift
  test rather than by a single source
- the gated end-to-end test has to detect the installed major and skip, because the *client*
  half of 2.x imports fine and only the spawned server dies — an absent gate turned a skip into
  an error, which is what issue #25 was

## Why not migrate now

The migration is not a rename; it is a reasonable amount of work in the one component that
mediates every model action against the vault, and it lands mid-alpha. Doing it under time
pressure in the security-relevant file is the worse trade.

## Re-check trigger

Not a date alone. Revisit when **any** of these happens:

- issue #13 is picked up (the migration itself)
- the v1 line stops receiving fixes, or a CVE lands in it
- a client the alpha testers actually use requires a 2.x-only protocol feature

## Accepted debt

Tracked by [#13](https://github.com/cronai-labs/okf-vault-kit/issues/13). The drift test that
keeps the copies of the pin honest lives in `tests/test_docs.py`; when #13 lands, that test is
the checklist of everything to update.

## Superseded — the debt is repaid

Migrated on 2026-09-16 under issue #2. `FastMCP` became `MCPServer` from `mcp.server.mcpserver`,
the bind address moved out of the constructor and into the transport, and the pin moved from
`mcp>=1.2,<2` to `mcp>=2,<3` in every place listed above. The drift test now checks the
declarations as well as the prose, which is what the section above claimed it did.

Two things the SDK changed underneath, both verified against mcp 2.2.0 over a real stdio session:

- **A refusal has to arrive as a `ToolError`.** 2.x forwards that exception's message to the
  model and withholds every other one's. The policy layer's refusals — "marked confidential",
  "denied by policy", "only SELECT / WITH queries are allowed" — are written for the model to
  read and act on, so the bridge raises them as `ToolError`. A genuine crash stays withheld,
  which is stricter than v1, where a traceback's absolute paths reached the model.
- **The streamable-HTTP app is told the address it is served on** and refuses a request whose
  `Host` header is not one of them. v1 left that guard off unless it was configured.

The caution that produced the original pin is unchanged, one major up: a 3.x is where the server
class gets renamed again. `mcp>=2,<3` says so, Dependabot ignores `>=3` for the same reason, and
the re-check trigger becomes: revisit when 3.x ships, or when a client the testers use needs a
protocol feature 2.x does not have.
