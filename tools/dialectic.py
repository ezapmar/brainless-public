#!/usr/bin/env python3
"""Critical dialectic engine (moderator) for the brainless vault.

Twice a day (12:30 and 21:20 on the always-on worker) the moderator takes the day's raw
captures (Telegram and Buzz voice notes, text, photos already transcribed into
Thinking/Daily/*-telegram.md and *-buzz.md), clusters them into topics and has
five live persona agents argue each topic in the Buzz channel #dialectic:

  Skeptic (Browne & Keeley), Gambler (Annie Duke), Scientist (Camuffo 2024),
  Postmortem (Edmondson), Strategist (Lafley & Martin).

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
  --topic "text"       argue one ad-hoc topic instead of today's captures.
  --local              run the personas through tools/llm.py instead of Buzz
                       (fallback when the relay is unreachable; also the Mac test).
  --sequential         mention personas one at a time (rate-limit fallback).
  --dry-run            steps 1 to 3 only; print what would be posted.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt  # noqa: E402
from calibrate import scan as calibration_scan  # noqa: E402
from owner_profile import OWNER, lang_name  # noqa: E402

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
CHANNEL_NAME = "dialectic"
MODERATOR = "moderator"
PERSONAS = [("skeptic", "Skeptic"), ("gambler", "Gambler"), ("scientist", "Scientist"),
            ("postmortem", "Postmortem"), ("strategist", "Strategist")]
NO_REPLY = "no reply"
ROUND1_WAIT = 480   # seconds to wait for round 1 replies
ROUND2_WAIT = 360
SEQ_WAIT = 180      # per persona in --sequential mode
POLL = 30
MAX_TOPICS = 6
MAX_CONTEXT = 12000
EXCLUDE = ("Contrarian Theses",)


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


def wiki_search(query, k=5):
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
        out.append(f"- `{path}`: {snippet.strip()[:220]}")
        if len(out) >= k:
            break
    return out


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
- "title": short English title (at most 8 words)
- "claim": the thesis {OWNER} defends or assumes on this topic, one paragraph in English, third person ("{OWNER} thinks ...")
- "sources": list of file names that feed this topic (the names on the --- lines above, verbatim)
Every file must belong to at least one topic. Write only a JSON array, nothing else. No em or en dashes.

{blob}"""
    topics = parse_json(run_prompt(prompt, timeout=240))
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
    return clean


def synthesize(topic, r1, r2, context, beliefs, calib):
    transcript = "\n\n".join(f"### {name} (round 1)\n{txt}" for name, txt in r1.items())
    transcript += "\n\n" + "\n\n".join(f"### {name} (round 2)\n{txt}" for name, txt in r2.items())
    prompt = f"""You are the moderator of the brainless critical dialectic engine. Five personas argued the thesis below.
Write a neutral, short synthesis in English. No em or en dashes. Do not invent; add nothing that is not in the debate.

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

Write exactly this format, nothing else:
### Synthesis
**Strongest counterargument:** (name who raised it)
**What would have to be true:** (at most 3 items)
**Proposed test:** (one, cheap, dated)
**Bet:** (probability the thesis proves right within 12 months, a percentage and a one-sentence reason)
**Contradiction:** (if it clashes with one of the beliefs or a decision in the calendar, one sentence with [[Note name]]; else "None")
**Changed minds:** (personas whose view changed in round 2 and why; else "Nobody")
### Proposal
(one paste-ready draft for Thinking/Ideas, Beliefs or Decisions; else "None")"""
    return run_prompt(prompt, timeout=300) or "### Synthesis\n(the LLM did not answer)"


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
English, no em or en dashes, no invention; where there is no wiki match say "none".
{blob[:MAX_CONTEXT]}"""
    return run_prompt(prompt, timeout=240) or ""


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


def wait_replies(channel, root_id, wanted, since, timeout):
    """wanted: {pubkey: display}. Returns {display: content} of replies after `since`."""
    deadline = time.time() + timeout
    got = {}
    while time.time() < deadline:
        for m in thread_messages(channel, root_id):
            pk = m.get("pubkey")
            ts = int(m.get("created_at") or 0)
            if pk in wanted and ts >= since and m.get("id") != root_id:
                name = wanted[pk]
                if name not in got or ts > got[name][0]:
                    got[name] = (ts, m.get("content") or "")
        if len(got) >= len(wanted):
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


def round_text(n, topic, context):
    if n == 1:
        return (f"## {topic['title']}\n\n**Thesis:** {topic['claim']}\n\n"
                f"**Source:** {', '.join(topic['sources']) or 'ad-hoc topic'}\n\n"
                f"**Prior context:**\n" + ("\n".join(context) if context else "- (no wiki match)") +
                "\n\n**Round 1:** each persona tests the thesis with its own method. At most 250 words, "
                f"ending with Finding / Strongest objection / Question for {OWNER}.")
    return ("**Round 2:** read the other personas' replies in this thread. Pick the strongest objection other "
            "than your own, agree with it or refute it (at most 3 sentences). End with Chosen objection / "
            "My answer / Did my view change.")


def run_rounds_buzz(topic, context, sequential):
    channel = channel_id(CHANNEL_NAME)
    if not channel:
        raise RuntimeError(f"channel #{CHANNEL_NAME} not in channels.json")
    wanted = {}
    for slug, name in PERSONAS:
        pk = pubkey(slug)
        if pk:
            wanted[pk] = name
        else:
            log(f"persona {slug}: no key, skipped")
    since = int(time.time()) - 5
    if sequential:
        root = post(channel, round_text(1, topic, context))
    else:
        root = post(channel, round_text(1, topic, context), mentions=list(wanted))
    if not root:
        raise RuntimeError("root message id not returned by relay")
    if sequential:
        r1 = {}
        for pk, name in wanted.items():
            post(channel, f"{name}, round 1 is yours.", reply_to=root, mentions=[pk])
            r1.update(wait_replies(channel, root, {pk: name}, since, SEQ_WAIT))
    else:
        r1 = wait_replies(channel, root, wanted, since, ROUND1_WAIT)
    log(f"round 1: {len(r1)}/{len(wanted)} replies")
    since2 = int(time.time()) - 5
    if sequential:
        r2 = {}
        for pk, name in wanted.items():
            post(channel, round_text(2, topic, context), reply_to=root, mentions=[pk])
            r2.update(wait_replies(channel, root, {pk: name}, since2, SEQ_WAIT))
    else:
        post(channel, round_text(2, topic, context), reply_to=root, mentions=list(wanted))
        r2 = wait_replies(channel, root, wanted, since2, ROUND2_WAIT)
    log(f"round 2: {len(r2)}/{len(wanted)} replies")
    for name in wanted.values():
        r1.setdefault(name, NO_REPLY)
        r2.setdefault(name, NO_REPLY)
    return channel, root, r1, r2


def run_rounds_local(topic, context):
    rules = team_rules_local()
    head = round_text(1, topic, context)
    r1 = {}
    for slug, name in PERSONAS:
        out = run_prompt(f"{persona_prompt(slug)}\n\n{rules}\n\n# MODERATOR MESSAGE\n{head}", timeout=240)
        r1[name] = out or NO_REPLY
    others = "\n\n".join(f"### {n}\n{t}" for n, t in r1.items())
    r2 = {}
    for slug, name in PERSONAS:
        out = run_prompt(f"{persona_prompt(slug)}\n\n{rules}\n\n# MODERATOR MESSAGE\n{head}\n\n"
                         f"# ROUND 1 REPLIES\n{others}\n\n# MODERATOR\n{round_text(2, topic, context)}",
                         timeout=240)
        r2[name] = out or NO_REPLY
    return None, None, r1, r2


# ------------------------------------------------------------- output

def render_topic(topic, r1, r2, synthesis, link=None):
    md = [f"## {topic['title']}", "", f"**Thesis:** {topic['claim']}", "",
          f"**Source:** {', '.join(topic['sources']) or 'ad-hoc topic'}"]
    if link:
        md.append(f"**Buzz:** {link}")
    md += ["", "### Round 1"]
    for name, txt in r1.items():
        md += [f"#### {name}", txt.strip(), ""]
    md += ["### Round 2"]
    for name, txt in r2.items():
        md += [f"#### {name}", txt.strip(), ""]
    md += [synthesis.strip(), ""]
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


def file_note(md, title, summary):
    r = subprocess.run([sys.executable, os.path.join(VAULT, "tools", "file_query.py"), "dialectic", title,
                        "--summary", summary, "--lang", "en"], cwd=VAULT, input=md, capture_output=True,
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


def write_status(date_str, mode, result, detail):
    """Worker-owned status file read by health_check (Mac), watchdog and the briefing."""
    line = f"- {date_str} {mode}: {result}, {detail}"
    old = [l for l in read_file(STATUS_MD).splitlines() if l.startswith("- ")]
    old = [l for l in old if not l.startswith(f"- {date_str} {mode}:")]
    lines = (old + [line])[-14:]
    write_file(STATUS_MD, "# Dialectic status\n\n"
               "Automatic: tools/dialectic.py (worker, 12:30 and 21:20). Last 14 runs; "
               "health_check and watchdog read this file.\n\n" + "\n".join(lines) + "\n")


# ------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", choices=["noon", "evening"])
    ap.add_argument("--topic")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--sequential", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    mode = args.run or ("noon" if now.hour < 17 else "evening")
    seen = load_seen()
    day = load_day(date_str)

    if args.topic:
        captures = [("ad-hoc-topic", args.topic)]
        topics = [{"title": args.topic[:80], "claim": args.topic, "sources": []}]
    else:
        captures = collect_captures(date_str, seen)
        if not captures:
            log("No new captures; round skipped.")
            if not args.dry_run:
                write_status(date_str, mode, "idle", "no new captures")
            return
        topics = cluster_topics(captures)
    log(f"{mode}: {len(captures)} captures, {len(topics)} topics")

    beliefs = beliefs_summary()
    calib = calibration_lines()
    for t in topics:
        t["context"] = wiki_search(f"{t['title']} {t['claim'][:120]}")

    if args.dry_run:
        for t in topics:
            print("=" * 70)
            print(round_text(1, t, t["context"]))
        print("=" * 70)
        print("Coverage (this run):")
        for t in topics:
            print(f"- {t['title']}: {', '.join(t['sources'])}")
        return

    sections, replies, expected, links, participants = [], 0, 0, [], []
    for t in topics:
        try:
            if args.local:
                channel, root, r1, r2 = run_rounds_local(t, t["context"])
            else:
                channel, root, r1, r2 = run_rounds_buzz(t, t["context"], args.sequential)
        except Exception as exc:
            log(f"round failed ({t['title']}): {exc}")
            write_status(date_str, mode, "error", str(exc)[:120])
            sys.exit(1)
        replies += sum(1 for v in list(r1.values()) + list(r2.values()) if v != NO_REPLY)
        expected += 2 * len(r1)
        participants += [n for n in r1 if n not in participants]
        synthesis = synthesize(t, r1, r2, "\n".join(t["context"]), beliefs, calib)
        link = None
        if root and channel:
            link = f"buzz://message?channel={channel}&id={root}"
            post(channel, synthesis, reply_to=root)
            links.append(link)
        sections.append(render_topic(t, r1, r2, synthesis, link))
        for s in t["sources"]:
            day["captures"][s] = {"topic": t["title"], "run": mode}
        day["topics"].append({"title": t["title"], "claim": t["claim"], "context": t["context"]})

    # Ad-hoc topics get their own file so they never overwrite the scheduled round's note.
    title = f"Topic {args.topic[:50]}" if args.topic else f"{mode.capitalize()} round"
    md = [f"# {title}", "",
          f"Personas: {', '.join(participants) or 'none'}. Replies: {replies}/{expected}. "
          f"Channel: #{CHANNEL_NAME}." + (" Local mode (no Buzz)." if args.local else ""), "",
          "## Topics", ""]
    md += sections
    if mode == "evening":
        scan = connection_scan(day)
        md += ["## Connection scan", "", scan or "(not produced)", "",
               "## Coverage", "", coverage_table(day, date_str), ""]
    text = no_dashes("\n".join(md))
    summary = (f"Dialectic {mode} round {date_str}: {len(topics)} topics argued by five critical-thinking "
               f"personas on Buzz, {replies}/{expected} replies, with synthesis" +
               (", whole-day connection scan and coverage table." if mode == "evening" else "."))
    path = file_note(text, title, summary)
    log(f"filed: {path}")

    if not args.topic:
        seen.update(name for name, _ in captures)
        save_seen(seen)
    day["runs"].append({"mode": mode, "time": now.strftime("%H:%M"), "path": path, "replies": replies,
                        "expected": expected})
    save_day(day)
    if not args.local:  # status file is worker-owned; a local test run must not race it
        write_status(date_str, mode, "ok", f"{len(topics)} topics, {replies}/{expected} replies, {path}")

    if mode == "evening":
        heads = "\n".join(f"- {t['title']}" for t in day["topics"])
        buzz_post_sh("briefing", "daily",
                     f"🗣️ Dialectic evening round: {len(day['topics'])} topics, {replies}/{expected} persona replies.\n"
                     f"{heads}\nNote: {path}")


if __name__ == "__main__":
    main()
