#!/usr/bin/env python3
"""think_surface.py - the lightweight daily thinking surface.

Composes _Agent-Context/THINKING.md: a one-screen nudge that keeps the
thinking loop running, kept deliberately separate from the operational daily
briefing. Four blocks, in leverage order (see Thinking/Thinking Cadence.md):

  1. Today's cadence step   - the next unchecked step in Thinking Cadence.md
  2. Decision scoreboard    - calibrate.py: due to grade / needs prediction / no review date
  3. Provocation of the day - one sharp tension (context drift, stale belief, or missing prediction)
  4. Resurfaced notes        - the freshest picks from RESURFACE.md, if still current

Fully deterministic: no LLM call, so it is cheap, fast, and cannot fail
silently. The single write is THINKING.md. Content language matches the vault
(Turkish); code and comments stay English per repo policy.

Runs each morning via launchd (<prefix>.brainless.think).
"""
import os
import re
import sys
from datetime import date, datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
import calibrate  # noqa: E402  (reuses the decision-scan logic)

AGENT = os.path.join(VAULT, "_Agent-Context")
CADENCE_FILE = os.path.join(VAULT, "Thinking", "Thinking Cadence.md")
DRIFT_FILE = os.path.join(AGENT, "CONTEXT-DRIFT.md")
RESURFACE_FILE = os.path.join(AGENT, "RESURFACE.md")
BELIEFS_DIR = os.path.join(VAULT, "Thinking", "Beliefs")
OUT_FILE = os.path.join(AGENT, "THINKING.md")

STALE_BELIEF_DAYS = 90
RESURFACE_FRESH_DAYS = 8
DRIFT_FRESH_DAYS = 10
DASH_RE = re.compile("\\s*[\u2014\u2013]\\s*")  # em / en dash -> " - " (repo hard-ban)


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def clean(text):
    """Strip banned em/en dashes from any reproduced content."""
    return DASH_RE.sub(" - ", text).rstrip()


def age_days(path):
    try:
        return (datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))).days
    except OSError:
        return 10**6


def cadence_step():
    """First unchecked '- [ ]' line in the cadence; wrap to top when all done."""
    try:
        with open(CADENCE_FILE, errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return "_Kadans dosyası okunamadı (Thinking/Thinking Cadence.md)._"
    steps = [l for l in lines if l.strip().startswith("- [")]
    for l in steps:
        if l.strip().startswith("- [ ]"):
            return clean(l.strip()[5:].strip())
    if steps:
        return "Tüm adımlar işaretli. W1'e dönüp yeni bir kararla döngüyü tekrar başlat."
    return "_Kadans adımı bulunamadı._"


def calibration_block():
    due, needs_pred, no_review = calibrate.scan()
    if not (due or needs_pred or no_review):
        return "Tüm kararlar güncel, notlanacak bir şey yok.", 0
    out = []
    if due:
        out.append("**Notlanmayı bekliyor** (review tarihi geçti, sonuç yazılmadı):")
        for name, rev in due:
            out.append(f"- {name}  (review: {rev})  ->  `/calibrate` ile tek satırda notla")
    if needs_pred:
        out.append("\n**Tahmin eksik** (karar verildi, kalibre tahmin yok):")
        for name in needs_pred:
            out.append(f"- {name}")
    if no_review:
        out.append("\n**Review tarihi yok** (pending/deferred):")
        for name in no_review:
            out.append(f"- {name}")
    return "\n".join(out), len(due)


def _fm_field(text, key):
    m = re.search(rf"^{key}:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def stale_beliefs():
    picks = []
    if not os.path.isdir(BELIEFS_DIR):
        return picks
    today = date.today()
    for name in sorted(os.listdir(BELIEFS_DIR)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(BELIEFS_DIR, name)
        try:
            with open(path, errors="replace") as fh:
                head = fh.read(600)
        except OSError:
            continue
        lc = calibrate.parse_date(_fm_field(head, "last_challenged"))
        if lc and (today - lc).days >= STALE_BELIEF_DAYS:
            picks.append((os.path.splitext(name)[0], (today - lc).days))
    picks.sort(key=lambda t: -t[1])  # most stale first
    return picks


def drift_bullets():
    if age_days(DRIFT_FILE) > DRIFT_FRESH_DAYS:
        return []
    try:
        with open(DRIFT_FILE, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    # Bullets under the first "stale / looks wrong" heading.
    m = re.search(r"##\s*Bayat[^\n]*\n(.*?)(?:\n##|\Z)", text, re.S)
    if not m:
        return []
    return [clean(l.strip()[2:]) for l in m.group(1).splitlines()
            if l.strip().startswith("- ")]


def provocation(needs_pred):
    """One sharp item. Priority: fresh drift > stale belief > missing prediction.
    Rotates the pick by day-of-year so the poke changes daily."""
    idx = datetime.now().timetuple().tm_yday
    drift = drift_bullets()
    if drift:
        return f"(drift) {drift[idx % len(drift)]}"
    beliefs = stale_beliefs()
    if beliefs:
        name, days = beliefs[idx % len(beliefs)]
        return (f"(bayat inanç) [[{name}]] {days} gündür sınanmadı. "
                f"Bir dated karşı-örnek yaz ya da güveni güncelle (Cadence W7).")
    if needs_pred:
        name = needs_pred[idx % len(needs_pred)]
        return (f"(tahmin eksik) [[{name}]] için tahmin + güven% gir; "
                f"aksi halde yargın notlanamaz (Cadence W1).")
    return "Bugün belirgin bir gerilim yok. Bir seed fikir yakala (Cadence W6)."


def resurface_block():
    if not os.path.exists(RESURFACE_FILE):
        return "_RESURFACE.md yok. `python3 tools/resurface.py` ile üret._"
    if age_days(RESURFACE_FILE) > RESURFACE_FRESH_DAYS:
        return (f"_RESURFACE.md bayat ({age_days(RESURFACE_FILE)} gün). "
                f"`/resurface` ya da `python3 tools/resurface.py` çalıştır._")
    try:
        with open(RESURFACE_FILE, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return "_RESURFACE.md okunamadı._"
    bullets = [clean(l) for l in text.splitlines() if l.strip().startswith("- ")]
    return "\n".join(bullets[:3]) if bullets else "_RESURFACE.md boş._"


def main():
    cal_text, due_count = calibration_block()
    _, needs_pred, _ = calibrate.scan()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    body = f"""# Düşünce Yüzeyi

> Oluşturulma: {now}. Hafif ve günlük; operasyonel brifingden ayrı tutulur. `tools/think_surface.py` üretir, elle düzenleme.

## 1. Bugünün adımı (Cadence)
{cadence_step()}

## 2. Karar defteri (Calibration)
{cal_text}

## 3. Günün provokasyonu
{provocation(needs_pred)}

## 4. Yeniden yüzeye çıkanlar
{resurface_block()}
"""
    with open(OUT_FILE, "w") as fh:
        fh.write(body)
    log(f"Yazıldı: {OUT_FILE} (notlanmayı bekleyen karar: {due_count})")


if __name__ == "__main__":
    main()
