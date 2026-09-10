---
name: calibrate
description: Grade matured decisions in one line so judgment compounds
argument-hint: (none, or a decision name to grade just that one)
---

## Inputs
None required. Optionally a decision note name to grade only that one.
`/calibrate` grades everything due; `/calibrate "Office Lease"` grades one.

## Reads
- Output of `python3 tools/calibrate.py` - what is DUE TO GRADE / NEEDS A PREDICTION / NO REVIEW DATE.
- `Thinking/Decisions/*.md` - the decision notes themselves.
- `Thinking/Calibration.md` - the scoreboard.
- `_Templates/Decision.md` - section names to match exactly.

## Behavior
This is the loop that turns a decision diary into a learning engine. The agent
drafts; the owner approves in one line. Never invent an outcome.

1. Run `python3 tools/calibrate.py` and read its three lists.
2. **DUE TO GRADE** (the priority). For each due decision:
   - Read the note; extract its **Prediction** and **Confidence%** (state them back).
   - Draft the shortest possible grading question, e.g.
     *"Office Lease: you predicted the landlord accepts a 3-year term with a break clause, ~NN%. Did that happen, or the fallback?"*
   - Propose a one-line **Outcome** and a one-line **Lesson** for the owner to confirm or correct.
   - On the owner's one-line answer, and only then, write to the vault (Thinking/ needs approval, so the confirmation IS the approval):
     - Replace `_Pending review._` in the note's `## Outcome` with the graded outcome + lesson.
     - Add or update the row in `Thinking/Calibration.md` (Outcome + Lesson columns).
     - If a relevant belief exists, add a dated one-line evidence entry under it.
3. **NEEDS A PREDICTION.** For each, first ask for the **reference class** and its rough **base rate** ("what kind of thing is this, and how often does that kind of thing work out?"). Only then ask for a falsifiable Prediction + Confidence% + Review date, with the Confidence% anchored to the base rate and the adjustment stated. Write them into the note's frontmatter (`confidence:`, `review:`) and Prediction block. Do not fabricate a confidence, and do not accept one that did not start from a base rate.
   Also check the note ends with a dated next action ("On <date>, I will <action>."). If it does not, ask for one; a decision without a dated action is an opinion.
4. **NO REVIEW DATE.** Propose a concrete review date; on confirmation set `review:` in frontmatter.
5. Re-run `python3 tools/calibrate.py` to confirm the item cleared.

## Output
- A short scoreboard of what was graded, each with the one-line lesson.
- A note of anything still open (e.g. a prediction the owner did not want to grade yet).

## Rules
- One decision at a time; keep each exchange to a single question.
- Never write an Outcome, Prediction, or Confidence the owner did not state.
- Match `_Templates/Decision.md` section names exactly.
- Em/en dashes are banned in all writes, including the scoreboard.
