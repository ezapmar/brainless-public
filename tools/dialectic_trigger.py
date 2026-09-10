#!/usr/bin/env python3
"""Buzz-native trigger for the dialectic engine.

Watches the #dialectic channel and starts a full moderated round on demand, so
a round can be kicked off from the Buzz app instead of the CLI or the 12:30 /
21:20 timers. Post this in #dialectic:

    !dialectic <thesis>

The thesis is argued by the persona agents and the moderator files the synthesis
note, exactly like a scheduled round. Phrase it as a claim, not a question, for
the sharpest arguments.

Runs from brainless-dialectic-trigger.timer (every 60s). Safety:
  - only a NON-bot author triggers a round. Bots are every identity with a key
    file in <buzz>/keys (moderator + personas + posters), so they can never
    self-trigger; a human (the owner's own Buzz identity) has no key file here.
  - a flock (~/.dialectic-round.lock) keeps two triggered rounds from
    overlapping; while one runs, later commands wait for the next tick.
  - a seen-id file keeps one command from firing twice, and the first run seeds
    the baseline so historic messages never fire.

Options: --dry-run (detect and report, launch nothing), --self-test (offline
logic check, no Buzz).
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
import dialectic as D  # noqa: E402  reuse buzz(), channel_id, post, pubkey, read/write, log

TRIGGER_RE = re.compile(r"^\s*!dialectic\s+(.+)", re.IGNORECASE | re.DOTALL)
BARE_RE = re.compile(r"^\s*!dialectic\s*$", re.IGNORECASE)
STATE_DIR = os.path.join(VAULT, ".agents", "state")
SEEN_FILE = os.path.join(STATE_DIR, "dialectic_trigger_seen")
LOCK_FILE = os.path.expanduser("~/.dialectic-round.lock")
WORKER_JOB = os.path.join(VAULT, ".agents", "scripts", "worker_job.sh")
MAX_THESIS = 500
FETCH_LIMIT = 25


def bot_pubkeys():
    """Every identity we control (moderator, personas, posters) -> its pubkey."""
    keys_dir = os.path.join(D.BUZZ_DIR, "keys")
    pks = set()
    try:
        names = os.listdir(keys_dir)
    except OSError:
        return pks
    for name in names:
        pk = D.pubkey(name)
        if pk:
            pks.add(pk)
    return pks


def recent_messages(channel, limit=FETCH_LIMIT):
    out = D.buzz(["messages", "get", "--channel", channel, "--limit", str(limit)])
    try:
        data = json.loads(out)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def load_seen():
    return set(D.read_file(SEEN_FILE).split())


def save_seen(seen):
    D.write_file(SEEN_FILE, "\n".join(sorted(seen)[-2000:]))


def find_triggers(messages, bots, seen):
    """-> (rounds, bare) where rounds=[(ts,id,thesis)] from non-bot authors."""
    rounds, bare = [], []
    for m in messages:
        mid = m.get("id")
        pk = m.get("pubkey")
        content = m.get("content") or ""
        if not mid or mid in seen or pk in bots:
            continue
        mo = TRIGGER_RE.match(content)
        if mo:
            thesis = D.no_dashes(mo.group(1).strip())[:MAX_THESIS]
            if thesis:
                rounds.append((int(m.get("created_at") or 0), mid, thesis))
        elif BARE_RE.match(content):
            bare.append(mid)
    rounds.sort()
    return rounds, bare


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="detect and report, launch nothing")
    ap.add_argument("--self-test", action="store_true", help="offline logic check, no Buzz")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    channel = D.channel_id(D.CHANNEL_NAME)
    if not channel:
        print(f"no #{D.CHANNEL_NAME} channel in channels.json", file=sys.stderr)
        return 1

    seen = load_seen()
    first_run = not os.path.exists(SEEN_FILE)
    bots = bot_pubkeys()
    messages = recent_messages(channel)
    rounds, bare = find_triggers(messages, bots, seen)

    # First run: baseline every current command as seen so history never fires.
    if first_run:
        for _, mid, _ in rounds:
            seen.add(mid)
        seen.update(bare)
        save_seen(seen)
        D.log(f"baseline: {len(rounds)} historic command(s) marked seen, none fired")
        return 0

    # Bare "!dialectic" with no thesis: one usage hint, then forget it.
    for mid in bare:
        seen.add(mid)
    if bare and not args.dry_run:
        D.post(channel, "Usage: `!dialectic <thesis>` - state a claim and the "
                        "personas will argue it (a full round takes ~15 min).")

    if not rounds:
        save_seen(seen)
        return 0

    ts, mid, thesis = rounds[-1]  # newest command wins
    if args.dry_run:
        print(f"would launch round on: {thesis!r} "
              f"({len(rounds)} pending, {len(bare)} bare)")
        return 0

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # A round is already running; do not mark these seen, retry next tick.
        D.log("round in progress; deferring trigger")
        return 0

    for _, oid, _ in rounds:  # all pending commands are now handled
        seen.add(oid)
    save_seen(seen)
    if len(rounds) > 1:
        D.post(channel, f"Starting the most recent request; skipped "
                        f"{len(rounds) - 1} earlier `!dialectic` message(s).")
    D.post(channel, f"Dialectic round starting on: {thesis}\n"
                    "Personas are arguing now; this takes about 15 minutes.")
    D.log(f"launching round: {thesis!r}")
    rc = subprocess.run(["/bin/bash", WORKER_JOB, "tools/dialectic.py", "--topic", thesis],
                        cwd=VAULT).returncode
    D.log(f"round exit {rc}")
    return 0


def self_test():
    bots = {"botpub_moderator", "botpub_skeptic"}
    seen = {"old1"}
    msgs = [
        {"id": "a1", "pubkey": "human", "content": "!dialectic We should delay the UK move", "created_at": "10"},
        {"id": "a2", "pubkey": "botpub_skeptic", "content": "!dialectic bot echo should not fire", "created_at": "11"},
        {"id": "old1", "pubkey": "human", "content": "!dialectic already seen", "created_at": "9"},
        {"id": "a3", "pubkey": "human", "content": "just chatting, no command", "created_at": "12"},
        {"id": "a4", "pubkey": "human", "content": "!dialectic Ship the SGK product first", "created_at": "13"},
        {"id": "b1", "pubkey": "human", "content": "!dialectic", "created_at": "14"},
        {"id": "b2", "pubkey": "human", "content": "  !DIALECTIC   Buy vs build  ", "created_at": "15"},
    ]
    rounds, bare = find_triggers(msgs, bots, seen)
    ids = [mid for _, mid, _ in rounds]
    ok = True
    checks = [
        (ids == ["a1", "a4", "b2"], f"round ids wrong: {ids}"),
        ("a2" not in ids, "bot echo triggered"),
        ("old1" not in ids, "already-seen fired"),
        (bare == ["b1"], f"bare wrong: {bare}"),
        (rounds[-1][2] == "Buy vs build", f"newest/parse wrong: {rounds[-1]}"),
    ]
    for cond, msg in checks:
        if not cond:
            print(f"FAIL: {msg}")
            ok = False
    print("all ok" if ok else "FAILURES above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
