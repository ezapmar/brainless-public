---
name: weekly
description: Themes, blind spots, and promotion candidates from recent dailies
argument-hint: [lookback-days, default 7]
---

## Inputs
Optional integer — lookback window in days. Default: 7.

## Reads
- Last N files in `.wiki/digests/` (by filename date).
- Any `daily-briefing-YYYY-MM-DD.md` at vault root within the window.
- Git log within the window (`git log --since="<N> days ago" --name-only`) to see what notes actually changed.
- `_Agent-Context/CONTEXT.md` for comparison.

## Behavior
1. Extract topics mentioned across the daily notes and briefings. Count frequency.
2. Cluster into **3 themes** (not more). Each theme gets a short label + the notes it spans.
3. Identify **one blind spot** — a life area (Health, Relationships, Finance, Learning, Career) or a known active project that was absent or underweight in the window. Compare against `Areas/` and `_Agent-Context/PROJECTS-ACTIVE.md` to detect gaps.
4. List **promotion candidates**: bullet items from daily notes that look like they should graduate into dedicated notes (an Idea, Decision, Person, Project log, etc.). For each, suggest destination folder and template.
5. Produce a **proposed diff for CONTEXT.md** reflecting anything that changed during the window (new priorities, shifted focus, closed loops).

## Output format

```
# Weekly review — last <N> days (<start date> → <end date>)

## Themes
### 1. <Theme label>
Notes: [[Daily 2026-04-13]], [[Daily 2026-04-15]], [[Briefing 2026-04-16]]
Summary: <2 sentences>

### 2. …

### 3. …

## Blind spot
**<Area or Project>** — not mentioned once in the window. Last touched <date>.

## Promotion candidates
| From (daily bullet) | → | Destination |
|---|---|---|
| "Met with Ozan re: SHA structure" (2026-04-16 briefing) | → | `Thinking/Decisions/` or `Thinking/Areas/Legal_SHA_GK/` as a meeting log |
| "Idea: Onay süreçleri kolaylaştırma" (2026-04-16) | → | `.wiki/ideas/` using `_Templates/Idea.md` |
| … | … | … |

## Proposed CONTEXT.md diff
```diff
  ## Current Priorities
  1. Refine the **company People graph**…
  2. Complete the initial Capture phase…
- 3. Optimize the **Second Brain Architecture**…
+ 3. Drive SHA / Genel Kurul preparation to close.
+ 4. Keep Wastespresso deal alive through close.
```

## Git activity summary
<optional: list of files changed most in window, flag any that were created and then not touched again — half-finished notes>
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.
Do not apply the CONTEXT.md diff automatically. Show the diff; The owner applies.
