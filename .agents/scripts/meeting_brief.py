#!/usr/bin/env python3
"""Automatic pre-meeting brief (worker, every 15 minutes).

For calendar events starting within the next ~45 minutes: attendees, a gist
from past Spiky reports and the related open items in the task ledger are
compiled by Claude into one brief and sent over Telegram.

Requires: tools/tasks-sync/token_gcal.json (calendar.readonly) and the gtasks
venv python (google libraries). State: .agents/state/brief_done.
"""
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
from llm import run_prompt
from owner_profile import OWNER, OWNER_FULL, WORKER, output_lang_directive  # noqa: E402
from i18n import t  # noqa: E402
from watchdog import send_telegram

TOKEN = os.path.join(VAULT, "tools", "tasks-sync", "token_gcal.json")
SPIKY_DIR = os.path.join(VAULT, "Inbox", "Spiky")
TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "brief_done")
WINDOW_MIN = 45
SELF = OWNER.split()[0].lower() if OWNER else "me"  # owner's first name, to drop self from attendees


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def get_service():
    creds = Credentials.from_authorized_user_file(
        TOKEN, ["https://www.googleapis.com/auth/calendar.readonly"])
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN, "w") as fh:
            fh.write(creds.to_json())
        os.chmod(TOKEN, 0o600)  # permissions must not loosen on refresh
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def upcoming_events(service):
    now = datetime.now(timezone.utc)
    r = service.events().list(
        calendarId="primary", singleEvents=True, orderBy="startTime",
        timeMin=now.isoformat(), timeMax=(now + timedelta(minutes=WINDOW_MIN)).isoformat(),
    ).execute()
    out = []
    for e in r.get("items", []):
        if "dateTime" not in e.get("start", {}):
            continue  # all-day event
        # timeMin looks at the end time: a running meeting is returned too. The brief
        # goes only to a meeting that has NOT STARTED YET.
        if datetime.fromisoformat(e["start"]["dateTime"]) <= datetime.now(timezone.utc):
            continue
        declined = any(a.get("self") and a.get("responseStatus") == "declined"
                       for a in e.get("attendees", []))
        if declined or e.get("status") == "cancelled":
            continue
        out.append(e)
    return out


def name_tokens(event):
    toks = set()
    for a in event.get("attendees", []):
        if a.get("self"):
            continue
        n = a.get("displayName") or a.get("email", "").split("@")[0]
        for part in re.split(r"[.\s_-]+", n):
            if len(part) > 2 and SELF not in part.lower():
                toks.add(part.capitalize())
    for w in re.findall(r"\w{4,}", event.get("summary", "")):
        toks.add(w)
    return toks


def gather_context(tokens):
    spiky = []
    files = sorted(os.listdir(SPIKY_DIR), reverse=True) if os.path.isdir(SPIKY_DIR) else []
    for f in files[:40]:
        body = read_file(os.path.join(SPIKY_DIR, f)) or ""
        hits = sum(1 for t in tokens if t.lower() in (f + body[:4000]).lower())
        if hits >= 1:
            spiky.append((hits, f, body[:2500]))
    spiky.sort(key=lambda x: (-x[0],))
    tasks = []
    for line in (read_file(TASKS_FILE) or "").splitlines():
        if line.strip().startswith("- [ ]") and any(
                t.lower() in line.lower() for t in tokens):
            tasks.append(line.strip())
    return spiky[:3], tasks[:10]


def make_brief(event, spiky, tasks):
    start = datetime.fromisoformat(event["start"]["dateTime"]).strftime("%H:%M")
    attendee_names = [(a.get("displayName") or a.get("email", ""))
                      for a in event.get("attendees", []) if not a.get("self")]
    attendees = ", ".join(attendee_names) or "(no attendee list)"
    notes = "\n\n".join(f"### {f}\n{b}" for _, f, b in spiky) or "(no past meeting notes found)"
    task_block = "\n".join(tasks) or "(no related open items)"
    # Plan 3: relationship signals of the attendees (deterministic, no LLM).
    try:
        from relationship_radar import attendee_signals
        rel = attendee_signals(attendee_names + [event.get("summary", "")])
    except Exception as e:
        log(f"relationship signals unavailable: {e}")
        rel = []
    rel_block = "\n".join(rel) if rel else "(no tracked person matched)"
    context = (read_file(CONTEXT_FILE) or "")[:2000]
    prompt = f"""You are the meeting preparation assistant of {OWNER}. From the material below write a SHORT brief (at most 12 lines, a Telegram message):
- First line: time, meeting name, counterpart.
- "{t("meeting_brief.label_history")}": the gist of the last conversations in 2-3 items (if any).
- "{t("meeting_brief.label_open_items")}": the owner {OWNER}'s promises regarding these people and what is expected from them (if any).
- "{t("meeting_brief.label_relationship")}": 1-2 lines on what matters from the RELATIONSHIP SIGNALS below (always mention it when momentum is low, when there has been no contact for a long time, or when there are open items).
- "{t("meeting_brief.label_watch")}": a one-sentence tactical note (if any; do not force it).
- No embellishment, directly usable information. Return only the brief text.
- {output_lang_directive()}

# MEETING: {event.get('summary', '(untitled)')} at {start}
# ATTENDEES: {attendees}

# RELATIONSHIP SIGNALS (attendees):
{rel_block}

# PAST SPIKY NOTES:
{notes[:7000]}

# TASK LEDGER (related rows):
{task_block}

# GENERAL CONTEXT:
{context}"""
    return run_prompt(prompt, timeout=180)


def main():
    subprocess.run(["git", "pull", "--rebase", "--autostash", "--quiet"],
                   cwd=VAULT, capture_output=True)
    done = set((read_file(STATE_FILE) or "").splitlines())
    service = get_service()
    for event in upcoming_events(service):
        # The same meeting can arrive as several invitations; title+time is unique.
        eid = f"{event.get('summary', '')}|{event['start'].get('dateTime', '')}"
        if eid in done:
            continue
        log(f"Preparing brief: {event.get('summary')}")
        spiky, tasks = gather_context(name_tokens(event))
        brief = make_brief(event, spiky, tasks)
        if brief:
            send_telegram(f"📅 {brief}")
            log("Brief sent")
        done.add(eid)
    # Do not clobber what a concurrently running copy wrote: merge before writing.
    done |= set((read_file(STATE_FILE) or "").splitlines())
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        fh.write("\n".join(sorted(done)[-200:]))


if __name__ == "__main__":
    main()
