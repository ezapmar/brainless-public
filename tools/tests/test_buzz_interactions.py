import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import buzz_interactions as worker
from buzz_delivery import Outbox
from buzz_fixture import Relay, OWNER, reply


class PollTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.relay = Relay()
        self.box = Outbox(Path(self.tmp.name), self.relay)
        self.msg = reply('b' * 64, 'A reply', 100)
        self.msg['channel'] = 'channel-tasks'
        self.relay.posts.append(self.msg)
        self.enterContext(patch.object(worker, 'answer_text', side_effect=lambda m: m['content']))

    def test_failed_handler_retains_input_but_cursor_can_advance(self):
        with patch.object(worker, 'today_handle', side_effect=OSError('failed')):
            self.assertEqual(worker.poll_channel(self.box, 'tasks', 'tasks', OWNER), 1)
        with self.box.db() as db:
            self.assertEqual(db.execute('SELECT status FROM incoming').fetchone()[0], 'pending')
            self.assertEqual(db.execute('SELECT since FROM cursors').fetchone()[0], 100)
        with patch.object(worker, 'today_handle', return_value=True) as handle:
            worker.poll_channel(self.box, 'tasks', 'tasks', OWNER)
            worker.poll_channel(self.box, 'tasks', 'tasks', OWNER)
            self.assertEqual(handle.call_count, 1)

    def test_failed_draft_blocks_later_approval_in_same_channel(self):
        later = {**reply('b' * 64, 'apply', 101), 'channel': 'channel-tasks'}
        self.relay.posts.append(later)
        with patch.object(worker, 'today_handle', side_effect=OSError('failed')) as handle:
            worker.poll_channel(self.box, 'tasks', 'tasks', OWNER)
            self.assertEqual(handle.call_count, 1)
        with self.box.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM incoming WHERE status="pending"').fetchone()[0], 2)

    def test_agent_and_foreign_author_do_not_trigger_handler(self):
        self.relay.posts = [{**self.msg, 'pubkey': 'f' * 64}]
        with patch.object(worker, 'today_handle') as handle:
            worker.poll_channel(self.box, 'tasks', 'tasks', OWNER)
            handle.assert_not_called()

    def test_incomplete_history_does_not_advance_cursor(self):
        with self.box.db() as db:
            db.execute('INSERT INTO cursors VALUES(?,?)', ('channel-tasks', 1))
        with patch.object(self.relay, 'history', side_effect=RuntimeError('capped')):
            with self.assertRaises(RuntimeError):
                worker.poll_channel(self.box, 'tasks', 'tasks', OWNER)
        with self.box.db() as db:
            self.assertEqual(db.execute('SELECT since FROM cursors').fetchone()[0], 1)
