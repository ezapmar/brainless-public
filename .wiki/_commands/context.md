---
name: context
description: Snapshot of the owner's current state; flag drift between CONTEXT.md and reality
argument-hint: (none)
---

## Inputs
None.

## Reads
- `_Agent-Context/CONTEXT.md`
- `_Agent-Context/PROJECTS-ACTIVE.md`
- `_Agent-Context/BELIEFS-SUMMARY.md`
- The 7 most recent files in `.wiki/digests/` (by filename date).
- Today's `daily-briefing-YYYY-MM-DD.md` at vault root, if it exists.

## Behavior
1. Load the files above. If any is missing, note it and continue.
2. Produce a compact status report (format below).
3. Compare CONTEXT.md's "Current Projects" and "Current Priorities" against what the last 7 dailies and today's briefing actually mention. If something in dailies/briefing is absent from CONTEXT.md, or vice versa, flag it as drift.
4. Identify the stalest active project — longest gap since its last Log entry.
5. Do not write any file.

## Output format

```
# Current state — <today's date>

## Who you are
<one line from CONTEXT.md>

## Active projects (from CONTEXT.md)
1. [[Project A]] — <status>
2. …

## This week's actual focus (from last 7 dailies + today's briefing)
- <theme 1>
- <theme 2>

## Drift detected
- <e.g. "CONTEXT.md omits SHA/Genel Kurul work, which dominated the last 3 dailies">
- <or "No drift — CONTEXT.md matches recent activity">

## Stalest thread
[[Project X]] — last log entry <date>, <N> days ago.

## One thing to revisit today
<single concrete suggestion>
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.
