#!/usr/bin/env python3
"""Toplanti oncesi otomatik brif (worker, 15 dk'da bir).

Onumuzdeki ~45 dk icinde baslayan takvim etkinlikleri icin: katilimcilar,
gecmis Spiky raporlarindan ozet, gorev defterindeki ilgili acik maddeler
Claude ile tek brif'e derlenir ve Telegram'dan gonderilir.

Gereksinim: tools/tasks-sync/token_gcal.json (calendar.readonly) ve
gtasks venv'in python'u (google kutuphaneleri). State: .agents/state/brief_done.
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
from owner_profile import OWNER, OWNER_FULL, WORKER  # noqa: E402
from watchdog import send_telegram

TOKEN = os.path.join(VAULT, "tools", "tasks-sync", "token_gcal.json")
SPIKY_DIR = os.path.join(VAULT, "Inbox", "Spiky")
TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "brief_done")
WINDOW_MIN = 45
SELF = "tunca"


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
        os.chmod(TOKEN, 0o600)  # yenilenince izin gevsemesin
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
            continue  # tum gun etkinligi
        # timeMin bitis saatine bakar: suren toplanti da doner. Brif sadece
        # HENUZ BASLAMAMIS toplantiya gider.
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
    attendees = ", ".join(attendee_names) or "(katilimci listesi yok)"
    notes = "\n\n".join(f"### {f}\n{b}" for _, f, b in spiky) or "(gecmis toplanti notu bulunamadi)"
    task_block = "\n".join(tasks) or "(ilgili acik madde yok)"
    # Plan 3: katilimcilarin iliski sinyalleri (deterministik, LLM'siz).
    try:
        from relationship_radar import attendee_signals
        rel = attendee_signals(attendee_names + [event.get("summary", "")])
    except Exception as e:
        log(f"iliski sinyali alinamadi: {e}")
        rel = []
    rel_block = "\n".join(rel) if rel else "(izlenen kisi eslesmedi)"
    context = (read_file(CONTEXT_FILE) or "")[:2000]
    prompt = f"""Sen {OWNER} için toplantı hazırlık asistanısın. Aşağıdaki malzemeden KISA bir brif yaz (en fazla 12 satır, Telegram mesajı):
- İlk satır: saat, toplantı adı, karşı taraf.
- "Geçmiş": son görüşmelerin özü 2-3 madde (varsa).
- "Açık maddeler": {OWNER} adlı sahibin bu kişilerle ilgili sözleri ve onlardan bekledikleri (varsa).
- "İlişki": aşağıdaki İLİŞKİ SİNYALLERİ'nden önemli olanı 1-2 satır ver (momentum düşükse, uzun süredir görüşülmediyse, ya da açık madde varsa mutlaka belirt).
- "Dikkat": tek cümlelik taktik not (varsa; zorlamadan).
- Süsleme yok, doğrudan kullanılabilir bilgi. Sadece brif metnini döndür.

# TOPLANTI: {event.get('summary', '(bassiz)')} saat {start}
# KATILIMCILAR: {attendees}

# İLİŞKİ SİNYALLERİ (katılımcılar):
{rel_block}

# GECMIS SPIKY NOTLARI:
{notes[:7000]}

# GOREV DEFTERI (ilgili satirlar):
{task_block}

# GENEL BAGLAM:
{context}"""
    return run_prompt(prompt, timeout=180)


def main():
    subprocess.run(["git", "pull", "--rebase", "--autostash", "--quiet"],
                   cwd=VAULT, capture_output=True)
    done = set((read_file(STATE_FILE) or "").splitlines())
    service = get_service()
    for event in upcoming_events(service):
        # Ayni toplantiya birden fazla davet gelebiliyor; baslik+saat tekildir.
        eid = f"{event.get('summary', '')}|{event['start'].get('dateTime', '')}"
        if eid in done:
            continue
        log(f"Brif hazirlaniyor: {event.get('summary')}")
        spiky, tasks = gather_context(name_tokens(event))
        brief = make_brief(event, spiky, tasks)
        if brief:
            send_telegram(f"📅 {brief}")
            log("Brif gonderildi")
        done.add(eid)
    # Es zamanli calisan bir kopyanin yazdiklarini ezme: yazmadan once birlestir.
    done |= set((read_file(STATE_FILE) or "").splitlines())
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        fh.write("\n".join(sorted(done)[-200:]))


if __name__ == "__main__":
    main()
