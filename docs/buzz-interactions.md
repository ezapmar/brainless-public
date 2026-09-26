# Telegram capture, Buzz conversation

> **In the loop:** [4. Get shit done](../README.md#4-bonus-get-shit-done). Where reminders arrive and where you answer them.

Telegram is an incoming inbox. Text, photos, voice and supported audio/video files
still enter `Thinking/Daily/` or `Inbox/Links/`. All receipts, failures, questions,
previews and approvals go to Buzz. The Telegram API helper permits only
`getUpdates` and `getFile`; old commands and callback buttons cannot apply changes.

| Output | Buzz channel |
|---|---|
| Capture receipts and media errors | `#inbox` |
| Today queue, commitments, meeting briefs | `#tasks` |
| Weekly thinking question and previews | `#thinking` |
| Watchdog, power, unit failures, updates | `#ops` |
| Relationship radar, thinker digest | `#radar` |
| Content suggestions | `#content` |
| Morning briefing, evening closeout | `#daily` |
| CRM snapshot | `#crm` |
| Link and concept proposals | `#dreaming` |

Reply inside the relevant thread. Today supports `apply`, `edit`, `defer` and
`dismiss`, plus the owner's language equivalents. Defer asks for a date, dismiss
for a reason. Apply must be a direct reply to the latest displayed preview.
Weekly thinking supports `apply`, `cancel` and `skip` in its own thread. These
bounded workflows are the only automatic paths that can write approved human
notes. Source changes invalidate approval. Repeated events do not apply twice.

Other owner replies receive a read-only, source-grounded answer in the same
thread and a `.wiki/digests/queries/` receipt. Explicit @mentions remain with the
existing Asistan/persona harnesses; the polling assistant does not also answer.
The existing `#research` and `#dialectic` harnesses are unchanged.

## Delivery and recovery

`tools/buzz_delivery.py` persists outgoing messages in
`.agents/state/buzz_outbox.sqlite3` before sending them. A failed send stays queued.
The outbox records the relay event id, not just the CLI exit code. If an
acknowledgement is lost, it looks for the exact message marker and author in relay
history before retrying. An incomplete history read retains the cursor.

`tools/buzz_interactions.py` stores incoming owner replies and advances the channel
cursor in one transaction. A failed reply remains pending; subsequent replies in
that channel wait so an apply cannot overtake its draft. Other channels continue.
It also drains the outbox. `buzz_post.sh` now reports `queued as`, which means
persisted locally; it does not claim remote delivery.

Telegram raw updates are journaled in `.agents/state/telegram_pending/` before
advancing the polling offset. Failed processing retains the input and reports to
Buzz. Successful captures use a stable timestamp/message-id filename on retries.
State, owner keys and pending inputs stay outside git export.

Buzz outages do not re-enable Telegram messages. Pending counts and the last
completed poll are recorded in `buzz_interactions_status.json`; watchdog and health
checks surface a stalled worker. If the relay and worker are both unavailable,
there is no real-time notification channel until service recovers.

## Installation and cutover

Requirements: the existing Buzz channels, named keys and owner pubkey must already
exist on the worker in `~/.config/brainless/buzz/`. No keys are created or printed
by the interaction worker. It reuses the keys configured for existing channels.

1. Back up `today_queue.json`, `thinking_loop.json` and any existing Buzz outbox.
2. Deploy the code and run `python3 -B -m unittest discover -s tools/tests`.
3. Install `brainless-buzz-interactions.service` and `.timer` from
   `.agents/systemd/` into the user's systemd directory; run daemon-reload.
4. Run `python3 tools/today_queue.py --send` once to migrate pending Today items.
   Running the interaction worker migrates an existing weekly question and preview
   without choosing a new question. Old Telegram message ids no longer authorize
   writes; the preview must be approved again in Buzz.
5. Enable `brainless-buzz-interactions.timer`. It runs two minutes after the
   previous poll completes. The existing Sunday thinking timer and daily Today
   timer use Buzz automatically.
6. Verify the saved previews in `#tasks` and `#thinking`, an inbox receipt, and
   `buzz_interactions_status.json`. Confirm no active code calls Telegram
   `sendMessage` or `answerCallbackQuery`.

To pause replies, stop the interaction timer and service. Messages remain queued.
Rollback must preserve the capture-only Telegram boundary; it must not silently
restore Telegram as an outgoing channel.
