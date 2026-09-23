# The method

> **In the loop:** [1. Gather information](../README.md#1-gather-information), [2. Think deeper and clearer](../README.md#2-think-deeper-and-clearer) and [3. Decide](../README.md#3-decide). The method underneath the code.

Four things in brainless are method rather than software: how a captured note is
worked over, why every input becomes Markdown, how a research pass is run, and what
keeps the compiled layer from growing into a pile. The code
enforces them, but none of them came from the code, and any of them would survive being
reimplemented in a different language next year. This page is the part worth keeping.
What was taken from each book, paper and tool, and where it shows up, is in
[References](references.md).

---

## 1. How a note is worked over

The method is **Zettelkasten**, Luhmann's slip-box (his own account is the 1981 essay
"Kommunikation mit Zettelkästen"; the readable modern one is Sönke Ahrens, *How to Take
Smart Notes*, 2017), with one adaptation: the reading and the filing are done by a
machine, and the judging is not. What survives from the
original is the part that actually did the work, which was never the index cards.

Four things make it a slip-box rather than a folder of files:

- **Atomicity.** One concept, one note, one home. A copy is a bug, a cross-link is a
  feature. `_Templates/Idea.md` says it out loud in the template: "one paragraph, if you
  need more it might be multiple ideas".
- **Permanent addresses.** Every idea carries `zk: YYYYMMDDHHmm` in its frontmatter,
  assigned once and never changed, so a note keeps its identity through every rename and
  every recompile. `tools/compile_resources.py` preserves the id across rebuilds;
  `tools/zk_id.py` reports notes that are missing one and assigns them on `--apply`.
- **Links are the structure, not the folders.** The graph is built from explicit
  `[[wikilinks]]`. `/connect` walks the link path between two notes and proposes the
  edges that should exist but do not.
- **The archive asks for the next note.** Luhmann's slip-box told him where it was thin.
  Here that is mechanical: `/backlog` counts unresolved links, ranks them by how often
  something points at a note that was never written, and hands back a queue of notes the
  archive is demanding.

The addresses tend to appear on the machine side first and reach the hand-written
seeds later; `tools/zk_id.py --apply` closes that gap, and it does not write to your
notes unless you ask it to.

The rest of the loop is how a captured thought becomes a slip worth addressing.

The front door is deliberately wide and deliberately dumb. A thought arrives as a voice
memo, a photograph of a handwritten page, a link, a Telegram line, a dropped PDF, and
the only job at that moment is to keep it. Nothing is tagged, filed or judged on the way
in (see [the capture flow](capture-flow.md)). Judging at capture time is how people stop
capturing.

Everything after that is a sequence of narrowing passes, each of which asks one question
and refuses to ask the others:

**Pass one, the same day: what is this about?** The capture is written into
`Thinking/Daily/` as a note with the transcript or text intact. Proper nouns get
corrected against the vault's own context, because a transcript that spells a colleague's
name three ways cannot be linked to anything.

**Pass two, the evening close-out: what mattered?** The 21:00 job reads the day and
proposes, in the owner's own vault but never into it: what happened, what carries over,
what is still open, and one question worth keeping. Proposals are pasted by a human or
not at all. This is the rule the whole system is built around, and it is why the engine
can run unattended against a folder holding someone's life.

**Pass three: how big is the work in here?** `tools/note_classify.py` labels every
capture as **task**, **story** or **epic** in the Atlassian sense. The label is not
bureaucracy: it is what decides whether the machine is allowed to spend a week of
research on a topic. The gate is deterministic first (a score over hand-written signals:
distinct links, active projects touched, people named, multi-cycle time expressions,
recurrence across three weeks, open questions) and only asks a model about the genuinely
ambiguous middle. A model may demote a candidate but can never promote a note the
deterministic gate refused, so a persuasively written note cannot talk its way into
importance.

**Pass four, over weeks: does this become something?** A question that keeps returning
graduates from a daily note to a seed in `Thinking/Ideas/`, a seed that survives becomes
a belief in `Thinking/Beliefs/`, and a belief that has to be acted on becomes a decision
in `Thinking/Decisions/` with a falsifiable prediction, a confidence percentage and a
review date. `/graduate` promotes what has matured and flags what has sat alone too long.
`/calibrate` grades a decision when its review date arrives, in one line, so judgment
compounds instead of evaporating.

Two rules hold the whole thing together:

- **One concept, one home.** A copy is a bug; a cross-link is a feature. Personal and
  work effects must be surfaced in both directions, which is why the compiler treats a
  missing cross-link as a defect rather than a style preference.
- **The human writes the human areas, the machine writes the compiled layer.** Both read
  both. `.wiki/` is regenerable and never hand-edited; `Work/`, `Personal/`, `Library/`
  and `Thinking/` are never written by an agent without an explicit yes.

---

## 2. Everything becomes Markdown

Every input is converted to Markdown before anything else touches it. A PDF, a DOCX, a
spreadsheet, a slide deck, a voice memo, a photograph: each one lands as a text file in
the vault, next to the original.

- Documents go through `tools/markitdown_native.py`, which calls markitdown as a
  **library** with one reused instance rather than shelling out per file.
- Audio is transcribed locally by whisper.cpp, on the machine, before any model sees it.
- Photographs are read into text and filed as notes.
- A converted source keeps two files: `<name>_raw.md`, the mechanical conversion, and an
  authored note beside it. The raw file is evidence; the authored note is thinking. They
  are never the same file, so a later reader can always tell which is which.

This is not a formatting preference. It buys five things that a proprietary store does
not:

1. **Grep and BM25 work.** `tools/wiki_search.py` is ripgrep plus a ranking function. No
   index server, no embedding refresh, no vendor.
2. **Git works.** Every change to every note is a diff with a date and an author. Two
   machines can write to the same vault under a documented sync protocol
   ([TRUNK-BASED-DEVELOPMENT.md](../_Agent-Context/TRUNK-BASED-DEVELOPMENT.md)).
3. **Models read it natively.** Markdown is the format language models were trained on.
   A prompt built from Markdown needs no adapter, and a model's answer drops straight
   back into the vault.
4. **It outlives the tooling.** Obsidian is the surface, not the substrate. If every
   tool in this repository disappeared tomorrow, the vault would still be a folder of
   readable files.
5. **Privacy is enforceable.** Plain files can be pattern-matched before they are
   compiled or exported, which is how private folders stay out of the shared layer.

The cost is honest: conversion loses layout, tables come across imperfectly, and a
scanned document is only as good as the OCR. Keeping the original next to the conversion
is the answer to that, not a claim that the conversion is lossless.

### Where the original lives, and why not in git

The markdown is the record. The original is a file, and git is a bad place for files:
a 40 MB scan never diffs, adds permanent weight, and deleting it later does not delete
it, as two history rewrites in this vault have now demonstrated. So binaries are not
tracked. Three cases:

1. **The original is in Google Drive.** It does not go to git at all. The markdown
   carries a source block naming the file, its size, its sha and its Drive path, so the
   note knows where its original is without holding it.
2. **Only part of the document matters.** Write that part into an authored note. A
   human decides which part; a script cannot.
3. **The whole document matters.** Convert it with markitdown and let the markdown be
   the artefact of record.

`tools/lighten_vault.py` does the mechanical half. It finds each tracked binary's
conversion, looks the original up in the local Drive mount by exact byte size (names do
not match, because the vault renames documents to its dated convention while Drive keeps
the name they arrived with), stamps the source block, and untracks the binary. It never
deletes anything from disk.

One rule it will not break: `--only-drive` is the default posture, because untracking a
file that exists nowhere else removes its only backup. A binary with no Drive copy stays
tracked, ignored-but-tracked, until a copy exists. `.gitignore` does not untrack what
git already follows, and here that quirk is load-bearing rather than annoying.

---

## 3. Research follows a social science method, not a search engine

Asking a model to "research X" produces a plausible essay. The method here is taken from
Quivy and Van Campenhoudt, *Manuel de recherche en sciences sociales*, adapted for a
vault and a set of agents. The full adaptation lives in the vault as a playbook; the
binding parts are these.

The book's three acts are **rupture** (break from what you already assume),
**construction** (build an explanatory model), **verification** (confront it with
reality), and it walks them in seven steps. The adaptation keeps all seven and gives
each one a file:

| # | Quivy step | Here |
|---|---|---|
| 1 | Opening question | a question note: one sentence, three tests, a list of what is not known, what is out of scope |
| 2 | Exploration | reading salvos filed as appendices, one angle per appendix, an interim synthesis between salvos, and the question rewritten if the salvo says so |
| 3 | Problematique | the lens, one sentence, written into the question note before any hypothesis |
| 4 | Explanatory model | the hypothesis table: hypothesis, expected observation, what would refute it, confidence |
| 5 | Observation | data collection tied to a hypothesis, every finding labelled `verified`, `claim` or `unknown` |
| 6 | Analysis | the synthesis note: an executive summary, a hypothesis-versus-finding table, and an interpretation of every deviation |
| 7 | Conclusion | the terminal steps: consequences, next actions, at least one seed for `Thinking/Ideas/`, and a decision note with a calibration line when the research settles one |

`/research` and `tools/weekly_research.py` walk the seven in that order. The salvo cap
in step 2 and the label filter in step 5 are enforced in code, along with the two guards
below; the other five are held by the prompt and the output template, which is a weaker
guarantee and is said so here. The binding rules, step by step:

**The opening question comes before any data.** One sentence, with three tests: every
term defined, answerable with the time and access actually available, and a real
question, one whose answer is not already implied by the way it is asked. The most
common failure in AI-assisted research is the reverse: fan out to agents first, then go
looking for the question the results happen to answer.

**The lens is named before the hypotheses.** One sentence saying which angle the
research looks through and what it is trying to explain. This is the book's
problematique step, and skipping it is how a set of hypotheses ends up scattered across
three unrelated questions: each one defensible, none of them adding up. Fixing the angle
first is also what makes a later pass comparable to this one.

**Hypotheses are written down and must be falsifiable.** A hypothesis states a relation
between two terms and can come back refuted. This is the same shape as the decision
template's prediction and confidence fields, which is what connects research to
calibration: a research pass that cannot be wrong teaches nothing when it is.

**Reading happens in salvos, with thinking in between.** At most five sources per salvo,
each on a deliberately different angle, then a pause to interpret before the next.
Unlimited parallel fan-out is the modern version of the trap the book calls insatiable
reading: consuming everything with no criterion for what to consume.

**Findings carry an epistemic label, or they are deleted.** Every line must be
`Kaynak: <URL>` plus one of `verified`, `claim` or `unknown`. Unlabelled lines are
stripped mechanically before the synthesis pass, and `unknown` lines are quarantined and
may not be used as support. When there is no evidence the report says so, because
absence of evidence is not evidence.

**The analysis compares what was observed against what the hypothesis expected, and
interprets the deviations.** The deviations are the finding. A report that only confirms
is a report that was not testing anything.

**The pass ends in a seed, not a summary.** At least one question the research fertilised
is proposed for `Thinking/Ideas/`, as a paste-ready block the owner moves or discards,
and a research that settles a decision proposes the decision note with its prediction,
confidence and review date. This is the step most often skipped, and it is where the
library loop and the thinking loop connect; a pass without it is reading, not research.

Two guards around the outside of the method, both about restraint:

- A research pass runs **only if the week produced an epic**. A week of stories and
  tasks gets no research, and the job exits in seconds having spent nothing. Researching
  every week is how a second brain turns into trivia.
- A topic already researched **within eight weeks is not researched again**. The
  recurrence is reported instead, with the earlier report linked, because a topic that
  keeps returning without closing is waiting for a decision, not for more evidence.

The same discipline applies to the adversarial pass: six critical-thinking personas
(Skeptic, Gambler, Scientist, Postmortem, Strategist, Methodologist) argue a topic in two rounds before
a synthesis is written, so the objection arrives before the commitment rather than after
it. See [how it makes you think clearer](thinking-clearer.md) for the rest of those
mechanisms.

## 4. What keeps the pile small

A system that reads everything has a second failure mode, quieter than losing notes: it
becomes very good at sounding informed. It touches every subject, holds none of them, and
the counters that measure it (pages, links, summaries) all go up while nothing gets
decided. The belief that underwrites capture here, "capture everything, filter later",
names its own failure in its falsification line: the number of Inbox files older than 14
days should stay near zero. On 19 September 2026 it was 125, up from 121 at an audit two
weeks earlier. Filter later had become filter never, and the counters looked fine.

Three mechanisms, each mapped to one of the three layers (the first three lines of the loop: gather, think, decide), and a rule about what is
deliberately left alone.

**Count, so the second layer can fail.** The layer called think clearly has no output of
its own: nobody can count clarity. `tools/wiki_prune.py --count` writes a stand-in to
`_Agent-Context/PILE-SCORECARD.md` every Sunday, with no model involved: captures per
graded decision, filed analyses per decision, Inbox files past the belief's fourteen days,
orphan wiki pages, the number of concept pages that draw on more than one home (a
bridge in Burt's sense; an edge count is not one), decisions made and beliefs challenged
in the window, and what was archived. For the first four weeks the numbers are the whole
point. Ceilings come from the data afterwards, calibrated against this vault's own numbers
rather than against a guess, and a breach shows up as a line in the
morning briefing through `health_check.py`, never as an automatic freeze.

**Prune, mechanically and with a reason.** `tools/wiki_prune.py --archive` applies rules
in the style of the research pass's `filter_claims()`: no judgement, a stated reason, and
a dry run that lists before an `--apply` that moves. A filed analysis that nothing has
linked to in 30 days moves to `.wiki/_archive/`; research, decision and dialectic notes
are exempt, because they carry hypotheses graded later. A summary whose source left the
compiled roots and that nothing links to in 60 days follows, as does any page with no link
in or out for 60 days that no job would rebuild. Nothing is deleted. Git keeps the
history and `.wiki/_archive/LOG.md` keeps every move with its rule and reason, so a move
can be undone with one `git mv`. The archive is outside the search, the index and the
linter, which is the point of moving it.

**Simplify at the digest, where the labels already exist.** `note_classify.py` tags every
capture epic, story or task, and until now only epic did anything. The nightly digest now
reads the label before it reads the note: a task, one sitting of work, contributes its
action items and no narrative; a story gets three lines; an epic is read in full, because
Sunday's research builds on it. The prompt has a ceiling on how much raw text enters it,
the same one the weekly reconcile uses, and the task ledger applies the near-duplicate
guard the meeting extractor already had, so a promise rephrased by tonight's model does
not become a second row.

**What is left alone, on purpose.** Nothing is judged at capture; the belief stands, and
the filter sits downstream where there is context. The human homes are never pruned by
the machine: the scorecard lists what it would move there and stops. One exception was
approved by hand: a meeting report that has been summarised, mined for its tasks and left
in `Inbox/` for two weeks moves to `Archive/`, which is what "a processed file leaves the
inbox" always meant.
