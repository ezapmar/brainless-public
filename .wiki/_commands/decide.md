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
- `Thinking/Beliefs/` (all files), these are the operating principles to invoke.
- `_Agent-Context/BELIEFS-SUMMARY.md`
- `Thinking/Decisions/` (all files), past decisions, especially any on similar topics.
- `_Agent-Context/CONTEXT.md`, current priorities and constraints.
- `_Templates/Decision.md`, match this template's section names exactly.

## Behavior
0. **Reversibility gate, before anything else.** Ask: if this turns out wrong, what does undoing it cost? Classify the decision as a **two-way door** (cheap to reverse: a pricing test, a hire on probation, a tool trial) or a **one-way door** (expensive or impossible to reverse: a lease, an exit structure, a school). If it is a two-way door, STOP here: say so plainly, recommend deciding today, and offer at most a three-line note. The full framework below is reserved for one-way doors. Over-deliberating reversible decisions is the failure mode this gate exists to prevent.
1. Read the template first so the output structure is faithful.
2. Identify which **specific beliefs** bear on this question. Quote them exactly, no paraphrasing.
3. Identify any **past decisions** that are analogous. Link them.
4. Lay out options. For each option:
   - What it is
   - Which beliefs support it (quoted)
   - Which beliefs resist it (quoted)
   - Second-order effects (what happens after the obvious first effect)
   - What would need to be true for this to be right
5. End with a "What would change my mind?" section, the information or event that would flip the decision.
6. **Prediction with a base rate.** Before any confidence figure, name the **reference class** (e.g. "new UK Ltds with no trading history applying for a lease") and its rough **base rate**. The Confidence% must start from that base rate and then adjust for the specifics, and the note must say what the adjustment was and why. A confidence that did not start from a base rate is not a confidence, it is a mood; do not emit one.
7. **One dated action.** The note must end with exactly one sentence of the form "On <date>, I will <action>." If that sentence cannot be written, the decision is not made: say so, and name what is missing. The date is mandatory. Deleting it later is itself a signal about the decision, not about the calendar.
8. **Do not pick.** The owner picks. Your job is to load the frame, not choose.

## Output format

Emit a full, paste-ready decision note that matches `_Templates/Decision.md`. Include frontmatter with today's date, `type: decision`, appropriate tags.

```markdown
---
date: <today>
type: decision
status: deliberating
reversibility: one-way   # one-way | two-way (two-way decisions do not get this note)
tags: [decision, <topic-tags>]
---

# Decision: <question>

## Context
<1 paragraph, why this decision, why now, pulled from CONTEXT.md>

## Beliefs invoked
- "<exact quote>", [[Belief Note]]
- "<exact quote>", [[Belief Note]]

## Past decisions that inform this
- [[Decision, X]], <how it's analogous>

## Options

### Option A: <name>
**What:** …
**Beliefs for:** "<quote>", [[Belief]]
**Beliefs against:** "<quote>", [[Belief]]
**Second-order:** …
**Needs to be true:** …

### Option B: <name>
…

## What would change my mind
- <signal 1>
- <signal 2>

## Prediction (calibration)
- **Reference class:** <what kind of thing is this>
- **Base rate:** <how often that kind of thing works out, roughly>
- **Prediction:** <falsifiable statement of what you expect>
- **Confidence:** <base rate, adjusted: NN%. Adjustment and why>
- **Review on:** <YYYY-MM-DD>

## Next action
On <date>, I will <one concrete action>.

## Status
Deliberating. To record the final call, update `status:` to `decided` and add a `## Decision` section with the chosen option and rationale.
```

After the paste-ready note, add a short agent commentary:

```
## Agent notes
- Beliefs that *should* apply but are missing from the vault: <list>
- Tension detected: <e.g. "Option A contradicts your 'Calm is contagious' belief, worth a second read">
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.
Never save the decision note yourself. Output is paste-ready for the owner to drop into `Thinking/Decisions/`.
