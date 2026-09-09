#!/usr/bin/env python3
"""Weekly note resurfacing for the brainless vault.

Picks 5 old notes worth re-reading this week, chosen for relevance to the
active projects in CONTEXT.md, and writes them to _Agent-Context/RESURFACE.md.
Briefing sessions include that list while it is fresh (under 7 days old).
Counters the collector's fallacy: captured notes nobody ever sees again.

Safety: the LLM only sees note titles and snippets embedded in the prompt;
the single write is RESURFACE.md. Runs Mondays 06:30 via launchd
(<prefix>.brainless.resurface).
"""
import os
import sys
import time
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt
from owner_profile import OWNER, lang_name  # noqa: E402

CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
OUT_FILE = os.path.join(VAULT, "_Agent-Context", "RESURFACE.md")
SOURCE_DIRS = ["Thinking", "Work", "Personal", "Library"]
EXCLUDE_PARTS = {"archive", "_backup", "_attachments", ".wiki", ".git", ".agents",
                 "venv", "__pycache__", "daily briefings", "resources"}
MIN_AGE_DAYS = 30       # only notes untouched for at least this long
MAX_CANDIDATES = 120    # cap the list shown to the model
SNIPPET_CHARS = 160


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def collect_candidates():
    cutoff = time.time() - MIN_AGE_DAYS * 86400
    found = []
    for base in SOURCE_DIRS:
        root_dir = os.path.join(VAULT, base)
        if not os.path.isdir(root_dir):
            continue
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")
                       and d.lower() not in EXCLUDE_PARTS]
            for name in files:
                if not name.endswith(".md") or name.endswith("_raw.md"):
                    continue
                path = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if mtime > cutoff:
                    continue
                rel = os.path.relpath(path, VAULT)
                try:
                    with open(path, errors="replace") as fh:
                        snippet = fh.read(2000)
                except OSError:
                    continue
                title = next((l.lstrip("# ").strip() for l in snippet.splitlines()
                              if l.startswith("#")), os.path.splitext(name)[0])
                body = " ".join(snippet.split())[:SNIPPET_CHARS]
                found.append((mtime, rel, title, body))
    # Oldest first, so long-forgotten notes get a chance before merely stale ones.
    found.sort(key=lambda t: t[0])
    return found[:MAX_CANDIDATES]


def main():
    try:
        with open(CONTEXT_FILE, errors="replace") as fh:
            context_md = fh.read()[:6000]
    except OSError as e:
        log(f"CONTEXT.md okunamadı: {e}")
        return

    candidates = collect_candidates()
    if len(candidates) < 5:
        log(f"Sadece {len(candidates)} aday var, resurface atlandı.")
        return

    lines = []
    for mtime, rel, title, body in candidates:
        age_days = int((time.time() - mtime) / 86400)
        lines.append(f"- {rel} | {title} | {age_days} gündür dokunulmamış | {body}")
    catalog = "\n".join(lines)

    date_str = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""Sen {OWNER} için 'brainless' sistemindeki not hatırlatma asistanısın. Çıktı dili: {lang_name(native=True)}.
Aşağıda 30+ gündür dokunulmamış notların listesi ve {OWNER} adlı sahibin güncel bağlamı var.
Bu hafta yeniden okunmaya EN ÇOK değer 5 notu seç.

SEÇİM KRİTERİ: Aktif projelere ve önceliklere bugün katkısı olabilecek notlar.
Nostalji değil, kullanım değeri. Listede olmayan bir notu asla uydurma.

ÇIKTI FORMATI (sadece bunu yaz):
- [[<dosya yolu, .md uzantısız>]]: <tek cümle, neden bu hafta okunmalı>
(tam 5 madde)

# GÜNCEL BAĞLAM (CONTEXT.md):
{context_md}

# ADAY NOTLAR:
{catalog}"""

    result = run_prompt(prompt, timeout=300)
    if not result:
        log("Claude çağrısı başarısız; RESURFACE.md yazılmadı.")
        return

    header = (
        f"# Haftanın Yeniden Yüzeye Çıkan Notları\n\n"
        f"> Oluşturulma: {date_str}. Brifingler bu listeyi dosya 7 günden tazeyken dahil eder.\n\n"
    )
    with open(OUT_FILE, "w") as fh:
        fh.write(header + result + "\n")
    log(f"Yazıldı: {OUT_FILE} ({len(candidates)} aday içinden)")


if __name__ == "__main__":
    main()
