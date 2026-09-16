---
type: decision
title: The sample set is defined by `example: true`, not by the title suffix
description: Frontmatter marks which notes ship as demonstration and `kit.py examples remove` deletes and repairs them; revisit if a sample ever needs to survive removal, or if Obsidian gains a native way to mark fixtures.
tags: [vault, samples, onboarding]
sources:
  - { name: "issue #10: deleting the `(example)` notes by hand left 15 broken links, measured", credibility: verified-firsthand }
  - { name: "`examples remove` on a copy of the shipped vault, then `validate --strict` and `reconcile`, both clean", credibility: verified-firsthand }
verified: true
verified_on: 2026-09-16
stale_after: 2027-03-16
status: active
---

# The sample set is defined by `example: true`, not by the title suffix

## Decision

A note that ships to demonstrate the vault carries **`example: true`** in its frontmatter. That
is the boundary, and it is the only thing any tool reads. The `(example)` suffix in a title stays,
as the *human* rendering of the same fact.

Removing the set is `kit.py examples remove`, which deletes those notes **and repairs what
referred to them** — body lines carrying a link, frontmatter list entries naming them, and the
regenerated `index.md`.

## Why

The set used to be signalled four inconsistent ways: `(example)` in a title, `-example` in a
filename only, `(example)` appended to a *line* inside a note that is not itself a sample, and no
marker at all. Each way was readable by a person and none by a program.

The documented instruction — "delete the (example) notes" — therefore could not be followed
correctly. Doing it by hand left **15 broken links** and a red `validate`, and kept two fictional
colleagues who still appeared in the people view, because their marker was in the filename rather
than the title.

Nothing caught it: `make lint` validates the *intact* template, so CI was structurally blind to
the state the documentation told users to create.

## Consequences

- Adding a sample means setting one frontmatter key. Forgetting it means the note survives
  removal, which a test now catches.
- The cross-links between samples stay. They are what the sample demonstrates, and
  `docs/examples.md`, `docs/knowledge-graph.md` and four test modules depend on them — so the fix
  had to be a repairing removal, not fewer links.
- A **scalar** frontmatter reference to a sample cannot be pruned automatically, because removing
  the key might remove a required one. No shipped sample is referenced that way; if one ever is,
  `examples remove` must grow a decision for it rather than guess.
- `99-system/conventions.md` teaches a filename in a code span that happens to name a sample.
  Removal is keyed on markdown *links*, so that line survives — deliberately.

## Re-check triggers

- A sample is added that must survive `examples remove` — the marker then means two things and
  needs splitting.
- Obsidian gains a native notion of fixture or template-only notes, which would be a better home
  for the boundary than a custom key.
- `examples remove` reports a scalar frontmatter reference it cannot repair.
