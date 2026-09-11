#!/usr/bin/env python3
"""_Agent-Context/TASKS.md <-> Google Tasks two-way sync (worker).

Section -> list mapping: "## <promises>" -> <promises>, "## <waiting>" -> <waiting>,
where both names come from the locale (tools/locale/<lang>/gtasks_sync.json);
headings in the current language and in English are both accepted when parsing.
Line format: - [ ] Task text | [[Meeting]] | YYYY-MM-DD
On the Google side the title is the clean task text; meeting+date go in the notes field.

Directions:
  local new   -> add to Google (with notes)
  local [x]   -> complete on Google
  Google completed -> [x] locally
  added by hand on Google -> dropped as a line into the matching section

Credentials: tools/tasks-sync/token.json + .credentials.json (gitignored).
Dependencies: google-api-python-client, google-auth-oauthlib (run with the venv).
"""
import os
import re
import sys
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from i18n import t, t_list  # noqa: E402

TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
CRED_DIR = os.path.join(VAULT, "tools", "tasks-sync")
SCOPES = ["https://www.googleapis.com/auth/tasks"]
# canonical section key -> (heading written/inserted under, Google Tasks list name)
SECTIONS = {key: ("## " + t(f"gtasks_sync.section_{key}"), t(f"gtasks_sync.section_{key}"))
            for key in ("promises", "waiting")}
# any accepted heading (current language or English) -> canonical section key
HEADING_ALIASES = {"## " + name: key for key in SECTIONS
                   for name in t_list(f"gtasks_sync.section_{key}")}
SOURCE_MARKERS = t_list("gtasks_sync.note_source")


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def get_service():
    token = os.path.join(CRED_DIR, "token.json")
    creds = Credentials.from_authorized_user_file(token, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token, "w") as fh:
                fh.write(creds.to_json())
            os.chmod(token, 0o600)  # permissions must not loosen after refresh
        else:
            raise RuntimeError("Google token invalid; re-auth needed on the machine")
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


def get_list_id(service, title):
    for item in service.tasklists().list(maxResults=100).execute().get("items", []):
        if item["title"] == title:
            return item["id"]
    return service.tasklists().insert(body={"title": title}).execute()["id"]


def parse_local(lines):
    """-> {section_key: [{idx, done, title, meeting, date}]}"""
    out = {k: [] for k in SECTIONS}
    current = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("## "):
            current = HEADING_ALIASES.get(s)
            continue
        m = re.match(r"- \[( |x)\] (.+)", s)
        if m and current:
            parts = [p.strip() for p in m.group(2).split(" | ")]
            out[current].append({
                "idx": i,
                "done": m.group(1) == "x",
                "title": parts[0],
                "meeting": parts[1].strip("[]") if len(parts) > 1 else "",
                "date": parts[2] if len(parts) > 2 else "",
            })
    return out


def main():
    with open(TASKS_FILE) as fh:
        lines = fh.readlines()
    service = get_service()
    changed = False

    for key, (header, list_name) in SECTIONS.items():
        # Line insertions shift the indexes; parse fresh for every section.
        local = parse_local(lines)
        list_id = get_list_id(service, list_name)
        remote = service.tasks().list(
            tasklist=list_id, showCompleted=True, showHidden=True,
            maxResults=100).execute().get("items", [])
        remote_by_title = {x["title"].strip(): x for x in remote}
        local_titles = {x["title"] for x in local[key]}

        for lt in local[key]:
            rt = remote_by_title.get(lt["title"])
            if rt is None:
                if not lt["done"]:
                    notes = "\n".join(x for x in [
                        t("gtasks_sync.note_meeting", meeting=lt["meeting"]) if lt["meeting"] else "",
                        t("gtasks_sync.note_date", date=lt["date"]) if lt["date"] else "",
                        t("gtasks_sync.note_source")] if x)
                    service.tasks().insert(tasklist=list_id, body={
                        "title": lt["title"], "notes": notes}).execute()
                    log(f"Added to Google [{list_name}]: {lt['title']}")
            else:
                if lt["done"] and rt["status"] == "needsAction":
                    service.tasks().patch(tasklist=list_id, task=rt["id"],
                                          body={"status": "completed"}).execute()
                    log(f"Closed on Google: {lt['title']}")
                elif not lt["done"] and rt["status"] == "completed":
                    lines[lt["idx"]] = lines[lt["idx"]].replace("- [ ]", "- [x]", 1)
                    changed = True
                    log(f"Closed locally: {lt['title']}")

        for title, rt in remote_by_title.items():
            if title not in local_titles and rt["status"] == "needsAction" \
                    and not any(m in (rt.get("notes") or "") for m in SOURCE_MARKERS):
                row = f"- [ ] {title} | | {datetime.now().strftime('%Y-%m-%d')}\n"
                for i, l in enumerate(lines):
                    if HEADING_ALIASES.get(l.strip()) == key:
                        end = i + 1
                        while end < len(lines) and not lines[end].startswith("## "):
                            end += 1
                        lines.insert(end, row)
                        break
                changed = True
                log(f"Pulled from Google [{list_name}]: {title}")
                local_titles.add(title)

    if changed:
        with open(TASKS_FILE, "w") as fh:
            fh.writelines(lines)


if __name__ == "__main__":
    main()
