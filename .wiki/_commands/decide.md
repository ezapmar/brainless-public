---
name: decide
description: Structured decision framework using your beliefs and past decisions
argument-hint: <decision question, in quotes>
---

## Inputs
A decision question. Examples:
- `/decide "Should we build Kolay Ticket on the consultant panel or integrate into prod DB?"`
- `/decide "Lease or buy a home in London?"`

If no question is given, ask for one.

## Reads
- `Thinking/Beliefs/` (all files) — these are the operating principles to invoke.
- `_Agent-Context/BELIEFS-SUMMARY.md`
- `Thinking/Decisions/` (all files) — past decisions, especially any on similar topics.
- `_Agent-Context/CONTEXT.md` — current priorities and constraints.
- `_Templates/Decision.md` — match this template's section names exactly.

## Behavior
1. Read the template first so the output structure is faithful.
2. Identify which **specific beliefs** bear on this question. Quote them exactly — no paraphrasing.
3. Identify any **past decisions** that are analogous. Link them.
4. Lay out options. For each option:
   - What it is
   - Which beliefs support it (quoted)
   - Which beliefs resist it (quoted)
   - Second-order effects (what happens after the obvious first effect)
   - What would need to be true for this to be right
5. End with a "What would change my mind?" section — the information or event that would flip the decision.
6. **Do not pick.** The owner picks. Your job is to load the frame, not choose.

## Output format

Emit a full, paste-ready decision note that matches `_Templates/Decision.md`. Include frontmatter with today's date, `type: decision`, appropriate tags.

```markdown
---
date: <today>
type: decision
status: deliberating
tags: [decision, <topic-tags>]
---

# Decision: <question>

## Context
<1 paragraph — why this decision, why now, pulled from CONTEXT.md>

## Beliefs invoked
- "<exact quote>" — [[Belief Note]]
- "<exact quote>" — [[Belief Note]]

## Past decisions that inform this
- [[Decision — X]] — <how it's analogous>

## Options

### Option A: <name>
**What:** …
**Beliefs for:** "<quote>" — [[Belief]]
**Beliefs against:** "<quote>" — [[Belief]]
**Second-order:** …
**Needs to be true:** …

### Option B: <name>
…

## What would change my mind
- <signal 1>
- <signal 2>

## Status
Deliberating. To record the final call, update `status:` to `decided` and add a `## Decision` section with the chosen option and rationale.
```

After the paste-ready note, add a short agent commentary:

```
## Agent notes
- Beliefs that *should* apply but are missing from the vault: <list>
- Tension detected: <e.g. "Option A contradicts your 'Calm is contagious' belief — worth a second read">
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.
Never save the decision note yourself. Output is paste-ready for the owner to drop into `Thinking/Decisions/`.
