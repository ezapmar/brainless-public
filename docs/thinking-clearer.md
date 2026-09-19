# How it makes you think clearer

Most of what brainless does to your thinking is done by forms and by order of
operations, not by the model. The model reads and argues. The forms decide what you have
to write down before you are allowed to move on, and the order decides what you see
first. This page lists those mechanisms, the failure each one is aimed at, and the file
or command where it lives, so you can check the claim against the code.

## Before deciding

**Criteria before options.** `_Templates/Decision.md` has a "Decision Criteria" section
with the instruction to fill it before looking at options. Once you have a favourite,
criteria get written to fit it. Setting them first makes the favourite pass or fail a
test it did not write.

**The reversibility gate.** `/decide` opens by asking what undoing the decision would
cost. A two-way door (a pricing test, a hire on probation, a tool trial) gets a
three-line note and the advice to decide today. Only a one-way door (a lease, an exit
structure, a school) gets the full framework. Deliberating reversible calls as if they
were irreversible is the most common way to waste a week.

**Kill the binary.** The template has an Option C slot labelled "force a third path".
Two options is usually one option and its absence.

**Beliefs quoted, never paraphrased.** `/decide` pulls the beliefs that bear on the
question and quotes them exactly, for and against each option. A belief that only
exists as a paraphrase in your head bends to fit the case at hand. A quote does not.

## While deciding

**Name the bias in advance.** The frontmatter field `default_risk` takes one of `ego`,
`emotion`, `social`, `inertia` or `none`. You state which one most threatens this
call before you make it, when you can still see it.

**A base rate before a confidence.** `/decide` and `/calibrate` refuse a confidence
figure that did not start from a reference class ("new UK Ltds with no trading history
applying for a lease") and its rough base rate, then adjust for the specifics and say
why. The command's own words: a confidence that did not start from a base rate is a mood.

**One dated action.** A decision note must end with one sentence in the form "On
<date>, I will <action>". If that sentence cannot be written, the decision is not made
and the note says what is missing. Deleting the sentence later is itself information.

## Testing it

**Five readings, blind.** Round one of the dialectic runs each persona in its own
thread without seeing the others. The first voice in a room sets the frame for everyone
after it; isolation removes the first voice.

**Votes counted before anyone writes.** A script computes the scorecard (affirmation
rate, who moved, who moved without new evidence) before the moderator writes a word.
The synthesis starts from numbers, not from the mood of the last message.

**The unanimity warning.** If every persona casts the same round one vote, the
scorecard prints a red line. Six voices on one base model agreeing is a reason to check
the framing, not a confirmation. A rolling 30 day scorecard also flags affirmation above
60 per cent and any persona that never votes NO, because a critic who always agrees is
decoration.

**A cheap dated test.** The Scientist persona, and the moderator after it, must name
the cheapest experiment that would settle the question, with a date and a result that
means stop. This converts an opinion into something that can lose.

## Living with it

**What would change my mind, written first.** `_Templates/Belief.md` has that section,
with the instruction to steel-man the opposite. Written before the evidence arrives,
it stops the goalposts moving when the evidence does. `/contradict` then checks every
belief against recent decisions and daily notes, flags beliefs unused for 90 days
(keep, delete, or refine?) and proposes the implicit ones you keep acting on but never
wrote down.

**Grade the decision, not the result.** When a review date passes, `/calibrate` asks one
question per decision and records the outcome next to the prediction and the confidence.
Over time `Thinking/Calibration.md` shows whether your 70 per cent is a 70 per cent. A
good outcome from a bad process is luck, and the scoreboard is how you tell.

**An ask small enough to happen.** The Today queue offers at most three items a day: one
decision, one commitment, one piece of evidence, each linked to its source. After two
deferrals it stops asking "did you do it" and asks for the blocker or a smaller next
step. Calibration systems fail by asking for too much; this one asks for one line.

**Weight by maturity.** Every note carries `seed`, `growing` or `evergreen`. Commands
are told to treat seeds as unreliable, growing as good for suggestions, evergreen as
ground truth. A fresh thought and a tested one do not get the same vote.

**Argue from the record.** Every command files its output into
`.wiki/digests/queries/`, and `/trace` can lay out how your view of a topic changed,
oldest first, with exact quotes. Memory rewrites the past to agree with the present;
the record does not. Tomorrow's argument starts from what was actually said.

**Propose, never pick.** `/decide` and `/ideas` end with the instruction not to choose
for the owner. `/calibrate` never writes an outcome the owner did not state. The tools
load the frame and count the votes. The decision stays yours, which is the only way the
grade can mean anything.

The commands themselves are listed in [commands.md](commands.md).
