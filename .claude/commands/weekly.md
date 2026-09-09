---
description: Themes, blind spots, and promotion candidates from recent dailies
argument-hint: [lookback-days]
---

Load and execute the canonical prompt at `.wiki/_commands/weekly.md`.

Before running, also load `.wiki/_commands/_shared-rules.md` and apply its rules throughout.

User arguments: $ARGUMENTS

## Loopback (required)
After producing your answer, file the full result so the wiki compounds:
`python3 tools/file_query.py weekly "<short title>"` (pipe the markdown to stdin).
Outputs land in `.wiki/digests/queries/` — required by `_shared-rules.md`.
