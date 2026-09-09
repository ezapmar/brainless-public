---
description: Turn the wiki's unresolved-link demand into a ranked "notes to write" queue
argument-hint: [top-N]
---

Load and execute the canonical prompt at `.wiki/_commands/backlog.md`.

Before running, also load `.wiki/_commands/_shared-rules.md` and apply its rules throughout.

User arguments: $ARGUMENTS

## Loopback (required)
After producing your answer, file the full result so the wiki compounds:
`python3 tools/file_query.py backlog "<short title>"` (pipe the markdown to stdin).
Outputs land in `.wiki/digests/queries/` — required by `_shared-rules.md`.
