#!/usr/bin/env python3
"""Thinking loop over Telegram (worker).

Purpose: move the reflection step (decision grading, prediction, belief
challenge, cadence step, seed idea) to the one channel the owner actually
answers on, Telegram.

Flow:
  1. `--ask` (Sunday 19:00, brainless-thinking.timer): picks ONE question in
     priority order, sends it to Telegram, records the pending question in state.
  2. The owner REPLIES to that message (voice or text); telegram_capture tries
     this module first on every turn via `try_handle`. The answer goes to the
     LLM, which produces a DRAFT to be written into the target note; the draft
     is sent back as a preview.
  3. "apply" -> the draft is written into the human area (Thinking/), the change
     is filed to loopback. "cancel" -> the draft is discarded. "skip" -> the
     question is skipped and a different kind of question comes next week.

Safety: the LLM only sees embedded text, no tools; the output is parsed as JSON
and the fields are sanitized. Writing under Thinking/ happens ONLY after "apply"
(AGENT-RULES rule 2: explicit approval). Written files come from a fixed list;
the LLM cannot choose a file path.
State: .agents/state/thinking_loop.json (gitignored).
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
from llm import run_prompt  # noqa: E402
from owner_profile import OWNER, OWNER_FULL, WORKER, LANG, output_lang_directive  # noqa: E402
from i18n import t, t_list  # noqa: E402

STATE_FILE = os.path.join(VAULT, ".agents", "state", "thinking_loop.json")
CONF_DIR = os.path.expanduser("~/.config/brainless")
DEC_DIR = os.path.join(VAULT, "Thinking", "Decisions")
BELIEF_DIR = os.path.join(VAULT, "Thinking", "Beliefs")
IDEAS_DIR = os.path.join(VAULT, "Thinking", "Ideas")
DAILY_DIR = os.path.join(VAULT, "Thinking", "Daily")
CADENCE = os.path.join(VAULT, "Thinking", "Thinking Cadence.md")
CALIBRATION = os.path.join(VAULT, "Thinking", "Calibration.md")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
QUERIES_DIR = os.path.join(VAULT, ".wiki", "digests", "queries")

SEED_GAP_DAYS = 14        # ask the seed question when no new seed for this many days
PENDING_TTL_DAYS = 13     # an unanswered question expires after two weeks
KINDS_ORDER = ("grade", "predict", "cadence", "belief", "seed")
# Reply keywords: current language plus English, so both kinds of vault work.
APPLY_WORDS = tuple(t_list("thinking_loop.apply_words"))
CANCEL_WORDS = tuple(t_list("thinking_loop.cancel_words"))
SKIP_WORDS = tuple(t_list("thinking_loop.skip_words"))
ANSWER_PREFIXES = tuple(t_list("thinking_loop.answer_prefix"))

FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] thinking_loop: {msg}")


def read(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def fm(text):
    m = FM_RE.match(text)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                out[k.strip()] = v.partition("#")[0].strip().strip('"')
    return out


def set_fm(text, key, value):
    """Replace or add the key line in the frontmatter (comments are dropped)."""
    m = FM_RE.match(text)
    if not m:
        return f"---\n{key}: {value}\n---\n" + text
    block = m.group(1)
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}\s*:", line):
            lines[i] = f"{key}: {value}"
            break
    else:
        lines.append(f"{key}: {value}")
    return text[:m.start(1)] + "\n".join(lines) + text[m.end(1):]


def clean(s, limit=600):
    """Reduce an LLM field to one piece: newlines are kept but link/path/
    dash injection is cut; em/en dashes are banned by house rule."""
    s = str(s).replace("\u2014", ", ").replace("\u2013", "-")
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    return s[:limit]


def send(text, reply_to=None):
    """Send a Telegram message; returns message_id (for state)."""
    import urllib.parse
    import urllib.request
    token = read(os.path.join(CONF_DIR, "telegram_token")).strip()
    chat = read(os.path.join(CONF_DIR, "telegram_chat_id")).strip()
    if not (token and chat):
        log("telegram not configured; message not sent")
        return None
    params = {"chat_id": chat, "text": text}
    if reply_to:
        params["reply_to_message_id"] = reply_to
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30) as r:
        out = json.load(r)
    return (out.get("result") or {}).get("message_id")


# ---------------------------------------------------------------------------
# Question selection
# ---------------------------------------------------------------------------
def next_cadence_step():
    for line in read(CADENCE).splitlines():
        m = re.match(r"\s*- \[ \]\s+(.+)", line)
        if m:
            return m.group(1).strip()
    return None


def stalest_belief():
    best = None
    if not os.path.isdir(BELIEF_DIR):
        return None
    for f in sorted(os.listdir(BELIEF_DIR)):
        if not f.endswith(".md"):
            continue
        meta = fm(read(os.path.join(BELIEF_DIR, f)))
        key = meta.get("last_challenged", "0000-00-00")
        if best is None or key < best[0]:
            best = (key, f[:-3])
    return best


def newest_seed_age_days():
    newest = 0
    if os.path.isdir(IDEAS_DIR):
        for f in os.listdir(IDEAS_DIR):
            if f.endswith(".md") and f.lower() != "readme.md":
                newest = max(newest, os.path.getmtime(os.path.join(IDEAS_DIR, f)))
    return (time.time() - newest) / 86400 if newest else 999


def candidates():
    """List of (kind, target, question) in priority order."""
    from calibrate import scan as calibration_scan
    due, needs_pred, _ = calibration_scan()
    out = []
    for name, rev in due:
        out.append(("grade", name, t("thinking_loop.q_grade", name=name, review=rev)))
    for name in needs_pred:
        out.append(("predict", name, t("thinking_loop.q_predict", name=name)))
    step = next_cadence_step()
    if step:
        out.append(("cadence", step, t("thinking_loop.q_cadence", step=step)))
    b = stalest_belief()
    if b:
        out.append(("belief", b[1], t("thinking_loop.q_belief", name=b[1], date=b[0])))
    if newest_seed_age_days() > SEED_GAP_DAYS:
        out.append(("seed", "", t("thinking_loop.q_seed")))
    return out


def pick_question(state):
    skipped = set(state.get("skipped", []))          # "kind:target"
    recent = state.get("recent_kinds", [])
    cands = candidates()
    if not cands:
        return None
    fresh = [c for c in cands if f"{c[0]}:{c[1]}" not in skipped]
    pool = fresh or cands
    # Do not ask the same kind three weeks in a row; take a different kind if there is one.
    if len(recent) >= 2 and recent[-1] == recent[-2]:
        alt = [c for c in pool if c[0] != recent[-1]]
        pool = alt or pool
    return pool[0]


def ask(dry_run=False):
    state = load_state()
    if state.get("phase") in ("asked", "drafted"):
        age = (time.time() - state.get("asked_at", 0)) / 86400
        if age < PENDING_TTL_DAYS:
            log(f"a pending question exists ({state.get('kind')}, {age:.0f} days); no new question asked")
            if not dry_run:
                send(t("thinking_loop.pending_open", question=state.get("question", "")))
            return
        state.setdefault("skipped", []).append(f"{state.get('kind')}:{state.get('target')}")
        log("unanswered question timed out, skipped")
    q = pick_question(state)
    if not q:
        log("nothing to ask (everything is up to date)")
        return
    kind, target, question = q
    text = t("thinking_loop.ask_message", question=question)
    if dry_run:
        print(text)
        return
    mid = send(text)
    state.update({"phase": "asked", "kind": kind, "target": target, "question": question,
                  "asked_at": time.time(), "message_id": mid, "draft": None})
    state["recent_kinds"] = (state.get("recent_kinds", []) + [kind])[-4:]
    save_state(state)
    log(f"question sent: {kind} / {target}")


# ---------------------------------------------------------------------------
# Answer -> draft
# ---------------------------------------------------------------------------
def target_path(kind, target):
    if kind in ("grade", "predict"):
        return os.path.join(DEC_DIR, target + ".md")
    if kind == "belief":
        return os.path.join(BELIEF_DIR, target + ".md")
    if kind == "cadence":
        return CADENCE
    return None


SCHEMAS = {
    "grade": ('{"outcome": "what happened, 2-4 sentences", "lesson": "one-sentence lesson about the judgment", '
              '"score": "hit|partial|miss"}'),
    "predict": ('{"prediction": "one falsifiable sentence", "confidence": 70, '
                '"review": "YYYY-MM-DD"}'),
    "belief": ('{"instance": "dated concrete event, 2-3 sentences", "verdict": "supports|challenges|neutral", '
               '"confidence": "high|medium|low"}'),
    "cadence": '{"done": true, "note": "what came out of it or what got in the way, 1-3 sentences"}',
    "seed": ('{"title": "short title in question form", "idea": "one paragraph", '
             '"why": "why it matters, 1-2 sentences", "connects": ["existing note name"], '
             '"question": "the sharpest open question"}'),
}


def draft(kind, target, answer):
    note = read(target_path(kind, target)) if target_path(kind, target) else ""
    context = read(CONTEXT_FILE)[:2500]
    known = ", ".join(sorted(f[:-3] for d in (BELIEF_DIR, DEC_DIR, IDEAS_DIR)
                             if os.path.isdir(d) for f in os.listdir(d) if f.endswith(".md")))[:1500]
    prompt = f"""You are the assistant that maintains {OWNER}'s thinking journal. {OWNER} answered the
question below by voice or text. Convert the answer into the structured fields to be written into the target note.

RULES:
- Do NOT add content or commentary; clean up what the owner said and place it into the fields.
- Fix proper names that look like transcription errors using the context.
- {output_lang_directive()}
- If a date is unclear, use today's date: {datetime.now().strftime('%Y-%m-%d')}.
- Return ONLY valid JSON, write nothing else. Schema:
{SCHEMAS[kind]}

# QUESTION KIND: {kind}
# TARGET NOTE ({target or 'new seed'}):
{note[:6000]}

# {OWNER}'S ANSWER:
{answer[:4000]}

# CONTEXT (for name corrections and connections):
{context}
# EXISTING NOTE NAMES (pick "connects" only from these): {known}"""
    out = run_prompt(prompt, timeout=240)
    if not out:
        return None
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def preview(kind, target, d):
    if kind == "grade":
        return t("thinking_loop.preview_grade", target=target, score=clean(d.get('score', '?'), 10),
                 outcome=clean(d.get('outcome')), lesson=clean(d.get('lesson')))
    if kind == "predict":
        return t("thinking_loop.preview_predict", target=target, prediction=clean(d.get('prediction')),
                 confidence=d.get('confidence', '?'), review=clean(d.get('review'), 10))
    if kind == "belief":
        return t("thinking_loop.preview_belief", target=target, instance=clean(d.get('instance')),
                 verdict=clean(d.get('verdict'), 12), confidence=clean(d.get('confidence'), 8))
    if kind == "cadence":
        done = t("thinking_loop.cadence_will_mark") if d.get("done") else t("thinking_loop.cadence_stays_open")
        return t("thinking_loop.preview_cadence", done=done, note=clean(d.get('note')))
    if kind == "seed":
        return t("thinking_loop.preview_seed", title=clean(d.get('title'), 120), idea=clean(d.get('idea')),
                 why=clean(d.get('why')), connects=', '.join(map(str, d.get('connects') or []))[:200])
    return json.dumps(d, ensure_ascii=False)[:800]


# ---------------------------------------------------------------------------
# Apply: write under Thinking/ (only after explicit approval)
# ---------------------------------------------------------------------------
def _replace_section_line(text, heading, pattern, replacement):
    """Under '## heading', up to the next '## ', replace the first line matching pattern."""
    lines = text.splitlines(keepends=True)
    in_sec = False
    for i, l in enumerate(lines):
        if l.startswith("## "):
            in_sec = l[3:].strip().startswith(heading)
            continue
        if in_sec and re.match(pattern, l.strip()):
            lines[i] = replacement
            return "".join(lines), True
    return text, False


def _update_calibration(target, **cols):
    text = read(CALIBRATION)
    if not text:
        return
    out = []
    for line in text.splitlines(keepends=True):
        if line.startswith("|") and f"[[{target}]]" in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            while len(cells) < 6:
                cells.append("")
            idx = {"prediction": 1, "conf": 2, "review": 3, "outcome": 4, "lesson": 5}
            for k, v in cols.items():
                if v:
                    cells[idx[k]] = v
            line = "| " + " | ".join(cells) + " |\n"
        out.append(line)
    write(CALIBRATION, "".join(out))


def apply(kind, target, d, answer):
    today = datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    touched = []
    if kind == "grade":
        path = target_path(kind, target)
        text = read(path)
        block = t("thinking_loop.note_grade_block", today=today, score=clean(d.get('score'), 10),
                  outcome=clean(d.get('outcome')), lesson=clean(d.get('lesson')))
        text, ok = _replace_section_line(text, "Outcome", r"^-\s*_Pending review\._", block)
        if not ok:
            text = text.rstrip("\n") + f"\n\n## Outcome ({today})\n{block}"
        text = set_fm(text, "graded", today)
        text = set_fm(text, "outcome_score", clean(d.get("score"), 10))
        write(path, text)
        touched.append(path)
        _update_calibration(target, outcome=clean(d.get("outcome"), 160),
                            lesson=clean(d.get("lesson"), 160))
        touched.append(CALIBRATION)
    elif kind == "predict":
        path = target_path(kind, target)
        text = read(path)
        conf = re.sub(r"[^\d]", "", str(d.get("confidence", "")))[:3] or "50"
        review = clean(d.get("review"), 10)
        if not re.match(r"\d{4}-\d{2}-\d{2}", review):
            review = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
        text, ok = _replace_section_line(text, "Prediction", r"^-\s*\*\*Prediction:\*\*",
                                         f"- **Prediction:** {clean(d.get('prediction'))}\n")
        text, _ = _replace_section_line(text, "Prediction", r"^-\s*\*\*Confidence:\*\*",
                                        f"- **Confidence:** {conf}%\n")
        text, _ = _replace_section_line(text, "Prediction", r"^-\s*\*\*Review on:\*\*",
                                        f"- **Review on:** {review}\n")
        if not ok:
            text = text.rstrip("\n") + (f"\n\n## Prediction (calibration)\n- **Prediction:** "
                                        f"{clean(d.get('prediction'))}\n- **Confidence:** {conf}%\n"
                                        f"- **Review on:** {review}\n")
        text = set_fm(text, "confidence", f"{conf}%")
        text = set_fm(text, "review", review)
        write(path, text)
        touched.append(path)
        _update_calibration(target, prediction=clean(d.get("prediction"), 160),
                            conf=f"{conf}%", review=review)
        touched.append(CALIBRATION)
    elif kind == "belief":
        path = target_path(kind, target)
        text = read(path)
        verdict = {"supports": t("thinking_loop.verdict_supports"),
                   "challenges": t("thinking_loop.verdict_challenges")}.get(
            str(d.get("verdict", "")).lower(), t("thinking_loop.verdict_neutral"))
        entry = t("thinking_loop.note_belief_entry", today=today, instance=clean(d.get('instance')),
                  verdict=verdict)
        if "## Challenge Log" in text:
            text = text.rstrip("\n") + "\n\n" + entry
        else:
            text = text.rstrip("\n") + "\n\n## Challenge Log\n\n" + entry
        text = set_fm(text, "last_challenged", today)
        conf = str(d.get("confidence", "")).lower()
        if conf in ("high", "medium", "low"):
            text = set_fm(text, "confidence", conf)
        write(path, text)
        touched.append(path)
    elif kind == "cadence":
        text = read(CADENCE)
        if d.get("done"):
            lines = text.splitlines(keepends=True)
            for i, l in enumerate(lines):
                if re.match(r"\s*- \[ \]", l) and target[:40] in l:
                    lines[i] = l.replace("- [ ]", "- [x]", 1).rstrip("\n") + f" ✅ {today}\n"
                    break
            write(CADENCE, "".join(lines))
            touched.append(CADENCE)
        cap = os.path.join(DAILY_DIR, f"{stamp}-thinking.md")
        status = t("thinking_loop.cadence_done") if d.get("done") else t("thinking_loop.cadence_not_done")
        write(cap, t("thinking_loop.note_cadence_capture", target=clean(target, 120), status=status,
                     note=clean(d.get('note')), stamp=stamp))
        touched.append(cap)
    elif kind == "seed":
        title = clean(d.get("title"), 90).rstrip("?") + "?"
        fname = re.sub(r'[\\/:*"<>|]', "", title).strip() or t("thinking_loop.seed_default_name", today=today)
        path = os.path.join(IDEAS_DIR, fname + ".md")
        connects = [f"[[{clean(c, 80)}]]" for c in (d.get("connects") or [])][:5] or ["[[CONTEXT]]"]
        write(path, (f"---\ndate: {today}\ntype: idea\nstatus: seed\ntags: [idea, status/seed]\n"
                     f"source: {t('thinking_loop.seed_source')}\n---\n\n# {title}\n\n## The Idea\n"
                     f"{clean(d.get('idea'), 1500)}\n\n## Why It Matters\n{clean(d.get('why'))}\n\n"
                     f"## Connects To\n" + "\n".join(f"- {c}" for c in connects) +
                     f"\n\n## Open Questions\n- {clean(d.get('question'))}\n\n## Next Action\n- marinate\n"))
        touched.append(path)
    # Loopback: question + answer + applied draft accumulate in the wiki.
    os.makedirs(QUERIES_DIR, exist_ok=True)
    # Long or markdown-heavy targets (cadence steps) go into the frontmatter shortened.
    target = clean(re.sub(r"[*`\[\]]", "", target or ""), 120)
    slug = re.sub(r"[^\w\s-]", "", (target or d.get("title", "seed")), flags=re.U).strip().lower()
    slug = re.sub(r"-{2,}", "-", re.sub(r"[\s/]+", "-", slug))[:50].strip("-") or kind
    qpath = os.path.join(QUERIES_DIR, f"{today}-thinking-{kind}-{slug}.md")
    write(qpath, (f"---\nlang: {LANG}\nsummary_en: Telegram thinking loop entry ({kind}) applied to "
                  f"{target or 'a new seed'} on {today}.\ncommand: thinking\nkind: {kind}\n"
                  f"target: \"{target}\"\ncompiled_at: {datetime.now().isoformat(timespec='seconds')}\n"
                  f"status: seed\ntags: [query, thinking, {kind}]\n---\n\n" +
                  t("thinking_loop.query_body", kind=kind, answer=answer.strip(),
                    payload=json.dumps(d, ensure_ascii=False, indent=1),
                    touched=", ".join(os.path.relpath(t_, VAULT) for t_ in touched))))
    return touched


# ---------------------------------------------------------------------------
# telegram_capture hook
# ---------------------------------------------------------------------------
def _is_reply_to_us(msg, state):
    r = msg.get("reply_to_message") or {}
    return bool(state.get("message_id")) and r.get("message_id") == state.get("message_id")


def try_handle(token, msg, chat_id, transcribe, notify):
    """telegram_capture.handle_message calls this FIRST. If the message belongs to
    this loop it is handled and True is returned (it does not enter the capture
    flow); otherwise False."""
    state = load_state()
    phase = state.get("phase")
    if phase not in ("asked", "drafted"):
        return False
    text = (msg.get("text") or "").strip()
    low = text.lower()
    if phase == "drafted" and low in APPLY_WORDS:
        d, kind, target = state.get("draft") or {}, state["kind"], state["target"]
        touched = apply(kind, target, d, state.get("answer", ""))
        rel = ", ".join(os.path.relpath(t_, VAULT) for t_ in touched)
        notify(t("thinking_loop.applied", files=rel))
        save_state({"skipped": state.get("skipped", []), "recent_kinds": state.get("recent_kinds", [])})
        log(f"applied: {kind} / {target}")
        return True
    if low in CANCEL_WORDS and phase in ("asked", "drafted"):
        notify(t("thinking_loop.cancelled"))
        state["phase"] = "asked"
        state["draft"] = None
        save_state(state)
        return True
    if low in SKIP_WORDS:
        state.setdefault("skipped", []).append(f"{state.get('kind')}:{state.get('target')}")
        save_state({"skipped": state["skipped"][-20:], "recent_kinds": state.get("recent_kinds", [])})
        notify(t("thinking_loop.skipped"))
        return True
    is_reply = _is_reply_to_us(msg, state)
    is_prefixed = low.startswith(tuple(p + ":" for p in ANSWER_PREFIXES) + tuple(p + " " for p in ANSWER_PREFIXES))
    if not (is_reply or is_prefixed):
        return False   # ordinary capture; telegram_capture continues
    # Answer: voice or text
    answer = None
    from telegram_capture import audio_file_id
    audio = audio_file_id(msg)
    if audio:
        answer = transcribe(token, audio[0])
        if not answer:
            notify(t("thinking_loop.transcribe_failed"))
            return True
    elif text:
        answer = re.sub(r"^(?:" + "|".join(re.escape(p) for p in ANSWER_PREFIXES) + r"):?\s*", "",
                        text, flags=re.I)
    if not answer:
        notify(t("thinking_loop.unsupported_message"))
        return True
    notify(t("thinking_loop.drafting"))
    d = draft(state["kind"], state["target"], answer)
    if not d:
        notify(t("thinking_loop.draft_failed"))
        return True
    state.update({"phase": "drafted", "draft": d, "answer": answer})
    save_state(state)
    notify(preview(state["kind"], state["target"], d) + t("thinking_loop.draft_footer"))
    log(f"draft ready: {state['kind']} / {state['target']}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ask", action="store_true", help="pick and send the weekly question")
    ap.add_argument("--dry-run", action="store_true", help="print without sending")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--simulate", metavar="ANSWER", help="answer the pending question locally (produces a draft, writes nothing)")
    args = ap.parse_args()
    if args.status:
        print(json.dumps(load_state(), ensure_ascii=False, indent=1))
        return
    if args.ask:
        ask(dry_run=args.dry_run)
        return
    if args.simulate:
        state = load_state()
        if state.get("phase") != "asked":
            q = pick_question(state)
            if not q:
                print("no question"); return
            state.update({"phase": "asked", "kind": q[0], "target": q[1], "question": q[2],
                          "asked_at": time.time(), "message_id": None})
        d = draft(state["kind"], state["target"], args.simulate)
        print(preview(state["kind"], state["target"], d) if d else "could not produce a draft")
        if d and not args.dry_run:
            state.update({"phase": "drafted", "draft": d, "answer": args.simulate})
            save_state(state)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
