---
name: ideas
description: Generate atomic idea candidates from intersections of beliefs, projects, and recent thinking
argument-hint: [focus area, e.g. "health" or "kolay"]
---

## Inputs
Optional focus area (freeform string). If omitted, draw from the whole vault.

## Reads
- `Thinking/Beliefs/` (all files)
- `_Agent-Context/BELIEFS-SUMMARY.md`
- `.wiki/ideas/` (all files)
- Last 14 files in `.wiki/digests/`
- `Work/` (all files)
- If a focus area is provided, restrict recent-daily and project reads to notes mentioning that area.

## Behavior
1. Build a mental list of: (a) beliefs currently held, (b) active projects, (c) themes mentioned in recent dailies.
2. Produce **3 candidate atomic ideas** where each idea is the **intersection of at least two distinct categories** — e.g. a belief × a project, a resource × an area. Pure single-category ideas are not allowed.
3. Phrase each idea as a **question**, not a statement. Atomic ideas in the owner's vault are provocations, not conclusions.
4. For each idea, cite the 2+ source notes it draws from using full `[[Wikilinks]]`.
5. Format each idea as a **paste-ready stub** using the structure of `_Templates/Idea.md` (read that template first to match field names exactly).
6. End with a one-liner on which of the 3 ideas feels most pursuing, with reasoning — but do not pick for the owner.

## Output format

```
# Idea candidates <optional: — focus: <area>>

## 1. <Short idea title>
**Question:** <the provocation>
**Intersection:** [[Source A]] × [[Source B]]
**Paste-ready stub:**
```markdown
---
date: <today YYYY-MM-DD>
type: idea
tags: [idea, status/seed]
---

# <Short idea title>

## The question
<the provocation, expanded to 2-3 sentences>

## Why it matters
<one paragraph>

## Connects to
- [[Source A]]
- [[Source B]]
```

## 2. …

## 3. …

## Which one pulls hardest?
<1-2 sentences — observational, not prescriptive>
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.
If `Thinking/Beliefs/` or `.wiki/ideas/` is empty, say so and warn that output will be thin.
