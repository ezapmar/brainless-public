#!/usr/bin/env python3
"""Weekly memory reconciliation for the brainless vault.

Compares _Agent-Context/CONTEXT.md against the last 7 days of reality
(briefings, close-outs, commit subjects) and writes a drift report to
_Agent-Context/CONTEXT-DRIFT.md. Report-only by design: CONTEXT.md is
never modified; the owner applies (or rejects) the proposed updates.

Safety: the LLM gets embedded context only (no file tools); the single
write is the report file. Runs Sundays 20:00 via launchd
(<prefix>.brainless.reconcile), before the 21:30 backup commit.
"""
import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt
from owner_profile import OWNER, lang_name  # noqa: E402

CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
REPORT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT-DRIFT.md")
BRIEFING_DIR = os.path.join(VAULT, "Daily Briefings")
PER_FILE_CAP = 4000
TOTAL_CAP = 24000


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def last_week_briefings():
    chunks = []
    today = datetime.now().date()
    for i in range(7):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for path in glob.glob(os.path.join(BRIEFING_DIR, f"daily-briefing-{d}.md")):
            try:
                with open(path, errors="replace") as fh:
                    chunks.append(f"--- {os.path.basename(path)} ---\n{fh.read()[:PER_FILE_CAP]}")
            except OSError:
                pass
    return "\n".join(chunks)


def week_commits():
    try:
        r = subprocess.run(
            ["git", "log", "--since=7.days", "--format=%ad %s", "--date=short"],
            cwd=VAULT, capture_output=True, text=True, timeout=30,
        )
        return r.stdout.strip()[:3000]
    except Exception:
        return ""


def main():
    try:
        with open(CONTEXT_FILE, errors="replace") as fh:
            context_md = fh.read()
    except OSError as e:
        log(f"CONTEXT.md okunamadı: {e}")
        return

    briefings = last_week_briefings()
    commits = week_commits()
    if not briefings and not commits:
        log("Son 7 günde veri yok, mutabakat atlandı.")
        return

    evidence = ""
    if briefings:
        evidence += f"\n# SON 7 GÜNÜN BRİFİNG VE KAPANIŞLARI:\n{briefings}"
    if commits:
        evidence += f"\n# SON 7 GÜNÜN COMMIT BAŞLIKLARI:\n{commits}"
    evidence = evidence[:TOTAL_CAP]

    date_str = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""Sen {OWNER} için 'brainless' sisteminde haftalık hafıza mutabakatı asistanısın. Çıktı dili: {lang_name(native=True)}.
Görev: CONTEXT.md (agent'ların giriş noktası) ile son 7 günün gerçekliğini karşılaştır, drift raporu yaz.

KURALLAR:
- Sadece kanıta dayan; kanıtta olmayan hiçbir şeyi iddia etme.
- Rapor kısa olsun (en fazla 25 satır). Drift yoksa tek satır yaz: "Drift yok, CONTEXT güncel."
- CONTEXT'i sen değiştirmiyorsun; sadece öneriyorsun.

RAPOR FORMATI (markdown):
## Bayat veya Yanlış Görünen
(CONTEXT'te olup gerçeklikle çelişen maddeler; madde başına 1 satır + kanıt kaynağı)
## Eksik Yeni Gelişmeler
(son 7 günde olup CONTEXT'e girmesi gereken şeyler)
## Önerilen Güncellemeler
(CONTEXT'e yazılmaya hazır, kısa taslak maddeler)

# MEVCUT CONTEXT.MD:
{context_md}
{evidence}"""

    result = run_prompt(prompt, timeout=300)
    if not result:
        log("Claude çağrısı başarısız; rapor yazılmadı.")
        return

    header = (
        f"# CONTEXT Drift Raporu\n\n"
        f"> Oluşturulma: {date_str}. Rapor salt öneridir; CONTEXT.md'yi {OWNER} onayıyla güncelleyin.\n\n"
    )
    with open(REPORT_FILE, "w") as fh:
        fh.write(header + result + "\n")
    log(f"Rapor yazıldı: {REPORT_FILE}")

    if "Drift yok" not in result and shutil.which("osascript"):  # macOS-only notification
        subprocess.run(
            ["osascript", "-e",
             'display notification "Haftalık CONTEXT mutabakatı hazır: drift bulundu" with title "brainless"'],
            check=False,
        )


if __name__ == "__main__":
    main()
