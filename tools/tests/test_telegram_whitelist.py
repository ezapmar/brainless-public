"""Offline regressions for the Telegram poller's whitelist.

The docstring promise: only the whitelisted chat is served, the first sender
becomes the whitelist only in an explicit setup run, an unreadable whitelist
file fails closed, and the offset advances either way so nothing is replayed.
The one outgoing Telegram call is the saved receipt, to the whitelisted chat.
Telegram is a mock; nothing leaves the machine.

Run: python3 -B -m unittest discover -s tools/tests -v
"""
import contextlib
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("BRAINLESS_VAULT", str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / ".agents/scripts"))
import telegram_capture as capture  # noqa: E402

OWNER, STRANGER = "42", "99"


def update(uid, chat, text="hello"):
    return {"update_id": uid, "message": {"message_id": uid, "chat": {"id": int(chat)}, "text": text}}


class PollerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.conf = root / "config"
        self.conf.mkdir()
        self.token = self.conf / "telegram_token"
        self.chat = self.conf / "telegram_chat_id"
        self.state = root / "state" / "telegram_offset"
        self.token.write_text("fixture-token")
        for name, value in (("CONF_DIR", str(self.conf)), ("TOKEN_FILE", str(self.token)),
                            ("CHAT_FILE", str(self.chat)), ("STATE_FILE", str(self.state))):
            self.enterContext(patch.object(capture, name, value))
        self.handle = self.enterContext(patch.object(capture, "handle_message", Mock(return_value="Saved title")))
        self.enterContext(patch.object(capture.time, "sleep", Mock()))
        self.enterContext(patch.dict(os.environ, {}, clear=False))
        os.environ.pop("BRAINLESS_TG_ALLOW_ADOPT", None)
        self.buzz = self.enterContext(patch("buzz_delivery.send", Mock(return_value=True)))
        self.out = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.out))

    def poll(self, updates, api_error=None):
        def api(token, method, params=None, timeout=30):
            if method == "getUpdates":
                if api_error:
                    raise api_error
                self.get_params = params
                return {"ok": True, "result": updates}
            return {"ok": True, "result": {"message_id": 1}}
        self.api = Mock(side_effect=api)
        with patch.object(capture, "api", self.api):
            capture.main()
        return self.out.getvalue()

    def sent(self):
        return [c.args[2] for c in self.api.call_args_list if c.args[1] == "sendMessage"]

    def offset(self):
        return self.state.read_text() if self.state.exists() else None

    def handled_chats(self):
        return [c.args[2] for c in self.handle.call_args_list]


class WhitelistTest(PollerFixture):
    def test_only_the_whitelisted_chat_is_served(self):
        self.chat.write_text(OWNER)
        log = self.poll([update(1, STRANGER, "let me in"), update(2, OWNER, "a thought")])
        self.assertEqual(self.handled_chats(), [OWNER])
        self.assertIn(f"Unauthorized chat ignored: {STRANGER}", log)
        self.assertEqual([m["chat_id"] for m in self.sent()], [OWNER], "the receipt goes to the owner only")
        self.assertEqual(self.buzz.call_count, 1)
        self.assertEqual(self.offset(), "2", "ignored updates still advance the offset")

    def test_no_whitelist_and_no_setup_flag_adopts_nobody(self):
        log = self.poll([update(7, STRANGER)])
        self.assertFalse(self.chat.exists())
        self.assertEqual(self.handle.call_count, 0)
        self.assertIn("adoption disabled", log)
        self.assertEqual(self.offset(), "7", "the stranger's message is consumed, not replayed next round")

    def test_setup_run_adopts_the_first_sender_and_locks(self):
        os.environ["BRAINLESS_TG_ALLOW_ADOPT"] = "1"
        self.poll([update(1, OWNER, "first"), update(2, STRANGER, "second"), update(3, OWNER, "third")])
        self.assertEqual(self.chat.read_text(), OWNER)
        self.assertEqual(stat.S_IMODE(self.chat.stat().st_mode), 0o600)
        self.assertEqual(self.handled_chats(), [OWNER], "the adopting message itself is not captured; later ones are")
        self.assertEqual(self.handle.call_args.args[1]["text"], "third")
        self.assertEqual([m["chat_id"] for m in self.sent()], [OWNER], "only the captured message is receipted")
        self.assertEqual(self.buzz.call_count, 2)

    def test_setup_flag_does_not_reopen_an_existing_whitelist(self):
        os.environ["BRAINLESS_TG_ALLOW_ADOPT"] = "1"
        self.chat.write_text(OWNER)
        self.poll([update(1, STRANGER)])
        self.assertEqual(self.chat.read_text(), OWNER)
        self.assertEqual(self.handle.call_count, 0)

    def test_unreadable_whitelist_fails_closed(self):
        self.chat.write_text(OWNER)
        real = capture.read_file
        with patch.object(capture, "read_file", lambda p: None if p == str(self.chat) else real(p)):
            log = self.poll([update(5, OWNER), update(6, STRANGER)])
        self.assertEqual(self.handle.call_count, 0)
        self.assertFalse(os.environ.get("BRAINLESS_TG_ALLOW_ADOPT"))
        self.assertEqual(self.chat.read_text(), OWNER, "an unreadable file is never overwritten by adoption")
        self.assertIn("round skipped for safety", log)
        self.assertEqual(self.offset(), "0", "nothing consumed: the round is retried once the file is readable")

    def test_today_button_from_a_stranger_is_ignored(self):
        self.chat.write_text(OWNER)
        callback = {"update_id": 3, "callback_query": {"id": "cb", "data": "today:x:1:apply",
                    "message": {"message_id": 30, "chat": {"id": int(STRANGER)}, "text": "queue"}}}
        self.poll([callback])
        self.assertEqual(self.handle.call_count, 0)
        self.assertEqual(self.offset(), "3")

    def test_old_owner_callback_is_consumed_without_action(self):
        self.chat.write_text(OWNER)
        callback = {"update_id": 4, "callback_query": {"id": "cb", "data": "today:x:1:apply",
                    "message": {"message_id": 30, "chat": {"id": int(OWNER)}, "text": "queue"}}}
        self.poll([callback])
        self.handle.assert_not_called()
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.offset(), "4")


class ReceiptTest(PollerFixture):
    def test_receipt_is_plain_and_not_repeated(self):
        self.chat.write_text(OWNER)
        self.handle.return_value = "[[TEGV]] ile [[Ebru Hanım|Ebru]] toplantısı"
        self.poll([update(1, OWNER)])
        (receipt,) = self.sent()
        self.assertNotIn("[[", receipt["text"])
        self.assertIn("TEGV ile Ebru toplantısı", receipt["text"])
        self.assertNotIn("[[", self.buzz.call_args.args[1])
        self.poll([])
        self.assertEqual(len(self.sent()), 0, "a finished record is gone; nothing to repeat")

    def test_failed_receipt_does_not_block_the_capture(self):
        self.chat.write_text(OWNER)
        def api(token, method, params=None, timeout=30):
            if method == "sendMessage":
                raise OSError("telegram down")
            return {"ok": True, "result": [update(1, OWNER)]}
        with patch.object(capture, "api", Mock(side_effect=api)):
            capture.main()
        self.assertIn("Telegram receipt failed", self.out.getvalue())
        self.assertFalse((self.state.parent / "telegram_pending/1.json").exists())


class LinkCommentTest(unittest.TestCase):
    def test_buzz_autolink_brackets_are_not_a_comment(self):
        import media_import
        raw = "<https://podcasts.apple.com/tr/podcast/x/id1?i=2>"
        with patch.object(media_import, "enqueue", Mock(return_value=("id", True))) as enqueue, \
                contextlib.redirect_stdout(io.StringIO()):
            capture.handle_link(raw, capture.URL_RE.search(raw))
        url, comment = enqueue.call_args.args[:2]
        self.assertEqual(url, "https://podcasts.apple.com/tr/podcast/x/id1?i=2")
        self.assertEqual(comment, "")


class PollerRobustnessTest(PollerFixture):
    def test_missing_token_stays_silent(self):
        self.token.unlink()
        self.chat.write_text(OWNER)
        with patch.object(capture, "api", Mock()) as api:
            capture.main()
        self.assertEqual(api.call_count, 0)
        self.assertIsNone(self.offset())

    def test_corrupt_offset_file_restarts_from_zero(self):
        self.chat.write_text(OWNER)
        self.state.parent.mkdir(parents=True)
        self.state.write_text("garbage")
        self.poll([update(9, OWNER)])
        self.assertEqual(self.get_params["offset"], 1)
        self.assertEqual(self.offset(), "9")

    def test_update_without_a_message_is_skipped_but_consumed(self):
        self.chat.write_text(OWNER)
        log = self.poll([{"update_id": 11, "edited_message": {"chat": {"id": int(OWNER)}}}, update(12, OWNER)])
        self.assertIn("update without a message skipped", log)
        self.assertEqual(self.handle.call_count, 1)
        self.assertEqual(self.offset(), "12")

    def test_a_failing_handler_does_not_stop_the_batch_or_the_offset(self):
        self.chat.write_text(OWNER)
        self.handle.side_effect = [RuntimeError("boom"), "Second title"]
        log = self.poll([update(1, OWNER), update(2, OWNER)])
        self.assertIn("Message handling error", log)
        self.assertEqual(self.handle.call_count, 2)
        self.assertEqual(self.offset(), "2")
        self.assertTrue((self.state.parent / "telegram_pending/1.json").exists())

    def test_telegram_outage_leaves_the_offset_alone(self):
        self.chat.write_text(OWNER)
        log = self.poll([], api_error=OSError("dns"))
        self.assertIn("getUpdates error (3 attempts)", log)
        self.assertEqual(self.api.call_count, 3)
        self.assertIsNone(self.offset())


if __name__ == "__main__":
    unittest.main()
