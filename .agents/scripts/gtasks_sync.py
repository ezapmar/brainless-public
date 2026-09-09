#!/usr/bin/env python3
"""_Agent-Context/TASKS.md <-> Google Tasks iki yonlu senkron (worker).

Bolum -> liste eslesmesi: "## Sözlerim" -> Sözlerim, "## Bekliyorum" -> Bekliyorum.
Satir formati: - [ ] Gorev metni | [[Toplanti]] | YYYY-MM-DD
Google tarafinda baslik temiz gorev metni; toplanti+tarih notes alaninda.

Yonler:
  yerel yeni -> Google'a ekle (notes ile)
  yerel [x]  -> Google'da tamamla
  Google tamamlandi -> yerelde [x]
  Google'da elle eklenen -> ilgili bolume satir olarak dus

Kimlik: tools/tasks-sync/token.json + .credentials.json (gitignore'da).
Bagimliliklar: google-api-python-client, google-auth-oauthlib (venv ile calistir).
"""
import os
import re
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
CRED_DIR = os.path.join(VAULT, "tools", "tasks-sync")
SCOPES = ["https://www.googleapis.com/auth/tasks"]
SECTIONS = {"## Sözlerim": "Sözlerim", "## Bekliyorum": "Bekliyorum"}


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
            os.chmod(token, 0o600)  # yenilenince izin gevsemesin
        else:
            raise RuntimeError("Google token gecersiz; makinede yeniden auth gerekli")
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


def get_list_id(service, title):
    for item in service.tasklists().list(maxResults=100).execute().get("items", []):
        if item["title"] == title:
            return item["id"]
    return service.tasklists().insert(body={"title": title}).execute()["id"]


def parse_local(lines):
    """-> {section_header: [{idx, done, title, meeting, date}]}"""
    out = {h: [] for h in SECTIONS}
    current = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("## "):
            current = s if s in SECTIONS else None
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

    for header, list_name in SECTIONS.items():
        # Satir eklemeleri indexleri kaydirir; her bolumde taze parse.
        local = parse_local(lines)
        list_id = get_list_id(service, list_name)
        remote = service.tasks().list(
            tasklist=list_id, showCompleted=True, showHidden=True,
            maxResults=100).execute().get("items", [])
        remote_by_title = {t["title"].strip(): t for t in remote}
        local_titles = {t["title"] for t in local[header]}

        for lt in local[header]:
            rt = remote_by_title.get(lt["title"])
            if rt is None:
                if not lt["done"]:
                    notes = "\n".join(x for x in [
                        f"Toplantı: {lt['meeting']}" if lt["meeting"] else "",
                        f"Tarih: {lt['date']}" if lt["date"] else "",
                        "Kaynak: brainless görev defteri"] if x)
                    service.tasks().insert(tasklist=list_id, body={
                        "title": lt["title"], "notes": notes}).execute()
                    log(f"Google'a eklendi [{list_name}]: {lt['title']}")
            else:
                if lt["done"] and rt["status"] == "needsAction":
                    service.tasks().patch(tasklist=list_id, task=rt["id"],
                                          body={"status": "completed"}).execute()
                    log(f"Google'da kapandi: {lt['title']}")
                elif not lt["done"] and rt["status"] == "completed":
                    lines[lt["idx"]] = lines[lt["idx"]].replace("- [ ]", "- [x]", 1)
                    changed = True
                    log(f"Yerelde kapandi: {lt['title']}")

        for title, rt in remote_by_title.items():
            if title not in local_titles and rt["status"] == "needsAction" \
                    and "brainless görev defteri" not in (rt.get("notes") or ""):
                row = f"- [ ] {title} | | {datetime.now().strftime('%Y-%m-%d')}\n"
                for i, l in enumerate(lines):
                    if l.strip() == header:
                        end = i + 1
                        while end < len(lines) and not lines[end].startswith("## "):
                            end += 1
                        lines.insert(end, row)
                        break
                changed = True
                log(f"Google'dan alindi [{list_name}]: {title}")
                local_titles.add(title)

    if changed:
        with open(TASKS_FILE, "w") as fh:
            fh.writelines(lines)


if __name__ == "__main__":
    main()
