# References

> **In the loop:** where the ideas behind the four lines come from.

Three kinds of credit, and nothing else. **Intellectual** sources are the books,
papers and essays the method and the six voices are built on. **Idea** sources are
mechanisms the forms and commands enforce whose standard statement the code does not
always name. **Technical** sources are the tools the engine calls, or the surface it
assumes.

This is the credit list for the public engine. It is not a bibliography of a private
vault. A book that happens to sit in someone's `Library/` is not a source of this
repository unless a prompt, a template or a script takes something from it. Each entry
says what was taken and where that shows up, so the claim can be checked against the
tree. Titles are cited. They are not reproduced. The public repo ships the prompts, not
the copies they were written against.

Where an entry says the code does not name the source, that is deliberate. The mechanism
is in the tree; the attribution is here.

---

## 1. Note-taking

**Niklas Luhmann, "Kommunikation mit Zettelkästen. Ein Erfahrungsbericht" (1981).**
In Horst Baier, Hans Mathias Kepplinger and Kurt Reumann (eds.), *Öffentliche Meinung
und sozialer Wandel*, pp. 222-228. Opladen: Westdeutscher Verlag.

His own account of the slip-box. What survives here is not the index cards: one concept
per note, a permanent address that outlives every rename, links as the structure rather
than folders, and an archive that shows where it is thin. The address format
`zk: YYYYMMDDHHmm` is an adaptation, not his numbering scheme.

Where: [docs/method.md](method.md), `tools/zk_id.py`, `tools/compile_resources.py`,
`/backlog`, `/connect`.

**Sönke Ahrens, *How to Take Smart Notes* (2017).** Independently published.

The readable modern account of the same practice. The method page names it as that
account. The adaptation is the part Ahrens could not have written: the reading and the
filing are done by a machine, and the judging is not.

Where: [docs/method.md](method.md).

**Andy Matuschak, "Evergreen notes."**
[notes.andymatuschak.org/Evergreen_notes](https://notes.andymatuschak.org/Evergreen_notes).

The maturity ladder `seed` / `growing` / `evergreen` is his vocabulary, extended with a
middle step he does not use. Commands are told to weight a seed as unreliable, a growing
note as good for a suggestion, and an evergreen as ground truth. His other point, that
the value of a link is the revisit while you write it and not the edge count, is why
`/connect` proposes edges and a human writes them. A machine-written wikilink skips the
only part that was doing the work.

Where: `.wiki/_commands/graduate.md`, `.wiki/_commands/_shared-rules.md`,
[docs/thinking-clearer.md](thinking-clearer.md). The code does not name him. The status
words are his.

---

## 2. The compiled layer

**Andrej Karpathy, `llm-wiki.md` (4 April 2026).**
[gist.github.com/karpathy/442a6bf555914893e9891c11519de94f](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

An idea file, not a tool. The claim taken from it: retrieval re-derives knowledge on
every question; a compiled wiki derives it once and keeps it current. Raw sources stay
raw. A generated layer (summaries, articles, an index, a log, a lint pass) is written by
the machine and can be rebuilt. Lint is not optional. A wiki that grows without a
contradiction check becomes confident and wrong.

Two of his preferences are adapted rather than copied. He prefers ingesting one source
at a time, discussed with a person. The nightly compile is a batch. The weekly review
and the rule that the machine proposes and a person pastes are the patch for that
difference. He also treats the schema file the agent reads at the start of a session as
part of the wiki, not as a prompt written once and forgotten.

A later popular essay restates the gist and is not the source of record. The pattern is
the gist.

Where: `.wiki/_commands/_shared-rules.md` ("post-Karpathy migration"),
`tools/compile_resources.py`, `tools/lint_wiki.py`, `tools/file_query.py`,
[docs/method.md](method.md).

---

## 3. Research

**Raymond Quivy and Luc Van Campenhoudt, *Manuel de recherche en sciences sociales*
(1995).** Paris: Dunod.

The adaptation follows the 1995 edition. Later editions keep the same skeleton.
Three acts: rupture, construction, verification. Seven steps, each given a file here:
opening question, exploration in salvos, the lens (problématique) before any hypothesis,
a falsifiable explanatory model, observation, analysis of the deviations, and a
conclusion that ends in a seed rather than a summary. Two of the book's traps are
enforced in code: insatiable reading (at most five sources a salvo, each on a different
angle) and collecting data before a hypothesis exists.

The authored note kept beside a mechanical conversion is called a fiche de lecture, the
French university reading card. That is a practice name from the same tradition, not a
second book.

Where: [docs/method.md](method.md), `tools/weekly_research.py`, the Methodologist prompt
in `.agents/buzz/personas/methodologist/system_prompt.md`.

**Douglas G. Altman and J. Martin Bland, "Absence of evidence is not evidence of
absence" (1995).** *BMJ*, 311(7003), 485.

The research pass uses the shorter form: when there is no evidence the report says so,
because absence of evidence is not evidence. Unlabelled findings are deleted before
synthesis, and a line marked `unknown` may not be used as support. The paper is the
careful statement of the related claim. The code does not name it.

Where: [docs/method.md](method.md), `tools/weekly_research.py`.

---

## 4. The six voices

Each persona is one book, or one book sharpened by one article. They do not cite each
other. The prompts are Markdown files in `.agents/buzz/personas/`. Edit them. The
moderator does not care how many there are.

**M. Neil Browne and Stuart M. Keeley, *Asking the Right Questions: A Guide to Critical
Thinking* (2006).** Prentice Hall.

The Skeptic. The edition the persona was written against is the 2006 Prentice Hall copy.
The book has other editions. What was taken: conclusion and reasons first, ambiguous
words, what is omitted, what other conclusions the same reasons would support, and the
difference between weak-sense critical thinking (defend a belief) and strong-sense
(test it, and change it when the test says so).

Where: `.agents/buzz/personas/skeptic/system_prompt.md`.

**Annie Duke, *Thinking in Bets: Making Smarter Decisions When You Don't Have All the
Facts* (2018).** New York: Portfolio. ISBN 9780735216358.

The Gambler. Life is poker, not chess. A decision is a bet under uncertainty. Separate
decision quality from outcome quality. Confidence is a number. "Twelve months on, it
failed: why" is the pre-mortem the persona is required to write.

Where: `.agents/buzz/personas/gambler/system_prompt.md`, the prediction block in
`_Templates/Decision.md`.

**Annie Duke, *Quit: The Power of Knowing When to Walk Away* (2022).** New York:
Portfolio.

Not a seventh persona. The quit rule: decide the condition under which you stop, with a
date, before you are in the moment, because in the moment you will not. Project notes
carry a `## Kill Criteria` section of the form `date | condition | consequence`.
`tools/kill_criteria.py` reads those lines and flags a date that has passed with the box
still open. It does not decide for you.

Where: `tools/kill_criteria.py`, `_Templates/Project.md`, [docs/scripts.md](scripts.md).

**Arnaldo Camuffo, Alfonso Gambardella, Danilo Messinese, Elena Novelli, Emilio Paolucci
and Chiara Spina, "A scientific approach to entrepreneurial decision-making: Large-scale
replication and extension" (2024).** *Strategic Management Journal*, 45(6), 1209-1237.
[doi:10.1002/smj.3580](https://doi.org/10.1002/smj.3580). Open access, CC BY 4.0.

The Scientist. Four randomised trials, 759 firms. Theory, a testable hypothesis, the
cheapest test, a result that means terminate and a result that means a focused pivot,
then a Bayesian update. The paper is a large-scale replication of Camuffo, Cordova,
Gambardella and Spina, "A scientific approach to entrepreneurial decision making:
Evidence from a randomized control trial" (2020), *Management Science*, 66(2), 564-586.
The persona is built on the 2024 paper. The 2020 trial is the study that paper
replicates, not a second method.

Where: `.agents/buzz/personas/scientist/system_prompt.md`.

**Stefan Thomke and Gary W. Loveman, "Act Like a Scientist" (2022).** *Harvard Business
Review*, May-June, 120-129. Reprint R2202J.

Sharpens the same persona. The practitioner instruments the paper leaves implicit:
anomaly hunting, a strong hypothesis against a weak one, an evidence hierarchy, and the
difference between correlation and a cause. It also names the social fact the paper
measures around: acting like a scientist threatens a leader's legitimacy, because rank
is read as proof of judgment.

Where: `.agents/buzz/personas/scientist/system_prompt.md`. Assigned to this persona only,
so the Skeptic keeps a different lens.

**Amy C. Edmondson, *Right Kind of Wrong: The Science of Failing Well* (2023).** New
York: Atria Books.

The Postmortem. Not all failures are alike. Ask the type first (basic, complex, or
intelligent), then the context, then whether the homework was done, then what the
smallest version is. Her earlier work on psychological safety is not this persona.

Where: `.agents/buzz/personas/postmortem/system_prompt.md`.

**A. G. Lafley and Roger L. Martin, *Playing to Win: How Strategy Really Works*
(2013).** Boston: Harvard Business Review Press.

The Strategist. Strategy is an integrated set of choices, not a plan: where to play, how
to win, which capabilities, which management systems. The persona passes on topics that
are not a strategic choice. That pass is part of the method. A voice that opines on
everything is not a strategist.

Where: `.agents/buzz/personas/strategist/system_prompt.md`.

**Quivy and Van Campenhoudt, as above.**

The Methodologist. Does not judge whether the thesis is right. Judges whether it is
stated so that it can be found out, and writes the path: research question, hidden
angle, concept, dimension, indicator, falsifiable hypothesis, cheapest observation.

Where: `.agents/buzz/personas/methodologist/system_prompt.md`.

---

## 5. Ideas the forms enforce

These are in the templates and the commands. The code describes the move and, except
where noted, does not name a book. The attribution is the standard source of that move.

**Jeff Bezos, 2015 Letter to Shareholders (published 2016).** Amazon.com, Inc.
[2015 Letter to Shareholders](https://s2.q4cdn.com/299287126/files/doc_financials/annual/2015-Letter-to-Shareholders.PDF).

Type 1 decisions are one-way doors: consequential, irreversible, and worth a slow
process. Type 2 decisions are two-way doors: reversible, and ruined by the same process.
`/decide` opens by asking what undoing the decision would cost. A two-way door gets
three lines and the advice to decide today. The full note is reserved for a one-way
door. The failure the gate is aimed at is the one the letter spends its length on:
treating a reversible call as if it were not.

Where: `.wiki/_commands/decide.md`, [docs/thinking-clearer.md](thinking-clearer.md),
[docs/commands.md](commands.md). The commands say "two-way door" and "one-way door".
They do not name the letter.

**Daniel Kahneman and Dan Lovallo, "Timid choices and bold forecasts: A cognitive
perspective on risk taking" (1993).** *Management Science*, 39(1), 17-31.

The outside view. A confidence figure has to start from a reference class and its base
rate, then adjust for the specifics and say what the adjustment was. A number that did
not start there is a mood, and `/decide` and `/calibrate` refuse to emit one. "Reference
class" is the name that later forecasting work uses for this same outside view. The
popular statement of "base rate, then adjust" is Philip E. Tetlock and Dan Gardner,
*Superforecasting* (2015), New York: Crown. The commands do not name either book. They
enforce the move.

Where: `.wiki/_commands/decide.md`, `.wiki/_commands/calibrate.md`.

**Norman Dalkey and Olaf Helmer, "An experimental application of the Delphi method to
the use of experts" (1963).** *Management Science*, 9(3), 458-467.

Independent judgments, then a round in which each person sees the others. Round one of
the dialectic runs each persona in its own thread, blind to the rest, because the first
voice in a room sets the frame. Round two quotes every reply and asks for the strongest
objection. A script counts the votes before the moderator writes a word. The code
describes that sequence and does not name Delphi.

Where: `tools/dialectic.py`, [docs/thinking-clearer.md](thinking-clearer.md).

**Daniel C. Dennett, *Intuition Pumps and Other Tools for Thinking* (2013).** New York:
W. W. Norton. Rapoport's Rules, in the chapter of that name.

The belief template tells you to steel-man the opposite, in writing, before the evidence
arrives, so the goalposts cannot move when it does. Steel-manning is the later name for
the charity rule Dennett states as Rapoport's Rules: re-express the other position so
clearly that its holder thanks you, before you criticise it. The template uses the later
name. It does not cite the book.

Where: `_Templates/Belief.md`, [docs/thinking-clearer.md](thinking-clearer.md).

**Howard Marks, *The Most Important Thing: Uncommon Sense for the Thoughtful Investor*
(2011).** New York: Columbia Business School Publishing. The chapter on second-level
thinking.

The decision template asks for second-order effects on each option: not "what happens",
but "what happens because of what happens". The template does not cite Marks. The field
is the move.

Where: `_Templates/Decision.md`.

**Christian Tietze, "The Collector's Fallacy" (2014).**
[zettelkasten.de/posts/collectors-fallacy](https://zettelkasten.de/posts/collectors-fallacy).

To have a note is not to know the note. `tools/resurface.py` picks old notes worth
re-reading against current projects, because a capture nobody sees again was a
collection, not a slip-box. The script names the fallacy. The essay is the source of
the name.

Where: `tools/resurface.py`, [docs/scripts.md](scripts.md).

**Ronald S. Burt, "Structural holes and good ideas" (2004).** *American Journal of
Sociology*, 110(2), 349-399.

A bridge is an edge between clusters, not another edge inside one. The pile scorecard
counts a concept article as a bridge only when its members come from two or more homes.
An article that restates a single home's notes is not a bridge, however many links it
carries. The code says "in Burt's sense" and implements that count. It does not
implement the paper's regression.

Where: `tools/wiki_prune.py`, [docs/method.md](method.md).

**Atlassian, epic / story / task.**
[atlassian.com/agile/project-management/epics](https://www.atlassian.com/agile/project-management/epics).

`tools/note_classify.py` labels a capture task, story or epic in this sense, not as a
literary category. The label is a gate. A research week runs only if the week produced
an epic. A model may demote a candidate. It may not promote a note the deterministic
gate refused.

Where: `tools/note_classify.py`, [docs/method.md](method.md).

**Mrinank Sharma and colleagues, "Towards Understanding Sycophancy in Language Models"
(2023).** Anthropic. [arXiv:2310.13548](https://arxiv.org/abs/2310.13548).

The scorecard's name for a critic who always agrees. The implementation is local and
mechanical, not a port of the paper: affirmation above 60 per cent over 30 days, a
persona that never votes NO, and a red line when all six cast the same first-round vote.
Six voices on one base model agreeing is a reason to check the framing, not a
confirmation.

Where: `tools/dialectic.py`, [docs/thinking-clearer.md](thinking-clearer.md).

**Paul Hammant, trunk-based development.**
[trunkbaseddevelopment.com](https://trunkbaseddevelopment.com/).

The sync policy names the pattern and adapts it for two machines rather than a human
team: one long-lived branch, linear history, fast-forward-only pushes, a single writer
per file, append-only for anything both touch. The incident that produced the file is
local. The pattern name is his.

Where: `_Agent-Context/TRUNK-BASED-DEVELOPMENT.md`.

---

## 6. Technical

The core is the Python standard library plus one pinned conversion library. Everything
else is a program the scripts call, or a surface you can read the same files in. None of
these are vendored.

### Substrate

**Markdown.** CommonMark ([commonmark.org](https://commonmark.org/), John MacFarlane),
GitHub Flavored Markdown, and Obsidian Flavored Markdown
([help.obsidian.md/obsidian-flavored-markdown](https://help.obsidian.md/obsidian-flavored-markdown)).
Every input becomes Markdown before anything else touches it. That is what makes grep
enough, makes a change a diff, and lets a model read the vault in the format it was
trained on. The cost, stated in the method page: conversion loses layout.

**Git.** [git-scm.com](https://git-scm.com/). The vault is a repository. Two writers are
serialised with `flock` (util-linux) against one lock. See the trunk policy above.

**Python 3.** The scripts use the standard library. `requirements-core.txt` exists so
the conversion step has one declared dependency and the rest do not grow by accident.

### Conversion

**markitdown, Microsoft, pinned at 0.1.7.**
[github.com/microsoft/markitdown](https://github.com/microsoft/markitdown).
Called as a library, one reused instance, not a subprocess per file. Extras are
`pdf`, `docx`, `xlsx`, `pptx`. The `all` extra is deliberately not used.

Where: `tools/markitdown_native.py`, `requirements-core.txt`.

**whisper.cpp, Georgi Gerganov.**
[github.com/ggerganov/whisper.cpp](https://github.com/ggerganov/whisper.cpp).
Local transcription. The binary is `whisper-cli`. Audio is resampled with **FFmpeg**
([ffmpeg.org](https://ffmpeg.org/)) to 16 kHz mono before it is transcribed. Silence
does not come back empty. It comes back as a hallucinated stock phrase, which
`tools/transcript_filter.py` exists to catch.

**OpenAI Whisper, the model whisper.cpp runs.** Alec Radford and colleagues, "Robust
Speech Recognition via Large-Scale Weak Supervision" (2022).
[github.com/openai/whisper](https://github.com/openai/whisper).
The engine does not call OpenAI's API for this. The weights run on the machine.

### Search

**ripgrep, Andrew Gallant.**
[github.com/BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep).
Candidate generation for wiki search. No index server.

**Stephen Robertson and Hugo Zaragoza, "The Probabilistic Relevance Framework: BM25 and
Beyond" (2009).** *Foundations and Trends in Information Retrieval*, 3(4), 333-389.

`tools/wiki_search.py` reranks ripgrep hits with a small BM25. The file calls it naive,
and it is: a ranking function in the script, not a search service. No embeddings, no
vector database. That refusal is a design choice, written in
[docs/scripts.md](scripts.md).

### Surface, not substrate

**Obsidian.** [obsidian.md](https://obsidian.md/).
Where a person reads, writes and follows `[[wikilinks]]`. The graph follows explicit
links. If Obsidian disappeared, the vault would still be a folder of files. Sync options,
including Obsidian Sync as one of them, are in [docs/syncing.md](syncing.md).

**Smart Connections, Brian Petro.**
[smartconnections.app](https://smartconnections.app/).
An optional community plugin. It shows related notes that do not yet have a written
edge. The engine does not require it and does not call it.

**JSON Canvas spec 1.0.**
[jsoncanvas.org/spec/1.0](https://jsoncanvas.org/spec/1.0/).
The canvas skill in `.agents/skills/json-canvas/` follows it. Canvases are a view. They
are not the record.

### Models

**Claude Code, Anthropic.** The default provider is the `claude` CLI
(`@anthropic-ai/claude-code`), a subscription sign-in, not an API key. Batch calls go
through `tools/llm.py` and deny execution tools, so a note cannot become a shell
command. The one exception is a research pass with `web=True`, which may search and
still may not touch the shell or the vault.

**OpenAI-style `/chat/completions`.** The other provider in `tools/llm.py`. It is an
interface, not a dependency on any one company. The docstring names Grok, OpenAI,
Together, Ollama and LM Studio as endpoints that speak it. Standard library only.

**Goose, Block, and llama.cpp, Georgi Gerganov.**
[github.com/block/goose](https://github.com/block/goose),
[github.com/ggerganov/llama.cpp](https://github.com/ggerganov/llama.cpp).
A third provider, for a lane that must not leave the machine. Default off. A lane pinned
local is pinned for privacy: a timeout is not consent to send the same text somewhere
else. What a small model can and cannot do is in
[docs/local-inference.md](local-inference.md), with measurements rather than promises.

### Conversation, schedulers, addons

These are not required for the loop. One laptop and a folder of Markdown is the
baseline.

**Buzz, Block.** [github.com/block/buzz](https://github.com/block/buzz).
The channel layer: receipts, the morning queue, the weekly question, the six voices as
live agents. Prompts and the installer are in `.agents/buzz/`. The engine also runs the
same argument locally with `brainless dialectic`, with no relay.

**Telegram Bot API.** [core.telegram.org/bots/api](https://core.telegram.org/bots/api).
An inbox only. Nothing is sent back over Telegram.

**systemd** (user timers, [freedesktop.org](https://www.freedesktop.org/software/systemd/man/systemd.timer.html))
and **launchd** (Apple's LaunchAgents). The same jobs, two formats. Units are in
`.agents/systemd/`.

**Google Tasks and Calendar APIs**, and the **Pipedrive API**. Addons. Tasks sync and a
pre-meeting brief; a read-only CRM snapshot. Neither is imported by the core. The CRM
seam is documented in [docs/addons/crm-pipedrive.md](addons/crm-pipedrive.md).

**Tailscale.** Used in the documented reference deployment so a laptop and an always-on
worker can see each other. Not a dependency of the engine. The deployment itself is in
the README, not here.

---

## 7. What is not a citation

A reader who has just been given a bibliography deserves the other half. These are
design rules of this repository. They are not taken from a paper, and looking for one
will not find it.

- The loop as one machine in four lines: gather, think, decide, get it done, and file the result back.
- Propose, never write. Commands paste a block. A person moves it, or it does not enter.
  The grade means something only if the decision stayed yours.
- The text-processing model does not get general file tools. Context is embedded in the
  prompt. Image reading is the narrow exception, and it receives one image.
- Epistemic labels (`verified`, `claim`, `unknown`) stripped in code before synthesis,
  rather than trusted to the model's manners.
- A research week only if the week produced an epic, and not again within eight weeks.
- The whitelist export. The public tree is a copy of a named list, plus a leak scan. It
  is never what remains after deleting from a private vault.
- The address format, the scorecard thresholds, and the three-item daily queue. The
  ideas they serve are cited above. The numbers are local.

The Watson line in the README is a metaphor for a recorder who asks the obvious
question. It is not a method, and it is not a product name.
