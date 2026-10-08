#!/usr/bin/env python3
"""concept_review.py: concept proposals the owner answers in Buzz.

The compiler proposes a concept when several summaries share an idea no concept
covers (tools/compile_resources.py, concept-assign and the weekly
concept-propose). A proposal used to wait for a hand edit of
_Agent-Context/concepts.md, and on 25/09/2026 five had waited up to three days
with nobody deciding. So each proposal now arrives as its own message in Buzz
#dreaming, next to the link proposals, and a reply decides it:

  evet   the row becomes `active`; the next compile assigns every in-scope
         summary again and writes the page
  hayır  the row becomes `retired`; it stays in the file so the idea is not
         proposed again
  atla   nothing changes; the proposal keeps waiting and keeps its seat

Bounds: no new proposals while MAX_PENDING wait (the compiler asks `room()`
before adding any). A personal proposal (family, health) shows no title in
Buzz, only where to read it in the vault, and is decided the same way.

Without Buzz (the lite profile) the same decision is made in the terminal:
`brainless concepts review` shows each waiting proposal and takes the same
words (yes/no/skip, evet/hayır/atla); `--decide SLUG yes` does one from a script.

Usage:
  python3 tools/concept_review.py              announce proposals not yet in Buzz
  python3 tools/concept_review.py --list       print what is waiting
  python3 tools/concept_review.py --review     decide them one by one here
  python3 tools/concept_review.py --decide SLUG yes|no
"""
import argparse
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import concepts as C
from vault_lock import lock_exclusive

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
REGISTRY = VAULT / "_Agent-Context" / "concepts.md"
STATE = VAULT / ".agents" / "state" / "concept_review.json"
CHANNEL = IDENTITY = "dreaming"
MAX_PENDING = 5
MAX_SOURCES = 5
DAY = 86400


@contextmanager
def registry_lock():
    """Serialise every write to concepts.md: the compile appends proposals while
    the reply worker (every two minutes) may be changing a status."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with (STATE.parent / "concepts-registry.lock").open("a") as lock:
        lock_exclusive(lock)
        yield


@contextmanager
def locked():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with STATE.with_suffix(".lock").open("a") as lock:
        lock_exclusive(lock)
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        state.setdefault("announced", {})
        state.setdefault("handled", [])
        yield state
        from today_queue import atomic_write
        atomic_write(STATE, json.dumps(state, ensure_ascii=False, indent=1) + "\n")


def rows() -> list[dict]:
    return C.parse_registry(C.read_page(REGISTRY))


def pending(all_rows=None) -> list[dict]:
    return [r for r in (all_rows if all_rows is not None else rows()) if r["status"] == "proposed"]


def room(all_rows=None) -> int:
    """How many new proposals the registry may take now."""
    return max(0, MAX_PENDING - len(pending(all_rows)))


def set_status(slug: str, status: str):
    from today_queue import atomic_write
    with registry_lock():
        atomic_write(REGISTRY, C.set_status(C.read_page(REGISTRY), slug, status))


def _line_no(slug: str) -> int:
    for i, line in enumerate(C.read_page(REGISTRY).splitlines(), 1):
        if line.split("|")[0].strip() == slug:
            return i
    return 0


def members(slug: str, catalogue=None) -> list[dict]:
    if catalogue is None:
        from compile_resources import summary_catalogue
        catalogue = summary_catalogue()
    return [s for s in catalogue if slug in (s["concepts"] or [])]


def preview(row: dict, mem: list[dict]) -> str:
    from i18n import t
    footer = t("dreaming.footer")
    if row["sensitivity"] == "personal":
        return "\n".join([t("concept_review.title_personal"), "",
                          t("concept_review.personal_where", line=_line_no(row["slug"])), "", footer])
    seen, sources = set(), []
    for s in mem:
        base = s["stem"].removesuffix("_raw")
        if base not in seen:
            seen.add(base)
            sources.append(s["source"])
    lines = [t("concept_review.title", title=row["title"]), ""]
    if row["scope"]:
        lines += [row["scope"], ""]
    if row["aliases"]:
        lines.append(t("concept_review.aliases", aliases=", ".join(row["aliases"])))
    lines.append(t("concept_review.sources", n=len(sources)))
    lines += [f"- `{s}`" for s in sources[:MAX_SOURCES]]
    if len(sources) > MAX_SOURCES:
        lines.append(t("concept_review.more", n=len(sources) - MAX_SOURCES))
    return "\n".join(lines + ["", t("concept_review.adds"), "", footer])


def announce(box=None, catalogue=None) -> int:
    """Post every waiting proposal that is not in Buzz yet. Returns how many.

    Without a Buzz identity on this machine (the lite profile) nothing is posted
    and nothing is marked announced, so the proposals stay pending for whatever
    reviews them there instead of vanishing into an outbox nobody delivers.
    """
    if box is None and not (Path.home() / ".config/brainless/buzz/keys" / IDENTITY).exists():
        return 0
    waiting = pending()
    if not waiting:
        return 0
    n = 0
    with locked() as state:
        for row in waiting:
            if row["slug"] in state["announced"]:
                continue
            if catalogue is None:
                from compile_resources import summary_catalogue
                catalogue = summary_catalogue()
            body = preview(row, members(row["slug"], catalogue))
            key = "concept:prop:" + hashlib.sha256(f"{row['slug']}\0{int(time.time())}".encode()).hexdigest()[:20]
            from buzz_delivery import Outbox
            box = box or Outbox()
            box.enqueue(IDENTITY, CHANNEL, body, key=key)
            box.deliver(key)
            rec = box.record(key)
            state["announced"][row["slug"]] = {"key": key, "event": rec["event"] if rec else None,
                                               "created": time.time()}
            n += 1
    return n


def waiting_list(now: float | None = None) -> list[tuple[str, int]]:
    """(label, days waiting) for the change brief; personal rows show no title."""
    from i18n import t
    now = now or time.time()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"announced": {}}
    out = []
    for r in pending():
        since = state.get("announced", {}).get(r["slug"], {}).get("created")
        days = int((now - since) // DAY) if since else 0
        label = t("concept_review.personal_label") if r["sensitivity"] == "personal" else r["title"]
        out.append((label, days))
    return out


# ─── Buzz replies (called from tools/buzz_interactions.py) ──────

def handle(msg, channel, owner, *, text=None, box=None):
    """Decide the proposal this message replies to. False when the message is
    not a reply to a concept proposal, so the next handler can take it."""
    from buzz_delivery import Outbox, event_id
    from i18n import t
    from today_buzz import direct_parent
    from dreaming import words
    box = box or Outbox()
    if msg.get("pubkey") != owner or channel != box.client.channel(CHANNEL, IDENTITY):
        return False
    mid, parent = event_id(msg), direct_parent(msg)
    if not mid or not parent:
        return False
    with locked() as state:
        for a in state["announced"].values():
            if not a.get("event"):
                rec = box.record(a["key"])
                a["event"] = rec["event"] if rec else None
        slug = next((s for s, a in state["announced"].items() if a.get("event") == parent), None)
        if not slug:
            return False
        if mid in state["handled"]:
            return True
        row = next((r for r in rows() if r["slug"] == slug), None)
        word = (msg.get("content", "") if text is None else text).strip().casefold().rstrip(".!")
        label = row["title"] if row and row["sensitivity"] != "personal" else t("concept_review.personal_label")
        if not row or row["status"] != "proposed":
            body = t("concept_review.already", decision=row["status"] if row else "?")
        elif word in words("apply"):
            set_status(slug, "active")
            body = t("concept_review.applied", title=label)
        elif word in words("reject"):
            set_status(slug, "retired")
            body = t("concept_review.rejected", title=label)
        elif word in words("skip"):
            body = t("concept_review.skipped")
        else:
            body = t("dreaming.unknown")
        key = "concept:reply:" + mid
        box.enqueue(IDENTITY, CHANNEL, body, key=key, parent=mid)
        box.deliver(key)
        state["handled"] = (state["handled"] + [mid])[-500:]
    return True


def decide(slug: str, word: str) -> str:
    """Apply one answer to one proposal; returns the message to show. The same
    words and the same effect as a Buzz reply."""
    from dreaming import words
    from i18n import t
    row = next((r for r in rows() if r["slug"] == slug), None)
    if not row:
        return t("concept_review.unknown", slug=slug)
    label = row["title"] if row["sensitivity"] != "personal" else t("concept_review.personal_label")
    if row["status"] != "proposed":
        return t("concept_review.already", decision=row["status"])
    word = word.strip().casefold().rstrip(".!")
    if word in words("apply") or word in ("y", "e"):
        set_status(slug, "active")
        return t("concept_review.applied", title=label)
    if word in words("reject") or word in ("n", "h"):
        set_status(slug, "retired")
        return t("concept_review.rejected", title=label)
    if word in words("skip") or word in ("s", "a", ""):
        return t("concept_review.skipped")
    return t("dreaming.unknown")


def review(ask=input, out=print, catalogue=None) -> int:
    """Terminal review of every waiting proposal. Returns how many were decided."""
    from i18n import t
    waiting = pending()
    if not waiting:
        out(t("concept_review.none_waiting"))
        return 0
    decided = 0
    for row in waiting:
        if catalogue is None:
            from compile_resources import summary_catalogue
            catalogue = summary_catalogue()
        body = preview(row, members(row["slug"], catalogue))
        out("\n".join(body.splitlines()[:-2]))          # the Buzz reply footer does not apply here
        answer = ask(t("concept_review.terminal_prompt"))
        msg = decide(row["slug"], answer)
        out(msg + "\n")
        decided += msg != t("concept_review.skipped")
    return decided


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true", help="print what is waiting and exit")
    ap.add_argument("--review", action="store_true", help="decide waiting proposals in the terminal")
    ap.add_argument("--decide", nargs=2, metavar=("SLUG", "ANSWER"), help="decide one proposal")
    args = ap.parse_args(argv)
    if args.decide:
        print(decide(*args.decide))
        return 0
    if args.review:
        review()
        return 0
    if args.list:
        for label, days in waiting_list():
            print(f"{days:>3}d  {label}")
        print(f"room for {room()} new proposal(s)")
        return 0
    print(f"announced {announce()} proposal(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
