---
name: backlog
description: Turn the wiki's unresolved-link demand into a ranked "notes to write" queue
argument-hint: [top-N, default 5]
---

## Inputs
Optional integer, how many demanded notes to surface. Default: 5.

## Reads
- Run `python3 tools/lint_wiki.py` to refresh `.wiki/_lint-report.md`.
- From that report, read the **`## Missing pages (entity candidates, by demand)`** section, each line is `` - `[[Target]]` wanted Nx ``. This is the box asking for a note: a concept the wiki keeps naming that nobody has written yet.
- Also read **`## Real broken links (actionable)`** to see *which* notes name each target (the demanders).
- Ignore the `## External / raw / Kolay references` section, those are intentional pointers, not gaps.

## Behavior
This is the Zettelkasten "the archive tells you what note it wants next" signal, made into a queue.
1. Rank the missing targets by demand count (highest first). Keep the top N.
2. Drop obvious noise: template placeholders, date-like stems, and anything already covered by an existing note under a different title (check with `python3 tools/wiki_search.py "<target>" --json --k 5` before listing it).
3. For each surviving target, classify the destination:
   - a person / company / initiative → `.wiki/entities/<Name>.md` (entity page)
   - a concept / thesis → `.wiki/articles/<slug>.md` or an atomic `.wiki/ideas/` note
4. For the top 3, emit a **paste-ready stub** matching the destination's convention (entity pages carry `lang:` + `summary_en:`; ideas follow `_Templates/Idea.md` incl. a stable `zk:` id). List the demanding notes as the stub's initial backlinks so it lands already connected.
5. Never mass-create files. Stubs are proposals; write only on an explicit `apply`.

## Output format

```
# To-write queue: top <N> by demand

| # | Wanted note | Demand | Destination | Demanded by |
|---|---|---|---|---|
| 1 | [[Target]] | 6x | entity | [[A]], [[B]], [[C]] … |
| 2 | … | … | … | … |

## Paste-ready stubs (top 3)

### 1. [[Target]] → `.wiki/entities/Target.md`
```markdown
---
lang: <output_lang from PROFILE.md>
summary_en: <one line>
compiled_at: <today>
status: seed
---
# Target

## Links
- [[A]]
- [[B]]
```

### 2. …

---
Reply `apply 1,2` to write the stubs, or paste them yourself. Human-tree targets stay proposals.
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.
`.wiki/entities/`, `.wiki/articles/`, `.wiki/ideas/` are LLM-owned, you may write there, but only on an explicit `apply`, one commit per batch. Never invent demand that isn't in the lint report (no hallucinated targets).
