---
description: Turn a saved conversation or e-mail thread plus rough notes into a commitment-checked draft reply in Writings/Drafts/.
argument-hint: <thread path or pasted context + rough notes>
---

Load and execute the canonical prompt at `.wiki/_commands/reply.md`.

Before running, also load `.wiki/_commands/_shared-rules.md` and apply its rules throughout.

User arguments: $ARGUMENTS

## Loopback (required)
After saving the draft in `Writings/Drafts/`, file the decision and commitment trace so the wiki compounds:
`python3 tools/file_query.py reply "<short title>"` (pipe the markdown analysis without Tier B/C secrets to stdin).
Outputs land in `.wiki/digests/queries/`, required by `_shared-rules.md`.
