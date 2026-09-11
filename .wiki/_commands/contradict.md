---
name: contradict
description: Find tensions between beliefs, decisions, and recent actions; flag unused beliefs
argument-hint: [scope: "beliefs" | "decisions" | "all", default "all"]
---

## Inputs
Optional scope keyword: `beliefs`, `decisions`, `all` (default).

## Reads
- `Thinking/Beliefs/` (all files) + `_Agent-Context/BELIEFS-SUMMARY.md`
- `Thinking/Decisions/` (all files)
- Project logs in `Work/` (last 30 days of log entries)
- Recent daily notes (last 14) for stated actions

## Behavior
1. Pair-wise scan for semantic conflicts:
   - **Belief ↔ Belief**, two stated principles that push in opposite directions.
   - **Belief ↔ Decision**, a decision whose rationale violates a stated belief.
   - **Belief ↔ Recent action**, something the owner did or wrote in a daily that contradicts a held belief.
2. For each pair, output exact quotes from both sides, file paths, and a one-sentence description of the tension. Do not editorialize, let the quotes do the work.
3. Detect **stale beliefs**: beliefs that are never cited, linked, or acted on in any decision or idea for ≥90 days. List them as "unused, keep, delete, or refine?".
4. Detect **implicit beliefs**: patterns that appear repeatedly in decisions and dailies but are never written down as an explicit belief. Propose them as new `Thinking/Beliefs/` candidates.

## Output format

```
# Contradiction scan: scope: <scope>

## Tensions detected

### 1. Belief ↔ Decision
**Belief**, "<exact quote>", [[Belief Note]] (`path`)
**Decision**, "<exact quote>", [[Decision Note]] (`path`), <date>
**Tension:** <one sentence>

### 2. Belief ↔ Belief
…

### 3. Belief ↔ Recent action
**Belief**, "<exact quote>", [[Belief Note]]
**Action**, "<quote from daily>", [[Daily 2026-04-14]]
**Tension:** …

## Stale beliefs (unused ≥90 days)
- "<belief text>", [[Belief Note]], last cited <date or "never">

## Implicit beliefs (pattern detected, never written)
1. "<proposed belief>", observed in [[Decision A]], [[Decision B]], [[Daily 2026-03-12]]
2. …

## Nothing to flag
<if all clean, state so, this is a valid output>
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.
Tone: neutral. You are not accusing the owner of inconsistency, you are surfacing friction for their own review. Contradictions are often signal of evolving thinking, not error.
