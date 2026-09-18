---
description: Cross-reference a new note with your existing Beliefs and Ideas to find resonance or dissonance.
argument-hint: <path/to/note>
---

Load and execute the canonical prompt at `.wiki/_commands/pollinate.md`.

Before running, also load `.wiki/_commands/_shared-rules.md` and apply its rules throughout.

User arguments: $ARGUMENTS

## Loopback (required)
After producing your answer, file the full result so the wiki compounds:
`python3 tools/file_query.py pollinate "<short title>"` (pipe the markdown to stdin).
Outputs land in `.wiki/digests/queries/`, required by `_shared-rules.md`.
