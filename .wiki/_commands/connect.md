---
name: connect
description: Find the link-path between two notes and propose missing edges
argument-hint: <note A> <note B>
---

## Inputs
Two note titles or concepts. Titles may be quoted if they contain spaces.
Examples:
- `/connect "Kolay Ticket" "Emergency Helper"`
- `/connect beliefs decisions`
- `/connect "Ada Lovelace" "Product Team"`

If fewer than 2 inputs are given, ask for the missing one.

## Reads
- All markdown files across the vault (same exclusions as `/trace`).
- Build a directed link graph from `[[Wikilink]]` occurrences in each note's body.

## Behavior
1. Resolve A and B to actual note paths. If either is ambiguous (multiple candidates), list options and ask which one.
2. If either doesn't exist, say so, do not invent.
3. Search the graph for the **shortest path** from A to B (undirected is fine, a wikilink in either direction counts as an edge).
4. If a path exists, print it as a chain of `[[links]]`.
5. Whether or not a path exists, propose **2-4 missing edges**, pairs of notes along or near the path that semantically relate but don't yet link. Justify each in one sentence.
6. For each proposed edge, output the **exact line** the owner can paste into the source note (e.g. `- Related: [[Target Note]]` under a `## Connects To` section).

## Output format

```
# Connect: <A> ↔ <B>

## Path
[[A]] → [[Intermediate 1]] → [[Intermediate 2]] → [[B]]

(or: "No path exists in the current graph. Both notes are connected components of size N and M.")

## Proposed new edges

### 1. [[A]] ↔ [[Candidate Note]]
**Why:** <one sentence>
**Paste into** `path/to/A.md` under `## Connects To`:
```
- [[Candidate Note]]
```

### 2. …

## Graph health note
<optional: "Note A has only 1 inbound link, consider surfacing it more">
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.
Do not apply edges automatically. Output is paste-ready, not auto-written.
