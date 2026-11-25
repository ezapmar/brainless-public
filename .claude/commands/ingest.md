---
description: Compile one source into .wiki/ immediately, link it across related pages, flag contradictions, and return a 3-sentence diff.
argument-hint: <vault-relative path to source file>
---

Load and execute the canonical prompt at `.wiki/_commands/ingest.md`.

Before running, also load `.wiki/_commands/_shared-rules.md` and apply its rules throughout.

User arguments: $ARGUMENTS

## Guardrails
- `raw/` is strictly read-only. Never write to human areas without explicit approval.
- Check `cloud_exclude` and `protected_homes` in `_Agent-Context/PROFILE.md` before sending any content to cloud tools.
- Cap edits at 15 `.wiki/` pages per source, and verify with `python3 tools/lint_wiki.py` before finishing.
- Zero em dashes or en dashes in all outputs.
