"""kill_review: the reply grammar, the one write into a project note, and the reply handler.

What must hold: only a numbered clause with a known word decides anything; a
decision changes exactly the criterion line (and the status for archive/hold)
and adds one Log line; a line edited after the message went out is left alone.

Run: python3 -B -m unittest tools.tests.test_kill_review -v
"""
import json
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

import kill_review as kr

OWNER = "o" * 64
TODAY = date(2026, 11, 19)
NOTE = """---
type: project
status: active
tags: [project, status/active, bank]
---
# Acme

## Kill Criteria
- [ ] 2026-10-15 | No meeting date | on-hold; drop from the board agenda
- [ ] 2027-01-10 | Later one | archived

## Related
- something

## Log
### 2026-09-01
- Started.
"""


class Client:
    def channel(self, name, identity):
        return name


class Box:
    def __init__(self):
        self.client, self.sent = Client(), {}

    def enqueue(self, identity, channel, body, *, key=None, parent=None):
        self.sent[key] = {"body": body, "parent": parent, "event": "tg500"}

    def deliver(self, key):
        pass

    def record(self, key):
        return self.sent.get(key)


def msg(content, parent="tg100", n=900):
    return {"id": f"tg{n}", "pubkey": OWNER, "content": content, "tags": [["e", parent, "", "reply"]]}


class KillReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.note = self.root / "Work" / "Acme" / "notes.md"
        self.note.parent.mkdir(parents=True)
        self.note.write_text(NOTE, encoding="utf-8")
        old = (kr.VAULT, kr.STATE)
        kr.VAULT, kr.STATE = self.root, self.root / "state" / "kill_review.json"
        self.addCleanup(lambda: (setattr(kr, "VAULT", old[0]), setattr(kr, "STATE", old[1])))
        self.item = {"no": 1, "project": "Acme", "path": "Work/Acme/notes.md", "date": "2026-10-15",
                     "condition": "No meeting date", "consequence": "on-hold", "days": 35, "status": "pending"}

    def text(self):
        return self.note.read_text(encoding="utf-8")

    def seed(self):
        kr.STATE.parent.mkdir(parents=True, exist_ok=True)
        kr.STATE.write_text(json.dumps({"batches": [{"key": "k", "event": "tg100", "created": time.time(),
                                                     "items": [self.item]}], "handled": []}))

    def test_dates_the_owner_types(self):
        pd = lambda s: kr.parse_date(s, TODAY)
        self.assertEqual(pd("2026-12-15"), date(2026, 12, 15))
        self.assertEqual(pd("15 Aralık"), date(2026, 12, 15))
        self.assertEqual(pd("15.12"), date(2026, 12, 15))
        self.assertEqual(pd("5 Ocak"), date(2027, 1, 5))        # no year: the next time it comes
        self.assertEqual(pd("2 hafta"), date(2026, 12, 3))
        self.assertIsNone(pd("31 Şubat"))
        self.assertIsNone(pd("yakında"))

    def test_reply_grammar(self):
        got, bad = kr.parse_reply("1 4 kapat, 6 ertele 3 hafta; 2 arşivle", {1, 2, 4, 6}, TODAY)
        self.assertEqual(got, {1: ("close", None), 4: ("close", None),
                               6: ("defer", date(2026, 12, 10)), 2: ("archive", None)})
        self.assertEqual(bad, [])

    def test_nothing_is_decided_without_a_number_or_with_a_bad_one(self):
        for reply in ("kapat", "9 kapat", "1 ertele", "1 ertele 1 Ocak 2020", "1 kapat hemen", "hepsi arşivle"):
            got, bad = kr.parse_reply(reply, {1, 2}, TODAY)
            self.assertEqual((got, bad), ({}, [reply]), reply)

    def test_close_ticks_only_that_line_and_logs_it(self):
        self.assertEqual(kr.apply(self.item, "close", None, TODAY), "ok")
        text = self.text()
        self.assertIn("- [x] 2026-10-15 | No meeting date | on-hold; drop from the board agenda", text)
        self.assertIn("- [ ] 2027-01-10 | Later one | archived", text)
        self.assertIn("status: active", text)
        self.assertIn("### 2026-09-01\n- Started.\n### 2026-11-19\n- ", text)
        self.assertTrue(text.rstrip().endswith("No meeting date"))
        self.assertEqual(text.count("### 2026-11-19"), 1)
        self.assertIn("## Related\n- something", text)

    def test_archive_changes_status_and_tag(self):
        self.assertEqual(kr.apply(self.item, "archive", None, TODAY), "ok")
        head = self.text().split("---")[1]
        self.assertIn("status: archived", head)
        self.assertIn("status/archived", head)
        self.assertNotIn("status/active", head)

    def test_defer_moves_the_date_and_keeps_the_box_open(self):
        self.assertEqual(kr.apply(self.item, "defer", date(2026, 12, 15), TODAY), "ok")
        self.assertIn("- [ ] 2026-12-15 | No meeting date | on-hold; drop from the board agenda", self.text())
        self.assertNotIn("2026-10-15 | No meeting", self.text().split("## Log")[0])

    def test_a_line_edited_since_is_left_alone(self):
        self.note.write_text(NOTE.replace("No meeting date", "No meeting date yet"), encoding="utf-8")
        before = self.text()
        self.assertEqual(kr.apply(dict(self.item, condition="No meeting date |"), "close", None, TODAY), "changed")
        self.assertEqual(self.text(), before)

    def test_two_decisions_on_one_day_share_a_log_heading(self):
        kr.apply(self.item, "defer", date(2026, 12, 15), TODAY)
        kr.apply(dict(self.item, date="2026-12-15"), "close", None, TODAY)
        self.assertEqual(self.text().count("### 2026-11-19"), 1)

    def test_a_reply_applies_and_answers(self):
        self.seed()
        box = Box()
        self.assertTrue(kr.handle(msg("1 beklet"), "projects", OWNER, box=box, today=TODAY))
        self.assertIn("status: on-hold", self.text())
        self.assertIn("Acme", box.sent["kill:reply:tg900"]["body"])
        state = json.loads(kr.STATE.read_text())
        self.assertEqual(state["batches"][0]["items"][0]["status"], "hold")
        before = self.text()                                   # the same reply again changes nothing
        self.assertTrue(kr.handle(msg("1 beklet"), "projects", OWNER, box=box, today=TODAY))
        self.assertEqual(self.text(), before)

    def test_other_messages_are_not_claimed(self):
        self.seed()
        box = Box()
        self.assertFalse(kr.handle(msg("1 kapat", parent="tg999"), "projects", OWNER, box=box, today=TODAY))
        self.assertFalse(kr.handle(msg("1 kapat"), "tasks", OWNER, box=box, today=TODAY))
        self.assertFalse(kr.handle(dict(msg("1 kapat"), pubkey="x"), "projects", OWNER, box=box, today=TODAY))

    def test_an_unreadable_reply_writes_nothing(self):
        self.seed()
        box, before = Box(), self.text()
        self.assertTrue(kr.handle(msg("hepsini kapat"), "projects", OWNER, box=box, today=TODAY))
        self.assertEqual(self.text(), before)


if __name__ == "__main__":
    unittest.main()
