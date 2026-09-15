---
type: system
title: Open Actions
description: Every open checkbox in the vault, grouped by where it lives — a live view, nothing to maintain.
tags: [system]
sensitivity: internal
---

# Open Actions

Every unchecked `- [ ]` in the vault, live. Write tasks where they arise (meeting, project, person note) as `- [ ] verb — owner, due YYYY-MM-DD`; this page collects them. Tick them where they live.

## From projects and decisions

```query
task-todo:"" (path:"03-projects" OR path:"06-decisions")
```

## From meetings

```query
task-todo:"" path:"02-meetings"
```

## From people notes (1:1 follow-ups)

```query
task-todo:"" path:"05-people"
```

## From daily and weekly notes

```query
task-todo:"" path:"01-journal"
```

## Everything else

```query
task-todo:"" -path:"01-journal" -path:"02-meetings" -path:"03-projects" -path:"05-people" -path:"06-decisions" -path:"90-templates" -path:"99-system" -path:"09-archive"
```

Tip: the `Waiting` section of the [Dashboard](dashboard.md) shows notes you flagged `state: waiting` — the things you are chasing rather than doing.
