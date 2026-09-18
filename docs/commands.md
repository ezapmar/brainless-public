# The commands

Every slash command is a Markdown prompt in `.wiki/_commands/`. Claude Code and Gemini
CLI wrappers point back there, so editing one file changes both agents. From the vault
root, `/decide` in an agent and `brainless decide` in a shell run the same prompt.

Three rules apply to all of them, from `.wiki/_commands/_shared-rules.md`:

- **They propose. You paste.** Human folders (`Work/`, `Personal/`, `Library/`,
  `Thinking/`, `Inbox/`) are never written without an explicit "apply". The one
  exception is `/calibrate`, where your one-line answer is the approval.
- **They quote, they do not paraphrase.** A claim about a note comes with the exact
  line and the path.
- **They file their output.** Every run lands in `.wiki/digests/queries/`, so the
  next command can build on it. A run that is not filed is invisible.

## Deciding

| Command | What it asks | What it guards against |
|---|---|---|
| `/decide "<question>"` | Reversibility first (two-way door: decide today, three lines; one-way door: full note). Then criteria, beliefs quoted for and against each option, a forced third option, second-order effects, what would change your mind, a reference class and base rate before any confidence, and one dated action | Deliberating the reversible; criteria fitted to a favourite; confidence without a base rate; a decision with no action attached |
| `/dialectic [topic]` | Five personas, two rounds. Round one blind, round two answering the strongest objection. Scorecard computed by script, then a synthesis with a counterargument, what must be true, a cheap dated test and a probability. Empty topic argues today's captures | The first voice framing the rest; a synthesis written from mood; five agreeing voices mistaken for confirmation |
| `/contradict [scope]` | Belief against belief, belief against decision, belief against what you actually did in the last 14 days, with quotes from both sides. Lists beliefs unused for 90 days and proposes implicit beliefs you keep acting on | Drift between what you say you believe and what you do; a belief file that is a museum |
| `/calibrate [decision]` | Runs `tools/calibrate.py`, then one question per decision that is due: did the prediction happen? Records outcome and lesson beside the confidence. Asks for a base rate, a prediction and a review date on any decision that lacks them | Ungraded decisions; grading the result instead of the process; a confidence that was never a number |

## Thinking

| Command | What it asks | What it guards against |
|---|---|---|
| `/ideas [focus]` | Three atomic ideas, each the intersection of at least two sources (a belief and a project, a resource and an area), each phrased as a question, each a paste-ready seed with a permanent id | Single-source ideas that are restatements; conclusions dressed as ideas |
| `/pollinate <note>` | Takes one new note, finds the beliefs it reinforces and the ones it challenges, checks which seeds it validates or kills, proposes two mutations | Reading only for agreement; new material that never touches old beliefs |
| `/trace <topic>` | Everything you wrote on a topic, oldest first, with the exact sentences and dates | Memory rewriting the past to match the present |
| `/connect <A> <B>` | The shortest link path between two notes and two to four edges that should exist and do not | Islands in the graph; connections that only exist in your head |
| `/weekly [days]` | Three themes from the window, one blind spot (an area or project absent from it), promotion candidates, a proposed diff to `CONTEXT.md` | The week that felt busy and touched nothing that matters |
| `/context` | Current priorities and projects against what the last seven days actually mention; flags drift both ways and names the stalest project | A context file that describes last month |

## Tending the vault

| Command | What it does |
|---|---|
| `/graduate [days]` | Seeds older than the cutoff, scored by inbound links and length: promote to growing or evergreen, or flag the lonely ones for archive |
| `/backlog [n]` | The notes the wiki keeps linking to that nobody has written, ranked by demand, with paste-ready stubs for the top three |
| `/sync` | Reads the last seven digests and proposes log entries for the project notes they mention |
| `/lint` | Broken links, orphans, stale summaries, missing frontmatter; the top three actions |
| `/recompile <path>` | Forces one source through the compiler again |

## The forms behind them

The commands are only as good as the templates they fill. `_Templates/Decision.md`
carries the criteria-before-options rule, the third-option slot, the `default_risk`
field (ego, emotion, social, inertia), the prediction block with confidence and review
date, and an Outcome section that starts as "Pending review" and nags until it is not.
`_Templates/Belief.md` carries "What Would Change My Mind" and a `last_challenged`
date. `_Templates/Idea.md` carries a permanent `zk:` id and a rule that every idea
links to at least one other note.

Why each of these exists is on [thinking-clearer.md](thinking-clearer.md).
