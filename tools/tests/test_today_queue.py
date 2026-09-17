"""Offline Today workflow tests using a fictional vault and mocked Telegram."""
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / ".agents/scripts"))
import calibrate
import today_queue as module
from today_queue import TodayQueue, keyboard
from today_telegram import handle


class QueueFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.day = date(2026, 1, 10)
        self.queue = TodayQueue(self.root, self.day)
        self.decision = self.write("Thinking/Decisions/Launch.md", "---\nstatus: pending\nreview: 2026-01-12\n---\n# Launch\nChoose a pilot.")
        self.ledger = self.write("_Agent-Context/TASKS.md", "# Tasks\n\n## Promises\n- [ ] Call supplier | [[Meeting]] | 2026-01-01\n- [ ] Reserve room | [[Meeting]] | 2026-01-02\n")
        self.belief = self.write("Thinking/Beliefs/Test small.md", "---\nstatus: evergreen\n---\n# Test small\nA small pilot reveals demand.")
        self.state = {"records": {}, "history": []}
        self.queue.build(self.state)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def item(self, category):
        return next(self.state["records"][key] for key in self.state["selected"]
                    if self.state["records"][key]["category"] == category)


class QueueTests(QueueFixture):
    def test_sends_at_most_three_items_and_does_not_resend(self):
        config = self.root / "config"
        self.write("config/telegram_token", "fixture-token")
        self.write("config/telegram_chat_id", "42")
        api = Mock(side_effect=[{"ok": True, "result": {"message_id": mid}} for mid in (10, 11, 12)])
        module.send_queue(self.queue, config=config, api=api)
        module.send_queue(self.queue, config=config, api=api)
        self.assertEqual(api.call_count, 3)
        self.assertTrue(self.queue.surface.exists())

    def test_partial_send_failure_retries_only_unsent_items(self):
        config = self.root / "config"
        self.write("config/telegram_token", "fixture-token")
        self.write("config/telegram_chat_id", "42")
        api = Mock(side_effect=[{"ok": True, "result": {"message_id": 10}}, OSError("offline")])
        with self.assertRaises(OSError):
            module.send_queue(self.queue, config=config, api=api)
        api = Mock(side_effect=[{"ok": True, "result": {"message_id": mid}} for mid in (11, 12)])
        module.send_queue(self.queue, config=config, api=api)
        self.assertEqual(api.call_count, 2)

    def test_bad_delivery_acknowledgement_does_not_mark_sent(self):
        config = self.root / "config"
        self.write("config/telegram_token", "fixture-token")
        self.write("config/telegram_chat_id", "42")
        api = Mock(return_value={"ok": False})
        with self.assertRaises(RuntimeError):
            module.send_queue(self.queue, config=config, api=api)
        self.assertFalse(self.queue.state_path.exists())

    def test_missing_telegram_config_is_a_noop(self):
        self.assertFalse(module.send_queue(self.queue, config=self.root / "missing-config", api=lambda *_: None))

    def test_three_categories_without_replenishment(self):
        self.assertEqual(len(self.state["selected"]), 3)
        self.assertEqual({self.state["records"][key]["category"] for key in self.state["selected"]}, set(module.CATEGORIES))
        selected = list(self.state["selected"])
        self.queue.act(self.state, self.item("commitment")["id"], "apply")
        self.queue.build(self.state)
        self.assertEqual(self.state["selected"], selected)

    def test_preview_does_not_write_sources(self):
        before = self.decision.read_bytes()
        item = self.item("decision")
        self.queue.act(self.state, item["id"], "answer", "Launch a two-week pilot.")
        self.assertEqual(self.decision.read_bytes(), before)
        self.assertEqual(item["status"], "drafted")
        self.assertFalse((self.root / ".wiki").exists())

    def test_apply_decision_and_receipt_are_idempotent(self):
        item = self.item("decision")
        self.queue.act(self.state, item["id"], "answer", "Launch a pilot.")
        self.queue.act(self.state, item["id"], "apply")
        self.queue.act(self.state, item["id"], "apply")
        self.assertIn("status: decided", self.decision.read_text())
        self.assertEqual(self.decision.read_text().count("Launch a pilot."), 1)
        self.assertEqual(len(self.state["history"]), 1)
        self.assertEqual(len(list((self.root / ".wiki/digests/queries").glob("*.md"))), 1)

    def test_apply_without_answer_does_not_resolve_decision(self):
        self.queue.act(self.state, self.item("decision")["id"], "apply")
        self.assertIn("status: pending", self.decision.read_text())

    def test_stale_button_and_changed_source_are_rejected(self):
        item = self.item("decision")
        revision = item["revision"]
        self.queue.act(self.state, item["id"], "answer", "Launch a pilot.")
        with self.assertRaises(ValueError):
            self.queue.act(self.state, item["id"], "apply", revision=revision)
        self.decision.write_text(self.decision.read_text() + "\nNew evidence")
        with self.assertRaises(ValueError):
            self.queue.act(self.state, item["id"], "apply")
        self.assertIn("New evidence", self.decision.read_text())

    def test_complete_only_selected_task_after_unrelated_ledger_edit(self):
        item = self.item("commitment")
        self.ledger.write_text(self.ledger.read_text() + "- [ ] Another task | [[Note]] | 2026-01-09\n")
        self.queue.act(self.state, item["id"], "apply")
        self.assertIn("- [x] Call supplier", self.ledger.read_text())
        self.assertIn("- [ ] Reserve room", self.ledger.read_text())
        self.assertIn("- [ ] Another task", self.ledger.read_text())

    def test_ambiguous_task_does_not_complete_two_rows(self):
        item = self.item("commitment")
        self.ledger.write_text(self.ledger.read_text() + item["line"] + "\n")
        with self.assertRaises(ValueError):
            self.queue.act(self.state, item["id"], "apply")

    def test_task_edit_preserves_source_and_date(self):
        item = self.item("commitment")
        self.queue.act(self.state, item["id"], "answer", "Email supplier")
        self.queue.act(self.state, item["id"], "apply")
        self.assertIn("- [ ] Email supplier | [[Meeting]] | 2026-01-01", self.ledger.read_text())
        self.assertEqual(self.state["history"][-1]["mode"], "revise")

    def test_discarded_task_edit_cannot_erase_the_title(self):
        item = self.item("commitment")
        self.queue.act(self.state, item["id"], "answer", "Email supplier")
        self.queue.act(self.state, item["id"], "edit")
        self.queue.act(self.state, item["id"], "apply")
        self.assertIn("- [x] Call supplier | [[Meeting]] | 2026-01-01", self.ledger.read_text())

    def test_reopened_commitment_can_return_the_next_day(self):
        item = self.item("commitment")
        before = self.ledger.read_text()
        self.queue.act(self.state, item["id"], "apply")
        self.ledger.write_text(before)
        self.queue = TodayQueue(self.root, self.day + timedelta(days=1))
        self.queue.build(self.state)
        self.assertIn(item["id"], self.state["selected"])

    def test_evidence_review_does_not_modify_source(self):
        item = self.item("evidence")
        before = self.belief.read_bytes()
        self.queue.act(self.state, item["id"], "answer", "The recent pilot supports this.")
        self.queue.act(self.state, item["id"], "apply")
        self.assertEqual(self.belief.read_bytes(), before)
        self.assertEqual(self.state["history"][-1]["mode"], "review")

    def test_defer_requires_future_date_and_two_deferrals_adapt(self):
        item = self.item("decision")
        for invalid in ("", "tomorrow", "2026-01-01", "2026-02-30"):
            with self.assertRaises(ValueError):
                self.queue.act(self.state, item["id"], "defer", invalid)
        for offset in (1, 2):
            until = self.day + timedelta(days=offset)
            self.queue.act(self.state, item["id"], "defer", until.isoformat())
            self.queue = TodayQueue(self.root, until)
            self.queue.build(self.state)
            item = self.item("decision")
        self.assertTrue(item["adaptive"])
        self.queue.act(self.state, item["id"], "answer", "I need two customer interviews.")
        self.queue.act(self.state, item["id"], "apply")
        self.assertIn("status: pending", self.decision.read_text())
        self.assertEqual(item["resolution"], "blocker")
        self.queue = TodayQueue(self.root, self.day + timedelta(days=9))
        self.queue.build(self.state)
        self.assertIn(item["id"], self.state["selected"])

    def test_dismiss_requires_reason_and_preserves_source(self):
        item = self.item("decision")
        before = self.decision.read_bytes()
        with self.assertRaises(ValueError):
            self.queue.act(self.state, item["id"], "dismiss")
        self.queue.act(self.state, item["id"], "dismiss", "No longer relevant")
        self.assertEqual(self.decision.read_bytes(), before)
        self.queue = TodayQueue(self.root, self.day + timedelta(days=1))
        self.queue.build(self.state)
        self.assertNotIn(item["id"], self.state["selected"])

    def test_future_deferred_decision_is_not_due(self):
        self.decision.write_text("---\nstatus: deferred\nreview: 2027-01-01\n---\nLater")
        self.assertFalse(any(item["category"] == "decision" for item in self.queue.candidates()))

    def test_private_and_outside_sources_rejected(self):
        self.write("Work/Official Docs/private.md", "PRIVATE_MARKER")
        self.write("_Agent-Context/RESURFACE.md", "2026-01-10\n- [[Work/Official Docs/private]]: review\n- [[../../outside]]: review")
        self.assertFalse(any("Official Docs" in item["source"] for item in self.queue.candidates()))
        with self.assertRaises(ValueError):
            self.queue.source("../../outside")
        with self.assertRaises(ValueError):
            self.queue.source("Work/Official Docs/private.md")
        self.write(".agents/state/internal.md", "INTERNAL_MARKER")
        with self.assertRaises(ValueError):
            self.queue.source("Work/../.agents/state/internal.md")

    def test_grading_updates_calibration_and_due_scan(self):
        self.decision.write_text("---\nstatus: decided\nconfidence: 60%\nreview: 2026-01-01\n---\n## Outcome\n- _Pending review._\n")
        calibration = self.write("Thinking/Calibration.md", "| Decision | Prediction | Conf. | Review | Outcome | Lesson |\n| [[Launch]] | A pilot | 60% | 2026-01-01 | pending | |\n")
        self.state = {"records": {}, "history": []}
        self.queue.build(self.state)
        item = self.item("decision")
        self.assertEqual(item["mode"], "grade")
        self.queue.act(self.state, item["id"], "answer", "Pilot succeeded.")
        self.queue.act(self.state, item["id"], "apply")
        self.assertIn("Pilot succeeded.", calibration.read_text())
        with patch.object(calibrate, "DEC", self.decision.parent):
            self.assertEqual(calibrate.scan()[0], [])

    def test_interrupted_apply_recovers_without_duplicate_source_write(self):
        item = self.item("decision")
        self.queue.act(self.state, item["id"], "answer", "Launch a pilot.")
        real_write = module.atomic_write
        def interrupt(path, text):
            if "queries" in path.parts:
                raise OSError("fixture interruption")
            return real_write(path, text)
        with patch.object(module, "atomic_write", side_effect=interrupt):
            with self.assertRaises(OSError):
                self.queue.act(self.state, item["id"], "apply")
        recovered = json.loads(self.queue.state_path.read_text())
        self.queue.finish_apply(recovered)
        self.queue.finish_apply(recovered)
        self.assertEqual(self.decision.read_text().count("Launch a pilot."), 1)
        self.assertEqual(len(recovered["history"]), 1)

    def test_keyboard_data_fits_telegram_limit(self):
        for row in keyboard(self.item("decision"))["inline_keyboard"]:
            for button in row:
                self.assertLessEqual(len(button["callback_data"].encode()), 64)


class TelegramTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.decision_item = self.item("decision")
        self.decision_item.update(chat_id="42", messages=[100], approval_message=100)
        with self.queue.locked() as state:
            state.update(self.state)
        self.counter = 200
        def response(token, method, params):
            self.counter += 1
            return {"ok": True, "result": {"message_id": self.counter}}
        self.api = Mock(side_effect=response)
        self.transcribe = Mock(return_value="Launch a pilot.")

    def message(self, text, mid=1, reply=100):
        return {"text": text, "message_id": mid, "reply_to_message": {"message_id": reply}}

    def handle(self, message, chat="42"):
        return handle("fixture-token", message, chat, self.api, self.transcribe, self.queue)

    def test_unrelated_apply_is_not_claimed(self):
        self.assertFalse(self.handle({"text": "apply", "message_id": 1}))
        self.assertFalse(self.handle(self.message("apply", reply=999)))
        self.api.assert_not_called()

    def test_cross_chat_reply_is_not_claimed(self):
        self.assertFalse(self.handle(self.message("Launch"), chat="99"))

    def test_reply_preview_then_explicit_apply(self):
        self.assertTrue(self.handle(self.message("Launch a pilot.")))
        state = json.loads(self.queue.state_path.read_text())
        item = state["records"][self.decision_item["id"]]
        self.assertEqual(item["status"], "drafted")
        self.assertTrue(self.handle(self.message("apply", mid=2, reply=item["approval_message"])))
        self.assertIn("status: decided", self.decision.read_text())

    def test_duplicate_delivery_does_not_redraft(self):
        message = self.message("Launch a pilot.")
        self.handle(message)
        first = self.queue.state_path.read_text()
        self.handle(message)
        self.assertEqual(self.queue.state_path.read_text(), first)

    def test_old_button_does_not_approve_new_draft(self):
        old_revision = self.decision_item["revision"]
        self.handle(self.message("Launch a pilot."))
        callback = {"message_id": 100, "_today_callback": {
            "id": "cb1", "data": f"today:{self.decision_item['id']}:{old_revision}:apply"}}
        self.handle(callback)
        self.assertIn("status: pending", self.decision.read_text())

    def test_defer_button_followed_by_date(self):
        item = self.decision_item
        self.handle({"message_id": 100, "_today_callback": {
            "id": "cb2", "data": f"today:{item['id']}:{item['revision']}:defer"}})
        state = json.loads(self.queue.state_path.read_text())
        item = state["records"][item["id"]]
        self.handle(self.message("2026-01-15", mid=2, reply=item["approval_message"]))
        state = json.loads(self.queue.state_path.read_text())
        self.assertEqual(state["records"][item["id"]]["until"], "2026-01-15")

    def test_voice_reply_uses_the_same_preview_flow(self):
        self.handle({"message_id": 9, "reply_to_message": {"message_id": 100},
                     "voice": {"file_id": "fixture-audio"}})
        self.transcribe.assert_called_once()
        state = json.loads(self.queue.state_path.read_text())
        self.assertEqual(state["records"][self.decision_item["id"]]["status"], "drafted")
        self.assertIn("status: pending", self.decision.read_text())

    def test_corrupt_queue_state_does_not_block_ordinary_capture(self):
        self.queue.state_path.write_text("invalid JSON")
        self.assertFalse(self.handle({"text": "A new thought", "message_id": 55}))


if __name__ == "__main__":
    unittest.main()
