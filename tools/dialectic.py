#!/usr/bin/env python3
"""Critical dialectic engine (moderator) for the brainless vault.

Twice a day (12:30 and 21:20 on the always-on worker) the moderator takes the day's raw
captures (Telegram and Buzz voice notes, text, photos already transcribed into
Thinking/Daily/*-telegram.md and *-buzz.md), clusters them into topics and has
six live persona agents argue each topic backstage in the Buzz channel
#dialectic-lab (falls back to #dialectic until that channel is in
channels.json). The owner's channel #dialectic gets one short card per topic
(render_card): thesis, computed verdict, one line per persona, the round 2
clash, the result, one question, in the language of the topic. The long part
of every moderator message (wiki context, beliefs, round 1 quotes) goes to a
brief file under .agents/state/dialectic_briefs/ that the personas read.
On a day with no captures the evening run argues one vault topic instead (a
decision past its review date, a decided note without a prediction, a pending
decision near review, a belief not challenged in 90 days, or a live question
from Thinking/Questions.md), rotated with a 14 day cooldown, so the loop keeps
closing on silent days; noon stays idle.

Round 1 is isolated: one root per persona, so no persona can read another before
answering (no-interaction first rounds maximise argument diversity). Round 2 is
one root that quotes every round 1 reply. Every reply ends with Vote (YES, NO,
CONDITIONAL) and Number (NN%), round 2 also with New evidence; score_topic()
turns those lines into a deterministic scorecard (affirmation rate, unanimity
warning, who moved and whether they cited new evidence). Rounds append to
.agents/state/dialectic_scores.jsonl and the rolling 30 day view is written to
_Agent-Context/DIALECTIC-SCORECARD.md with two flags: sycophancy (affirmation
above AFFIRM_WARN) and a persona that never votes NO. Personas:

  Skeptic (Browne & Keeley), Gambler (Annie Duke), Scientist (Camuffo 2024),
  Postmortem (Edmondson), Strategist (Lafley & Martin),
  Methodologist (Quivy & Van Campenhoudt).

Every persona gets the same grounding the moderator gets: the wiki search hits
for the topic, the owner's core beliefs and the decision calendar, so the
argument is tied to what the vault already holds and not to the thesis alone.

Each persona is a buzz-acp harness with its own prompt (see
.agents/buzz/personas/). The moderator is a plain signing identity: it posts
one root message per topic mentioning the personas (round 1), waits for the
replies, posts round 2 (rebut the strongest objection), waits again, then
writes a synthesis with the LLM and files the whole thread into
.wiki/digests/queries/ through tools/file_query.py.

Safety by design:
- Vault is read-only for the personas (Claude settings deny list) and for this
  script except the loopback note, the status file and its own state.
- The LLM never gets file tools (tools/llm.py deny list).
- Personas only answer the moderator and the owner (harness allowlist) and are
  told never to mention anyone, so agents cannot trigger each other.
- A missing persona reply is recorded as "no reply"; it never blocks filing.

Modes:
  --run noon|evening   default by clock (before 17:00 = noon). Evening adds the
                       whole-day connection scan, coverage table and #daily post.
  --run night          the local-model experiment (02:00 timer, idle window):
                       personas run through tools/llm.py with the persona lane
                       routed to the worker's own model, the moderator stays on
                       the cloud and grades each local reply usable or not.
                       Argues a replay of the day's first topic when there was
                       one (so cloud and local can be compared on the same
                       thesis), else a vault topic. Writes no score, marks no
                       capture seen, and files under its own note; one line per
                       night in the status file's night section. Kill rule:
                       after five nights, fewer than three usable nights closes
                       the timer and the result is logged, no bigger model.
  --topic "text"       argue one ad-hoc topic instead of today's captures.
  --local              run the personas through tools/llm.py instead of Buzz
                       (fallback when the relay is unreachable; also the Mac test).
  --parallel           mention all personas at once (default is one at a time,
                       which keeps six Claude sessions under the rate limit).
  --dry-run            steps 1 to 3 only; print what would be posted.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt  # noqa: E402
from calibrate import scan as calibration_scan  # noqa: E402
from owner_profile import OWNER, lang_name, output_lang_directive  # noqa: E402
from lang_detect import detect  # noqa: E402

CAPTURE_DIR = os.path.join(VAULT, "Thinking", "Daily")
STATE_DIR = os.path.join(VAULT, ".agents", "state")
SEEN_FILE = os.path.join(STATE_DIR, "dialectic_seen")
DAY_FILE = os.path.join(STATE_DIR, "dialectic_day.json")
STATUS_MD = os.path.join(VAULT, "_Agent-Context", "DIALECTIC-STATUS.md")
BELIEFS_FILE = os.path.join(VAULT, "_Agent-Context", "BELIEFS-SUMMARY.md")
PERSONA_DIR = os.path.join(VAULT, ".agents", "buzz", "personas")
TEAM_FILE = os.path.join(VAULT, ".agents", "buzz", "team_instructions.md")
BUZZ_DIR = os.path.expanduser("~/.config/brainless/buzz")
BUZZ_BIN = os.path.expanduser("~/.cargo/bin/buzz")
CHANNEL_NAME = "dialectic"      # the stage: one card per topic, what the owner reads
LAB_CHANNEL = "dialectic-lab"   # backstage: the rounds; falls back to the stage until it exists
BRIEF_DIR = os.path.join(STATE_DIR, "dialectic_briefs")
MODERATOR = "moderator"
PERSONAS = [("skeptic", "Skeptic"), ("gambler", "Gambler"), ("scientist", "Scientist"),
            ("postmortem", "Postmortem"), ("strategist", "Strategist"), ("methodologist", "Methodologist")]
NO_REPLY = "no reply"
ROUND1_WAIT = 480   # seconds to wait for round 1 replies
ROUND2_WAIT = 360
SEQ_WAIT = 180      # per persona in --sequential mode
POLL = 30
MAX_TOPICS = 6
MAX_CONTEXT = 12000
# Scorecard: deterministic scoring of every round (no LLM). See score_topic().
SCORES_FILE = os.path.join(STATE_DIR, "dialectic_scores.jsonl")
SCORECARD_MD = os.path.join(VAULT, "_Agent-Context", "DIALECTIC-SCORECARD.md")
VOTES = ("YES", "NO", "CONDITIONAL")
AFFIRM_WARN = 0.60      # 30 day share of YES votes above this = sycophancy flag
MOVE_POINTS = 15        # a Number shift of this many points counts as a moved view
SCORE_WINDOW_DAYS = 30
SCORECARD_ROWS = 20
R1_QUOTE_CHARS = 1500   # per persona, when round 1 replies are quoted into the round 2 root
EXCLUDE = ("Contrarian Theses",)
# Vault-topic fallback: when a day brings no captures, argue one thing the vault
# is waiting on instead of idling. Rotated through this state file.
FALLBACK_FILE = os.path.join(STATE_DIR, "dialectic_fallback.json")
FALLBACK_COOLDOWN_DAYS = 14
DECISIONS_DIR = os.path.join(VAULT, "Thinking", "Decisions")
BELIEFS_DIR = os.path.join(VAULT, "Thinking", "Beliefs")
QUESTIONS_FILE = os.path.join(VAULT, "Thinking", "Questions.md")
STALE_BELIEF_DAYS = 90
PENDING_HORIZON_DAYS = 45


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def read_file(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def write_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def no_dashes(text):
    """House rule: no em or en dashes anywhere in output, filenames included."""
    return (text or "").replace("\u2014", " - ").replace("\u2013", "-").replace("  - ", " - ")


# ---------------------------------------------------------------- inputs

def load_seen():
    return set(read_file(SEEN_FILE).split())


def save_seen(seen):
    write_file(SEEN_FILE, "\n".join(sorted(seen)[-2000:]))


def load_day(date_str):
    try:
        data = json.loads(read_file(DAY_FILE) or "{}")
    except ValueError:
        data = {}
    if data.get("date") != date_str:
        data = {"date": date_str, "runs": [], "captures": {}, "topics": []}
    return data


def save_day(data):
    write_file(DAY_FILE, json.dumps(data, ensure_ascii=False, indent=1))


def todays_capture_files(date_str):
    """Telegram and Buzz captures of today still in Thinking/Daily (archived at 23:00)."""
    out = []
    if not os.path.isdir(CAPTURE_DIR):
        return out
    for name in sorted(os.listdir(CAPTURE_DIR)):
        if not name.startswith(date_str) or not name.endswith(".md"):
            continue
        if not (name.endswith("-telegram.md") or name.endswith("-buzz.md")):
            continue
        if any(x in name for x in EXCLUDE):
            continue
        out.append(name)
    return out


def collect_captures(date_str, seen):
    chunks = []
    for name in todays_capture_files(date_str):
        if name in seen:
            continue
        text = read_file(os.path.join(CAPTURE_DIR, name)).strip()
        if text:
            chunks.append((name, text[:6000]))
    return chunks


def _frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                out[k.strip()] = v.partition("#")[0].strip()
    return out


def _section(text, heading, limit=700):
    """Body of the first '## <heading>...' section, trimmed to one line."""
    m = re.search(r"^## " + re.escape(heading) + r"[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not m:
        return ""
    body = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
    lines = []
    for l in body.splitlines():
        l = re.sub(r"^\s*>\s*(\[![a-z]+\]\s*)?", "", l).strip()  # quotes and callouts
        if l and l != "---":
            lines.append(l)
    return " ".join(lines)[:limit]


def _days_since(date_str):
    try:
        return (datetime.now() - datetime.strptime(date_str.strip()[:10], "%Y-%m-%d")).days
    except (ValueError, AttributeError):
        return None


def vault_topic_candidates():
    """Things the vault is waiting on, in leverage order: a decision past its
    review date, a decided note without a prediction, a pending decision near
    review, a belief not challenged for a long time, a live open question.
    Each item -> (key, topic dict)."""
    out = []
    try:
        due, needs_pred, _ = calibration_scan()
    except Exception:
        due, needs_pred = [], []
    due_names = {n for n, _ in due}
    decisions = {}
    if os.path.isdir(DECISIONS_DIR):
        for name in sorted(os.listdir(DECISIONS_DIR)):
            if name.endswith(".md"):
                decisions[name[:-3]] = read_file(os.path.join(DECISIONS_DIR, name))
    for name, rev in due:
        text = decisions.get(name, "")
        fm = _frontmatter(text)
        claim = (f"{OWNER} decided [[{name}]] and the review date {rev} has passed without an outcome "
                 f"being written. Decision: {_section(text, 'Decision') or '(not stated)'} "
                 f"Prediction: {_section(text, 'Prediction') or '(none)'} "
                 f"Confidence: {fm.get('confidence') or '(none)'}. The thesis to test: the call was "
                 f"right and the predicted outcome is what happened. Argue what evidence would grade "
                 f"it, and what {OWNER} should write as the outcome.")
        out.append((f"decision-due:{name}", {"title": f"Grade: {name[:60]}", "claim": claim, "sources": []}))
    for name in needs_pred:
        if name in due_names:
            continue
        text = decisions.get(name, "")
        claim = (f"{OWNER} decided [[{name}]] but wrote no falsifiable prediction and no confidence. "
                 f"Decision: {_section(text, 'Decision') or '(not stated)'} "
                 f"Context: {_section(text, 'Context', 400)} The thesis to test: this decision can be "
                 f"stated as a prediction with a base rate and a review date. Propose the prediction "
                 f"and attack it.")
        out.append((f"decision-nopred:{name}", {"title": f"Predict: {name[:58]}", "claim": claim, "sources": []}))
    for name, text in decisions.items():
        fm = _frontmatter(text)
        if fm.get("status", "").lower() not in ("pending", "deferred", "deliberating"):
            continue
        days = _days_since(fm.get("review", "") or fm.get("revisit", ""))
        if days is None or days > 0 or days < -PENDING_HORIZON_DAYS:
            continue
        claim = (f"{OWNER} has an open decision [[{name}]] with a review date in {-days} days and no call "
                 f"yet. Context: {_section(text, 'Context', 500)} Options: {_section(text, 'Options', 600)} "
                 f"The thesis to test: the option {OWNER} currently leans to is the right one. Name the "
                 f"lean from the note, then argue it.")
        out.append((f"decision-pending:{name}", {"title": f"Decide: {name[:59]}", "claim": claim, "sources": []}))
    beliefs = []
    if os.path.isdir(BELIEFS_DIR):
        for name in sorted(os.listdir(BELIEFS_DIR)):
            if not name.endswith(".md"):
                continue
            text = read_file(os.path.join(BELIEFS_DIR, name))
            fm = _frontmatter(text)
            days = _days_since(fm.get("last_challenged", "") or fm.get("date", ""))
            if days is not None and days > STALE_BELIEF_DAYS:
                beliefs.append((days, name[:-3], text))
    for days, name, text in sorted(beliefs, reverse=True):
        claim = (f"{OWNER} holds the belief [[{name}]], last challenged {days} days ago. "
                 f"The belief: {_section(text, 'The Belief', 500)} "
                 f"Why held: {_section(text, 'Why I Hold This', 500)} "
                 f"What would change it: {_section(text, 'What Would Change My Mind', 400)} "
                 f"The thesis to test is the belief itself, against what happened in the last {days} days.")
        out.append((f"belief-stale:{name}", {"title": f"Challenge: {name[:56]}", "claim": claim, "sources": []}))
    active = read_file(QUESTIONS_FILE).split("## Retired")[0]
    for line in active.splitlines():
        m = re.match(r"^\s*\d+\.\s+(.*\S)", line)
        if not m:
            continue
        q = re.sub(r"\*\(.*?\)\*", "", m.group(1)).strip()
        claim = (f"{OWNER} keeps this question open in Thinking/Questions.md and has not committed to an "
                 f"answer: \"{q}\" Treat the answer his beliefs and past decisions imply as the thesis, "
                 f"state it, then test it.")
        out.append((f"question:{q[:60]}", {"title": f"Question: {q[:56]}", "claim": claim, "sources": []}))
    return out


def load_fallback():
    try:
        return json.loads(read_file(FALLBACK_FILE) or "{}")
    except ValueError:
        return {}


def pick_vault_topic():
    """First candidate not argued within the cooldown; None when all are fresh."""
    used = load_fallback()
    for key, topic in vault_topic_candidates():
        days = _days_since(used.get(key, ""))
        if days is None or days >= FALLBACK_COOLDOWN_DAYS:
            topic["fallback_key"] = key
            return topic
    return None


def mark_vault_topic(topic, date_str):
    used = load_fallback()
    used[topic["fallback_key"]] = date_str
    write_file(FALLBACK_FILE, json.dumps(used, ensure_ascii=False, indent=1))


def beliefs_summary():
    text = read_file(BELIEFS_FILE)
    out, grab = [], False
    for line in text.splitlines():
        if line.startswith("## Core Beliefs"):
            grab = True
            continue
        if grab and line.startswith("## "):
            break
        if grab and line.strip():
            out.append(line.strip())
    return "\n".join(out)


def calibration_lines():
    try:
        due, needs_pred, no_review = calibration_scan()
    except Exception:
        return ""
    lines = []
    for name, rev in due:
        lines.append(f"- Decision awaiting grading: [[{name}]] (review {rev})")
    for name in needs_pred:
        lines.append(f"- Prediction missing: [[{name}]]")
    return "\n".join(lines[:8])


SNIPPET_CHARS = 400
WIKI_K = 8


def wiki_search(query, k=WIKI_K):
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(VAULT, "tools", "wiki_search.py"), query, "--json", "--k", str(k + 4)],
            cwd=VAULT, capture_output=True, text=True, timeout=60)
        hits = json.loads(r.stdout or "[]")
    except Exception:
        return []
    out = []
    for h in hits:
        path = h.get("path", "")
        if not path or path.startswith(".wiki/_") or ("/queries/" in path and "-dialectic-" in path):
            continue
        snippet = no_dashes((h.get("snippet") or "").replace("\n", " ").strip())
        # Drop frontmatter fragments the search index returns (lang, summary_en, tags, ---).
        snippet = re.sub(r"^.*?summary_en:\s*", "", snippet)
        snippet = re.sub(r"^.*?---\s*", "", snippet, count=1) if snippet.count("---") else snippet
        snippet = re.sub(r"\s*(tags|command|title|compiled_at|status):.*$", "", snippet)
        out.append(f"- `{path}`: {snippet.strip()[:SNIPPET_CHARS]}")
        if len(out) >= k:
            break
    # The owner's own decisions and beliefs outrank a summary of someone else's
    # book: they are what the personas are asked to argue against.
    own = [l for l in out if re.search(r"decision|belief", l.split("`")[1], re.I)]
    return own + [l for l in out if l not in own]


# ------------------------------------------------------------- LLM steps

def parse_json(text):
    """Best-effort JSON extraction from an LLM reply."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    blob = m.group(1) if m else text
    start = blob.find("[")
    end = blob.rfind("]")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(blob[start:end + 1])
    except ValueError:
        return None


def cluster_topics(captures):
    """Group captures into 1..MAX_TOPICS topics. Falls back to one topic per capture."""
    blob = "\n\n".join(f"--- {name} ---\n{text}" for name, text in captures)[:MAX_CONTEXT]
    prompt = f"""Below are the raw notes {OWNER} captured today (voice note transcripts, texts). The notes may be in {lang_name()}.
Group them into at most {MAX_TOPICS} discussion topics. For each topic give:
- "title": short title, at most 8 words, IN THE SAME LANGUAGE as the notes that feed this topic
- "claim": the thesis {OWNER} defends or assumes on this topic, one paragraph, third person ("{OWNER} thinks ..."), IN THE SAME LANGUAGE as those notes
- "sources": list of file names that feed this topic (the names on the --- lines above, verbatim)
Every file must belong to at least one topic. Write only a JSON array, nothing else. No em or en dashes.

{blob}"""
    topics = parse_json(run_prompt(prompt, timeout=240, lane="dialectic-topics"))
    names = [n for n, _ in captures]
    clean = []
    if isinstance(topics, list):
        for t in topics[:MAX_TOPICS]:
            if not isinstance(t, dict) or not t.get("title"):
                continue
            srcs = [s for s in (t.get("sources") or []) if s in names]
            clean.append({"title": str(t["title"]).strip(), "claim": str(t.get("claim") or "").strip(),
                          "sources": srcs})
    covered = {s for t in clean for s in t["sources"]}
    for name, text in captures:
        if name not in covered:
            first = next((l.lstrip("# ").strip() for l in text.splitlines() if l.strip()), name)
            clean.append({"title": first[:80], "claim": text[:800], "sources": [name]})
    # Each topic argues in the language of the notes behind it. A Turkish voice
    # note should not come back as an English debate just because the vault's
    # engine is written in English; the owner thinks in the language he spoke.
    by_name = dict(captures)
    for topic in clean:
        blob = " ".join(by_name.get(s, "") for s in topic["sources"]) or topic.get("claim", "")
        topic["lang"] = detect(blob)[0]
    return clean


def synthesize(topic, r1, r2, context, beliefs, calib, score=None):
    transcript = "\n\n".join(f"### {name} (round 1)\n{txt}" for name, txt in r1.items())
    transcript += "\n\n" + "\n\n".join(f"### {name} (round 2)\n{txt}" for name, txt in r2.items())
    prompt = f"""You are the moderator of the brainless critical dialectic engine. {len(r1)} personas argued the thesis below.
Write a neutral, short synthesis. {output_lang_directive(topic.get("lang"))} Do not invent; add nothing that is not in the debate.

# THESIS: {topic['title']}
{topic['claim']}
Source files: {', '.join(topic['sources']) or 'ad-hoc topic'}

# PRIOR CONTEXT (wiki search)
{context or '(none found)'}

# {OWNER.upper()}'S BELIEFS
{beliefs or '(no summary)'}

# DECISION CALENDAR (computed by the system)
{calib or '(none)'}

# DEBATE
{transcript[:MAX_CONTEXT]}

# SCORECARD (computed by the system from the Vote / Number / New evidence lines; do not contradict it)
{render_scorecard(score) if score else '(not scored)'}

Write exactly this format, nothing else:
### Synthesis
**Conclusion:** (two or three sentences: what the debate settled, what it left open, what {OWNER} should do next)
**Strongest counterargument:** (name who raised it)
**What would have to be true:** (at most 3 items)
**Proposed test:** (one, cheap, dated)
**Bet:** (probability the thesis proves right within 12 months, a percentage and a one-sentence reason)
**Contradiction:** (if it clashes with one of the beliefs or a decision in the calendar, one sentence with [[Note name]]; else "None")
**Changed minds:** (the personas the scorecard marks as moved, and the evidence they cited; else "Nobody")
**Unanimity warning:** (if the scorecard says round 1 was unanimous: one sentence on what shared framing all of them may have accepted; else "None")
### Method trace
**Starting question:** (the thesis as a research question, taking the Methodologist's rewrite if there is one)
**Hypotheses tested:** (at most 3, each one line, naming the persona who put it up)
**Tests on the table:** (the concrete checks the personas proposed, each with the persona)
**Deviations:** (where the personas' findings contradict each other, or "None")
### Proposal
(one paste-ready draft for Thinking/Ideas, Beliefs or Decisions; else "None")"""
    return run_prompt(prompt, timeout=300, lane="dialectic-synthesis") or "### Synthesis\n(the LLM did not answer)"


def connection_scan(day):
    """Evening only: whole-day topics against the wiki. Deterministic retrieval, LLM judgement."""
    topics = day.get("topics") or []
    if not topics:
        return ""
    blob = ""
    for t in topics:
        blob += f"\n## {t['title']}\n{t.get('claim','')[:600]}\nWiki matches:\n" + "\n".join(t.get("context") or ["- (none)"])
    prompt = f"""Below are today's discussed topics and the wiki search results for each. Connectivity matters most; no topic may be skipped.
Produce one table row per topic:
| Topic | Prior references (as [[wikilink]] if any) | Missing link to add | Source status (in Library / not in Library) |
Then under "**Source gaps:**" list the topics with no external source in the vault and which kind of source (book, paper, data) would help.
{output_lang_directive()} No invention; where there is no wiki match say "none".
{blob[:MAX_CONTEXT]}"""
    return run_prompt(prompt, timeout=240, lane="dialectic-connect") or ""


# ------------------------------------------------------------- Buzz side

def relay_url():
    return (os.environ.get("BUZZ_RELAY_URL") or read_file(os.path.join(BUZZ_DIR, "relay_url")).strip()
            or "http://localhost:3000")


def key_field(identity, label):
    for line in read_file(os.path.join(BUZZ_DIR, "keys", identity)).splitlines():
        if line.startswith(label):
            return line.split(":", 1)[1].strip()
    return ""


def secret(identity):
    return key_field(identity, "Secret key")


def pubkey(identity):
    return key_field(identity, "Public key")


def channel_id(name):
    try:
        return json.loads(read_file(os.path.join(BUZZ_DIR, "channels.json")) or "{}").get(name)
    except ValueError:
        return None


def buzz(args, identity=MODERATOR, stdin=None, timeout=60):
    env = dict(os.environ, BUZZ_RELAY_URL=relay_url(), BUZZ_PRIVATE_KEY=secret(identity))
    r = subprocess.run([BUZZ_BIN, *args], input=stdin, capture_output=True, text=True,
                       timeout=timeout, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"buzz {' '.join(args[:2])} rc={r.returncode}: {r.stderr[:200]}")
    return r.stdout


def _find_id(obj):
    if isinstance(obj, dict):
        for k in ("id", "event_id", "eventId"):
            v = obj.get(k)
            if isinstance(v, str) and len(v) == 64:
                return v
        for v in obj.values():
            found = _find_id(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_id(v)
            if found:
                return found
    return None


def post(channel, content, reply_to=None, mentions=()):
    args = ["messages", "send", "--channel", channel, "--content", "-"]
    if reply_to:
        args += ["--reply-to", reply_to]
    for m in mentions:
        args += ["--mention", m]
    out = buzz(args, stdin=no_dashes(content))
    try:
        return _find_id(json.loads(out))
    except ValueError:
        return None


def _messages(obj, acc):
    if isinstance(obj, dict):
        if "pubkey" in obj and "content" in obj:
            acc.append(obj)
        else:
            for v in obj.values():
                _messages(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _messages(v, acc)


def thread_messages(channel, root_id):
    try:
        out = buzz(["messages", "thread", "--channel", channel, "--event", root_id, "--limit", "100"])
        acc = []
        _messages(json.loads(out), acc)
        return acc
    except Exception as exc:
        log(f"thread read failed: {exc}")
        return []


def _collect(channel, root_id, wanted, since, got):
    """One pass over a thread: newest reply per wanted pubkey after `since`."""
    for m in thread_messages(channel, root_id):
        pk = m.get("pubkey")
        ts = int(m.get("created_at") or 0)
        if pk in wanted and ts >= since and m.get("id") != root_id:
            name = wanted[pk]
            if name not in got or ts > got[name][0]:
                got[name] = (ts, m.get("content") or "")


def wait_replies(channel, root_id, wanted, since, timeout):
    """wanted: {pubkey: display}. Returns {display: content} of replies after `since`."""
    deadline = time.time() + timeout
    got = {}
    while True:
        _collect(channel, root_id, wanted, since, got)
        if len(got) >= len(wanted) or time.time() >= deadline:
            break
        time.sleep(POLL)
    return {name: txt for name, (ts, txt) in got.items()}


def wait_replies_many(channel, roots, wanted, since, timeout):
    """roots: {pubkey: root_id}, one isolated thread per persona. Polls all until each replied."""
    deadline = time.time() + timeout
    got = {}
    while True:
        for pk, rid in roots.items():
            if wanted[pk] not in got:
                _collect(channel, rid, {pk: wanted[pk]}, since, got)
        if len(got) >= len(roots) or time.time() >= deadline:
            break
        time.sleep(POLL)
    return {name: txt for name, (ts, txt) in got.items()}


# ------------------------------------------------------------- rounds

def persona_prompt(slug):
    return read_file(os.path.join(PERSONA_DIR, slug, "system_prompt.md")).replace("{{OWNER}}", OWNER)


def team_rules_local():
    """Team rules without the Buzz posting contract (local mode)."""
    text = read_file(TEAM_FILE).replace("{{OWNER}}", OWNER)
    return text.split("## Buzz contract")[0]


def topic_label(topic):
    if topic.get("fallback_key"):
        return "vault topic (" + topic["fallback_key"] + ")"
    return "ad-hoc topic"


def grounding_text(topic):
    """The vault side of the argument, the same block the moderator sees: the
    owner's core beliefs and the decision calendar. main() attaches both to the
    topic once; without them a persona argues the thesis in a vacuum."""
    parts = []
    if topic.get("beliefs"):
        parts.append(f"**{OWNER}'s core beliefs:**\n{topic['beliefs']}")
    if topic.get("calib"):
        parts.append(f"**Decision calendar:**\n{topic['calib']}")
    return "\n\n".join(parts)


def write_brief(topic, context, n, r1=None):
    """The long part of a moderator message, written to a file the personas read
    (they have Read on the vault). Keeps the Buzz message to a few lines."""
    os.makedirs(BRIEF_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", topic["title"].lower())[:40].strip("-") or "topic"
    path = os.path.join(BRIEF_DIR, f"{datetime.now().strftime('%Y-%m-%d-%H%M')}-{slug}-r{n}.md")
    body = [f"# {topic['title']}", "", f"**Thesis:** {topic['claim']}", "",
            f"**Source:** {', '.join(topic['sources']) or topic_label(topic)}", "",
            "**Prior context:**", ("\n".join(context) if context else "- (no wiki match)")]
    ground = grounding_text(topic)
    if ground:
        body += ["", "Use a belief or calendar line only where it bears on the thesis; ignore the rest.", "", ground]
    if r1 is not None:
        body += ["", "**Round 1 replies:**", ""]
        body += [f"### {name}\n{(txt or NO_REPLY)[:R1_QUOTE_CHARS]}\n" for name, txt in r1.items()]
    write_file(path, no_dashes("\n".join(body)) + "\n")
    return path


R1_ASK = ("Test the thesis with your own method (do not read the thread or other personas). Relate it to the prior "
          "context and the beliefs where they bear on it, naming the file. At most 250 words, ending with "
          "Finding / Strongest objection / Question for {owner} / Vote (YES, NO or CONDITIONAL: does the thesis hold "
          "as stated) / Number (NN%: probability the thesis proves right within 12 months).")
R2_ASK = ("Read the round 1 replies. Pick the strongest objection other than your own, agree with it or refute it "
          "(at most 3 sentences), citing at least one vault file by name from the prior context, the beliefs or your "
          "own search, or saying that nothing in the vault bears on it. End with Chosen objection / My answer / "
          "Vote (YES, NO or CONDITIONAL) / Number (NN%) / New evidence (one fact or argument that was not in your "
          "round 1 reply, or \"none\").")


def round_text(n, topic, context, persona=None, r1=None, brief=None):
    """Moderator text. Round 1 goes to ONE persona per root (isolated: nobody can
    read anyone else before answering). Round 2 is one root that quotes every
    round 1 reply, so personas read each other only through the moderator.
    With `brief` (Buzz) the context, beliefs and quotes live in that file and the
    message stays short; without it (local mode, no file tools) all is inline."""
    who = f"{persona}, this round is yours alone. " if (persona and n == 1) else ""
    if brief:
        head = (f"## {topic['title']}" + (" (round 2)" if n == 2 else "") +
                f"\n\n**Thesis:** {topic['claim']}\n\n**Brief:** `{brief}` (read it first; it holds the prior context, "
                f"the beliefs" + (" and the round 1 replies" if n == 2 else "") + ")")
        ask = R1_ASK.format(owner=OWNER) if n == 1 else R2_ASK
        return f"{head}\n\n**Round {n}:** {who}{ask}"
    ground = grounding_text(topic)
    ground = ("\n\n" + ground) if ground else ""
    if n == 1:
        return (f"## {topic['title']}\n\n**Thesis:** {topic['claim']}\n\n"
                f"**Source:** {', '.join(topic['sources']) or topic_label(topic)}\n\n"
                f"**Prior context:**\n" + ("\n".join(context) if context else "- (no wiki match)") + ground +
                f"\n\n**Round 1:** {who}From this message only. " + R1_ASK.format(owner=OWNER))
    quoted = "\n\n".join(f"### {name}\n{(txt or NO_REPLY)[:R1_QUOTE_CHARS]}" for name, txt in (r1 or {}).items())
    return (f"## {topic['title']} (round 2)\n\n**Thesis:** {topic['claim']}\n\n"
            f"**Prior context:**\n" + ("\n".join(context) if context else "- (no wiki match)") + ground +
            f"\n\n**Round 1 replies:**\n\n{quoted or '(none)'}\n\n**Round 2:** " + R2_ASK)


def run_rounds_buzz(topic, context, sequential):
    channel = channel_id(LAB_CHANNEL) or channel_id(CHANNEL_NAME)
    if not channel:
        raise RuntimeError(f"neither #{LAB_CHANNEL} nor #{CHANNEL_NAME} in channels.json")
    brief1 = write_brief(topic, context, 1)
    wanted = {}
    for slug, name in PERSONAS:
        pk = pubkey(slug)
        if pk:
            wanted[pk] = name
        else:
            log(f"persona {slug}: no key, skipped")
    # Round 1: one root per persona. Each thread holds only the moderator and
    # that persona, so there is nothing to read before answering.
    since = int(time.time()) - 5
    roots, r1 = {}, {}
    for pk, name in wanted.items():
        rid = post(channel, round_text(1, topic, context, persona=name, brief=brief1), mentions=[pk])
        if not rid:
            log(f"round 1 root for {name} not returned by relay")
            continue
        roots[pk] = rid
        if sequential:
            r1.update(wait_replies(channel, rid, {pk: name}, since, SEQ_WAIT))
    if not roots:
        raise RuntimeError("no round 1 root message id returned by relay")
    if not sequential:
        r1 = wait_replies_many(channel, roots, wanted, since, ROUND1_WAIT)
    for name in wanted.values():
        r1.setdefault(name, NO_REPLY)
    log(f"round 1: {sum(1 for v in r1.values() if v != NO_REPLY)}/{len(wanted)} replies")
    # Round 2: one root quoting every round 1 reply; synthesis is filed under it.
    since2 = int(time.time()) - 5
    brief2 = write_brief(topic, context, 2, r1=r1)
    root = post(channel, round_text(2, topic, context, r1=r1, brief=brief2), mentions=[] if sequential else list(wanted))
    if not root:
        raise RuntimeError("round 2 root message id not returned by relay")
    if sequential:
        r2 = {}
        for pk, name in wanted.items():
            post(channel, f"{name}, round 2 is yours.", reply_to=root, mentions=[pk])
            r2.update(wait_replies(channel, root, {pk: name}, since2, SEQ_WAIT))
    else:
        r2 = wait_replies(channel, root, wanted, since2, ROUND2_WAIT)
    for name in wanted.values():
        r2.setdefault(name, NO_REPLY)
    log(f"round 2: {sum(1 for v in r2.values() if v != NO_REPLY)}/{len(wanted)} replies")
    return channel, root, r1, r2


def run_rounds_local(topic, context):
    rules = team_rules_local()
    r1 = {}
    for slug, name in PERSONAS:
        head = round_text(1, topic, context, persona=name)
        out = run_prompt(f"{persona_prompt(slug)}\n\n{rules}\n\n# MODERATOR MESSAGE\n{head}", timeout=240, lane="dialectic-persona")
        r1[name] = out or NO_REPLY
    r2 = {}
    head2 = round_text(2, topic, context, r1=r1)
    for slug, name in PERSONAS:
        out = run_prompt(f"{persona_prompt(slug)}\n\n{rules}\n\n# MODERATOR MESSAGE\n{head2}", timeout=240, lane="dialectic-persona")
        r2[name] = out or NO_REPLY
    return None, None, r1, r2


# ------------------------------------------------------------- scorecard

VOTE_RE = re.compile(r"\*{0,2}vote\*{0,2}\s*[:：]\s*\*{0,2}\s*(YES|NO|CONDITIONAL|ABSTAIN)\b", re.I)
NUM_RE = re.compile(r"\*{0,2}number\*{0,2}\s*[:：]\s*\*{0,2}\s*(\d{1,3})\s*(?:%|percent)", re.I)
EVID_RE = re.compile(r"\*{0,2}new evidence\*{0,2}\s*[:：]\s*\*{0,2}\s*(.+)", re.I)
PASS_RE = re.compile(r"^\s*\**pass\**\s*:", re.I)
NONE_RE = re.compile(r"^\W*(none|no new evidence|nothing new|n/?a)\b", re.I)


def parse_reply(text):
    """Deterministic read of one persona reply: vote, number, new evidence."""
    out = {"replied": bool(text) and text != NO_REPLY, "vote": None, "number": None, "evidence": None}
    if not out["replied"]:
        return out
    if PASS_RE.match(text):
        out["vote"] = "ABSTAIN"
    else:
        m = VOTE_RE.search(text)
        out["vote"] = m.group(1).upper() if m else None
    m = NUM_RE.search(text)
    if m:
        out["number"] = max(0, min(100, int(m.group(1))))
    m = EVID_RE.search(text)
    if m:
        ev = m.group(1).strip().strip("*").strip()
        out["evidence"] = None if NONE_RE.match(ev) else ev[:200]
    return out


def score_topic(r1, r2):
    """Score one topic from the raw replies. No LLM: what is counted must be
    reproducible from the filed note. Moved = vote changed or Number shifted
    MOVE_POINTS or more. Unanimity = every voting persona cast the same round 1
    vote (abstentions excluded, at least 3 votes)."""
    rows = []
    for name in r1:
        a, b = parse_reply(r1[name]), parse_reply(r2.get(name, NO_REPLY))
        moved = None
        if a["replied"] and b["replied"]:
            vote_changed = bool(a["vote"] and b["vote"] and a["vote"] != b["vote"])
            num_moved = (a["number"] is not None and b["number"] is not None
                         and abs(a["number"] - b["number"]) >= MOVE_POINTS)
            moved = vote_changed or num_moved
        rows.append({"persona": name, "r1_vote": a["vote"], "r1_number": a["number"],
                     "r2_vote": b["vote"], "r2_number": b["number"], "moved": moved,
                     "evidence": b["evidence"], "replied_r1": a["replied"], "replied_r2": b["replied"]})
    voted = [r for r in rows if r["r1_vote"] in VOTES]
    yes = sum(1 for r in voted if r["r1_vote"] == "YES")
    unanimous = len(voted) >= 3 and len({r["r1_vote"] for r in voted}) == 1
    moved_rows = [r for r in rows if r["moved"]]
    moved_ev = sum(1 for r in moved_rows if r["evidence"])
    return {"rows": rows, "voted": len(voted), "yes": yes,
            "affirm": (yes / len(voted)) if voted else None,
            "unanimous": unanimous, "unanimous_vote": voted[0]["r1_vote"] if unanimous else None,
            "moved": len(moved_rows), "moved_with_evidence": moved_ev,
            "moved_without_evidence": len(moved_rows) - moved_ev,
            "both": sum(1 for r in rows if r["replied_r1"] and r["replied_r2"]),
            "unparsed": sum(1 for r in rows if r["replied_r1"] and r["r1_vote"] is None)}


def _pct(x):
    return "n/a" if x is None else f"{round(100 * x)}%"


def _fmt_vote(vote, number):
    if not vote:
        return "?"
    return vote if number is None else f"{vote} {number}%"


def verdict(score):
    """Deterministic call from the final (round 2) votes, round 1 where a
    persona did not answer twice: a majority of YES is Go, a majority of NO is
    Stop, anything else (CONDITIONAL, a tie, too few votes) is Test first.
    The moderator never writes this line; the table does."""
    votes, numbers = [], []
    for r in score["rows"]:
        v = r["r2_vote"] if r["replied_r2"] else r["r1_vote"]
        n = r["r2_number"] if r["replied_r2"] else r["r1_number"]
        if v in VOTES:
            votes.append(v)
        if n is not None:
            numbers.append(n)
    if not votes:
        return "Test first", None, votes
    top = max(VOTES, key=votes.count)
    if votes.count(top) * 2 > len(votes) and top == "YES":
        call = "Go"
    elif votes.count(top) * 2 > len(votes) and top == "NO":
        call = "Stop"
    else:
        call = "Test first"
    median = None
    if numbers:
        ns = sorted(numbers)
        median = ns[len(ns) // 2] if len(ns) % 2 else round((ns[len(ns) // 2 - 1] + ns[len(ns) // 2]) / 2)
    return call, median, votes


def render_verdict(score):
    call, median, votes = verdict(score)
    tally = ", ".join(f"{votes.count(v)} {v}" for v in VOTES if votes.count(v))
    num = f", median {median}%" if median is not None else ""
    return f"**Verdict:** {call} ({tally or 'no votes'}{num}; computed from the final votes)"


def render_scorecard(score):
    md = ["| Persona | Round 1 | Round 2 | Moved |", "|---|---|---|---|"]
    for r in score["rows"]:
        r1c = _fmt_vote(r["r1_vote"], r["r1_number"]) if r["replied_r1"] else NO_REPLY
        r2c = _fmt_vote(r["r2_vote"], r["r2_number"]) if r["replied_r2"] else NO_REPLY
        moved = "yes" if r["moved"] else ("no" if r["moved"] is False else "n/a")
        md.append(f"| {r['persona']} | {r1c} | {r2c} | {moved} |")
    md.append("")
    md.append(f"Affirmation (round 1 YES): {score['yes']}/{score['voted']} ({_pct(score['affirm'])}). "
              f"Moved: {score['moved']}/{score['both']}, of which without new evidence: {score['moved_without_evidence']}.")
    if score["unanimous"]:
        md += ["", f"> [!warning] Unanimity warning: all {score['voted']} votes were "
               f"{score['unanimous_vote']} in round 1. {score['voted']} voices on one base model agreeing is a signal to "
               f"check the framing, not a confirmation."]
    if score["unparsed"]:
        md.append(f"Unparsed replies (no Vote line): {score['unparsed']}.")
    return "\n".join(md)


def _field(text, label, limit=220):
    """The text after a bold label such as **Finding:** in a persona reply, on
    the same line or the next non-empty one, cut to one sentence-ish length."""
    if not text or text == NO_REPLY:
        return ""
    m = re.search(r"\*{0,2}" + label + r"\*{0,2}\s*[:：]?\*{0,2}[ \t]*(.*)", text, re.I)
    if not m:
        return ""
    rest = m.group(1).strip()
    if not rest:
        tail = text[m.end():].strip().splitlines()
        rest = tail[0].strip() if tail else ""
    rest = re.sub(r"^\W+", "", rest.replace("|", "/"))
    return (rest[:limit].rsplit(" ", 1)[0] + "...") if len(rest) > limit else rest


def render_method_tables(r1, r2, score):
    by = {r["persona"]: r for r in score["rows"]}
    md = ["| Persona | Finding | Strongest objection | Vote |", "|---|---|---|---|"]
    for name, txt in r1.items():
        row = by.get(name, {})
        if txt == NO_REPLY:
            md.append(f"| {name} | {NO_REPLY} | | |")
            continue
        if PASS_RE.match(txt):
            md.append(f"| {name} | Pass | | Abstain |")
            continue
        md.append(f"| {name} | {_field(txt, 'finding') or '?'} | {_field(txt, 'strongest objection') or '?'} | "
                  f"{_fmt_vote(row.get('r1_vote'), row.get('r1_number'))} |")
    md += ["", "| Persona | Chosen objection | New evidence | Vote |", "|---|---|---|---|"]
    for name, txt in r2.items():
        row = by.get(name, {})
        if txt == NO_REPLY:
            md.append(f"| {name} | {NO_REPLY} | | |")
            continue
        ev = row.get("evidence") or "none"
        if len(ev) >= 200:      # parse_reply keeps 200 characters; do not end a cell mid-word
            ev = ev[:180].rsplit(" ", 1)[0] + "..."
        md.append(f"| {name} | {_field(txt, 'chosen objection') or '?'} | {ev} | "
                  f"{_fmt_vote(row.get('r2_vote'), row.get('r2_number'))} |")
    return "\n".join(md)


def split_synthesis(text):
    """{'synthesis': ..., 'method trace': ..., 'proposal': ...} from the
    moderator's fixed format; whatever is not under a known heading lands in
    'synthesis' so nothing the LLM wrote is lost."""
    out, key = {}, "synthesis"
    for line in (text or "").splitlines():
        m = re.match(r"^#{2,4}\s+(.+?)\s*$", line)
        if m:
            key = m.group(1).strip().lower()
            continue
        out.setdefault(key, []).append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def append_score(date_str, mode, topic, score):
    rec = {"date": date_str, "mode": mode, "title": topic["title"][:80],
           "kind": "vault" if topic.get("fallback_key") else ("adhoc" if not topic["sources"] else "capture"),
           "yes": score["yes"], "voted": score["voted"], "unanimous": score["unanimous"],
           "moved": score["moved"], "moved_with_evidence": score["moved_with_evidence"], "both": score["both"],
           "votes": {r["persona"]: r["r1_vote"] for r in score["rows"]}}
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SCORES_FILE, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load_scores(window_days=SCORE_WINDOW_DAYS):
    recs = []
    for line in read_file(SCORES_FILE).splitlines():
        try:
            recs.append(json.loads(line))
        except ValueError:
            continue
    cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d")
    return [r for r in recs if r.get("date", "") >= cutoff]


def scorecard_markdown():
    """Rolling 30 day scorecard from the jsonl: affirmation, unanimity, movement,
    per persona votes, and the two flags (sycophancy, a persona that never says NO)."""
    recs = load_scores()
    lines = ["# Dialectic scorecard", "",
             f"Auto-generated by tools/dialectic.py, last {SCORE_WINDOW_DAYS} days, updated "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M')}. Do not edit. Votes are round 1 votes on "
             f"{OWNER}'s thesis; YES = the persona affirmed it.", ""]
    if not recs:
        lines.append("No scored rounds yet.")
        return "\n".join(lines) + "\n"
    yes = sum(r["yes"] for r in recs); voted = sum(r["voted"] for r in recs)
    topics3 = [r for r in recs if r["voted"] >= 3]
    unan = sum(1 for r in topics3 if r["unanimous"])
    moved = sum(r["moved"] for r in recs); both = sum(r["both"] for r in recs)
    moved_ev = sum(r["moved_with_evidence"] for r in recs)
    affirm = yes / voted if voted else None
    flags = []
    if affirm is not None and affirm > AFFIRM_WARN and voted >= 10:
        flags.append(f"Sycophancy flag: {_pct(affirm)} of round 1 votes affirmed the thesis (threshold {_pct(AFFIRM_WARN)}).")
    per = {}
    for r in recs:
        for name, v in (r.get("votes") or {}).items():
            d = per.setdefault(name, {"YES": 0, "NO": 0, "CONDITIONAL": 0, "ABSTAIN": 0, "none": 0})
            d[v if v in d else "none"] += 1
    for name, d in per.items():
        cast = d["YES"] + d["NO"] + d["CONDITIONAL"]
        if cast >= 5 and d["NO"] == 0:
            flags.append(f"{name} has not voted NO in {cast} votes.")
    lines += ["## Last 30 days", "",
              f"- Topics scored: {len(recs)}",
              f"- Affirmation: {yes}/{voted} ({_pct(affirm)})",
              f"- Unanimous round 1: {unan}/{len(topics3)} ({_pct(unan / len(topics3) if topics3 else None)})",
              f"- Moved in round 2: {moved}/{both} ({_pct(moved / both if both else None)}), "
              f"without new evidence: {moved - moved_ev}",
              ""]
    lines += ["## Flags", ""] + ([f"- {f}" for f in flags] or ["- none"]) + [""]
    lines += ["## Per persona (round 1 votes)", "", "| Persona | YES | NO | CONDITIONAL | Abstain | Unparsed | YES share |",
              "|---|---|---|---|---|---|---|"]
    for name, d in per.items():
        cast = d["YES"] + d["NO"] + d["CONDITIONAL"]
        lines.append(f"| {name} | {d['YES']} | {d['NO']} | {d['CONDITIONAL']} | {d['ABSTAIN']} | {d['none']} | "
                     f"{_pct(d['YES'] / cast if cast else None)} |")
    lines += ["", f"## Last {SCORECARD_ROWS} topics", "",
              "| Date | Run | Kind | Topic | YES/voted | Unanimous | Moved (with evidence) |", "|---|---|---|---|---|---|---|"]
    for r in recs[-SCORECARD_ROWS:][::-1]:
        lines.append(f"| {r['date']} | {r['mode']} | {r['kind']} | {r['title']} | {r['yes']}/{r['voted']} | "
                     f"{'yes' if r['unanimous'] else 'no'} | {r['moved']} ({r['moved_with_evidence']}) |")
    return "\n".join(lines) + "\n"


def write_scorecard():
    write_file(SCORECARD_MD, no_dashes(scorecard_markdown()))


# ------------------------------------------------------------- output

def _quote(text):
    return "\n".join("> " + l if l.strip() else ">" for l in text.strip().splitlines())


def render_topic(topic, r1, r2, synthesis, link=None, score=None):
    """One topic, two pages and an appendix. Page one is what a decision needs:
    the thesis, the computed verdict, the moderator's conclusion and fields, the
    vote table, the proposal. Page two is the method trace: the question, the
    hypotheses and tests, and one row per persona. The full transcript stays in
    the same note, folded, so nothing is lost and nothing has to be read."""
    parts = split_synthesis(synthesis)
    md = [f"## {topic['title']}", "", f"**Thesis:** {topic['claim']}", "",
          f"**Source:** {', '.join(topic['sources']) or topic_label(topic)}"]
    if link:
        md.append(f"**Buzz:** {link}")
    md += ["", "### Decision summary", ""]
    if score:
        md += [render_verdict(score), ""]
    md += [parts.get("synthesis") or "(the moderator did not answer)", ""]
    if score:
        md += [render_scorecard(score), ""]
    md += ["### Proposal", "", parts.get("proposal") or "None", ""]
    md += ["### Method trace", ""]
    if parts.get("method trace"):
        md += [parts["method trace"], ""]
    if score:
        md += [render_method_tables(r1, r2, score), ""]
    body = ["**Round 1**", ""]
    for name, txt in r1.items():
        body += [f"**{name}**", "", txt.strip(), ""]
    body += ["**Round 2**", ""]
    for name, txt in r2.items():
        body += [f"**{name}**", "", txt.strip(), ""]
    md += ["> [!note]- Full transcript", _quote("\n".join(body)), ""]
    return "\n".join(md)


CARD_LABELS = {
    "tr": {"thesis": "Tez", "verdict": "Karar", "Go": "Devam", "Stop": "Dur", "Test first": "Önce test et",
           "YES": "EVET", "NO": "HAYIR", "CONDITIONAL": "ŞARTLI", "ABSTAIN": "pas", "median": "medyan",
           "no_reply": "cevap yok", "clash": "Çatışma", "most": "en çok tartışılan itiraz: {n} ({k} persona)",
           "moved": "oy değiştiren: {m} ({e} yeni kanıtla)", "result": "Sonuç", "test": "Test",
           "ask": "Sana soru", "lab": "Tartışma", "note": "Not"},
    "en": {"thesis": "Thesis", "verdict": "Verdict", "Go": "Go", "Stop": "Stop", "Test first": "Test first",
           "YES": "YES", "NO": "NO", "CONDITIONAL": "CONDITIONAL", "ABSTAIN": "pass", "median": "median",
           "no_reply": "no reply", "clash": "Clash", "most": "most argued objection: {n} ({k} personas)",
           "moved": "moved: {m} ({e} with new evidence)", "result": "Result", "test": "Test",
           "ask": "Question for you", "lab": "Debate", "note": "Note"},
}


def _cut(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "..."


def topic_lang(topic):
    """Language of an ad-hoc or vault topic. Short text falls back to the vault
    default in lang_detect; a short thesis with no Turkish letters is English."""
    text = f"{topic['title']} {topic['claim']}"
    code, conf = detect(text)
    if conf == 0.0 and text.isascii():
        return "en"
    return code


def render_card(topic, r1, r2, synthesis, score, link=None, note=None):
    """The stage card: the only thing the owner has to read. One line per
    persona, the clash from round 2, the moderator's result, one question. In
    the language of the topic; the full debate stays backstage and in the note."""
    L = CARD_LABELS.get(topic.get("lang"), CARD_LABELS["en"])
    call, median, votes = verdict(score)
    tally = ", ".join(f"{votes.count(v)} {L[v]}" for v in VOTES if votes.count(v))
    med = "" if median is None else (f", {L['median']} %{median}" if topic.get("lang") == "tr" else f", {L['median']} {median}%")
    md = [f"## {topic['title']}", "", f"**{L['thesis']}:** {_cut(topic['claim'], 220)}",
          f"**{L['verdict']}:** {L[call]}" + (f" ({tally}{med})" if tally else ""), ""]
    by = {r["persona"]: r for r in score["rows"]}
    for name, txt in r1.items():
        row = by.get(name, {})
        final = L.get(row.get("r1_vote"), "?")
        if row.get("replied_r2") and row.get("r2_vote") and row.get("r2_vote") != row.get("r1_vote"):
            final += " → " + L.get(row["r2_vote"], "?")
        if txt == NO_REPLY:
            md.append(f"**{name}** · {L['no_reply']}")
        elif PASS_RE.match(txt):
            md.append(f"**{name}** · {L['ABSTAIN']}")
        else:
            md.append(f"**{name}** · {final} · {_cut(_field(txt, 'strongest objection', 400), 150) or '?'}")
    # Clash: whose objection round 2 picked most, and who moved.
    picks = {}
    for txt in r2.values():
        chosen = _field(txt, "chosen objection", 400)
        for name in r1:
            if name.lower() in chosen.lower():
                picks[name] = picks.get(name, 0) + 1
                break
    clash = []
    if picks:
        top = max(picks, key=picks.get)
        clash.append(L["most"].format(n=top, k=picks[top]))
    clash.append(L["moved"].format(m=score["moved"], e=score["moved_with_evidence"]))
    md += ["", f"**{L['clash']}:** " + "; ".join(clash)]
    parts = split_synthesis(synthesis)
    body = parts.get("synthesis", "")
    result = _field(body, "conclusion", 400)
    if result:
        md.append(f"**{L['result']}:** {_cut(result, 300)}")
    test = _field(body, "proposed test", 300)
    if test:
        md.append(f"**{L['test']}:** {_cut(test, 200)}")
    # One question: from the persona whose objection drew the most fire, else the first that asked one.
    order = ([max(picks, key=picks.get)] if picks else []) + list(r1)
    for name in order:
        q = _field(r1.get(name, ""), "question for " + OWNER.lower(), 300) or _field(r1.get(name, ""), "question for", 300)
        if q:
            md.append(f"**{L['ask']}:** {_cut(q, 200)} ({name})")
            break
    tail = []
    if link:
        tail.append(f"{L['lab']}: {link}")
    if note:
        tail.append(f"{L['note']}: `{os.path.relpath(note, VAULT) if os.path.isabs(note) else note}`")
    if tail:
        md += ["", " · ".join(tail)]
    return "\n".join(md)


def coverage_table(day, date_str):
    files = todays_capture_files(date_str)
    rows = ["| Capture | Topic | Run |", "|---|---|---|"]
    for name in files:
        info = day["captures"].get(name)
        if info:
            rows.append(f"| {name} | {info['topic']} | {info['run']} |")
        else:
            rows.append(f"| {name} | **skipped** | |")
    return "\n".join(rows)


def file_note(md, title, summary, lang="auto"):
    """lang defaults to auto: file_query detects the body language, so a Turkish
    debate is indexed as Turkish instead of being mislabelled English."""
    r = subprocess.run([sys.executable, os.path.join(VAULT, "tools", "file_query.py"), "dialectic", title,
                        "--summary", summary, "--lang", lang], cwd=VAULT, input=md, capture_output=True,
                       text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"file_query failed: {r.stderr[:200]}")
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""


def buzz_post_sh(identity, channel, text):
    script = os.path.join(VAULT, ".agents", "scripts", "buzz_post.sh")
    if not os.access(script, os.X_OK):
        return
    try:
        subprocess.run([script, identity, channel], input=text, text=True, capture_output=True,
                       timeout=45, cwd=VAULT)
    except Exception as exc:
        log(f"buzz post skipped: {exc}")


NIGHT_KEEP = 10
NIGHT_USABLE_SHARE = 2 / 3     # a night counts as usable when this share of local replies passed the judge


def write_status(date_str, mode, result, detail):
    """Worker-owned status file read by health_check (Mac), watchdog and the
    briefing. Day rounds are `- <date> <mode>:` lines (last 14). The night
    experiment keeps its own `- night <date>:` lines in a second section so the
    checks that read the last day line never see a night line."""
    all_lines = read_file(STATUS_MD).splitlines()
    nights = [l for l in all_lines if l.startswith("- night ")]
    days = [l for l in all_lines if l.startswith("- ") and not l.startswith("- night ")]
    if mode == "night":
        nights = [l for l in nights if not l.startswith(f"- night {date_str}:")] + [f"- night {date_str}: {result}, {detail}"]
        nights = nights[-NIGHT_KEEP:]
    else:
        days = [l for l in days if not l.startswith(f"- {date_str} {mode}:")] + [f"- {date_str} {mode}: {result}, {detail}"]
        days = days[-14:]
    text = ("# Dialectic status\n\n"
            "Automatic: tools/dialectic.py (worker, 12:30 and 21:20). Last 14 runs; "
            "health_check and watchdog read this file.\n\n" + "\n".join(days) + "\n")
    if nights:
        text += ("\n## Night experiment (local personas)\n\n"
                 f"02:00 on the worker, persona lane on the local model, moderator on the cloud. A night is usable "
                 f"when at least {round(100 * NIGHT_USABLE_SHARE)}% of the local replies passed the judge. Kill rule: "
                 "after five nights, fewer than three usable nights closes the timer.\n\n" + "\n".join(nights) + "\n")
    write_file(STATUS_MD, text)


def night_topic(date_str):
    """Replay the day's first cloud topic when there was one, so the same thesis
    is argued by both models; otherwise a vault topic. None on a truly empty day."""
    try:
        raw = json.loads(read_file(DAY_FILE) or "{}")
    except ValueError:
        raw = {}
    yesterday = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    if raw.get("topics") and raw.get("date") in (date_str, yesterday):
        t = dict(raw["topics"][0])
        t.setdefault("sources", [])
        t["replay"] = raw["date"]
        return t
    return pick_vault_topic()


def judge_local_replies(topic, r1, r2):
    """The cloud moderator grades every local reply usable or not. Deterministic
    first: no Vote line is unusable whatever the prose says. Returns
    ({persona: {"r1": bool, "r2": bool}}, usable, total)."""
    replies = {}
    for rnd, rd in (("r1", r1), ("r2", r2)):
        for name, txt in rd.items():
            if txt != NO_REPLY:
                replies[f"{name} {rnd}"] = txt
    parsed = {k: bool(VOTE_RE.search(v)) for k, v in replies.items()}
    blob = "\n\n".join(f"### {k}\n{v[:1800]}" for k, v in replies.items())[:MAX_CONTEXT]
    prompt = f"""Below are persona replies from a critical dialectic round, produced by a small local model. Grade each reply "usable" or "unusable". Usable means all of: it argues the thesis (not a generic essay), it stays in the named persona's method, it is coherent and in the language of the thesis, it does not invent vault files or facts that are not in the thesis, and it ends with the Vote and Number lines.

# THESIS: {topic['title']}
{topic['claim'][:1500]}

# REPLIES
{blob}

Write only a JSON object mapping each reply heading (exactly as written after ###) to "usable" or "unusable". Nothing else."""
    verdicts = {}
    out = run_prompt(prompt, timeout=300, lane="dialectic-judge") or ""
    m = re.search(r"\{.*\}", out, re.S)
    if m:
        try:
            verdicts = {str(k): str(v).strip().lower() for k, v in json.loads(m.group(0)).items()}
        except ValueError:
            verdicts = {}
    grades = {}
    for k in replies:
        name, rnd = k.rsplit(" ", 1)
        ok = parsed[k] and verdicts.get(k, "unusable") == "usable"
        grades.setdefault(name, {})[rnd] = ok
    usable = sum(1 for g in grades.values() for ok in g.values() if ok)
    return grades, usable, len(replies)


def render_judgement(grades, usable, total):
    md = [f"**Local replies usable:** {usable}/{total} (graded by the cloud moderator; a reply without a Vote line is unusable)", "",
          "| Persona | Round 1 | Round 2 |", "|---|---|---|"]
    for name, g in grades.items():
        cell = lambda k: ("usable" if g.get(k) else ("unusable" if k in g else NO_REPLY))
        md.append(f"| {name} | {cell('r1')} | {cell('r2')} |")
    return "\n".join(md)


# ------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", choices=["noon", "evening", "night"])
    ap.add_argument("--topic")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--sequential", action="store_true", help="(default) mention personas one at a time")
    ap.add_argument("--parallel", action="store_true", help="mention every persona at once")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scorecard", action="store_true",
                    help="print the rolling scorecard computed from this machine's score log, then exit")
    args = ap.parse_args()
    if args.scorecard:
        # Print only. The file is written by the scheduled worker run so a laptop
        # test never overwrites the worker's copy (same rule as DIALECTIC-STATUS.md).
        print(no_dashes(scorecard_markdown()))
        return

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    mode = args.run or ("noon" if now.hour < 17 else "evening")
    seen = load_seen()
    day = load_day(date_str)

    if mode == "night":
        # Local-model experiment: one topic, personas on the worker's model,
        # nothing scored, nothing marked seen. See the module docstring.
        topic = night_topic(date_str)
        if not topic:
            log("Night: nothing to argue (no day topic, no vault topic).")
            if not args.dry_run:
                write_status(date_str, mode, "idle", "no topic")
            return
        captures = [("night-topic", topic["claim"])]
        topics = [topic]
        args.local = True
    elif args.topic:
        captures = [("ad-hoc-topic", args.topic)]
        topics = [{"title": args.topic[:80], "claim": args.topic, "sources": []}]
    else:
        captures = collect_captures(date_str, seen)
        if not captures:
            # No raw material today. The evening slot argues one vault topic
            # (a decision past review, a stale belief, a live question) so the
            # loop keeps closing on silent days; noon stays idle.
            fallback = pick_vault_topic() if (mode == "evening" and not day["runs"]) else None
            if not fallback:
                log("No new captures; round skipped.")
                if not args.dry_run:
                    write_status(date_str, mode, "idle", "no new captures")
                return
            log(f"No new captures; vault topic instead: {fallback['title']}")
            topics = [fallback]
        else:
            topics = cluster_topics(captures)
    log(f"{mode}: {len(captures)} captures, {len(topics)} topics")

    beliefs = beliefs_summary()
    calib = calibration_lines()
    for t in topics:
        if not t.get("context"):     # a replayed topic carries the day's context already
            t["context"] = wiki_search(f"{t['title']} {t['claim'][:120]}")
        t["beliefs"], t["calib"] = beliefs, calib
        t.setdefault("lang", topic_lang(t))

    if args.dry_run:
        for t in topics:
            print("=" * 70)
            print(round_text(1, t, t["context"], persona=PERSONAS[0][1], brief="<brief file, round 1>"))
            print("-" * 70)
            print(f"(round 1: {len(PERSONAS)} isolated roots like the one above, one per persona; "
                  f"round 2: one root quoting all replies, layout below)")
            print("-" * 70)
            print(round_text(2, t, t["context"], r1={name: "(round 1 reply)" for _, name in PERSONAS},
                             brief="<brief file, round 2>"))
            print("-" * 70)
            print("Brief file (prior context + beliefs):")
            print(round_text(1, t, t["context"], persona=PERSONAS[0][1]))
        print("=" * 70)
        print("Coverage (this run):")
        for t in topics:
            print(f"- {t['title']}: {', '.join(t['sources'])}")
        return

    sections, replies, expected, links, participants, scores, cards = [], 0, 0, [], [], [], []
    started = time.time()
    judged = None
    for t in topics:
        try:
            if args.local:
                channel, root, r1, r2 = run_rounds_local(t, t["context"])
            else:
                channel, root, r1, r2 = run_rounds_buzz(t, t["context"], not args.parallel)
        except Exception as exc:
            log(f"round failed ({t['title']}): {exc}")
            write_status(date_str, mode, "error", str(exc)[:120])
            sys.exit(1)
        replies += sum(1 for v in list(r1.values()) + list(r2.values()) if v != NO_REPLY)
        expected += 2 * len(r1)
        participants += [n for n in r1 if n not in participants]
        score = score_topic(r1, r2)
        scores.append(score)
        synthesis = synthesize(t, r1, r2, "\n".join(t["context"]), beliefs, calib, score)
        link = None
        if root and channel:
            link = f"buzz://message?channel={channel}&id={root}"
            post(channel, render_verdict(score) + "\n\n" + render_scorecard(score) + "\n\n" + synthesis, reply_to=root)
            links.append(link)
            cards.append((t, r1, r2, synthesis, score, link))
        section = render_topic(t, r1, r2, synthesis, link, score)
        if mode == "night":
            judged = judge_local_replies(t, r1, r2)
            section = section.replace("### Decision summary", render_judgement(*judged) + "\n\n### Decision summary", 1)
            sections.append(section)
            continue
        sections.append(section)
        append_score(date_str, mode, t, score)
        for s in t["sources"]:
            day["captures"][s] = {"topic": t["title"], "run": mode}
        day["topics"].append({"title": t["title"], "claim": t["claim"], "context": t["context"]})

    # Ad-hoc topics get their own file so they never overwrite the scheduled round's note.
    title = f"Topic {args.topic[:50]}" if args.topic else f"{mode.capitalize()} round"
    minutes = round((time.time() - started) / 60)
    if mode == "night":
        title = "Night round (local personas)"
        origin = (f"replay of the {topics[0]['replay']} round" if topics[0].get("replay")
                  else f"vault topic ({topics[0].get('fallback_key', '?')})")
        head = (f"Experiment: personas on the worker's local model, moderator on the cloud. Topic: {origin}. "
                f"Replies: {replies}/{expected}. Usable: {judged[1]}/{judged[2]}. Wall time: {minutes} min.")
    else:
        head = (f"Personas: {', '.join(participants) or 'none'}. Replies: {replies}/{expected}. "
                f"Channel: #{CHANNEL_NAME}." + (" Local mode (no Buzz)." if args.local else ""))
    md = [f"# {title}", "", head, ""]
    md += sections
    if mode == "evening":
        scan = connection_scan(day)
        md += ["## Connection scan", "", scan or "(not produced)", "",
               "## Coverage", "", coverage_table(day, date_str), ""]
    text = no_dashes("\n".join(md))
    summary = (f"Dialectic {mode} round {date_str}: {len(topics)} topics argued by {len(participants)} critical-thinking "
               f"personas on Buzz, {replies}/{expected} replies, with synthesis" +
               (", whole-day connection scan and coverage table." if mode == "evening" else "."))
    if mode == "night":
        summary = (f"Night dialectic {date_str}: one topic argued by {len(participants)} personas on the worker's local "
                   f"model, {replies}/{expected} replies, {judged[1]}/{judged[2]} judged usable, cloud synthesis.")
    path = file_note(text, title, summary)
    log(f"filed: {path}")
    stage = channel_id(CHANNEL_NAME)
    for t, r1, r2, synthesis, score, link in cards:
        if stage:
            post(stage, render_card(t, r1, r2, synthesis, score, link, path))

    if mode == "night":
        usable_night = judged[2] > 0 and judged[1] / judged[2] >= NIGHT_USABLE_SHARE
        write_status(date_str, mode, "ok", f"{'usable' if usable_night else 'not usable'}, {judged[1]}/{judged[2]} replies passed, "
                     f"{replies}/{expected} replies, {minutes} min, {path}")
        log(f"night: {judged[1]}/{judged[2]} usable, {minutes} min")
        return

    if not args.topic:
        seen.update(name for name, _ in captures)
        save_seen(seen)
    for t in topics:
        if t.get("fallback_key"):
            mark_vault_topic(t, date_str)
    day["runs"].append({"mode": mode, "time": now.strftime("%H:%M"), "path": path, "replies": replies,
                        "expected": expected})
    save_day(day)
    yes = sum(sc["yes"] for sc in scores); voted = sum(sc["voted"] for sc in scores)
    unanimous = sum(1 for sc in scores if sc["unanimous"]); moved = sum(sc["moved"] for sc in scores)
    both = sum(sc["both"] for sc in scores)
    stats = (f"affirm {yes}/{voted} ({_pct(yes / voted if voted else None)}), unanimous {unanimous}/{len(scores)}, "
             f"moved {moved}/{both}")
    log(f"scorecard: {stats}")
    if not args.local:  # status and scorecard files are worker-owned; a local test run must not race them
        write_scorecard()
        kind = "vault topic" if any(t.get("fallback_key") for t in topics) else "topics"
        write_status(date_str, mode, "ok", f"{len(topics)} {kind}, {replies}/{expected} replies, {stats}, {path}")

    if mode == "evening":
        heads = "\n".join(f"- {t['title']}" for t in day["topics"])
        buzz_post_sh("briefing", "daily",
                     f"🗣️ Dialectic evening round: {len(day['topics'])} topics, {replies}/{expected} persona replies, "
                     f"{stats}.\n{heads}\nNote: {path}")


if __name__ == "__main__":
    main()
