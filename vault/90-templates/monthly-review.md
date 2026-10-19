---
type: monthly
title: "{{title}}"
description: 
date: "{{date:YYYY-MM-DD}}"
tags: [review, monthly]
sensitivity: internal
generated: { by: human:me, at: "{{date:YYYY-MM-DD[T]HH:mm:ssZ}}" }
---

# Monthly review — {{date:MMMM YYYY}}

20 minutes, last working day of the month. Name the note `YYYY-MM` and keep it in `01-journal/monthly/`. The lists below are live searches over this month's day notes in `01-journal/daily/{{date:YYYY}}/{{date:MM}}/`: they show the tasks where they live, so tick them there. Copy no task into this note — one task lives in one place. Writing the review after the month has ended? Change the month in the three paths.

## Days

```query
path:"01-journal/daily/{{date:YYYY}}/{{date:MM}}"
```

## Open tasks

Carry each one forward, delegate it, or drop it — in the day note that holds it.

```query
task-todo:"" path:"01-journal/daily/{{date:YYYY}}/{{date:MM}}"
```

## Done this month

```query
task-done:"" path:"01-journal/daily/{{date:YYYY}}/{{date:MM}}"
```

## Reflection

- What moved:
- What stalled, and why:
- What I will do differently next month:
