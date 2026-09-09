---
description: Argue a topic with the five critical-thinking personas, then synthesize
argument-hint: [topic | note name | empty = today's captures]
---

Load and execute the canonical prompt at `.wiki/_commands/dialectic.md`.

Before running, also load `.wiki/_commands/_shared-rules.md` and apply its rules throughout.

User arguments: $ARGUMENTS

## Loopback (required)
After producing your answer, file the full result so the wiki compounds:
`python3 tools/file_query.py dialectic "<short title>"` (pipe the markdown to stdin).
Outputs land in `.wiki/digests/queries/` — required by `_shared-rules.md`.
