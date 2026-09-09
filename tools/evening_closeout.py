#!/usr/bin/env python3
"""Evening close-out for the brainless vault.

Appends an "Akşam Kapanışı" section to today's briefing file. Two jobs:
  1. Reflect: what happened today, what carries over, open loops.
  2. Compound (forcing function): turn today's raw material into durable
     thinking by PROPOSING one candidate seed, one decision worth
     crystallising, and any belief/action contradiction. It also lists
     overdue calibration reviews (computed, never guessed).

The section only ever PROPOSES. It never writes to Thinking/; the vault is
human-authored truth (AGENT-RULES rule 2). The owner commits the seed/decision
by hand; the briefing just makes that a paste-away, not a blank page.

Safety by design:
- Read-only against the vault; the ONLY write is appending one section to
  today's briefing file (created if the morning run didn't happen).
- The LLM gets its context embedded in the prompt and is never granted
  file tools, so it cannot write, move, or delete anything.
- Skips the LLM call entirely on idle days (no captures, no file changes).
- Overdue calibration reviews are computed in Python (calibrate.scan), so
  that block is deterministic and cannot be hallucinated.

Run `--dry-run` to print the section to stdout instead of writing it.

Runs daily at 21:00 via the scheduler (<prefix>.brainless.closeout), before
the 21:30 backup commit and the 23:00 nightly archiver.
"""
import os
import subprocess
import sys
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt
from calibrate import scan as calibration_scan
from owner_profile import OWNER, lang_name  # noqa: E402

CAPTURE_DIR = os.path.join(VAULT, "Thinking", "Daily")
TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")  # tek görev defteri
BELIEFS_FILE = os.path.join(VAULT, "_Agent-Context", "BELIEFS-SUMMARY.md")
BRIEFING_DIR = os.path.join(VAULT, "Daily Briefings")
STATUS_FILE = os.path.join(VAULT, ".agents", "state", "llm_status")
MAX_CONTEXT = 15000  # chars of embedded context; keeps the call cheap
MARKER = "## Akşam Kapanışı"


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def latest_llm_status():
    """Read the breadcrumb llm.py leaves, so a failed run names its real cause
    (auth expiry vs timeout) instead of a vague 'nothing to close'."""
    try:
        with open(STATUS_FILE, errors="replace") as fh:
            parts = fh.read().strip().split("\t")
        return (parts[1] if len(parts) > 1 else "error",
                parts[2] if len(parts) > 2 else "")
    except OSError:
        return (None, "")


def todays_captures():
    chunks = []
    if os.path.isdir(CAPTURE_DIR):
        for name in sorted(os.listdir(CAPTURE_DIR)):
            path = os.path.join(CAPTURE_DIR, name)
            # Sadece .md: foto capture'lari Thinking/Daily'ye .jpg birakiyor;
            # binary icerik prompt'a girerse subprocess null byte'ta patliyor.
            if os.path.isfile(path) and not name.startswith(".") and name.endswith(".md"):
                try:
                    with open(path, errors="replace") as fh:
                        chunks.append(f"--- {name} ---\n{fh.read()}")
                except OSError:
                    pass
    return "\n".join(chunks)


def todays_changes():
    """Names of vault files touched today, from git (status + today's commits)."""
    names = set()
    try:
        r = subprocess.run(
            ["git", "log", "--since=midnight", "--name-only", "--format="],
            cwd=VAULT, capture_output=True, text=True, timeout=30,
        )
        names.update(l for l in r.stdout.splitlines() if l.strip())
        r = subprocess.run(
            ["git", "status", "--porcelain"], cwd=VAULT,
            capture_output=True, text=True, timeout=30,
        )
        names.update(l[3:] for l in r.stdout.splitlines() if l.strip())
    except Exception:
        pass
    # Plumbing noise (logs, agent state) is not "what happened today".
    interesting = [n for n in sorted(names)
                   if not n.startswith(("logs/", ".agents/", ".wiki/_lint", "_Agent-Context/HEALTH"))]
    return "\n".join(interesting[:80])


def open_tasks():
    try:
        with open(TASKS_FILE, errors="replace") as fh:
            return "\n".join(l for l in fh.read().splitlines()
                             if l.strip().startswith("- [ ]"))[:3000]
    except OSError:
        return ""


def beliefs_summary():
    """The Core Beliefs one-liners: reference for seed + contradiction."""
    try:
        with open(BELIEFS_FILE, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
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


def calibration_block():
    """Deterministic list of decisions needing attention (never LLM-guessed)."""
    due, needs_pred, no_review = calibration_scan()
    if not (due or needs_pred or no_review):
        return ""
    lines = ["### 📊 Karar takvimi"]
    if due:
        lines.append("**Notlanmayı bekliyor (review tarihi geçti, sonuç yazılmadı):**")
        for name, rev in due:
            lines.append(f"- [[{name}]] (review {rev})")
    if needs_pred:
        lines.append("**Tahmin eksik (decided ama forecast yok):**")
        for name in needs_pred:
            lines.append(f"- [[{name}]]")
    if no_review:
        lines.append("**Review tarihi yok (pending/deferred):**")
        for name in no_review:
            lines.append(f"- [[{name}]]")
    return "\n".join(lines)


def build_section(date_str, captures, changes):
    """Assemble the close-out section. Caller guarantees material exists, so a
    None return here means the LLM call itself failed (see latest_llm_status)."""
    context = ""
    if captures:
        context += f"\n# BUGÜNÜN HAM NOTLARI (Thinking/Daily):\n{captures}"
    if changes:
        context += f"\n# BUGÜN DOKUNULAN DOSYALAR:\n{changes}"
    tasks = open_tasks()
    if tasks:
        context += f"\n# AÇIK GÖREVLER (_Agent-Context/TASKS.md):\n{tasks}"
    context = context[:MAX_CONTEXT]

    beliefs = beliefs_summary()

    prompt = f"""Sen {OWNER} için 'brainless' sistemindeki akşam kapanışı asistanısın.
Tarih: {date_str}. Aşağıdaki bağlama dayanarak KISA bir günlük kapanış yaz ({lang_name(native=True)}):

## Bugün Ne Oldu
(en fazla 3 madde, dosya listesini tekrarlama, anlam çıkar)
## Yarına Devreden
(somut, en fazla 3 madde; yoksa "Yok" yaz)
## Açık Döngüler
(cevap bekleyen veya unutulma riski olan şeyler; yoksa bölümü atla)

## Düşünce Döngüsü
Amaç: günün ham malzemesini kalıcı düşünceye çevirmek. SADECE bağlamda gerçekten varsa öner; zorlama, uydurma. Yoksa ilgili maddeye "Yok" yaz. Bunlar birer ÖNERİ; {OWNER} kendi eliyle işleyecek.
- 🌱 Aday tohum: Bugünün notlarından/işinden çıkan 1 açık soru. Format: **Soru?** + neden önemli (1 cümle) + hangi mevcut nota bağlanır ([[Not adı]]). Thinking/Ideas/ altına yapıştırılabilir olsun.
- ⚖️ Kristalize edilecek karar: Bugün Work/ altında bir karar olgunlaşıyorsa, Thinking/Decisions/ altına taşımayı öner ve yanlışlanabilir bir tahmin cümlesi ekle. Yoksa "Yok".
- 🔀 Çelişki: Aşağıdaki inançlarından biriyle bugünkü bir eylem/karar çelişiyorsa tek cümlede göster. Yoksa bu maddeyi atla.

İNANÇLARIM (tohum ve çelişki için referans):
{beliefs or "(özet bulunamadı)"}

Sadece markdown içeriğini yaz, başka hiçbir şey yazma. Uydurma: bağlamda olmayanı yazma. Karar review tarihlerinden BAHSETME (o listeyi sistem ekliyor).
{context}"""

    result = run_prompt(prompt, timeout=300)
    if not result:
        return None

    calib = calibration_block()
    if calib:
        result = f"{result}\n\n{calib}"
    return f"\n\n---\n\n{MARKER}\n\n{result}\n"


def main():
    dry_run = "--dry-run" in sys.argv
    date_str = datetime.now().strftime("%Y-%m-%d")
    briefing_path = os.path.join(BRIEFING_DIR, f"daily-briefing-{date_str}.md")

    captures = todays_captures()
    changes = todays_changes()
    if not captures and not changes:
        log("Kapatılacak bir şey yok: bugün capture da dosya değişikliği de yok.")
        return

    if os.path.exists(briefing_path) and not dry_run:
        with open(briefing_path, errors="replace") as fh:
            if MARKER in fh.read():
                log("Close-out already present for today. Skipping.")
                return

    section = build_section(date_str, captures, changes)
    if section is None:
        outcome, detail = latest_llm_status()
        if outcome == "auth":
            log(f"LLM kimlik doğrulama süresi dolmuş ({detail[:80]}). "
                f"Terminalde `claude` ile yeniden giriş yap; capture/değişiklik hazır.")
        elif outcome == "timeout":
            log("LLM çağrısı zaman aşımına uğradı; kapanış yazılmadı.")
        else:
            log(f"LLM çağrısı başarısız ({outcome or 'bilinmiyor'}): {detail[:80]}")
        return

    if dry_run:
        print(section)
        return

    os.makedirs(BRIEFING_DIR, exist_ok=True)
    if not os.path.exists(briefing_path):
        header = f"# {date_str} Günlük Not\n\n(Sabah brifingi bugün üretilmedi; bu dosya akşam kapanışıyla açıldı.)\n"
        section = header + section
    with open(briefing_path, "a") as fh:
        fh.write(section)
    log(f"Close-out appended to {briefing_path}")
    post_to_buzz(section)


def post_to_buzz(text):
    """Best effort (Buzz Katman 1): akşam kapanışını #gunluk kanalına Brifing kimliğiyle bas."""
    script = os.path.join(VAULT, ".agents", "scripts", "buzz_post.sh")
    if not os.access(script, os.X_OK):
        return
    try:
        subprocess.run([script, "brifing", "daily"], input=text, text=True,
                       capture_output=True, timeout=45, cwd=VAULT)
    except Exception as exc:
        log(f"buzz post skipped: {exc}")


if __name__ == "__main__":
    main()
