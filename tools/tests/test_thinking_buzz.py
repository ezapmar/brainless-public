import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / '.agents/scripts'))
import thinking_buzz as adapter
import thinking_loop as loop
from buzz_delivery import Outbox
from buzz_fixture import Relay, OWNER, reply


class ThinkingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, rel in [('VAULT', ''), ('STATE_FILE', '.agents/state/thinking_loop.json'),
                          ('BELIEF_DIR', 'Thinking/Beliefs'), ('DEC_DIR', 'Thinking/Decisions'),
                          ('IDEAS_DIR', 'Thinking/Ideas'), ('DAILY_DIR', 'Thinking/Daily'),
                          ('CALIBRATION', 'Thinking/Calibration.md'), ('CADENCE', 'Thinking/Cadence.md'),
                          ('QUERIES_DIR', '.wiki/digests/queries')]:
            self.enterContext(patch.object(loop, name, str(self.root / rel)))
        self.note = self.root / 'Thinking/Beliefs/Pilots.md'
        self.note.parent.mkdir(parents=True)
        self.note.write_text('---\nconfidence: medium\n---\n# Pilots\nTest first.\n')
        Path(loop.STATE_FILE).parent.mkdir(parents=True)
        self.state = {'phase': 'drafted', 'kind': 'belief', 'target': 'Pilots', 'question': 'What changed?',
                      'message_id': 123, 'asked_at': 1, 'answer': 'Pilot worked',
                      'draft': {'instance': 'Pilot worked', 'verdict': 'supports', 'confidence': 'high'}}
        Path(loop.STATE_FILE).write_text(json.dumps(self.state))
        self.relay = Relay()
        self.box = Outbox(self.root, self.relay)
        adapter.ask(box=self.box)

    def current(self):
        return json.loads(Path(loop.STATE_FILE).read_text())

    def handle(self, msg):
        return adapter.handle(msg, 'channel-thinking', OWNER, box=self.box)

    def test_migration_preserves_preview_but_discards_telegram_approval(self):
        state = self.current()
        self.assertNotIn('message_id', state)
        self.assertIn('Pilot worked', self.relay.posts[0]['content'])
        self.assertEqual(state['phase'], 'drafted')
        self.assertIsNotNone(state['buzz_approval'])

    def test_approval_writes_once_and_files_receipt(self):
        msg = reply(self.current()['buzz_approval'], 'apply')
        self.handle(msg)
        self.handle(msg)
        self.assertEqual(self.note.read_text().count('Pilot worked'), 1)
        self.assertEqual(self.current()['phase'], 'applied')
        self.assertEqual(len(list((self.root / '.wiki/digests/queries').glob('*.md'))), 1)

    def test_unrelated_apply_cannot_write(self):
        before = self.note.read_text()
        self.assertFalse(self.handle(reply('e' * 64, 'apply')))
        self.assertEqual(self.note.read_text(), before)

    def test_changed_source_rejects_apply(self):
        self.note.write_text(self.note.read_text() + '\nNew information')
        self.handle(reply(self.current()['buzz_approval'], 'apply'))
        self.assertNotIn('Pilot worked', self.note.read_text())
        self.assertIn('New information', self.note.read_text())

    def test_old_draft_cannot_approve_new_answer(self):
        old = self.current()['buzz_approval']
        with patch.object(loop, 'draft', return_value={'instance': 'New result', 'verdict': 'supports', 'confidence': 'high'}):
            self.handle(reply(old, 'New result'))
        self.handle(reply(old, 'apply', 101))
        self.assertNotIn('New result', self.note.read_text())

    def test_interrupted_write_recovers_without_appending_twice(self):
        msg = reply(self.current()['buzz_approval'], 'apply')
        real = adapter.atomic_write
        def fail_receipt(path, body):
            if 'queries' in Path(path).parts:
                raise OSError('interrupted')
            return real(path, body)
        with patch.object(adapter, 'atomic_write', side_effect=fail_receipt):
            with self.assertRaises(OSError):
                self.handle(msg)
        self.handle(msg)
        self.assertEqual(self.note.read_text().count('Pilot worked'), 1)
        self.assertEqual(self.current()['phase'], 'applied')

    def test_lost_ack_does_not_repeat_write(self):
        self.relay.lose_ack = True
        msg = reply(self.current()['buzz_approval'], 'apply')
        self.handle(msg)
        self.handle(msg)
        self.assertEqual(self.note.read_text().count('Pilot worked'), 1)
        self.assertEqual(len(self.relay.posts), 2)
