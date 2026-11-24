# Local inference: running lanes on the worker

> **In the loop:** [Keep it yours](../README.md#keep-it-yours). Keeping the model calls on your own hardware.

The batch brain talks to a model through one door, `tools/llm.py`. By default that door
leads to the `claude` CLI in the cloud. This guide is about the other door: a model
running on the always-on worker itself, either as a local `llama-server` on loopback or
through the Goose CLI, so that some of the vault's prompts never leave the machine.

Routing has one source of truth since 2026-11-21: `brainless.toml` at the vault root,
read by `tools/config.py`. `env.sh` no longer exports any lane variable, because two
lists drift and the drift is silent until a prompt is already on its way to the cloud.
An environment variable still overrides the file for a one-off run, and the same file
also holds the fail-closed `local_only` list described below.

Two reasons to want local inference, and they pull in different directions:

- **Privacy.** A voice note about a doctor's appointment, a meeting transcript, a
  folder classification decision. Content that is nobody else's business, going through
  a model that runs on hardware you own.
- **Resilience.** A subscription that expires, a token that rotates, an outage, a flight
  with no wifi. A lane with a local fallback keeps producing, badly, instead of stopping.

Privacy wants the local model as the **primary** for a lane. Resilience wants it as the
**fallback** for a lane that normally runs in the cloud. Both are one key in
`brainless.toml`, and the difference is which side you put it on.

---

## The provider

A `llama-server` process on loopback is the default local provider. It serves an
OpenAI-compatible API, which is why the provider name is `openai-compatible`:

```toml
# brainless.toml
[llm]
base_url = "http://127.0.0.1:8080/v1"
model = "gemma-4-12b"
```

Point `tools/llm.py` at something else with the `openai-compatible` key, or with
`BRAINLESS_LLM_BASE_URL` for one run. Nothing in a caller names a provider, so swapping
the backend is one file.

Goose is the second option. `goose` runs prompts through `goose run`. It is an agent
harness, not a model. It bundles llama.cpp and its own model store, so a downloaded GGUF
runs with no ollama daemon and no second service to supervise:

```bash
goose local-models search "Qwen3-4B-Instruct-2507 GGUF"
goose local-models download 'unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M'
goose local-models list
```

The model name for a downloaded model is the provider name `local`:

```toml
# brainless.toml: only when the goose provider is the one serving the model
[llm.lanes.note-classify]
provider = "goose"
model = "unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"
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
call site in the vault names its lane, and each lane is routed separately in
`brainless.toml`:

```toml
[llm.lanes.note-classify]
provider = "openai-compatible"

[llm.lanes.spiky-actions]
provider = "openai-compatible"
model = "gemma-4-12b"
```

The section is the lane name, so `compile-aliases` is `[llm.lanes.compile-aliases]`. The
same three keys work for every lane: `provider`, `model` and `fallback`.

```bash
python3 tools/llm.py --lanes          # every lane, its provider, its source, LOCAL-ONLY
python3 tools/llm.py --probe note-classify   # one throwaway call, timed
```

`--lanes` prints where each setting came from (`env`, `brainless.toml` or `default`), so a
lane that is not where you expected is visible without reading the code.

### `local_only`: the fail-closed list

A provider key tells a lane where to go. It does not stop the lane being sent somewhere
else by a stray environment variable or a typo. `local_only` does:

```toml
[llm]
local_only = ["note-classify", "capture-note", "spiky-actions"]
```

A lane named there may only run on a local provider. If its provider resolves to a cloud
one, `tools/llm.py` refuses the call, logs the reason to `.agents/state/llm_log`, and
returns nothing: no fallback can carry the text out either. `tools/health_check.py`
turns its routing row red when a listed lane stops resolving locally, which is how the
refusal gets noticed before a night of quiet failures. This is the lane-level half of
the privacy boundary; `cloud_exclude` in `_Agent-Context/PROFILE.md` is the source-level
half, and `tools/cloud_gate.py` owns both rules.

Lanes are marked `short` or `long` in the `LANES` table in `tools/llm.py`. Short means a
bounded prompt and a structured answer: the local candidates. Long means thousands of
tokens of prose, usually Turkish, where a small model produces fluent nonsense.

---

## Fallback, and why it only goes one way

```toml
[llm]
fallback = "openai-compatible"          # cloud lanes degrade to the worker

[llm.lanes.compile]
fallback = ""                           # no fallback for this one
```

With that set, a lane whose primary provider fails (timeout, auth error, outage) is
retried once on the local model. This is the resilience half.

The reverse is refused. A lane pinned to a local provider was pinned there for privacy,
and a local timeout is not consent to send the same text to a cloud provider instead.
`llm.py` drops such a fallback and logs it, and a lane in `local_only` never gets one at
all. If you really mean it, `BRAINLESS_LLM_ALLOW_CLOUD_FALLBACK=1` says so explicitly,
per machine, and it is deliberately not the default.

Local calls are slow, so their timeout is multiplied by `BRAINLESS_LOCAL_TIMEOUT_FACTOR`
(default 3). Every caller tuned its timeout against a cloud model; this saves changing
twenty of them.

---

## What the worker can actually do

Measured on omarchy, the small always-on box that ran the engine until 2026-11-15, on
2026-09-19. The engine now runs on one Mac, but these numbers are still the honest
description of what a CPU-only box can do with a small model, and they are why the long
prose lanes stay in the cloud. This is the part to read before planning anything
ambitious:

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

## Where routing is configured today

`brainless.toml` at the vault root, committed with the vault, read by `tools/config.py`
before anything reads the environment. As of 2026-11-21 it names the local provider
(`openai-compatible` on `127.0.0.1:8080`, Gemma 4 12B) and pins eight lanes in
`local_only`:

| Lane | Why it is local |
|---|---|
| `note-classify` | One word out of three, after a deterministic first pass. |
| `dreaming-judge` | A short triage answer, one sentence of JSON. |
| `compile-aliases` | Three to five alternative names per page, JSON out. |
| `compile-local` | The compile work for a cloud-excluded source (see PROFILE.md). |
| `capture-note` | A voice note filed into the vault; the most personal content there is. |
| `capture-photo` | A photograph of a document, read by a local vision model. |
| `spiky-actions` | Meeting action items, short JSON list out. |
| `ask` | An owner question answered from the vault, read-only. |

Every one of them is a short, structured call. The long Turkish prose lanes (`compile`,
`nightly`, `closeout`, `reconcile`, `research-*`) stay on `claude-cli`: this file exists
to record that the local model was measured on them and was not good enough, not to
pretend otherwise.

There is deliberately **no global fallback**. A nightly digest or a weekly synthesis
written by a small model is worse than a missing one, because it reads fluent and lands
in the wiki. Those lanes should fail visibly and turn the health check red. The fallbacks
that do exist are set per lane, and only where a missing answer hurts more than a
degraded one: a Telegram capture keeps its raw update in a local journal until a note is
written, but the owner is waiting for the receipt in `#inbox`, and a meeting action
nobody extracted never comes back.

A lane is pinned local as a **primary** with its own `[llm.lanes.<lane>]` block and its
name in `local_only`. It needs no fallback key, because a local lane never escalates to
cloud, and a failed call there is harmless: the deterministic result simply stands, and
the gate cannot promote what it already refused.

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

Editing `brainless.toml` needs no restart: `tools/llm.py` reads the file on every run, so
`python3 tools/llm.py --lanes` shows the new routing immediately. A value in the
environment still wins over the file for that process, which is useful for a probe and
dangerous in a job; `--lanes` prints the source of each setting for exactly that reason.

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
4. Everything else stays on `claude-cli`. A lane that needs resilience gets a per-lane
   `fallback` key in `brainless.toml`; there is no global fallback, and there should not
   be.

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
| `dialectic-persona` | Generative prose, but bounded (250 words, a fixed method card, a fixed tail) and produced at a time of day when the box is idle. Stays cloud. The night experiment below tested whether the local model can hold a persona; it cannot (0 usable nights out of 11, closed 2026-10-02). |
| `compile`, `nightly`, `closeout`, `reconcile`, `content`, `thinker-digest`, `thinking-loop`, `meeting-brief`, `capture-link`, `dialectic-synthesis`, `dialectic-connect`, `research-*` | Generative prose. No deterministic substitute exists, and a small local model would produce fluent nonsense. These stay cloud, with a local fallback for resilience. |

---

## The night window experiment

**Closed 2026-10-02 by the kill rule.** Result at the end of this section.

The worker is idle between 01:00 and 06:00: no voice notes, so no whisper, and no
timer that talks to a person. `brainless-dialectic-night.timer` uses that window to run
`tools/dialectic.py --run night` at 02:00 with one variable changed (the unit below is
the historical omarchy drop-in; the same variable works as an environment override
today):

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

### Result, 2026-10-02

The rule failed on the fifth night (2026-09-26: zero usable nights out of five). The
timer kept running six more nights before it was closed on 2026-10-02; those nights did
not change the answer. Eleven nights, `Qwen3-4B-Instruct-2507` Q4_K_M, graded by the
cloud judge, from the night section of `DIALECTIC-STATUS.md`:

| Night | Replies passed | Wall time |
|---|---|---|
| 2026-09-22 | 0/12 | 91 min |
| 2026-09-23 | 5/12 | 83 min |
| 2026-09-24 | 0/12 | 111 min |
| 2026-09-25 | 0/12 | 93 min |
| 2026-09-26 | 6/12 | 91 min |
| 2026-09-27 | 0/12 | 104 min |
| 2026-09-28 | 2/12 | 110 min |
| 2026-09-29 | 2/12 | 99 min |
| 2026-09-30 | 3/12 | 106 min |
| 2026-10-01 | 3/12 | 87 min |
| 2026-10-02 | 1/12 | 115 min |

- **Usable nights: 0/11.** The bar was 8/12 replies; the best night reached 6/12.
  Overall 22 of 132 replies passed (17%).
- **Reliability was never the problem.** Every night returned 12/12 replies with Vote
  lines. The model answers on time and in format; it fails on content.
- **Wall time: 99 min on average (83 to 115),** against the one hour budgeted. Still
  inside the idle window, but the budget was off by about two thirds.
- **How the replies fail.** Read from the 2026-10-02 note: the six personas collapse
  into one voice. They pick the same objection, cite the same single vault file, and
  vote the same way (all six CONDITIONAL at 40 to 55%, tripping the unanimity warning).
  The method cards do not survive: in round 2 the "chosen objection" field comes back
  as `?`. A 4B model can produce a persona's format but not hold its method.

**Decision.** Timer disabled on the worker and its unit removed from
`.agents/systemd/` (the installer enables every `*.timer` it finds, so leaving the file
would bring it back on the next install). The service unit stays for manual runs:
`systemctl --user start brainless-dialectic-night.service`. As the rule says, no larger
model is seeded for this lane. `dialectic-persona` stays on the cloud.
