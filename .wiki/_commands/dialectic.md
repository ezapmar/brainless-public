---
name: dialectic
description: Argue a topic with the five critical-thinking personas (Skeptic, Gambler, Scientist, Postmortem, Strategist), then synthesize
argument-hint: [topic text | note name | empty = today's unseen captures]
---

## Inputs
Optional topic: free text, or a `[[Note]]` name. Empty: today's `Thinking/Daily/*-telegram.md` and `*-buzz.md`.

## Reads
- Persona method cards: `.agents/buzz/personas/<slug>/system_prompt.md` for `skeptic`, `gambler`, `scientist`, `postmortem`, `strategist`, plus `.agents/buzz/team_instructions.md` (the round rules; ignore the Buzz posting contract when running locally).
- `_Agent-Context/BELIEFS-SUMMARY.md` (Core Beliefs block), `Thinking/Decisions/` titles.
- `python3 tools/wiki_search.py "<topic>" --json --k 5` for prior context. Never `Thinking/Contrarian Theses.md`.

## Behavior
1. State the thesis in one paragraph: claim, source, prior wiki context (links).
2. Round 1, isolated: answer as each persona in turn, strictly with that persona's method card and WITHOUT reading the other personas' round 1 answers (write each as if it were the only one), at most 250 words each, ending with **Finding / Strongest objection / Question for the owner / Vote / Number**. Vote is YES, NO or CONDITIONAL (does the thesis hold as stated); Number is NN%, the probability the thesis proves right within 12 months. A persona may answer "Pass" (counts as abstain).
3. Round 2: each persona reads all round 1 answers, picks the strongest objection raised by another persona, agrees or refutes it in at most 3 sentences, ending with **Chosen objection / My answer / Vote / Number / New evidence** (one fact or argument not in its own round 1, or "none").
4. Scorecard, computed not argued: one table row per persona (round 1 vote and number, round 2 vote and number, moved = vote changed or number shifted 15 points or more, new evidence). Then one line: affirmation = round 1 YES votes over votes cast; moved count and how many moved without new evidence. If every voting persona cast the same round 1 vote (3 or more votes), add a red "Unanimity warning" line: five voices on one base model agreeing is a signal to check the framing, not a confirmation.
5. Synthesis (moderator voice): strongest counterargument, what would have to be true, proposed test, confidence bet (%), contradiction with a belief or decision (cite file, or "None"), changed minds (must match the scorecard), unanimity warning (or "None"), and one paste-ready proposal (seed, belief or decision stub) or "None".
6. The same engine runs twice daily on the always-on worker with live Buzz agents (`tools/dialectic.py`); this command is the local, on-demand twin. Output must match the scheduled note structure so both compound in the same place. File with `--lang en`.

## Output format

```
# Dialectic: <topic>

## Thesis
<claim, source, prior context links>

## Round 1
### Skeptic
…
### Gambler
…
### Scientist
…
### Postmortem
…
### Strategist
…

## Round 2
### <persona>
**Chosen objection** … **My answer** … **Vote:** … **Number:** … **New evidence:** …

## Scorecard
| Persona | Round 1 | Round 2 | Moved | New evidence |
|---|---|---|---|---|
| Skeptic | NO 35% | NO 30% | no | none |
| … | … | … | … | … |

Affirmation (round 1 YES): a/b (NN%). Moved: c/d, of which without new evidence: e.
<red "Unanimity warning" line when all round 1 votes agree>

## Synthesis
**Strongest counterargument:** …
**What would have to be true:** …
**Proposed test:** …
**Bet:** <%> …
**Contradiction:** [[Belief or Decision]] (`path`) or None
**Changed minds:** …
**Unanimity warning:** … or None

## Proposal
<paste-ready stub for Thinking/Ideas, Beliefs or Decisions, or "None">
```

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`. English output, no em or en dashes. Personas never write to the vault; the proposal is text for the owner to apply. Do not soften: a persona that finds nothing wrong must say so and why.
