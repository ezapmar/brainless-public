# How brainless works

The short version is in the [README](../README.md). This page is the long one: the
loop with its mechanics and file names, the privacy rules, the addons and the exact
setup I run.

## The loop

Four lines, with the mechanics and the file names. brainless is one loop, and each part
is there because the one before it is useless alone. Notes that nobody reads are a
warehouse. Summaries that nobody argues with are a tidier warehouse. And an argument that
nobody grades is entertainment, which Galatasaray already provides.

```mermaid
flowchart LR
  G["1. Gather information<br/>capture everything, the machine reads"]
  T["2. Think deeper and clearer<br/>ask what you know, six voices argue"]
  D["3. Decide<br/>write a bet, grade it later"]
  X["4. Bonus: get shit done<br/>promises become tasks, three a morning"]
  G --> T --> D --> X
  D -->|"filed back, so tomorrow builds on today"| G
```

### Three layers, and the failure they guard against

The bet underneath all four lines is connectivity. A note on its own is storage. Notes
wired to each other are a map, and the value shows up when you move across it: an
argument from a meeting in May lands beside a belief written in March and the two change
each other.

Connectivity alone fails in a predictable way. A system that touches every subject and
holds none of them is very good at sounding informed and no use at all when something
has to be decided. So the loop is read as three layers, each one worth its cost only if
it feeds the next.

1. **Collect.** Everything comes in through one door and the machine reads it. Line 1
   below.
2. **Think clearly.** The point is not a tidier disk, it is what you can hold in your own
   head afterwards, which is why the vault argues instead of agreeing. Line 2.
3. **Decide.** A small number of dated calls, each carrying a prediction and a review
   date. Line 3.

Line 4 is the bonus: the promises that fall out of all three, kept where they cannot be
lost.

### 1. Gather information

#### Capture everything, filter later

Handwritten pages, voice notes, photos, links, documents, half sentences, Telegram
messages and Buzz `#inbox` posts all come in through the same wide front door. They land
in `Inbox/`, `Inbox/Links/` and `Thinking/Daily/` as Markdown, and nothing is judged on
the way in. Judging a thought at capture time is how good ones die in a car park. The
[capture flow guide](capture-flow.md) maps every entrance and every scheduled job.

#### Let the machine read so you do not have to

A pile of captures is only useful if somebody reads it, and I was never going to be that
somebody. So a compiler turns every note into a summary, clusters summaries into
articles, mirrors project status and writes a daily digest into `.wiki/`. Documents keep
their originals and their raw Markdown conversions. The high-value ones also get a
Summary and a Fiche de Lecture, and a missing or empty output is retried instead of
quietly accepted.

Obsidian is the surface, not the substrate. **Every input becomes Markdown** before
anything else touches it: documents through markitdown called as a library, audio
through whisper on the machine, photographs read into text. A converted source keeps
two files, the mechanical `_raw.md` conversion and an authored note beside it, so a
later reader can always tell evidence from thinking. That choice is what makes grep and
BM25 enough instead of an index server, makes every change a git diff, and means the
vault outlives every tool in this repository. Graph view follows the explicit
`[[wikilinks]]`, and Smart Connections shows related notes that do not have a written
edge yet. You own the notes. The machine owns `.wiki/` and can rebuild it from scratch
whenever it likes, which means nobody has to spend a Sunday curating a knowledge graph.

The method underneath is **Zettelkasten**: one concept per note, a permanent `zk:`
address that survives every rename, links rather than folders as the structure, and an
archive that tells you which note it wants next. `/backlog` is that last part made
mechanical, counting unresolved links and handing back a ranked queue of notes the vault
is asking for. The passes a note goes through afterwards, and why judging is postponed
at each one, are written up in [the method](method.md). The books, papers and tools
behind the method, the voices and the engine are listed in
[References](references.md).

### 2. Think deeper and clearer

#### Argue before you act

Six personas test a thesis in two rounds. In round one each persona gets its own
thread, applies its own method without seeing the others, and votes YES, NO or
CONDITIONAL with a probability. In round two each reads the rest, picks the strongest
objection, answers it, votes again and names the new evidence that moved it, or says
"none".

```mermaid
flowchart TD
  T["Thesis<br/>yours, or clustered from today's notes"]
  R1["Round one, isolated<br/>Skeptic, Gambler, Scientist, Postmortem, Strategist, Methodologist<br/>each in its own thread, blind to the others"]
  R2["Round two, quoted<br/>answer the strongest objection, vote again,<br/>name the new evidence or say none"]
  SC["Scorecard, by script<br/>affirmation rate, who moved, evidence cited,<br/>unanimity warning"]
  MOD["Moderator synthesis<br/>best counterargument, what would have to be true,<br/>a cheap dated test, a probability,<br/>any clash with your own beliefs"]
  F["Filed into .wiki/digests/queries/"]

  T --> R1
  R1 -->|"YES, NO or CONDITIONAL, with a probability"| R2
  R2 --> SC
  SC --> MOD
  MOD --> F
```

A script scores the round before the moderator writes a word, so the synthesis starts
from counted votes and not from a mood. A rolling 30 day scorecard raises a flag when
affirmation climbs above 60 per cent, or when a persona never votes NO. Six voices that
always agree with you are a fan club, and I did not need software for that.

The voices come from the shelf.

| Persona | Book | What it asks |
|---|---|---|
| Skeptic | Browne and Keeley, *Asking the Right Questions* | What is the conclusion, what are the reasons, which words are ambiguous, what is omitted, what other conclusions are possible |
| Gambler | Annie Duke, *Thinking in Bets* | Write it as a bet. How confident, in a number. Is this outcome or decision quality. Twelve months on, it failed: why |
| Scientist | Camuffo, Gambardella et al., *A scientific approach to entrepreneurial decision-making* | What is the theory, what must be true, what is the cheapest test, what result means terminate and what means pivot |
| Postmortem | Amy Edmondson, *Right Kind of Wrong* | If this fails, is it basic, complex or intelligent failure. Was the homework done. What is the smallest version |
| Strategist | Lafley and Martin, *Playing to Win* | Where to play, how to win, which capabilities, which systems. Passes on topics that are not strategy |
| Methodologist | Quivy and Van Campenhoudt, *Manuel de recherche en sciences sociales* | Rewrites the thesis as a research question, names the hidden angle, builds concept, dimension and indicator, writes the falsifiable hypothesis and the cheapest observation plan |

They never mention each other and only answer the moderator and you, so agents cannot
set each other off. It is the best-behaved meeting in my week. Each persona's prompt is
a Markdown file in `.agents/buzz/personas/`. Edit them. Add a seventh. The moderator does
not care how many there are.

#### Research, but only when the week earns it

The loop above runs on what you already know. Sometimes a topic turns up that the vault
cannot answer, and the tempting move is to point agents at the internet and read the
essay they come back with. That produces something plausible every time, which is the
problem with it.

So research here follows a social science method, taken from Quivy and Van Campenhoudt,
*Manuel de recherche en sciences sociales*, and adapted for a vault and a set of agents.
The parts the code enforces:

- **The opening question is written first**, in one sentence, before any source is
  opened. Every term defined, answerable with the access you actually have, and a real
  question rather than one whose answer is implied by how it is asked.
- **The lens is named before the hypotheses**, in one sentence: which angle the research
  looks through and what it is trying to explain. Without it a set of hypotheses
  scatters across three unrelated questions, each defensible, none adding up.
- **Hypotheses are falsifiable**, in the same shape as a decision's prediction and
  confidence, which is what lets a research pass be graded later instead of admired.
- **Reading happens in salvos**, at most five sources each, every one on a different
  angle, with interpretation in between. Unlimited fan-out is the modern form of the
  trap the book calls insatiable reading.
- **Every finding carries `Kaynak: <URL>` and one of `verified`, `claim`, `unknown`.**
  Unlabelled lines are deleted mechanically before synthesis, `unknown` lines may not be
  used as support, and when there is no evidence the report says so, because absence of
  evidence is not evidence.
- **The analysis interprets the deviations** between what was expected and what was
  found. A report that only confirms was not testing anything.

Two guards keep it rare. A pass runs only if the week produced an **epic**, so a week of
ordinary work costs nothing and the job exits in seconds. And a topic already researched
within eight weeks is not researched again: the recurrence is reported instead, because
a question that keeps coming back without closing is waiting for a decision, not for
more evidence.

The full adaptation, the three acts and the seven steps each mapped to a file, is in
[the method](method.md).

#### Where the clarity comes from

Nothing above makes you smarter. It changes what you have to write down before you may
move on, and the order you see things in. Criteria before options, so a favourite
cannot write its own test. A base rate before a confidence, so the number is a number.
Five readings taken blind, so the first voice does not set the room. One dated action
at the end, so a decision cannot pass as an opinion. A red line when all five agree.
Each mechanism, with the file it lives in, is in
[docs/thinking-clearer.md](thinking-clearer.md); every command, with what it asks
and what it guards against, is in [docs/commands.md](commands.md).

---

### 3. Decide

#### Write what you believe and what you decided, as bets

Reading is still not thinking. The compiled layer can tell you what you wrote, but it
cannot tell you what you hold to be true, so that part is typed by hand. Beliefs live in
`Thinking/Beliefs/`, each with a line for "what would change my mind". Decisions live in
`Thinking/Decisions/`, each with a prediction, a confidence and a review date.

The format matters more than it looks. A bet can lose, and something that can lose can
be argued with.

#### Grade yourself

An argument ends in a bet, and a bet is worth nothing until somebody settles it. When a
review date passes, the system nags until you write the outcome. `brainless calibrate`
lists what is due, and the [Today queue](today-queue.md) keeps the daily ask small.
`brainless today` offers at most three items: a decision, a commitment and one piece of
evidence to review, each with a link to its source. Answers are previews until you apply
them. You can defer an item to a date or dismiss it with a reason, and the existing
morning worker sends the queue to Buzz #tasks, so there is no new timer to install.

Ten years of ungraded decisions is one year repeated ten times. If you do only this
step, you are ahead of most people I know. Including me, for most of this year.

Every analysis, graded or not, is filed back into `.wiki/digests/queries/`. That is
where the loop closes, because tomorrow's question gets to build on today's answer.

### 4. Bonus: get shit done

Decisions come with promises attached, and promises are the first thing to fall out of a
busy head.

- **Promises become tasks.** Commitments from meeting reports and the day's captures are
  appended to the task ledger in `_Agent-Context/TASKS.md`, with a near-duplicate check so
  a promise rephrased on a later day does not become a second task. An optional sync keeps
  the ledger and Google Tasks in step both ways.
- **At most three things a morning.** `brainless today` offers a decision due for
  grading, the oldest open commitment and one piece of evidence to review, each with a
  link to its source. Empty categories stay empty, and finishing an item does not refill
  its slot. See the [Today queue](today-queue.md).
- **Answer in a thread.** The queue, reminders and the weekly question arrive in Buzz.
  Answers are previews until you apply them: "apply" writes the note, "defer to Friday"
  moves it, "dismiss" drops it with a reason. See [Buzz interactions](buzz-interactions.md).

---

## Keep it yours

All of it runs on your own hardware: the scripts, the schedulers, the vault, the search
index. The only thing that ever leaves the machine is a prompt to whichever model you
configured, and pointing it at Ollama or LM Studio keeps even that at home.

You do not have to choose once, for everything. Each call site names a **lane**, and a
lane can be routed to its own model: the short private ones, like deciding how large
the work in a note is, on a model running on your own box through the `goose` provider,
while the long prose stays with a large model. The reverse is also useful, a cloud lane
falling back to the local model when a subscription expires or the network is down. The
fallback only goes that way: a lane pinned local was pinned for privacy, and a timeout
is not consent to send the same text somewhere else. `python3 tools/llm.py --lanes`
shows where each one currently goes, and [docs/local-inference.md](local-inference.md)
covers what a small model can and cannot do, with measurements from a laptop-class
worker rather than promises.

A loop that runs unattended against a folder holding your life needs one rule above all
the others, which is that nothing writes into your notes without you. The machine
proposes and you apply. Private folders never reach the compiled layer. The engine and
the notes are separate repos, so you can share one and keep the other.

The rule is enforced by who owns which folder.

| Folder | Owner | What lives there |
|---|---|---|
| `Work/`, `Personal/`, `Library/` | you | projects, life, reference material |
| `Inbox/` | you | unsorted capture, triaged later |
| `Thinking/` | you | `Daily/`, `Ideas/`, `Beliefs/`, `Decisions/`, calibration |
| `.wiki/` | the machine | compiled layer, regenerable, never hand-edited |
| `_Agent-Context/` | shared | `PROFILE.md`, context, tasks, health, the rules agents follow |
| `_Templates/` | shared | note templates |
| `tools/` | engine | compiler, lint, search, filer, calibration, dialectic |
| `.agents/` | engine | scheduled jobs, systemd units, persona definitions |

Every script in `tools/` and `.agents/scripts/` is described in the
[scripts reference](scripts.md): what it is, what it does, and why it is built the
way it is.

A project is any folder under `Work/` or `Personal/` with a `notes.md`. Project mirrors
track changes across all permitted source files and exclude private descendants. Use
`## Next Action` in a project note for its next concrete step. `## Kill Criteria` stays
separate on the dashboard, because the condition under which you would stop is a
different thing from a task.

**The text-processing LLM never gets general file tools.** Context is embedded in the
prompt. It cannot read, write, move or delete anything. Image OCR is the narrow
exception, and even there the image reader receives only the specific image it needs.
That is the reason a script can run unattended against a folder that holds your life.

Inside that fence the LLM is the connective tissue of the loop. Local whisper.cpp
transcribes voice. Claude reads handwritten images and cleans the text, fixes names
against the current context, and adds links between a capture and the notes it touches.
The original image, source document or raw conversion stays recoverable, and a failed
model call does not erase the input.

Privacy follows from the same separation.

- The repo you clone contains no notes. Content folders are gitignored in the engine
  repo; your vault is wherever `BRAINLESS_VAULT` points.
- Identity papers, health folders and official documents are ignored anywhere in the
  tree by default. Add your own folder names to `private_segments` in `PROFILE.md` and
  they will never be compiled.
- This public repo is produced from my private vault by `tools/export_public.py`, a
  whitelist copy plus a leak scan that refuses to pass on identity numbers, keys, tokens
  or a name. If you fork this for your own vault, use the same script.

---
## Addons

The loop above runs on one laptop with none of these. Each one removes a specific
friction, and each needs its own credentials in `~/.config/brainless/`.

- **Telegram capture.** Voice notes, photos, links and text from your phone become
  `Thinking/Daily/` notes. Transcription runs locally with whisper.cpp. It is an inbox
  only: nothing is sent back over Telegram.
- **Buzz conversation.** Everything the system says to you goes through Buzz channels:
  capture receipts in `#inbox`, the Today queue in `#tasks`, the weekly question in
  `#thinking`, alerts in `#ops`. You reply in the thread; `apply` on the latest preview
  is the only way an automated path writes a human note. Messages wait in a local outbox
  until the relay acknowledges them. See [Buzz interactions](buzz-interactions.md).
- **Writing and narrative agents.** Two more channels, `#writing` and `#narratives`,
  each with a live agent that drafts long-form pieces or children's stories in
  conversation with you. It reads the whole vault, writes only to its drafts folder, runs
  your editing lint before every draft and reports the counts. A weekly writing map and a
  pitch round every second month feed it. See [Writing agents](writing-agent.md).
- **Buzz personas.** The six voices as live agents on a self-hosted
  [Buzz](https://github.com/block/buzz) relay. They answer in a channel twice a day and
  whenever you mention them. This is how I run it; `.agents/buzz/` has the prompts and
  the installer.
- **Google Tasks and Calendar.** Two-way task sync and a brief before each meeting.
- **Meeting transcripts.** An IMAP ingest for meeting report e-mails.
- **CRM snapshot.** A read-only pull from your CRM (Pipedrive first; the provider
  seam in `.agents/scripts/crm_capture.py` takes others) into a status block the
  briefing copies and an event log per company under `Inbox/CRM/`. Organisation
  and deal level only, no people, no LLM. Drop your token in
  `~/.config/brainless/pipedrive_api_token`; without it the script is a no-op.
  Setup, flags, example output and the provider seam:
  [docs/addons/crm-pipedrive.md](addons/crm-pipedrive.md).
- **An always-on worker.** A second machine that runs the timers, pulls, commits and
  pushes, so your laptop can sleep. The units in `.agents/systemd/` are built for it, and
  every network job waits for the connection first, so a worker that sleeps and wakes
  never spams failures for the seconds it takes the network to come back.

---

## Usage example: our real setup

This is the exact deployment I run, on three devices. Nothing here is invented. The only
edits are to secret values. Tokens, keys, relay URLs and the Tailscale address are shown
as placeholders, because they must never be committed. Everything else (the units, the
paths, the schedule) is what actually runs.

- **iPhone 16 Pro.** Capture and conversation. A Telegram bot receives voice notes, photos,
  links and text through the day. Voice is transcribed locally, images are read by Claude
  vision, and each one lands as a Markdown note in `Thinking/Daily/`. Everything coming
  back (receipts, the Today queue, the weekly question, alerts) is a Buzz channel on the
  same phone, and replies go in the thread.
- **MacBook Pro (macOS).** The author. I write notes and run the slash commands here. Claude
  Code signed in with a subscription, no API key. launchd runs the hourly compile, the
  nightly digest and the weekly lint. This machine pushes when I close it for the night.
- **A laptop running [Omarchy](https://omarchy.org) (Arch Linux).** The always-on worker.
  It stays on behind Tailscale and does everything unattended: the systemd timers, the
  Telegram capture with whisper.cpp, the Buzz relay that hosts the six personas as live
  agents, the two-minute reply worker that reads my answers in Buzz threads, and the
  Writer and Narrator agents in `#writing` and `#narratives`. It pulls, runs, commits
  and pushes so the Mac can sleep.

```mermaid
flowchart LR
  iphone["iPhone 16 Pro<br/>Telegram"]
  mac["MacBook Pro (macOS)<br/>author: Claude Code<br/>launchd timers"]
  vault[("Git repo: the vault")]
  chan["Buzz channels<br/>#dialectic #inbox #tasks #thinking #ops"]

  subgraph omarchy["Laptop running Omarchy (Arch), always on, Tailscale"]
    cap["Telegram capture<br/>whisper.cpp + ffmpeg"]
    tmr["systemd user timers"]
    relay["Buzz relay<br/>6 persona agents (read-only)<br/>outbox + reply worker"]
  end

  iphone -->|voice, photo, link| cap
  cap --> vault
  cap -->|receipt| relay
  mac <-->|git pull / push| vault
  omarchy <-->|flock: pull, run, commit, push| vault
  tmr -->|dialectic 12:30 and 21:20, Today 08:00, alerts| relay
  relay -->|synthesis, queue, questions| chan
  chan <-.->|you read and reply on the phone| iphone
```

Both machines run the same `claude` CLI, no API key. One rule keeps two writers from
fighting: one owner per file, append-only for anything both touch. We learned it the hard
way. On 4 September both machines wrote the same briefing file and every push failed for
51 hours. Two computers, one file and no adults in the room. Git operations on the worker
are now serialised with `flock` against a 30 minute backup timer, and
`_Agent-Context/TRUNK-BASED-DEVELOPMENT.md` has the full convention.

### The loop, on the clock

The same loop, with times attached. These are the worker's timers, installed
verbatim by `.agents/systemd/install.sh`:

| When | Runs | What it does |
|---|---|---|
| 12:30 and 21:20 | `tools/dialectic.py` | six personas argue the day's new notes (isolated round one, quoted round two), moderator posts the scorecard and synthesis to `#dialectic` and files them; on a silent day the evening run argues one vault topic instead |
| 02:00 | `tools/dialectic.py --run night` | the local-model experiment: the six personas answer through the worker's own model, one at a time, while the moderator and a judge lane stay on the cloud and grade each reply; a replay of the day's first topic, scored nowhere, with a five-night kill rule (see [local inference](local-inference.md#the-night-window-experiment)) |
| 21:00 | `closeout` | propose one seed idea, one decision worth writing down, one contradiction with your beliefs |
| 23:00 | `nightly` | write the digest, archive the raw capture, compile `.wiki/`, lint |
| Mon 05:00, Fri 21:00 | `dashboard` | rebuild the active-projects view from every `notes.md` |
| Mon 06:30 | `resurface` | bring decisions due for grading back to the top |
| Mon 06:50 | `writing-index` | rebuild the writing map (`_Agent-Context/WRITING.md`), one-line summary to `#writing` |
| 1st of every second month, 09:00 | `writing-ideas` | three long-form pitches, one thread each in `#writing` |
| 08:00 | `reminder` | the Today queue: one decision, one commitment, one piece of evidence, posted to `#tasks` |
| every 2 min | `buzz-interactions` | read your replies in Buzz threads, draft, apply on approval, drain the outbox |
| Sun 19:00 | `thinking` | weekly themes, blind spots, promotion candidates; one reflective question posted to `#thinking` |
| Sun 20:00 | `reconcile` | cross-check recent notes against your written beliefs |
| Sun 22:00 | `lint` | fix links and frontmatter across `.wiki/` |

### Real configuration

**1. The vault profile, `_Agent-Context/PROFILE.md`.** Written by the installer, then
edited. `owner_name` and `output_lang` shape every prompt; `worker_name` is the commit
suffix the always-on machine signs with; `private_segments` names folders the compiler
must never read.

```markdown
---
owner_name: Tunca
output_lang: tr
company_area: Work
worker_name: omarchy
owner_full_name: Tunca
private_segments: Official Docs, Security Incidents
---
```

**2. Backend and vault, in the shell profile (`.env.example` documents every variable).**
Identical on both machines:

```bash
export BRAINLESS_VAULT=~/projects/brainless
export BRAINLESS_OUTPUT_LANG=tr
export BRAINLESS_LLM_PROVIDER=claude-cli   # signed-in claude CLI, no API key
```

**3. The worker timer, one real unit pair.** Every scheduled job is a `oneshot` service
wrapped in `flock` so it never races the git backup. The dialectic pair:

```ini
# ~/.config/systemd/user/brainless-dialectic.service
[Service]
Type=oneshot
WorkingDirectory=%h/projects/brainless
# flock serialises git against the 30 minute backup timer (pull, commit, push).
ExecStart=/usr/bin/flock -w 300 %h/.brainless-git.lock \
  /bin/bash %h/projects/brainless/.agents/scripts/worker_job.sh tools/dialectic.py
```

```ini
# ~/.config/systemd/user/brainless-dialectic.timer
[Timer]
OnCalendar=*-*-* 12:30:00
OnCalendar=*-*-* 21:20:00
Persistent=true
RandomizedDelaySec=60
```

`worker_job.sh` is the pattern behind all of them: `git pull --rebase --autostash`, run the
tool, then commit and push, so the Mac finds the result in the morning.

**4. The Buzz personas, as sandboxed agents.** Each persona is a `buzz-acp` process paired
with Claude Code, run from a systemd template unit and locked to read-only vault access.
This is the part that lets an unattended agent speak in a channel without ever touching
your notes:

```ini
# ~/.config/systemd/user/buzz-persona@.service  (start with: systemctl --user start buzz-persona@skeptic)
[Service]
Type=simple
WorkingDirectory=%h/buzz-%i
EnvironmentFile=%h/.config/brainless/buzz/%i.env
ExecStart=%h/.cargo/bin/buzz-acp
Restart=on-failure
```

```jsonc
// .agents/buzz/personas/settings.json  (the whole security model in one file)
"allow": [ "Read", "Glob", "Grep",
           "Bash(python3 .../tools/wiki_search.py *)",
           "Bash(.../buzz messages send *)" ],
"deny":  [ "Write", "Edit", "WebFetch", "WebSearch",
           "Bash(git push *)", "Bash(rm *)", "Bash(sudo *)", "Bash(ssh *)",
           "Read(~/.config/**)", "Read(~/.ssh/**)", "Read(~/.claude/**)" ]
```

**5. Addon secrets, never in the repo.** Each persona reads one env file under
`~/.config/brainless/`, filled from `.agents/buzz/personas/env.template`. Placeholders,
exactly as shipped:

```bash
# ~/.config/brainless/buzz/skeptic.env  (filled by install_personas.sh, never committed)
BUZZ_PRIVATE_KEY=__SECRET__
BUZZ_RELAY_URL=__RELAY_URL__          # the Tailscale relay, not a public address
BUZZ_ACP_AGENT_OWNER=__OWNER_PUBKEY__
BUZZ_ACP_RESPOND_TO_ALLOWLIST=__MODERATOR_PUBKEY__
BUZZ_ACP_CHANNELS=__CHANNEL_UUID__
```

**6. The Mac side, launchd.** `install.sh --schedule` writes three agents into
`~/Library/LaunchAgents`: an hourly compile (`StartInterval 3600`), the nightly processor
at 23:00, and the weekly lint on Sunday at 22:00. Same jobs as the worker's systemd timers,
in Apple's format.

### A day, concretely

```bash
# morning on the Mac: grade what has come due
$ brainless calibrate
2 decisions past their review date. Write the outcome for each:
  - 2026-06-14  "Ship the pricing change without a beta"   predicted: +8% conversion

# a thesis you want tested before a meeting
$ brainless dialectic "Move the team to a four-day week for one quarter"
Skeptic     : the conclusion hides two claims, output and morale, measured differently
Scientist   : cheapest test is one team for six weeks, terminate if throughput falls >10%
Gambler     : write it as a bet. 60% it holds. In twelve months it failed because ...
Moderator   : strongest objection is measurement. What must be true: a throughput metric
              you trust weekly. Cheap dated test: one team, six weeks, review 2026-11-01.
              Clashes with belief "async beats synchronous for deep work".

# it is filed for you; tomorrow's question can build on it
$ ls .wiki/digests/queries/ | tail -1
2026-09-09-dialectic-four-day-week.md
```

At 12:30 and 21:20 the Omarchy worker runs the same dialectic unattended and posts it to
the `#dialectic` channel, which I read on the phone. Overnight it writes the digest and
rebuilds the projects view, so when I open the Mac in the morning the day is compiled and
the decisions due for grading are waiting at the top.

You do not need three devices to start. One laptop is the baseline. The iPhone is for
capture without friction, and the Omarchy worker is where you go when the Mac being closed
starts to cost you.

---

## Where it is going now

For a year the system was a pipeline. Notes went in, a compiler read them, timers posted
the results and I read them on the phone. It talked. I did not talk back. The morning
queue arrived as a message with buttons on it, and the buttons were the whole
conversation.

That changed on 21 September 2026. Telegram now only listens. Every answer the system
gives me, every question it asks and every approval it needs lives in a Buzz channel, in
a thread, next to the message it belongs to. I reply in the thread the way I would reply
to a colleague. "Apply" under a preview writes the note. "Defer to Friday" moves it.
Anything else gets an answer with the vault paths it stands on.

Two channels went further. In `#writing` an agent drafts long-form pieces with me, one
thread per piece, and runs my own editing lint before it shows me anything. In
`#narratives` another one writes children's stories, chapter by chapter, with an
illustration note at the end of each. Both read the whole vault and can write to exactly
one folder. Moving a draft out of that folder is my job.

So the work now is workflows and conversation. The pipeline stays. What I am building on
top of it is the set of bounded exchanges a person and a system can have without either
of them making a mess. A question with a revision-bound approval. A draft with a lint
report. A pitch with its sources. Each one is small on purpose. I do not know yet which
of them will survive a month of daily use, and I would rather find that out in threads
than in a roadmap. See [Buzz interactions](buzz-interactions.md) and
[Writing agents](writing-agent.md) for how each exchange is bounded.

## Honest limits

- The default path assumes a Claude subscription. The OpenAI-compatible path works but
  I have tested it less, and one piece does not cross over: reading handwriting and
  whiteboard photos uses the Claude CLI's own file-reading tool, so on another backend
  image capture is the part that will not work yet. Text, voice and documents are fine.
- `output_lang` in `PROFILE.md` sets the language of every LLM output. Fixed labels and
  Buzz replies ship in English and Turkish (`tools/locale/`); another language is a new
  locale directory.
- The six personas are my shelf. Yours may be different, and should be.
- I am not a decision scientist. I am an operator who got tired of being wrong in the
  same way twice.

