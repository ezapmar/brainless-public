#!/usr/bin/env python3
"""Iliski radari (worker, haftalik).

Plan 3: kisi dossier'larinin (Plan 1) uzerine sinyal/uyari katmani. LLM'siz,
deterministik: Spiky toplantilarindan son temas + kadans + skor momentumu,
TASKS.md'den karsiliklilik (kime borclusun / kim sana borclu) hesaplar; sapma
varsa Telegram'a haftalik radar dusurur ve .wiki/relationships/radar.md yazar.

Esikler (sahip, 2026-08-28): sessizlik 20 hafta = sari, 32 hafta = kirmizi;
momentum 15+ puan dususu bayrak. Kapsam entities.md'deki person + kapsam etiketi.
"""
import os
import re
import statistics
import sys
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
from watchdog import send_telegram

SPIKY_DIR = os.path.join(VAULT, "Inbox", "Spiky")
TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
REGISTRY = os.path.join(VAULT, "_Agent-Context", "entities.md")
OUT_FILE = os.path.join(VAULT, ".wiki", "relationships", "radar.md")

STALE_YELLOW_WEEKS = 20
STALE_RED_WEEKS = 32
MOMENTUM_DROP = 15
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
SCORE_LABELS = ("Spiky Score", "Attention Score", "Interaction Score", "Emotion Score")


def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


def load_people():
    """entities.md -> person kayitlari: {name, terms, scope}."""
    people = []
    for line in read(REGISTRY).splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "|" not in s:
            continue
        parts = [p.strip() for p in s.split("|")]
        if len(parts) < 2 or parts[1] != "person":
            continue
        name = parts[0]
        aliases = [a.strip() for a in (parts[2].split(",") if len(parts) > 2 else []) if a.strip()]
        scope = parts[3] if len(parts) > 3 and parts[3] else "other"
        people.append({"name": name, "terms": [name] + aliases, "scope": scope})
    return people


def parse_score(text):
    """Not govdesindeki 'Spiky Score' bloğundan ilk sayiyi al -> int | None."""
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.strip() == "Spiky Score":
            for j in range(i + 1, min(i + 6, len(lines))):
                if re.fullmatch(r"\d{1,3}", lines[j].strip()):
                    return int(lines[j].strip())
    return None


def participants_line(text):
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.strip() == "Participants":
            for j in range(i + 1, min(i + 4, len(lines))):
                if lines[j].strip():
                    return lines[j]
    return ""


def meetings_for(person):
    """Kisinin katildigi Spiky toplantilari: [(date, score, fname)] kronolojik."""
    out = []
    if not os.path.isdir(SPIKY_DIR):
        return out
    low_terms = [t.casefold() for t in person["terms"]]
    for f in os.listdir(SPIKY_DIR):
        if not f.endswith(".md"):
            continue
        text = read(os.path.join(SPIKY_DIR, f))
        pline = participants_line(text).casefold()
        if not any(t in pline for t in low_terms):
            continue
        m = DATE_RE.match(f)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            continue
        out.append((d, parse_score(text), f))
    out.sort(key=lambda x: x[0])
    return out


def tasks_for(person):
    """(bekledigin, borclu_oldugun) TASKS.md satirlari + en eski gun sayisi."""
    waiting, owe = [], []
    section = None
    low_terms = [t.casefold() for t in person["terms"]]
    for line in read(TASKS_FILE).splitlines():
        s = line.strip()
        if s.startswith("## "):
            section = s[3:].strip()
            continue
        m = re.match(r"- \[ \] (.+)", s)
        if not m:
            continue
        row = m.group(1)
        if section == "Bekliyorum" and any(row.casefold().startswith(t) for t in low_terms):
            waiting.append(row)
        elif section == "Sözlerim" and any(t in row.casefold() for t in low_terms):
            owe.append(row)
    return waiting, owe


def weeks_since(d):
    return (datetime.now() - d).days / 7.0


def analyze():
    rows = []
    for p in load_people():
        mtgs = meetings_for(p)
        if not mtgs:
            continue
        dates = [d for d, _, _ in mtgs]
        last = dates[-1]
        stale_w = weeks_since(last)
        # kadans: ardisik toplantilar arasi medyan gun
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        cadence_d = statistics.median(gaps) if gaps else None
        # momentum: skorlu toplantilar; son skor vs oncekilerin ortalamasi
        scores = [s for _, s, _ in mtgs if s is not None]
        drop = None
        if len(scores) >= 2:
            prior_avg = statistics.mean(scores[:-1])
            if scores[-1] <= prior_avg - MOMENTUM_DROP:
                drop = round(prior_avg - scores[-1])
        waiting, owe = tasks_for(p)
        rows.append({
            "name": p["name"], "scope": p["scope"], "terms": p["terms"],
            "last": last, "stale_w": stale_w,
            "cadence_d": cadence_d, "last_score": scores[-1] if scores else None,
            "drop": drop, "waiting": waiting, "owe": owe, "n": len(mtgs),
        })
    return rows


def attendee_signals(attendee_strings):
    """meeting_brief icin: katilimci adlarina eslesen kisilerin ilişki sinyali.
    -> ['Ad: son temas Xh once, kadans ~Yg, son skor Z, ondan N madde bekliyorsun']"""
    low = [a.casefold() for a in attendee_strings if a]
    if not low:
        return []
    out = []
    for r in analyze():
        if not any(t.casefold() in a for a in low for t in r["terms"]):
            continue
        parts = [f"son temas {int(r['stale_w'])} hafta once"]
        if r["cadence_d"]:
            parts.append(f"normal kadans ~{int(r['cadence_d'])} gun")
        if r["last_score"] is not None:
            parts.append(f"son toplanti skoru {r['last_score']}")
        if r["drop"]:
            parts.append(f"MOMENTUM DUSUK ({r['drop']} puan)")
        if r["waiting"]:
            parts.append(f"ONDAN {len(r['waiting'])} acik madde bekliyorsun")
        if r["owe"]:
            parts.append(f"ONA {len(r['owe'])} madde borclusun")
        out.append(f"{r['name']} ({r['scope']}): " + ", ".join(parts))
    return out


def build_report(rows):
    red = [r for r in rows if r["stale_w"] >= STALE_RED_WEEKS]
    yellow = [r for r in rows if STALE_YELLOW_WEEKS <= r["stale_w"] < STALE_RED_WEEKS]
    momentum = [r for r in rows if r["drop"]]
    waiting = [r for r in rows if r["waiting"]]

    def fmt(r):
        return f"{r['name']} ({r['scope']}), son temas {int(r['stale_w'])} hafta once"

    msg = []
    if red:
        msg.append("🔴 Uzun sessizlik (32+ hafta):")
        msg += [f"- {fmt(r)}" for r in sorted(red, key=lambda r: -r["stale_w"])]
    if yellow:
        msg.append("\n🟡 Sessizleşti (20+ hafta):")
        msg += [f"- {fmt(r)}" for r in sorted(yellow, key=lambda r: -r["stale_w"])]
    if momentum:
        msg.append("\n📉 Momentum düşüşü (15+ puan):")
        msg += [f"- {r['name']}: son toplantı skoru {r['last_score']} ({r['drop']} puan düşük)"
                for r in momentum]
    if waiting:
        msg.append("\n⏳ Beklediklerin (açık maddeler):")
        for r in waiting:
            msg.append(f"- {r['name']}: {len(r['waiting'])} madde")
    return "\n".join(msg) if msg else ""


def write_snapshot(rows):
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    lines = ["---", "lang: tr",
             "summary_en: Deterministic relationship radar: per-person last contact, cadence, "
             "Spiky score momentum, and open reciprocity from meeting corpus + TASKS.md.",
             f"compiled_at: {datetime.now().isoformat(timespec='seconds')}",
             "type: relationships", "---", "# İlişki Radarı", "",
             f"Güncelleme: {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
             f"Eşik: sessizlik {STALE_YELLOW_WEEKS}h sarı / {STALE_RED_WEEKS}h kırmızı, "
             f"momentum {MOMENTUM_DROP}+ puan.", "",
             "| Kişi | Kapsam | Son temas | Kadans (gün) | Son skor | Momentum | Bekliyorsun | Borçlusun |",
             "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: -r["stale_w"]):
        cad = int(r["cadence_d"]) if r["cadence_d"] else "-"
        mom = f"-{r['drop']}" if r["drop"] else "-"
        lines.append(f"| [[{r['name']}]] | {r['scope']} | {r['last'].strftime('%Y-%m-%d')} "
                     f"({int(r['stale_w'])}h) | {cad} | {r['last_score'] or '-'} | {mom} "
                     f"| {len(r['waiting'])} | {len(r['owe'])} |")
    with open(OUT_FILE, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    rows = analyze()
    if not rows:
        print("radar: veri yok")
        return
    write_snapshot(rows)
    report = build_report(rows)
    if report:
        send_telegram("🤝 İlişki radarı (haftalık):\n\n" + report +
                      "\n\nDetay: .wiki/relationships/radar.md")
        print(f"radar gonderildi ({len(rows)} kisi analiz edildi)")
    else:
        print(f"radar temiz ({len(rows)} kisi, bayrak yok)")


if __name__ == "__main__":
    main()
