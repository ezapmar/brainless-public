#!/usr/bin/env python3
"""calibrate.py: close the decision feedback loop.

Surfaces decisions that need attention so judgment can compound:
  - DUE TO GRADE : review date has passed but Outcome is still "Pending review"
  - NEEDS PREDICTION : a `decided` note with no Prediction/Confidence
  - NO REVIEW DATE : pending/deferred note with no review date set

Run it manually, or it feeds the dashboard. Read-only.
"""
import os
import re
from datetime import date, datetime
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
DEC = VAULT / "Thinking" / "Decisions"
FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def fm(text):
    m = FM_RE.match(text)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                out[k.strip()] = v.partition("#")[0].strip()
    return out


def parse_date(s):
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def scan():
    today = date.today()
    due, needs_pred, no_review = [], [], []
    if not DEC.exists():
        return due, needs_pred, no_review
    for p in sorted(DEC.glob("*.md")):
        text = p.read_text(errors="ignore")
        meta = fm(text)
        status = meta.get("status", "").lower()
        rev = parse_date(meta.get("review", "")) or parse_date(meta.get("revisit", ""))
        graded = "_Pending review._" not in text and "## Outcome" in text and \
            not re.search(r"## Outcome.*?\n.*?_Pending review\._", text, re.S)
        has_pred = bool(meta.get("confidence")) or "**Prediction:**" in text and \
            not re.search(r"\*\*Prediction:\*\*\s*<!--", text)

        if rev and rev <= today and not graded:
            due.append((p.stem, rev))
        if status == "decided" and not has_pred:
            needs_pred.append(p.stem)
        if status in ("pending", "deferred") and not rev:
            no_review.append(p.stem)
    return due, needs_pred, no_review


def main():
    due, needs_pred, no_review = scan()
    if not (due or needs_pred or no_review):
        print("✅ All decisions calibrated, nothing due to grade.")
        return
    if due:
        print("📊 DUE TO GRADE (review date passed, outcome not written):")
        for name, rev in due:
            print(f"   - {name}  (review was {rev})")
    if needs_pred:
        print("\n🔮 NEEDS A PREDICTION (decided, but no calibrated forecast):")
        for name in needs_pred:
            print(f"   - {name}")
    if no_review:
        print("\n📅 NO REVIEW DATE SET (pending/deferred):")
        for name in no_review:
            print(f"   - {name}")


if __name__ == "__main__":
    main()
