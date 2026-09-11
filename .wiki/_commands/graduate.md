---
name: graduate
description: Find seed notes mature enough to promote; flag lonely seeds for archival
argument-hint: [min-age-days, default 14]
---

## Inputs
Optional integer, minimum age in days for a seed to be considered. Default: 14.

## Reads
- Every markdown file tagged `#status/seed` (search frontmatter `tags` and inline tags). Treat a `status: seed` frontmatter scalar the same as the tag.
- For each candidate, count **inbound wikilinks**, how many other notes contain `[[this note]]`.
- Read each candidate's frontmatter `date` and body length (word count).
- For any note you propose promoting to **evergreen**, also read its body (Key Points / sections) so you can judge whether it holds more than one idea.

## Behavior
1. Filter to seeds older than the cutoff.
2. For each, compute:
   - `age_days` (today − frontmatter date)
   - `inbound_links` (how many other notes link to this)
   - `word_count`
3. Classify with these heuristics (tune later):

| Signal | → propose |
|---|---|
| `inbound_links ≥ 3` AND `age ≥ 14` | promote to `#status/growing` |
| `inbound_links ≥ 7` AND `age ≥ 30` AND `word_count ≥ 200` | promote to `#status/evergreen` |
| `inbound_links = 0` AND `age ≥ 30` | flag for archive-or-kill |
| otherwise | leave alone |

4. For each proposed promotion, output the **exact frontmatter diff** to apply.
5. **Fission (atomicity).** For each note newly proposed for `#status/evergreen`, judge whether it is atomic (one idea) or a container (several distinct Key Points). A reaching-evergreen note is stable enough to mine. If it holds ≥2 distinct ideas, propose splitting each into its own paste-ready atomic idea stub:
   - Use the structure of `_Templates/Idea.md` (read it first to match field names).
   - Give each new stub a stable `zk:` id = current timestamp `YYYYMMDDHHmm`, consecutive minutes so none collide. The id never changes once written.
   - Every stub must link back to its parent via `## Connects to → [[Parent Note]]`, and the parent keeps its Key Points (fission copies, it does not gut the source).
   - These are proposals only. Never write idea files automatically.
6. Never apply changes automatically. The owner must confirm one-by-one or say "apply all".

## Output format

```
# Graduate review: cutoff <N> days

## Propose → #status/growing (<count>)

### [[Note Name]] (`path/to/note.md`)
- age: <N> days · inbound: <N> · words: <N>
- **Diff:**
  ```diff
  - tags: [idea, status/seed]
  + tags: [idea, status/growing]
  ```

### …

## Propose → #status/evergreen (<count>)
<same format>

## Fission → atomic ideas (from new evergreens) (<count>)

### From [[Parent Note]] (`path/to/parent.md`): <N> ideas
```markdown
---
date: <today YYYY-MM-DD>
zk: <YYYYMMDDHHmm>
type: idea
status: seed
tags: [idea, status/seed]
---

# <Atomic idea title>

## The Idea
<one Key Point, rewritten as a single atomic idea>

## Connects To
- [[Parent Note]]
```
<one stub per distinct Key Point; consecutive zk minutes>

## Lonely seeds: archive or kill (<count>)
- [[Note]], 0 inbound, <N> days old. Consider moving to `Archive/` or deleting.

## Leave alone (<count>)
<just a count, not a list>

---
Reply `apply 1,3,5` to apply specific diffs, `apply all promotions`, or `skip` to do nothing.
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.
If the owner replies with an `apply` instruction, only then modify the tag frontmatter. Touch no other fields. One commit per apply batch.
Fission stubs are paste-ready proposals only, never write idea files automatically. `Thinking/Ideas/` is human-owned (paste there yourself); only write to `.wiki/ideas/` on an explicit `apply ideas`.
