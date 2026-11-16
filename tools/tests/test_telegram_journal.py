"""Offline regressions for the Telegram poller's pending journal.

The offset advances before a message is processed, so the journal is the only
copy of a message that failed: every authorised update is written to
.agents/state/telegram_pending/<update_id>.json first, retried on every run,
and removed only once it has been handled. The handler's result is stored
before delivery so a retry never reruns OCR or transcription, and a receipt
that went out is not sent again. A handler that keeps failing is retried on a
backoff ladder, its attempts counted in the record, and parked with one Buzz
notice after the last attempt. Telegram and Buzz are mocks; nothing leaves
the machine. The fixture is copied from test_telegram_whitelist.py.

Run: python3 -B -m unittest discover -s tools/tests -v
"""
import atexit
import contextlib
from datetime import datetime
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]

_IMPORT_VAULT = tempfile.mkdtemp(prefix="brainless-telegram-journal-")
atexit.register(shutil.rmtree, _IMPORT_VAULT, True)
_OLD_VAULT = os.environ.get("BRAINLESS_VAULT")
os.environ["BRAINLESS_VAULT"] = _IMPORT_VAULT
try:
    import telegram_capture as capture
finally:
    if _OLD_VAULT is None:
        os.environ.pop("BRAINLESS_VAULT", None)
    else:
        os.environ["BRAINLESS_VAULT"] = _OLD_VAULT

OWNER, STRANGER = "42", "99"
DATE = 1791446400  # a fixed capture time, so the note stamp is predictable


def update(uid, chat, text="hello", date=DATE):
    return {"update_id": uid, "message": {"message_id": uid, "date": date,
                                          "chat": {"id": int(chat), "type": "private"},
                                          "from": {"id": int(chat), "is_bot": False}, "text": text}}


class JournalFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        self.conf = root / "config"
        self.conf.mkdir()
        self.token = self.conf / "telegram_token"
        self.chat = self.conf / "telegram_chat_id"
        self.state = self.vault / ".agents" / "state" / "telegram_offset"
        self.journal = self.state.parent / "telegram_pending"
        self.daily = self.vault / "Thinking" / "Daily"
        self.token.write_text("fixture-token")
        self.chat.write_text(OWNER)
        for name, value in (("CONF_DIR", str(self.conf)), ("TOKEN_FILE", str(self.token)),
                            ("CHAT_FILE", str(self.chat)), ("STATE_FILE", str(self.state)),
                            ("VAULT", str(self.vault)), ("CAPTURE_DIR", str(self.daily)),
                            ("LINKS_DIR", str(self.vault / "Inbox" / "Links"))):
            self.enterContext(patch.object(capture, name, value))
        self.handle = self.enterContext(patch.object(capture, "handle_message", Mock(return_value="Saved title")))
        self.enterContext(patch.object(capture.time, "sleep", Mock()))
        self.enterContext(patch.dict(os.environ, {}, clear=False))
        os.environ.pop("BRAINLESS_TG_ALLOW_ADOPT", None)
        self.buzz = self.enterContext(patch("notify.send", Mock(return_value=True)))
        self.enterContext(patch("interactions.main", Mock(return_value=0)))
        self.out = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.out))

    def poll(self, updates):
        def api(token, method, params=None, timeout=30):
            if method == "getUpdates":
                return {"ok": True, "result": updates}
            return {"ok": True, "result": {"message_id": 1}}
        self.api = Mock(side_effect=api)
        self.mark = len(self.buzz.call_args_list)  # sent() reads this poll only
        with patch.object(capture, "api", self.api):
            capture.main()
        return self.out.getvalue()

    def sent(self):
        """Receipts to the chat: since 2026-11-15 one outbox message per note."""
        return [{"text": c.args[1].splitlines()[0]} for c in self.buzz.call_args_list[self.mark:]
                if c.kwargs.get("key", "").endswith(":saved")]

    def buzz_keys(self):
        return [c.kwargs.get("key") for c in self.buzz.call_args_list]

    def pending(self):
        return sorted(p.name for p in self.journal.glob("*.json")) if self.journal.exists() else []

    def record(self, uid):
        return json.loads((self.journal / f"{uid}.json").read_text())

    def journal_write(self, uid, record):
        self.journal.mkdir(parents=True, exist_ok=True)
        (self.journal / f"{uid}.json").write_text(json.dumps(record))


class RetryTest(JournalFixture):
    def test_a_failed_message_is_journalled_and_retried_on_the_next_run(self):
        self.handle.side_effect = [RuntimeError("ocr down"), "Second try"]
        log = self.poll([update(1, OWNER, "a thought")])
        self.assertIn("Message handling error: RuntimeError", log)
        self.assertEqual(self.pending(), ["1.json"])
        record = self.record(1)
        self.assertEqual({k: record[k] for k in ("chat", "message")},
                         {"chat": OWNER, "message": update(1, OWNER, "a thought")["message"]})
        self.assertEqual((record["attempts"], record["last_error"]), (1, "RuntimeError: ocr down"))
        self.assertEqual(self.state.read_text(), "1", "the offset moves on; the journal holds the message")
        self.assertEqual(self.sent(), [], "no receipt for a message that was not saved")
        self.assertIn("telegram:failure:1", self.buzz_keys())

        self.poll([])
        self.assertEqual(self.handle.call_count, 2)
        self.assertEqual(self.handle.call_args.args[1], update(1, OWNER, "a thought")["message"])
        self.assertEqual(self.pending(), [], "a handled message leaves the journal")
        self.assertEqual([m["text"] for m in self.sent()],
                         [capture.t("telegram_capture.reply_saved", title="Second try")])
        self.assertIn(f"telegram:{OWNER}:1:saved", self.buzz_keys())

    def test_a_message_that_succeeds_leaves_no_journal(self):
        self.poll([update(1, OWNER), update(2, OWNER)])
        self.assertEqual(self.handle.call_count, 2)
        self.assertTrue(self.journal.is_dir())
        self.assertEqual(self.pending(), [])
        self.assertEqual(len(self.sent()), 2)

    def test_the_result_is_stored_before_delivery_so_a_retry_does_not_rerun_the_handler(self):
        calls = []

        def flaky(channel, body, key=None, **kw):
            calls.append(key)
            if key.endswith(":saved") and calls.count(key) == 1:
                raise OSError("buzz outbox unwritable")
            return True
        self.buzz.side_effect = flaky
        self.poll([update(1, OWNER)])
        self.assertEqual(self.record(1)["result"], "Saved title")
        self.assertNotIn("acked", self.record(1))
        self.assertEqual(stat.S_IMODE((self.journal / "1.json").stat().st_mode), 0o600,
                         "the rewritten record stays owner-only")

        self.poll([])
        self.assertEqual(self.handle.call_count, 1, "OCR and transcription are not repeated")
        self.assertEqual(self.pending(), [])
        self.assertEqual(len(self.sent()), 1)

    def test_a_stored_result_is_receipted_without_rerunning_the_handler(self):
        msg = update(7, OWNER)["message"]
        self.journal_write(7, {"chat": OWNER, "message": msg, "result": "Done", "acked": True})
        self.poll([])
        self.handle.assert_not_called()
        self.assertEqual(len(self.sent()), 1, "one receipt; the outbox dedupes by key if it was already sent")
        self.assertIn(f"telegram:{OWNER}:7:saved", self.buzz_keys())
        self.assertEqual(self.pending(), [])

    def test_a_message_with_nothing_to_save_is_dropped_without_a_receipt(self):
        self.handle.return_value = None
        self.poll([update(3, OWNER, "/start")])
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.sent(), [])
        self.assertNotIn(f"telegram:{OWNER}:3:saved", self.buzz_keys())

    def test_a_journalled_message_from_another_chat_is_kept_and_not_handled(self):
        self.journal_write(5, {"chat": STRANGER, "message": update(5, STRANGER)["message"]})
        self.poll([])
        self.handle.assert_not_called()
        self.assertEqual(self.pending(), ["5.json"])

    def test_a_replayed_update_does_not_overwrite_its_journal_entry(self):
        self.journal_write(3, {"chat": OWNER, "message": update(3, OWNER, "original")["message"]})
        self.poll([update(3, OWNER, "replayed")])
        self.assertEqual(self.handle.call_count, 1)
        self.assertEqual(self.handle.call_args.args[1]["text"], "original")
        self.assertEqual(self.pending(), [])

    def test_a_new_journal_entry_is_owner_only_and_dated(self):
        self.handle.side_effect = RuntimeError("boom")
        msg = update(4, OWNER)
        del msg["message"]["date"]
        self.poll([msg])
        self.assertEqual(stat.S_IMODE((self.journal / "4.json").stat().st_mode), 0o600)
        self.assertIsInstance(self.record(4)["message"]["date"], int,
                              "a date is fixed at journalling, so the retry writes the same file name")

    def test_the_receipt_lists_the_notes_written_for_the_message(self):
        stamp = datetime.fromtimestamp(DATE).strftime("%Y-%m-%d-%H%M%S") + "-1"
        self.daily.mkdir(parents=True)
        (self.daily / f"{stamp}-telegram.md").write_text("# Saved title\n")
        (self.daily / "unrelated.md").write_text("# other\n")
        self.poll([update(1, OWNER)])
        saved = [c for c in self.buzz.call_args_list if c.kwargs.get("key", "").endswith(":saved")]
        self.assertEqual(len(saved), 1)
        body = saved[0].args[1]
        self.assertIn(f"Thinking/Daily/{stamp}-telegram.md", body)
        self.assertNotIn("unrelated.md", body)


class BackoffTest(JournalFixture):
    """A message whose handler always fails is not re-downloaded every run."""

    def setUp(self):
        super().setUp()
        self.clock = DATE + 1000
        self.enterContext(patch.object(capture.time, "time", Mock(side_effect=lambda: self.clock)))
        self.handle.side_effect = RuntimeError("corrupt audio")

    def parked_keys(self):
        return [k for k in self.buzz_keys() if k and k.startswith("telegram:parked:")]

    def test_each_failed_attempt_is_counted_in_the_record(self):
        self.poll([update(1, OWNER)])
        self.assertEqual(self.record(1)["attempts"], 1)
        self.poll([])                                  # the first retry is the next run
        record = self.record(1)
        self.assertEqual(record["attempts"], 2)
        self.assertEqual(record["last_attempt"], self.clock)
        self.assertEqual(record["last_error"], "RuntimeError: corrupt audio")
        self.assertEqual(self.handle.call_count, 2)

    def test_a_retry_inside_the_backoff_window_is_skipped(self):
        self.poll([update(1, OWNER)])
        self.poll([])
        self.assertEqual(self.handle.call_count, 2)
        wait = capture.retry_delay(2)
        self.assertGreater(wait, 0)
        self.clock += wait - 1
        self.poll([])
        self.assertEqual(self.handle.call_count, 2, "still inside the window: no download, no transcript")
        self.assertEqual(self.record(1)["attempts"], 2)
        self.clock += 1
        self.poll([])
        self.assertEqual(self.handle.call_count, 3)

    def test_the_message_is_parked_after_the_last_attempt_with_one_notice(self):
        self.poll([update(1, OWNER)])
        for _ in range(capture.MAX_ATTEMPTS + 3):
            self.clock += max(capture.RETRY_DELAYS)
            self.poll([])
        self.assertEqual(self.handle.call_count, capture.MAX_ATTEMPTS)
        record = self.record(1)
        self.assertEqual(record["state"], "parked")
        self.assertEqual(record["attempts"], capture.MAX_ATTEMPTS)
        self.assertEqual(self.pending(), ["1.json"], "a parked message stays on disk")
        self.assertEqual(self.parked_keys(), ["telegram:parked:1"], "one notice, sent once")
        self.assertEqual(self.sent(), [])

    def test_a_parked_record_is_neither_handled_nor_announced_again(self):
        msg = update(8, OWNER)["message"]
        self.journal_write(8, {"chat": OWNER, "message": msg, "attempts": capture.MAX_ATTEMPTS,
                               "last_attempt": 0, "last_error": "x", "state": "parked"})
        self.poll([])
        self.handle.assert_not_called()
        self.assertEqual(self.buzz.call_count, 0)
        self.assertEqual(self.pending(), ["8.json"])

    def test_the_token_never_reaches_the_record(self):
        self.handle.side_effect = OSError("GET https://api.telegram.org/file/botfixture-token/x failed")
        self.poll([update(1, OWNER)])
        self.assertNotIn("fixture-token", (self.journal / "1.json").read_text())
        self.assertIn("<token>", self.record(1)["last_error"])

    def test_a_success_after_failures_clears_the_journal(self):
        self.handle.side_effect = [RuntimeError("blip"), "Recovered"]
        self.poll([update(1, OWNER)])
        self.poll([])
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.parked_keys(), [])

    def test_the_ladder_is_smart_processors(self):
        from smart_processor import BACKOFF_SCHEDULE
        self.assertEqual(capture.RETRY_DELAYS, (0, *BACKOFF_SCHEDULE))
        self.assertEqual([capture.retry_delay(n) for n in (0, 1, 2)], [0, 0, BACKOFF_SCHEDULE[0]])


if __name__ == "__main__":
    unittest.main()
