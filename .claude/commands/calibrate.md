---
description: Grade matured decisions in one line so judgment compounds
argument-hint: (none, or a decision name to grade just that one)
---

Load and execute the canonical prompt at `.wiki/_commands/calibrate.md`.

Before running, also load `.wiki/_commands/_shared-rules.md` and apply its rules throughout.

User arguments: $ARGUMENTS

## Loopback (required)
After grading, file the result so the wiki compounds:
`python3 tools/file_query.py decide "<short title>"` (pipe the markdown to stdin).
Outputs land in `.wiki/digests/queries/` - required by `_shared-rules.md`.
