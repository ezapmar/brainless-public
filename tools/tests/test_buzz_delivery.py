import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from buzz_delivery import Outbox, Buzz
from buzz_fixture import Relay


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.relay = Relay()
        self.box = Outbox(self.root, self.relay)

    def test_lost_ack_reconciles_without_duplicate_send(self):
        self.box.enqueue('tasks', 'tasks', 'One message', key='one')
        self.relay.lose_ack = True
        self.assertIsNone(self.box.deliver('one'))
        self.box = Outbox(self.root, self.relay)
        self.assertIsNotNone(self.box.deliver('one'))
        self.assertEqual(len(self.relay.posts), 1)

    def test_delivered_body_has_no_visible_marker(self):
        self.box.enqueue('inbox', 'inbox', 'Saved: note', key='clean')
        self.box.deliver('clean')
        self.assertEqual(self.relay.posts[0]['content'], 'Saved: note')

    def test_offline_survives_restart(self):
        self.relay.offline = True
        self.box.enqueue('watchdog', 'ops', 'Alert', key='alert')
        self.assertIsNone(self.box.deliver('alert'))
        self.assertIsNone(self.box.record('alert')['event'])
        self.relay.offline = False
        self.box = Outbox(self.root, self.relay)
        self.assertIsNotNone(self.box.deliver('alert'))

    def test_reused_key_cannot_change_content(self):
        self.box.enqueue('tasks', 'tasks', 'A', key='same')
        with self.assertRaises(ValueError):
            self.box.enqueue('tasks', 'tasks', 'B', key='same')

    def test_exactly_full_history_grows_limit(self):
        client = Buzz()
        batch = [{'id': f'{n:064x}', 'pubkey': 'a'*64, 'content': 'x', 'created_at': 1} for n in range(100)]
        with patch.object(client, 'call', side_effect=[batch, batch + [{'id': 'f'*64, 'pubkey': 'a'*64, 'content': 'y', 'created_at': 1}]]) as call:
            self.assertEqual(len(client.history('tasks', 'channel', 0)), 101)
            self.assertEqual(call.call_count, 2)

    def test_capped_history_fails_without_claiming_complete(self):
        client = Buzz()
        batch = [{'id': f'{n:064x}', 'pubkey': 'a'*64, 'content': 'x', 'created_at': 1} for n in range(100)]
        with patch.object(client, 'call', return_value=batch):
            with self.assertRaises(RuntimeError):
                client.history('tasks', 'channel', 0)
