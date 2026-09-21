"""Offline Today workflow tests using a fictional vault and mocked Buzz."""
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
from today_queue import TodayQueue
from today_buzz import handle, send_queue
from buzz_delivery import Outbox
from buzz_fixture import Relay, OWNER, reply


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
        relay = Relay()
        box = Outbox(self.root, relay)
        send_queue(self.queue, box=box)
        send_queue(self.queue, box=box)
        self.assertEqual(len(relay.posts), 3)
        self.assertTrue(self.queue.surface.exists())

    def test_offline_queue_is_durable_and_retries(self):
        relay = Relay()
        relay.offline = True
        box = Outbox(self.root, relay)
        send_queue(self.queue, box=box)
        state = json.loads(self.queue.state_path.read_text())
        self.assertTrue(all('buzz_root' not in i for i in state['records'].values()))
        relay.offline = False
        send_queue(self.queue, box=box)
        self.assertEqual(len(relay.posts), 3)

    def test_migration_ignores_telegram_sent_on_and_reposts_preview(self):
        item = self.item('decision')
        self.queue.act(self.state, item['id'], 'answer', 'Launch a pilot.')
        item.update(sent_on=self.day.isoformat(), messages=[123], approval_message=123, chat_id='42')
        with self.queue.locked() as state:
            state.update(self.state)
        relay = Relay()
        send_queue(self.queue, box=Outbox(self.root, relay))
        self.assertIn('Launch a pilot.', relay.posts[0]['content'])
        state = json.loads(self.queue.state_path.read_text())
        self.assertNotIn('approval_message', state['records'][item['id']])

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



class BuzzTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.relay = Relay()
        self.box = Outbox(self.root, self.relay)
        send_queue(self.queue, box=self.box)
        self.key = self.item('decision')['id']

    def current(self):
        return json.loads(self.queue.state_path.read_text())['records'][self.key]

    def handle(self, msg, channel='channel-tasks'):
        return handle(msg, channel, OWNER, queue=self.queue, box=self.box)

    def test_preview_then_explicit_apply(self):
        self.handle(reply(self.current()['buzz_root'], 'Launch a pilot.'))
        self.assertIn('status: pending', self.decision.read_text())
        self.handle(reply(self.current()['buzz_approval'], 'apply', 101))
        self.assertIn('status: decided', self.decision.read_text())

    def test_unrelated_cross_channel_and_cross_owner_ignored(self):
        msg = reply(self.current()['buzz_root'], 'apply')
        self.assertFalse(self.handle(msg, 'channel-inbox'))
        self.assertFalse(self.handle({**msg, 'pubkey': 'b' * 64}))
        self.assertFalse(self.handle(reply('f' * 64, 'apply')))
        self.assertIn('status: pending', self.decision.read_text())

    def test_duplicate_delivery_does_not_redraft(self):
        msg = reply(self.current()['buzz_root'], 'Launch a pilot.')
        self.handle(msg)
        revision = self.current()['revision']
        self.handle(msg)
        self.assertEqual(self.current()['revision'], revision)
        self.assertEqual(len(self.relay.posts), 4)

    def test_old_preview_does_not_approve_new_draft(self):
        self.handle(reply(self.current()['buzz_root'], 'Pilot A'))
        old = self.current()['buzz_approval']
        self.handle(reply(old, 'Pilot B', 101))
        self.handle(reply(old, 'apply', 102))
        self.assertIn('status: pending', self.decision.read_text())
        self.assertIn('Pilot B', self.relay.posts[-1]['content'])

    def test_defer_then_date(self):
        self.handle(reply(self.current()['buzz_root'], 'defer'))
        prompt = self.relay.posts[-1]['id']
        self.handle(reply(prompt, '2026-01-15', 101))
        self.assertEqual(self.current()['until'], '2026-01-15')

    def test_failed_reply_delivery_retains_preview_for_retry(self):
        self.relay.lose_ack = True
        self.handle(reply(self.current()['buzz_root'], 'Launch a pilot.'))
        self.handle(reply(self.current()['buzz_root'], 'Launch a pilot.'))
        self.assertEqual(len(self.relay.posts), 4)
        self.assertEqual(self.current()['status'], 'drafted')

    def test_telegram_transport_cannot_apply(self):
        from today_telegram import handle as retired
        self.assertFalse(retired('token', {'text': 'apply'}, '42', Mock(), Mock(), self.queue))
        self.assertIn('status: pending', self.decision.read_text())


if __name__ == '__main__':
    unittest.main()
