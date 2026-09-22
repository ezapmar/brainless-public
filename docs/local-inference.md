# Local inference: running lanes on the worker

> **In the loop:** [Keep it yours](../README.md#keep-it-yours). Keeping the model calls on your own hardware.

The batch brain talks to a model through one door, `tools/llm.py`. By default that door
leads to the `claude` CLI in the cloud. This guide is about the other door: running a
model on the always-on worker itself, through the Goose CLI, so that some of the vault's
prompts never leave the machine.

Two reasons to want it, and they pull in different directions:

- **Privacy.** A voice note about a child's therapy session, a meeting transcript, a
  folder classification decision. Content that is nobody else's business, going through
  a model that runs on hardware you own.
- **Resilience.** A subscription that expires, a token that rotates, an outage, a flight
  with no wifi. A lane with a local fallback keeps producing, badly, instead of stopping.

Privacy wants the local model as the **primary** for a lane. Resilience wants it as the
**fallback** for a lane that normally runs in the cloud. Both are configured the same
way and the difference is which side you put it on.

---

## The provider

`BRAINLESS_LLM_PROVIDER=goose` runs prompts through `goose run`. Goose is an agent
harness, not a model. It bundles llama.cpp and its own model store, so a downloaded GGUF
runs with no ollama daemon and no second service to supervise:

```bash
goose local-models search "Qwen3-4B-Instruct-2507 GGUF"
goose local-models download 'unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M'
goose local-models list
```

The provider name for a downloaded model is `local`:

```bash
export BRAINLESS_GOOSE_PROVIDER=local
export BRAINLESS_GOOSE_MODEL='unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M'
```

**Security.** Goose ships extensions, and the "developer" extension can run shell
commands. Untrusted text flows through these prompts all day, so every call from
`llm.py` passes `--no-profile`, which loads no extensions at all, and never passes a
`--with-*` flag. With no extension there is no tool, and the worst a poisoned note can
do is produce a wrong sentence. That is the Goose-side equivalent of the
`--disallowedTools` list used for the Claude CLI. Do not add extensions to these calls.
For the same reason `web=True` is not supported on this provider: a web call is routed
to `claude-cli` instead, and the log line says so.

---

## Lanes

One global provider was too blunt an instrument. A four billion parameter model quantised
to four bits answers "is this note a task, a story or an epic" about as well as anything
needs to. It does not write a Turkish weekly synthesis anyone wants to read. So every
call site in the vault names its lane, and each lane is routed separately:

```bash
python3 tools/llm.py --lanes          # every lane and where it currently goes
python3 tools/llm.py --probe note-classify   # one throwaway call, timed
```

Route a single lane to the worker's model:

```bash
export BRAINLESS_LLM_PROVIDER_NOTE_CLASSIFY=goose
```

The variable is the lane name upper-cased with dashes turned into underscores. The same
pattern works for `BRAINLESS_CLAUDE_MODEL_<LANE>` and `BRAINLESS_GOOSE_MODEL_<LANE>`, so
a lane can also keep a cheaper or larger model than the rest of the pipeline.

Lanes are marked `short` or `long` in the `LANES` table in `tools/llm.py`. Short means a
bounded prompt and a structured answer: the local candidates. Long means thousands of
tokens of prose, usually Turkish, where a small model produces fluent nonsense.

---

## Fallback, and why it only goes one way

```bash
export BRAINLESS_LLM_FALLBACK=goose     # cloud lanes degrade to the worker
```

With that set, a lane whose primary provider fails (timeout, auth error, outage) is
retried once on the local model. This is the resilience half.

The reverse is refused. A lane pinned to a local provider was pinned there for privacy,
and a local timeout is not consent to send the same text to a cloud provider instead.
`llm.py` drops such a fallback and logs it. If you really mean it,
`BRAINLESS_LLM_ALLOW_CLOUD_FALLBACK=1` says so explicitly, per machine, and it is
deliberately not the default.

Per-lane overrides work here too, including switching the fallback off:

```bash
export BRAINLESS_LLM_FALLBACK_COMPILE=none
```

Local calls are slow, so their timeout is multiplied by `BRAINLESS_LOCAL_TIMEOUT_FACTOR`
(default 3). Every caller tuned its timeout against a cloud model; this saves changing
twenty of them.

---

## What the worker can actually do

Measured on omarchy, the always-on box, on 2026-09-19. This is the part to read before
planning anything ambitious:

| | |
|---|---|
| CPU | Intel i7-1065G7, 4 cores / 8 threads, 1.3 GHz base, scaling at 75% |
| RAM | 15 GB |
| GPU | Iris Plus G7 integrated, not usable for inference here |
| Internet | about 2 Mbps down (310 KB/s from Cloudflare, 48 KB/s from HuggingFace) |
| Already running | whisper.cpp `large-v3-turbo` for every voice note, plus 24 timers |

Measured with `tools/llm_bench.py`, same prompts both sides, `Qwen3-0.6B` Q4_K_M
locally against Opus 4.8 through the Claude CLI. The 4B model was measured separately
on real notes, further down:

| Case | Prompt | claude-cli | goose, local 0.6B |
|---|---|---|---|
| `classify` (one word out of three) | 1.5k chars | 7.8s | 26.5s |
| `extract` (JSON action items) | 9k chars | 16.2s | 158s |
| `summarise` (five bullets) | 12k chars | 11.7s | 257s |

The 4B model, `Qwen3-4B-Instruct-2507` Q4_K_M, is the one actually in use. On the
synthetic 1.5k classify prompt it takes 67s. On **real** notes, where the prompt is
about 7.5k characters, it takes **227s on average against 5s for Opus**. Measure with
real prompt sizes: the synthetic number was off by a factor of three.

Two more numbers worth knowing. A trivial "reply OK" costs 22s, so roughly twenty
seconds of every local call is model load and fixed overhead. And the same probe run
while a second local call was in flight took 109s: four cores do not divide, they
collapse. Never schedule two local lanes at once, and remember whisper is already using
those cores every time a voice note arrives.

Two consequences, both load-bearing:

1. **Downloading a model on the worker is impractical.** A 2.4 GB GGUF at 48 KB/s is
   fourteen hours. Download on a machine with a real connection and copy it over the
   Tailscale link instead (about 200 KB/s, so still hours for a large file). Prefer the
   smallest model that passes the lane's quality bar.
2. **There is no headroom for long prompts.** Generation on this CPU is single digit
   tokens per second, and prompt ingestion is not free either. A 12,000 character page
   summary is a multi-minute call that also competes with whisper for the same four
   cores. Short lanes only.

### Seeding a model over a slow link

Downloading on a fast machine and copying the file across works, because Goose adopts a
file that is already in the HuggingFace cache instead of fetching it again. Verified on
2026-09-19: after the copy, `goose local-models download` returned in one second and
registered the model.

```bash
# On a machine with a real connection:
curl -L -o Qwen3-0.6B-Q4_K_M.gguf \
  https://huggingface.co/unsloth/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q4_K_M.gguf
shasum -a 256 Qwen3-0.6B-Q4_K_M.gguf     # this hash is the blob's file name
curl -s https://huggingface.co/api/models/unsloth/Qwen3-0.6B-GGUF | grep '"sha"'  # the revision

# On the worker, build the cache layout:
DIR=~/.cache/huggingface/hub/models--unsloth--Qwen3-0.6B-GGUF
mkdir -p $DIR/{blobs,refs,snapshots/$REV} && printf %s $REV > $DIR/refs/main

# Copy (resumable, this is the slow part), then link and register:
rsync -a --partial --inplace model.gguf worker:$DIR/blobs/$SHA
ln -sf ../../blobs/$SHA $DIR/snapshots/$REV/Qwen3-0.6B-Q4_K_M.gguf
goose local-models download 'unsloth/Qwen3-0.6B-GGUF:Q4_K_M'   # adopts the file
goose local-models list
```

Check the hash on the worker before registering. A truncated copy that Goose adopts
fails later, at inference time, where the error will make no sense.

---

## What is configured on the worker today

`~/.config/environment.d/10-brainless-llm.conf`, read by the systemd user manager, so
every `brainless-*.service` inherits it. As of 2026-09-19 it sets the local backend
(`goose` + `local` + the 0.6B model), a timeout factor of 4, and a **fallback** on three
lanes only:

```
BRAINLESS_LLM_FALLBACK_CAPTURE_NOTE=goose
BRAINLESS_LLM_FALLBACK_NOTE_CLASSIFY=goose
BRAINLESS_LLM_FALLBACK_SPIKY_ACTIONS=goose
```

Those three are the lanes where a missing answer hurts most: a Telegram capture keeps
its raw update in a local journal until a note is written, but the owner is waiting for
the receipt in `#inbox`, and a meeting action nobody extracted never comes back. A
degraded answer beats a dropped one there. There is deliberately **no global fallback**:
a nightly digest or a weekly synthesis written by a small model is worse than a missing
one, because it reads fluent and lands in the wiki. Those lanes should fail visibly and
turn the health check red.

`note-classify` is pinned local as a **primary** since 2026-09-19:
`BRAINLESS_LLM_PROVIDER_NOTE_CLASSIFY=goose` with the 4B model. It carries no fallback
line, because a local lane never escalates to cloud, and a failed call there is
harmless: the deterministic label simply stands, and the gate cannot promote what it
already refused.

### How that decision was made

Do not move a lane on a vibe. The real `adjudicate()` prompt was run over eight real
vault notes through both providers:

- **First run: 4 out of 8 agreement**, and every single disagreement went the same way.
  The local model answered `epic` where Opus answered `story`. That is the expensive
  direction, since an epic triggers a week of research.
- The cause was the prompt, not the model. The rubric said "when you hesitate, answer
  story" but never said how rare an epic is, and a small model reads a list of
  large-looking signal counts as evidence of largeness.
- Adding one sentence, that an epic is about one note in twenty, **fixed all four**.
- That alone proves nothing: a prompt that always answers `story` would also score 4/4.
  So the opposite case was tested too, with a constructed note describing an
  unmistakable six month programme with four dependent work packages. Both the local
  model and Opus still called it an epic. The tightened prompt is a classifier, not a
  constant.
- Both models also called a one-line reminder a `story` rather than a `task`. They are
  wrong in the same way, which does not matter for this gate: only the epic boundary
  feeds the research trigger.

Net: **8/8 agreement on real notes plus the constructed epic**, at 227s per call
against 5s. The sentence now lives in `RUBRIC` in `tools/note_classify.py` for every
provider, since it improved calibration without costing the cloud path anything.

The remaining honest gap: the sample contained no naturally occurring epic, because
this vault had not run the classifier yet. Watch `.agents/state/llm_log` and the first
real epic the gate produces before trusting the lane completely.

After editing that file, `systemctl --user daemon-reexec` makes the manager re-read it.

## Rollout order

The order below moves the most sensitive content off the cloud first, while keeping the
lanes that would visibly degrade where they are.

1. `note-classify`. **Done, 2026-09-19.** Already mostly deterministic:
   `tools/note_classify.py` scores a note with hand-written features and only calls a
   model for candidates in the ambiguous band, asking for one word out of three.
2. `spiky-actions`. Meeting reports, which are sensitive, in and a short JSON list out.
   Now guarded by a deterministic duplicate check, so a weaker model repeating itself
   cannot reach a person. The open question is volume: ten reports in one run at four
   minutes each is forty minutes of pegged CPU on a box that also runs whisper. Run the
   agreement check on real reports first, the same way `note-classify` was moved.
3. `capture-note`. Voice notes, the most personal content in the vault, but the output
   is a whole note file, so check the quality before trusting it.
4. Everything else stays on `claude-cli`, with `BRAINLESS_LLM_FALLBACK=goose` for
   resilience.

Verify each step with `--probe <lane>` and by reading `.agents/state/llm_log`, which
keeps the last 300 calls with their lane, provider, outcome and wall time. The single
latest outcome stays in `.agents/state/llm_status`, which is what `health_check.py`
reads.

---

## The cheapest win is not a model at all

Before routing a lane anywhere, ask whether it needs a model. The language decision used
to be a prompt; it is now `tools/lang_detect.py`, which is stopwords and a character
class, costs nothing, and cannot leak. `note_classify.py` is the same idea at a larger
scale: deterministic features first, model only for the genuinely ambiguous middle.

Every call that stops existing is faster than any local model, private by construction
and immune to an outage. Look there first.

### Audit, 2026-09-19

Every call site was read with one question: how much of this is really a language
problem. The answer, lane by lane:

| Lane | Finding |
|---|---|
| `note-classify` | Already deterministic first. The model sees only the ambiguous band and answers with one word. Nothing left to remove; move it local. |
| `spiky-actions` | The prompt asked the model not to repeat a task already in the ledger, which is a string comparison wearing a prompt. Now also enforced after the model by `is_duplicate()` in `spiky_actions.py`: diacritics folded, words cut to five characters because Turkish suffixes change every week, duplicate dropped at 60% stem overlap. A weak local model can now repeat itself without a duplicate reaching Google Tasks. |
| `resurface` | Sends 120 candidate notes and asks for the best 5. A deterministic pre-filter (keyword overlap with CONTEXT.md, recency, link count) could cut the catalogue to about 30 and the prompt by most of its length. Worth doing, changes a weekly output, so not done in passing. |
| `capture-photo` | The privacy hole in the capture path: photographs of documents go to a cloud vision model. Local OCR (tesseract) plus a local model for the filing decision would close it, and this is the largest single privacy win available. Needs its own change; vision is not something the Goose provider does today. |
| `dialectic-topics` | Clustering the day's captures. Already falls back to one topic per capture when the model fails, so the failure mode is safe, but the work itself is genuinely a language problem. |
| `dialectic-persona` | Generative prose, but bounded (250 words, a fixed method card, a fixed tail) and produced at a time of day when the box is idle. Stays cloud for the 12:30 and 21:20 rounds; the night experiment below measures whether the local model can hold a persona at all. |
| `compile`, `nightly`, `closeout`, `reconcile`, `content`, `thinker-digest`, `thinking-loop`, `meeting-brief`, `capture-link`, `dialectic-synthesis`, `dialectic-connect`, `research-*` | Generative prose. No deterministic substitute exists, and a small local model would produce fluent nonsense. These stay cloud, with a local fallback for resilience. |

---

## The night window experiment

The worker is idle between 01:00 and 06:00: no voice notes, so no whisper, and no
timer that talks to a person. `brainless-dialectic-night.timer` uses that window to run
`tools/dialectic.py --run night` at 02:00 with one variable changed:

```
Environment=BRAINLESS_LLM_PROVIDER_DIALECTIC_PERSONA=goose
```

The six personas answer through the worker's own model, one at a time (two concurrent
local calls collapse, see above). The moderator's synthesis and a new `dialectic-judge`
lane stay on the cloud, so the experiment is graded by the same model that grades the
day rounds. The topic is a replay of the day's first cloud topic when there was one, so
the two can be compared on the same thesis, else a vault topic. Nothing is scored,
no capture is marked seen, the note is filed under its own name, and one line per
night lands in the night section of `_Agent-Context/DIALECTIC-STATUS.md`.

What "usable" means: a reply that argues the thesis, stays in its persona's method, is
coherent in the thesis's language, invents no vault file, and ends with the Vote and
Number lines. A reply with no Vote line is unusable before the judge reads it. A night
is usable when at least two thirds of its local replies pass.

Budget: twelve persona calls at the measured 227 s plus overhead is about an hour,
inside the window even at twice that. The local timeout factor applies.

**Kill rule, decided 2026-09-19.** After five nights, fewer than three usable nights
closes the timer (`systemctl --user disable --now brainless-dialectic-night.timer`),
the result and the wall times go into this section, and no larger model is seeded
for it. What to try next is a separate decision. If the rule passes, the night round
stays and the day rounds stay on the cloud.
