---
name: trace
description: Chronological evolution of the owner's thinking on a topic
argument-hint: <topic>
---

## Inputs
A topic string (one or more words). Example: `/trace ticket-system`, `/trace UK housing`.

If no argument is provided, ask: "Which topic should I trace?"

## Reads
- Prefer `python3 tools/wiki_search.py "<topic>" --json --k 20` first for ranked candidates.
- Then read raw sources for chronology: `Thinking/Daily/`, `Work/`, `Thinking/Decisions/`, `Thinking/Beliefs/`, and the company area in `Work/`.
- Excludes: `_Templates/`, `.obsidian/`, `.smart-env/`, `.git/`, `venv/`, `.claude/`, `.gemini/`, `.agents/`, `tools/logs/`, `raw/_attachments/`, `_Backup/`.

## Behavior
1. Find every note whose **content or title** mentions the topic (case-insensitive, handle obvious variants, e.g. "ticket" also matches "ticket-system", "Kolay Ticket").
2. For each match, read its `date` frontmatter. If no frontmatter date, fall back to the filename date (for dailies) or file mtime.
3. Sort chronologically, oldest first.
4. For each entry, extract the 1-2 sentences where the topic appears, quote exactly.
5. Identify inflection points: dates where the framing of the topic visibly shifted. Call these out in a final summary.
6. Also include note maturity (`#status/seed|growing|evergreen`) so user can weight early vs. refined thinking.

## Output format

```
# Trace: <topic>

## Timeline

| Date | Note | Maturity | Extract |
|---|---|---|---|
| 2025-11-03 | [[Note Name]] (path/to/note.md) | seed | "…exact quote…" |
| 2026-01-14 | [[Other Note]] (path/to/other.md) | growing | "…exact quote…" |
| … | … | … | … |

## Inflection points
- **2026-01-14**, shift from X to Y framing. Triggered by [[Note]].
- **2026-03-22**, decision made, see [[Decision Note]].

## Current position
<1-2 sentence summary of where the thinking stands today, citing the most recent evergreen or growing note on the topic>

## Gaps
<notes that should probably exist but don't, e.g. "no Decision note exists despite the thinking being clearly settled">
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.

Do not invent dates. If a note has no reliable date, place it in a separate "Undated" section at the top.
