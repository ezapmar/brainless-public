---
name: sync
description: Propagate updates from recent Daily Notes to Project Notes
argument-hint: (none)
---

## Inputs
Recent daily notes (last 7 days).

## Reads
- `.wiki/digests/` (last 7 files by date)
- `Work/` (all .md files)

## Behavior
1. List the most recent 7 files in `.wiki/digests/`.
2. Extract all `[[Project]]` links from these daily notes. A "Project" link is any wikilink that matches a file name in the `Work/` folder.
3. For each unique project found:
   - Identify the context (the bullet point, paragraph, or section) where it was mentioned in the daily note.
   - Read the corresponding file in `Work/`.
   - Compare the information from the daily note with the project's `Log` and `Current Status`.
   - If the information is new or provides a more recent update than what is in the project note, prepare a proposed update for the `Log` section of that project note.
4. Format the output as a series of proposed changes for the user to review.

## Output format
A list of projects with proposed updates:

### [[Project Name]]
**Source**: [[YYYY-MM-DD]]
**Status in Project Note**: <brief summary>
**New Info Found**: <the extracted context>
**Proposed Change**:
```markdown
### YYYY-MM-DD
- <the update from the daily note>
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`. Do not modify files automatically; only propose changes.
