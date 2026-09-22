# Today queue

> **In the loop:** [3. Decide](../README.md#3-decide) and [4. Get shit done](../README.md#4-bonus-get-shit-done). The morning three.

Today selects at most three items, with no model calls during selection:

1. A pending decision due within 14 days, a deferred decision whose review date
   has arrived, or a decided note with an overdue, ungraded outcome.
2. The oldest unfinished commitment in the ledger's Promises or Waiting section,
   captured at least two days ago. Undated commitments remain eligible.
3. A source from the current resurfacing list (less than seven days old), falling
   back to a belief note when there is no eligible resurfaced source.

Private and missing sources are excluded. Each item includes its source path;
the local note uses Obsidian links. Empty categories stay empty. Completing an
item does not refill its slot that day.

## Local use

```bash
brainless today                 # read-only preview
brainless today --build         # save _Agent-Context/TODAY.md and queue state
brainless today --send          # deliver to Buzz #tasks
```

For scripted or local interaction, use the item ID printed in the note:

```bash
brainless today --action ITEM_ID answer --text "Run a two-week pilot."
brainless today --action ITEM_ID apply
brainless today --action ITEM_ID defer --text YYYY-MM-DD
brainless today --action ITEM_ID dismiss --text "No longer relevant."
```

The date supplied to `defer` must be in the future. IDs above are placeholders;
the command prints the actual IDs. `edit` discards a preview and accepts a new
answer. For a commitment, the new answer becomes a proposed task title.

## Buzz

The existing `.agents/scripts/task_reminder.py` entry point now sends Today
instead of the old stale-task list. Existing daily reminder schedules need no
new timer once their checkout has been updated. Installations without a reminder
schedule can use the local commands or schedule that entry point themselves.

The Buzz interaction worker handles replies within the corresponding #tasks
thread. Reply directly to the latest preview to apply it. The configured owner
pubkey, channel, thread and revision must all match. Telegram only captures notes;
its old commands and buttons cannot apply anything.

- **Apply:** a commitment is marked complete. A decision or evidence response
  requires a preview first. Applying a task-title edit leaves the task open.
- **Edit:** reply with revised wording, then inspect and apply the new preview.
- **Defer:** reply with a future `YYYY-MM-DD` date.
- **Dismiss:** reply with a reason; the source remains unchanged. The item stays
  suppressed until its source changes.

Voice replies use the existing transcription path. Answers are recorded as the
owner supplied them, without an LLM rewriting or expanding them.

After two deferrals, the prompt asks for a blocker or smaller next step. Applying
that answer records the blocker and pauses the item for seven days; it does not
mark the original decision or commitment complete.

## Recorded changes

A decision answer appends a dated Decision section and sets `status: decided`.
An outcome answer appends a dated Outcome section, sets `graded`, and updates the
matching Calibration table row when one exists. Evidence reviews and blockers
are recorded in `.wiki/digests/queries/`, without modifying their source notes.
Every applied action has a receipt there. Seven-day counts appear in TODAY.md.

Queue state, delivery IDs, deferrals, draft revisions, and a recovery journal live
in the gitignored `.agents/state/today_queue.json`. Run the Buzz sender and
poller against the same vault on one machine: this state is intentionally local.
Back up that state with the worker if you need to preserve dismissal history.

Approvals are tied to the displayed draft revision. Changed sources and ambiguous duplicate task
rows cannot be overwritten by an old preview. Approved writes can resume after a
process interruption. A stale source requires review at the source or the next
day's rebuilt queue.

Successful sends are remembered individually. Pending delivery survives restarts;
lost acknowledgements are reconciled against the author's message marker before
retrying. See [Buzz interactions](buzz-interactions.md).

## Verification

```bash
python3 -B -m unittest discover -s tools/tests -v
```

Tests use a fictional vault and a mocked Buzz relay. No credentials,
messages to real chats, or model calls are needed.
