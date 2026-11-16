---
description: Structured decision framework using your beliefs and past decisions
argument-hint: <decision question in quotes>
---

Load and execute the canonical prompt at `.wiki/_commands/decide.md`.

Before running, also load `.wiki/_commands/_shared-rules.md` and apply its rules throughout.

User arguments: $ARGUMENTS

## Loopback (required)
After producing your answer, file the full result so the wiki compounds:
`python3 tools/file_query.py decide "<short title>"` (pipe the markdown to stdin).
Outputs land in `.wiki/digests/queries/`, required by `_shared-rules.md`.
