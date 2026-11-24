---
description: End-to-end traceability audit of one active project from raw sources through .wiki/, tasks, contradictions, and drafts.
argument-hint: <project name>
---

Load and execute the canonical prompt at `.wiki/_commands/trace.md`.

Before running, also load `.wiki/_commands/_shared-rules.md` and apply its rules throughout.

User arguments: $ARGUMENTS

## Loopback (required)
After producing the audit, file the full result so the wiki compounds:
`python3 tools/file_query.py trace "<project name>"` (pipe the markdown to stdin).
Outputs land in `.wiki/digests/queries/`, required by `_shared-rules.md`.
