---
description: Deep research on the week's epic, with labelled sources and a falsification pass
argument-hint: [question, or blank for the week's epic]
---

Load and execute the canonical prompt at `.wiki/_commands/research.md`.

Before running, also load `.wiki/_commands/_shared-rules.md` and apply its rules throughout.

The scheduled implementation is `tools/weekly_research.py`; prefer running it
(`python3 tools/weekly_research.py --topic "<question>"`) over improvising the
passes by hand, so a manual run and the Sunday run produce the same shape.

User arguments: $ARGUMENTS

## Loopback (required)
After producing your answer, file the full result so the wiki compounds:
`python3 tools/file_query.py research "<short title>" --lang auto` (pipe the markdown to stdin).
Outputs land in `.wiki/digests/queries/`, required by `_shared-rules.md`.
