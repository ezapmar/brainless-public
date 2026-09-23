# Changelog

All notable changes to the public brainless engine. Dates are the day of the public
push. The private vault this is exported from has its own history.

## 0.5.0 (2026-09-23)

Search by meaning, and a wiki that proposes its own links.

- **Hybrid search.** `wiki_search.py` fuses BM25 with local embeddings
  (`tools/semantic_index.py`, multilingual-e5-large through fastembed, on the CPU; no vault
  text reaches an API). On the 34 golden questions MRR rose from 74 to 87 and hit@1 from 62%
  to 79%, better on every kind of question. An optional addon: `pip install -r
  requirements-search.txt`, then `python3 tools/semantic_index.py build`. Without it, search
  is BM25 as before. A smaller model and a reranker were measured and rejected.
- **Passage-level sources.** Every result carries the passage that matched, the line of the
  match and the last `[mm:ss]` marker before it, so an answer cites `path:line`. The snippet
  now folds Turkish letters on both sides; a Turkish query no longer shows the frontmatter.
- **Retrieval eval by kind.** The golden set grew from 22 to 34 questions, each tagged name,
  paraphrase, crosslang or claim, and the eval scores each kind apart and records the mode.
- **Read-only MCP server.** `tools/mcp_server.py` gives any MCP client `search`, `read_page`
  and `index`. Standard library only. Over stdio, or over HTTP bound to one address with a
  bearer token (`brainless-mcp.service` for the worker). It never serves a page git ignores,
  and a hard-coded deny list backs that up. It cannot write.
- **Link suggestions.** `tools/link_suggest.py` proposes links between pages that mean the
  same thing but do not link: mutual nearest neighbours in the top 1% of pairs. Homes for
  orphan pages first, then new links, then near-identical pages for the dedupe procedure.
  Report only, gitignored and out of the graph.
- **Dreaming.** `tools/dreaming.py` runs at 04:00 on the worker. A model judges each
  suggested pair (link, duplicate or none, with one sentence why) and posts up to five a
  night to Buzz `#dreaming`. The owner answers yes, no or skip. Approved links live in
  `_Agent-Context/links.md`, and the compiler replays them on every compile, so a rewritten
  summary keeps them. Duplicates become merge rows; nothing is deleted. A kill rule stops it
  when under 30% of proposals are approved. Setup: `.agents/buzz/install_dreaming_channel.sh`.
- **Documents to the inbox.** A PDF or office file posted to Buzz `#inbox` is converted
  with markitdown on the worker into `Inbox/Documents`; the original never reaches git.
- **Worker reach.** `tools/worker_reach.py` tells the Mac when it loses the worker
  (Tailscale down, ssh failing, or no worker commit for hours), with a local notification
  and a HEALTH.md row. The worker commits its run log twice an hour, not every run.
- **The wiki compiles every night.** The compile, lint report, search index, link
  suggestions and retrieval check moved out of the nightly digest into their own job
  (`tools/nightly_compile.py`, `brainless-compile.timer` at 23:20). They used to run only
  on nights with captures, so a quiet day left edits uncompiled and the index stale.
- The installer steps past systemd units already linked in place. The Mac's nightly sync
  refreshes the search index and the link suggestions.

## 0.4.1 (2026-09-23)

- `_Agent-Context/LEARNINGS.md`: the owner's standing preferences and the lessons from
  correcting an agent, each with the rule, why, and how to apply it. They live in the vault,
  not in one tool's memory. The SessionStart hook loads the file, and the writing and
  narratives agents and the Buzz reply worker read it. `AGENT-RULES.md` Rule 1 names it.
  The public export ships a stub with one example entry.

## 0.4.0 (2026-09-23)

- **Concept pages.** A summary says what one source said; a concept page says what the
  vault knows about one idea, and every new source updates it in place.
  - A disagreement keeps both positions with sources and dates.
  - An outdated claim is struck through, never deleted.
  - Every claim carries a confidence label.
  - `tools/concepts.py` validates each rewrite.
  - New concepts are proposed into `_Agent-Context/concepts.md` and wait for a yes.
  - The old articles phase is retired, and `--only concept-migrate` moves its pages over.
- **A connected graph.**
  - A deterministic linker ties summaries to the people, companies and concepts they
    mention. It holds back when a first name belongs to someone else.
  - INDEX.md gives one line per page, with topic indexes in `.wiki/_index/`.
  - Aliases in `_Agent-Context/aliases.md` catch the words people actually use.
  - Search folds Turkish letters.
  - A project mirror that shares a name with an entity becomes "(project)".
- **Measured.**
  - `tools/wiki_metrics.py` tracks the orphan rate, links per page, the main component and
    the bridge pages, week on week, in the scorecard and in HEALTH.md.
  - `tools/retrieval_eval.py` scores golden questions every night.
  - `tools/wiki_dedupe.py` proposes merges and never merges.
  - `brainless graph` writes a Gephi file coloured by page type.
- **Checks, not requests.**
  - Claude Code hooks (`tools/hooks/claude_guard.py`) ask before human-area writes and
    deletions, and refuse secrets and misplaced briefings.
  - `tools/output_guard.py` refuses model chatter and nested pages before they are
    written.
  - `tools/run_log.py` records every scheduled run with its counts, so a job that ran and
    did nothing shows up.
  - The nightly job no longer swallows a failed digest or the compiler's exit code.
- **New doors.**
  - `brainless media` turns YouTube and Apple Podcasts episode links into timestamped
    transcripts, queued and transcribed locally.
  - `brainless chats` brings Claude or ChatGPT history in, triaged and filtered first.
- **Backups.** `brainless backup` seals the vault and its history with `age` once a month,
  into a folder sync does not reach. `verify` is the restore test.
- **Answer rule.** Answers from the vault cite a page for every claim, keep outside
  knowledge under its own heading, and end with what was read and what is not covered.

## 0.3.2 (2026-09-22)

- The docs follow the README's four lines. Each page opens with the line it serves
  (gather, think, decide, get shit done) or says it is setup. `docs/how-it-works.md` is
  regrouped under the four lines with a new get-shit-done section, and `docs/scripts.md`
  names its sections the same way.
- "Every command proposes and you paste" becomes "the machine proposes and you apply",
  and "the same five steps" becomes "the same loop", wherever they appeared.
- Link labels in `docs/commands.md`, `docs/thinking-clearer.md` and `docs/scripts.md` read
  as page names, and `docs/syncing.md` points at the reference setup in
  `docs/how-it-works.md` instead of a README section that no longer exists.

## 0.3.1 (2026-09-22)

- The README is rewritten for people, not for the code: the problem, the solution and
  four lines (gather, think, decide, get shit done), then the install. The long
  technical half moves to `docs/how-it-works.md` and the wiki, where the README links.
- `docs/references.md`: the books, papers and tools the method, the voices and the
  engine are built on, each with what was taken and where it shows up. Also a wiki page.
- `setup.sh` is gone; `install.sh` has done its job since 0.1. Unused weekly-question
  labels are dropped from `tools/locale/*/thinking_loop.json`.

## 0.3.0 (2026-09-21)

- Writing and narrative agents: `.agents/buzz/install_agent_channel.sh <slug>` installs a
  conversational Buzz agent with its own channel from a card in `.agents/buzz/agents/`,
  pinned to a model and effort level (`BUZZ_AGENT_MODEL`, default `claude-fable-5-1`).
  Two cards ship: Writer (`#writing`, long-form pieces) and Narrator (`#narratives`,
  children's stories). Each writes only inside its drafts folder, loads the editing
  files first and runs the editor lint before every draft.
- `tools/writing_index.py` compiles `_Agent-Context/WRITING.md`, the map of every
  long-form asset; `tools/writing_ideas.py` posts a pitch round to `#writing` every
  second month. New `PROFILE.md` fields: `editor_dir`, `writings_dir`, `drafts_dir`,
  `narratives_dir`, `longform_dirs`, `corpus_dirs`.
- The polling reply worker leaves harness channels alone (`HARNESS_CHANNELS`), so an
  owner message in `#writing` gets exactly one reply.

## 0.2.0 (2026-09-21)

- Telegram is capture-only; receipts and errors arrive in Buzz `#inbox`. Nothing is
  sent back over Telegram any more: no replies, no buttons, no `/today`. Old Telegram
  message ids stop authorising writes; pending previews are re-posted to Buzz and must be
  approved there. This is the reason for the minor version bump.
- Today and weekly thinking use owner-only Buzz threads with revision-bound
  approval, pending-preview migration and recoverable writes.
- Notifications use a durable Buzz outbox with relay acknowledgement and lost-ack
  reconciliation. The interaction timer retries failed delivery and saved replies.
- Added delivery, workflow and capture-only regression coverage. See
  [Buzz interactions](docs/buzz-interactions.md) for cutover and recovery.

- Give the nightly compile a wall-clock budget so a backlog at the front cannot eat
  the whole window. Summaries, projects and entities check the clock before each file;
  articles and ideas reserve the seconds their one call needs. The index always runs.
  The nightly passes most of its timeout to the child as `--budget-seconds` and runs
  it unbuffered, so a killed run still leaves a journal of where it stopped.
- `health_check.py` notifies with `osascript` on macOS and `notify-send` on Linux, and
  skips the call when neither is present, so a RED report on the worker no longer
  crashes the run after writing the file.
- `build_dashboard.py` reads last-log dates from the Log section only, and counts both
  `### YYYY-MM-DD` headings and `- [YYYY-MM-DD]` list lines. A project that dates its
  log as a list no longer looks months stale.

- The method page now lists the seven Quivy steps with the file each one maps to, and
  adds the seventh (the pass ends in a seed and, when it settles one, a decision note),
  which the research command already enforced but the page never said. The dated audit
  it used to carry moves here: on 2026-09-19 all ten seeds in `Thinking/Ideas/` lacked
  a `zk:` address while all six compiled ideas had one, the wrong way round;
  `tools/zk_id.py --apply` is the fix. The page no longer cites a tool that does not
  ship (`editor_lint.py`), names a reading route for Zettelkasten, and the README's
  Install section gains an "Optional inputs" note for voice (ffmpeg + whisper.cpp) and
  photographs (a backend with vision).
- Count the pile, then drain it. `tools/wiki_prune.py --count` writes
  `_Agent-Context/PILE-SCORECARD.md` every Sunday with no model: captures per graded
  decision, filed analyses per decision, Inbox files past fourteen days (the capture
  belief's own falsification line), orphan wiki pages, concept articles that bridge two
  homes, decisions and challenged beliefs in the window. `--archive` applies mechanical
  rules with a stated reason, dry run by default: an unlinked analysis after 30 days, a
  summary whose source left the compiled roots after 60, any orphan after 60, and a
  meeting report that has been summarised and mined after 14 days in Inbox. Nothing is
  deleted; every move is logged to `.wiki/_archive/LOG.md` with its rule and reason, and
  the archive is out of search, index and lint. `health_check.py` carries the Inbox count
  into the briefing, red above ten. `lint_wiki.link_graph()` is now the one definition of
  "linked" for lint and prune alike.
- The nightly digest reads the epic/story/task label before it reads the note: a task
  contributes its action items and no narrative, a story three lines, an epic is read in
  full. The digest runs the classifier first so tonight's captures are labelled tonight,
  caps the raw text it reads, and the task ledger applies the same near-duplicate guard
  the meeting extractor had (`tools/task_dedup.py`), so a promise rephrased by the model
  does not become a second row.
- A sixth persona, Methodologist, built on Quivy and Van Campenhoudt's research
  method: it rewrites the thesis as a research question, names the hidden angle,
  builds concept, dimension and indicator, and writes the falsifiable hypothesis
  and the cheapest observation plan. Every persona now receives what the moderator
  received (the topic's wiki hits, the owner's core beliefs, the decision calendar)
  and round two must cite a vault file. Six sessions at once strain a single
  subscription, so personas are mentioned one at a time by default; `--parallel`
  restores the old behaviour.
- Each dialectic topic is filed as two pages and a folded transcript. Page one is
  what a decision needs: a verdict computed from the final votes (Go, Stop or Test
  first, with the median number), the moderator's conclusion and fields, a compact
  vote table and the proposal. Page two is the method trace: the research question,
  the hypotheses and tests, one row per persona with its finding and objection.
  The raw rounds sit under a collapsed callout in the same note. The unanimity
  warning is a callout, not an HTML span. `/dialectic` produces the same layout.
- `--run night` and `brainless-dialectic-night.timer`: a 02:00 experiment that runs
  the persona lane on the worker's local model while the moderator and a new
  `dialectic-judge` lane stay on the cloud and grade each local reply. Replays the
  day's first topic for a like-for-like comparison, scores nothing, and keeps its
  own section in the status file with a five-night kill rule.
- Add epic-triggered weekly research. `tools/note_classify.py` tags every capture
  in the daily and meeting folders as epic, story or task in the Atlassian sense,
  and `tools/weekly_research.py` runs a research cycle only when the week produced
  an epic. A week of stories and tasks costs nothing: the job exits in seconds
  without calling a model. The epic gate is deliberately conservative (a score,
  several independent signals, and either recurrence or breadth), targeting zero
  or one epic per week, and an epic already researched within eight weeks is not
  researched again: the recurrence is reported instead, because a topic that keeps
  returning without closing is waiting for a decision, not for more evidence.
- Research follows the vault's Quivy method: one opening question, the lens named
  in one sentence before any hypothesis is written, falsifiable hypotheses, a
  single salvo of at most five sources each on a different angle, then an
  interpretation of the deviations. Naming the lens is the book's problematique
  step, and it is what keeps three hypotheses serving one question instead of
  scattering across three. Every finding must carry
  `Kaynak: <URL>` and one of `verified`, `claim` or `unknown`; unlabelled lines
  are deleted mechanically before synthesis and `unknown` lines may not be used
  as support. When there is no evidence the report says so, because absence of
  evidence is not evidence.
- Open the research lane in `tools/llm.py`: `run_prompt(web=True)` grants
  WebSearch and WebFetch for that one call. The execution tools stay denied even
  then, and the fetched text reaches the synthesis pass fenced as untrusted data,
  so the furthest a poisoned page can reach is a wrong sentence in a report.
- Add a `goose` provider to `tools/llm.py` for local inference on the worker,
  invoked with `--no-profile` so it loads no extensions and has no shell. This is
  the goose-side equivalent of the deny list.
- Route the model per lane, not per installation. Every call site now names a
  lane (`python3 tools/llm.py --lanes`), and `BRAINLESS_LLM_PROVIDER_<LANE>`
  points that lane at its own provider, so a note classifier answering with one
  word out of three can run on hardware you own while a weekly synthesis still
  goes to a large model. Two reasons, pulling in opposite directions: privacy
  wants the local model as a lane's primary, resilience wants it as a fallback
  for the cloud lanes (`BRAINLESS_LLM_FALLBACK=goose`), and both are the same
  setting seen from different sides.
- The fallback only goes one way. Cloud may degrade to local; local never
  escalates to cloud, because a lane pinned local was pinned for privacy and a
  timeout is not consent to send the same text somewhere else.
  `BRAINLESS_LLM_ALLOW_CLOUD_FALLBACK=1` says otherwise, explicitly, per machine.
- Log the routing: `.agents/state/llm_log` keeps the last 300 calls with lane,
  provider, outcome and wall time, next to the single latest outcome in
  `llm_status` that the health check reads. `tools/llm_bench.py` times the real
  prompt shapes against a provider, because whether a local model is viable is a
  measurement, not an opinion.
- Guard the task ledger deterministically in `spiky_actions.py`. The prompt asked
  the model not to repeat a task already in the ledger, which is a string
  comparison wearing a prompt; a duplicate is now dropped after the model, with
  Turkish suffixes folded, so a weaker local model cannot send the same promise
  to a person twice.
- State the base rate in the epic rubric: an epic is about one note in twenty.
  Found by measurement, not by taste. Without it a local 4B model promoted four
  notes out of eight to epic that Opus called stories, always in that direction,
  which is the expensive one because an epic triggers a research pass. With it
  the two models agreed on all eight, and both still called an unmistakable
  multi-month programme an epic, so the sentence tightened calibration without
  turning the classifier into a constant.
- New guide: `docs/local-inference.md`, including what the worker hardware can
  actually do, how to seed a model over a slow link, and the order in which
  lanes should move.
- Add `_Agent-Context/SOURCES.json`, a machine-readable source registry. Scripts
  may only append to `candidates`; promoting a candidate is the owner's call, at
  most one new stream per month, and a source that has fed nothing for eight weeks
  is flagged rather than removed.
- Add `tools/lang_detect.py` and answer in the language that was used. Research
  reports follow the triggering note, and the dialectic engine now argues each
  topic in the language of the notes behind it instead of forcing English.
- Reframe both READMEs around connectivity and the three layers (collect, think
  clearly, decide), including the failure mode the layering guards against, and
  correct the origin of the name.

## 0.1.6 (2026-09-18)

- Add `docs/scripts.md`: a reference for every Python script in `tools/` and
  `.agents/scripts/`, each with a definition, a description and the design
  philosophy behind it, plus a map of how the scripts relate. Linked from the README.

## 0.1.5 (2026-09-18)

- Add the Today queue: at most one decision, one commitment, and one evidence
  review. Telegram supports preview, apply, edit, defer, and dismiss. Repeated
  deferrals ask for a blocker or smaller step; seven-day completion counts and
  source links appear in TODAY.md. The existing morning reminder sends the queue.
- Record approved decision outcomes in calibration and recover interrupted Today
  writes without duplicating entries. See `docs/today-queue.md`.
- Preserve original documents and raw conversions after processing. Failed,
  missing, or empty AI summaries now trigger retries; generated output is
  validated before replacing an existing summary.
- Apply project privacy checks to every descendant source file.
- Rebuild project mirrors when any permitted source changes, is added, renamed,
  or removed. Existing mirrors rebuild once under the new dependency policy.
- Keep kill criteria out of dashboard actions and open loops. Prefer explicit
  Next Action sections, now included in the project template.
- Add offline regression tests for document retention, project privacy, dashboard
  actions, and the Today queue; run them in public CI.
- Rewrite the README around one loop and add `docs/capture-flow.md` on the
  capture flow and the Obsidian network.
- Keep private finance tooling out of the export. `tools/export_public.py` now
  excludes the finance folder, because a fixture of real figures passes any word
  scan. Deployment-specific agent rules move to a private companion file that
  `_Agent-Context/AGENT-RULES.md` points to and the export never lists.
- Workers pull with `--autostash`, the cron wrapper commits its regenerated
  context blocks locally so the tree does not sit dirty, and the content engine
  reads the editing rules alongside the production guide.
- `VERSION` catches up: 0.1.4 shipped with the file still reading 0.1.3.

## 0.1.4 (2026-09-15)

- Dialectic scorecard. Round one is isolated (one Buzz root per persona, so nobody
  reads anyone else before answering); round two is one root that quotes every
  round one reply. Replies end with `Vote:` (YES, NO, CONDITIONAL) and `Number:`
  (NN%), round two also with `New evidence:`. `score_topic()` turns those lines into
  a deterministic per-topic scorecard (affirmation rate, who moved and whether they
  cited evidence, a unanimity warning) that is filed with the note, posted to the
  channel and fed to the synthesis. Rounds append to
  `.agents/state/dialectic_scores.jsonl`; the worker writes the rolling 30 day view
  to `_Agent-Context/DIALECTIC-SCORECARD.md` with two flags: sycophancy
  (affirmation above 60 percent) and a persona that never votes NO.
  `brainless dialectic --scorecard` prints it. Persona rules and the `/dialectic`
  command carry the same headings; redeploy the personas after upgrading.
- Kill criteria. Every project `notes.md` may carry a `## Kill Criteria` section of
  `- [ ] YYYY-MM-DD | condition | consequence` lines (the project template has it).
  `tools/kill_criteria.py` scans them deterministically: a past date with an open
  box is a breach. Breaches turn the new health check row red, are written to
  `_Agent-Context/KILL-CRITERIA.md` for the briefing, lead the dashboard's new
  Kill criteria section and the think surface's provocation; PROJECTS-ACTIVE
  shows each project's next criterion, and projects without one are listed.
- Vault topic fallback: on a day with no captures the evening dialectic argues one
  thing the vault is waiting on (a decision past review, a decided note without a
  prediction, a pending decision near review, a stale belief, a live question),
  rotated with a 14 day cooldown, instead of idling.
- Health check: a "Morning briefing" row goes yellow on weekdays after 08:30 when the
  day's briefing is missing or lacks the health block.
- Think surface: the cadence step says how many days it has waited when it exceeds
  two weeks.
- Every thinking command prompt now ends with a Loopback step that files its
  output through `file_query.py`.

## 0.1.3 (2026-09-11)

- The engine is English end to end: every comment, docstring, log line, help
  string, shell message, prompt and convention document. No Turkish left in code.
- Output language is a setting, any language: `output_lang` in `PROFILE.md` takes
  any ISO code. LLM prompts carry a language directive; deterministic strings
  (health labels, bot replies, generated headings, section names) come from
  `tools/locale/<code>/<script>.json` through `tools/i18n.py`, English as the
  fallback. Shipped locales: `en`, `tr`. Add a directory to add a language.
  See `docs/localization.md`.
- Scripts that parse headings they wrote earlier accept both the current
  language and English, so existing vaults keep working.
- Buzz identities renamed to English: `briefing`, `thinking`, `tasks`, `content`
  (key files under `~/.config/brainless/buzz/keys/`), and the owner env file is
  `assistant.env`. Existing relays rename the files once.
- Installer accepts any two or three letter language code.
- Document conversion calls markitdown as a Python library
  (`tools/markitdown_native.py`), not the CLI: one reused converter instance, no
  PATH discovery, no per file subprocess. `smart_processor.py` and
  `batch_markitdown.py` share it.
- No em or en dashes anywhere in the tree; the leak scan and CI stay as before.

## 0.1.2 (2026-09-11)

- CRM snapshot addon: read-only pull from Pipedrive into `_Agent-Context/CRM.md` and
  per-organisation event logs under `Inbox/CRM/`. Organisation and deal level only,
  no person data, no LLM. Provider seam for other CRMs. Systemd timer for the worker,
  hourly run on the laptop. Documented with a Pipedrive example in
  `docs/addons/crm-pipedrive.md`.
- Health check row for the CRM snapshot, and an optional Buzz `#crm` channel
  (`install_crm_channel.sh`, `buzz_crm_sync.sh`) that receives the block when it changes.
- `buzz_post.sh` no longer needs coreutils `timeout` on macOS.
- Flag severity: an organisation without an open deal is never red; red is reserved
  for deals.
- `VERSION` file and `brainless version`.
- `docs/` ships with the export.

## 0.1.1 (2026-09-10)

Not tagged at the time; tagged retroactively on the last commit of the day.

- One-line installer (`install.sh`) and the `brainless` command.
- README rewritten around the decision loop and the five voices, with the real
  three-device setup as a usage example.
- Calibration (`brainless calibrate`) and the think-surface command.
- Transcript filter, dialectic trigger, failure alerts on the worker.
- CI leak-scan gate: every push and pull request runs the export scan.

## 0.1.0 (2026-09-09)

- First public export: engine, commands, personas, installer, MIT license.
