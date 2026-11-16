# brainless architecture

Since 2026-11-15 brainless runs on one Mac. There is no second machine, no chat
relay and no VPN. This page is the whole system; if it disagrees with the code,
the code wins and this page is wrong.

```
iPhone / iPad ──Telegram──▶ [Mac] capture.sh (every 2 min)
                                 ├─ plain message   → note in Thinking/Daily or Inbox/Links
                                 ├─ reply to bot    → reply queue → interactions.py (approve / follow up)
                                 └─ "?question"     → reply queue → read-only cited answer (local model)
iPhone / iPad ◀─iCloud Drive─▶ [Mac] the vault (Obsidian)
[Mac] hourly    cron_wrapper.sh  CRM snapshot, Spiky mail, Google Tasks, meeting brief,
                                 morning set once a day after 07:00, health check
[Mac] 23:00     night.sh         ingest → nightly → close-out → compile → index → links
                                 → lint → dreaming → Sunday weeklies → health
                                 → one commit + push
[Mac] always    llama-server     Gemma 4 12B on 127.0.0.1:8080, the local lanes
GitHub (private)                 off-site copy only, one push a night
```

## Jobs

Four launchd jobs, installed by `bash .agents/launchd/install.sh` (rerun it after
changing a script path; `--uninstall` removes them). All of them source
`.agents/scripts/env.sh`, the one place for PATH, PYTHONPATH and model routing.

| Job | When | Script | Writes git? |
|---|---|---|---|
| capture | every 120 s | `.agents/scripts/capture.sh` → `telegram_capture.py` → `tools/interactions.py` | no |
| hourly | every 3600 s | `.agents/scripts/cron_wrapper.sh` | no |
| night | 23:00, or on wake if missed | `.agents/scripts/night.sh` | yes, once |
| llm | always on | `llama-server` | no |

launchd never starts a second copy of a running job. A hand-started run can
still overlap the scheduled one, so `night.sh` holds a pid lock in
`.agents/state/night.pid` and a second shift exits at once. Only `night.sh`
(through `vault_backup.sh`) commits, so there is one writer and no conflict path. A night that ran as a next-morning catch-up skips the evening
close-out, which would otherwise be filed under the wrong date.
`.agents/state/night_last.json` records how the last night went; `health_check.py`
turns it red after two days.

Two Python lanes: Homebrew's `python3` for the stdlib-only steps, and the 3.13
venv (`BRAINLESS_SEARCH_PY`, see `env.sh`) for everything that needs a wheel.
Ingest (markitdown), the semantic index and links, and dreaming run in the venv;
install its packages with `requirements-core.txt`, `requirements-search.txt` and
`requirements-google.txt`. Nothing is installed into the system Python.

## Telegram

`tools/notify.py` is a durable outbox on the bot. Every message is queued in
`.agents/state/outbox.sqlite3` before it is sent, retried with backoff, and sent
at most once per key. The first line of a message is its channel label
(`#tasks`, `#thinking`, `#dreaming`, `#ops`, `#inbox`, ...). A reply is routed by
the label of the message it answers, so the today queue, the thinking loop,
dreaming and concept review keep their own approval rules
(`tools/today_reply.py`, `tools/thinking_reply.py`, `tools/dreaming.py`,
`tools/concept_review.py`). Telegram cannot say whether a timed-out send was
accepted, so a lost acknowledgement can produce one duplicate notice; it never
repeats a vault write.

## Models

Every prompt goes through `tools/llm.py` with a lane name (`python3 tools/llm.py --lanes`).

- Local (Gemma 4 12B, `openai-compatible` on loopback): note-classify,
  dreaming-judge, compile-aliases, capture-note, capture-photo (the image is
  attached; measured 2.9 s for a clean printed page), spiky-actions, ask.
- Claude CLI (subscription): compile, compile-concept, nightly, close-out,
  reconcile, resurface, research, thinking loop, digests, content, the morning
  briefing. These write the pages everything else reads; a weak writer caps what
  any reader can get back.
- A local lane never falls back to Claude. If the server is down the job fails
  and retries; the text does not leave the Mac.

To move a lane, add its name to the loop in `env.sh` after checking agreement
with Claude on real samples (`docs/local-inference.md`).

## Mobile

The vault is opened from iCloud Drive in Obsidian on the iPhone and iPad.
Capture, questions and approvals go through Telegram, so the phone needs no
engine at all.

## Backups

- Git history, pushed nightly to a private GitHub repo (the push is refused
  when the repo is public or cannot be checked).
- `tools/vault_archive.py`: a monthly age-encrypted archive outside the synced
  folder, the copy that a bad sync cannot overwrite.
