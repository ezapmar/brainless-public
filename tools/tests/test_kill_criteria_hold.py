"""Unit tests for on-hold separation in tools/kill_criteria.py."""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import kill_criteria as kc


class KillCriteriaHoldSeparationTest(unittest.TestCase):
    def test_on_hold_breaches_go_to_pending_close_not_red_breached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p_active = root / "Work" / "Live" / "notes.md"
            p_hold = root / "Work" / "Parked" / "notes.md"
            p_hold_empty = root / "Work" / "ParkedEmpty" / "notes.md"
            for p in (p_active, p_hold, p_hold_empty):
                p.parent.mkdir(parents=True, exist_ok=True)

            p_active.write_text(
                "---\nstatus: active\n---\n# Live\n\n## Kill Criteria\n"
                "- [ ] 2026-10-15 | still open | escalate\n",
                encoding="utf-8",
            )
            p_hold.write_text(
                "---\nstatus: on-hold\n---\n# Parked\n\n## Kill Criteria\n"
                "- [ ] 2026-10-15 | old condition | archived\n",
                encoding="utf-8",
            )
            p_hold_empty.write_text(
                "---\nstatus: on-hold\n---\n# ParkedEmpty\n",
                encoding="utf-8",
            )

            projects = [
                ("Work", "Live", p_active),
                ("Work", "Parked", p_hold),
                ("Work", "ParkedEmpty", p_hold_empty),
            ]
            with mock.patch.object(kc.bd, "project_notes", return_value=projects):
                res = kc.scan(today=date(2026, 11, 24))

            self.assertEqual([i["project"] for i in res["breached"]], ["Live"])
            self.assertEqual([i["project"] for i in res["pending_close"]], ["Parked"])
            self.assertEqual(res["missing"], [])

            rendered = kc.render(res)
            self.assertIn("Live", rendered)
            self.assertIn("Parked", rendered)


if __name__ == "__main__":
    unittest.main()
