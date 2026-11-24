"""Unit tests for tools/attention_audit.py."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import attention_audit as aa


SAMPLE_PROJECTS = """# Active Projects Summary

## Active (2)

### 1. Pricing Review [Work]
- **Status**: active. Son log: 2026-10-29
- **Next action**: Confirm tier changes with finance
- **Link**: [[Pricing Review]] (`Work/Pricing Review/notes.md`)

### 2. Garden Shed [Personal]
- **Status**: active. Son log: 2026-10-05
- **Next action**: Order timber
- **Link**: [[Garden Shed]] (`Personal/Garden Shed/notes.md`)

## On Hold (1)

### 1. Parked Idea [Personal]
- **Status**: on-hold. Son log: 2026-08-01
"""


class AttentionAuditTest(unittest.TestCase):
    def test_parses_active_projects_only_and_detects_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_Agent-Context").mkdir()
            (root / "_Agent-Context" / "PROJECTS-ACTIVE.md").write_text(SAMPLE_PROJECTS, encoding="utf-8")

            (root / "Thinking" / "Daily").mkdir(parents=True)
            (root / "Thinking" / "Daily" / "2026-11-23.md").write_text(
                "Reviewed Pricing Review numbers today.\n", encoding="utf-8"
            )
            (root / "Work" / "Side Quest").mkdir(parents=True)
            (root / "Work" / "Side Quest" / "draft.md").write_text("Working on side quest.\n", encoding="utf-8")
            # Protected home must never surface in ATTENTION.md.
            (root / "Personal" / "Official Docs").mkdir(parents=True)
            (root / "Personal" / "Official Docs" / "passport.md").write_text("Private.\n", encoding="utf-8")

            now = datetime.now()
            _, summary = aa.run_audit(root, days=7, dry_run=False, now=now)

            self.assertEqual(summary["stated"], 2)
            self.assertEqual(summary["active"], 1)
            self.assertEqual(summary["quiet"], 1)
            self.assertEqual(summary["unstated"], 1)

            out = root / "_Agent-Context" / "ATTENTION.md"
            self.assertTrue(out.exists())
            saved = out.read_text(encoding="utf-8")
            self.assertIn("[[Pricing Review]]", saved)
            self.assertIn("[[Garden Shed]] [Personal]: 0 touches", saved)
            self.assertIn("`Work/Side Quest`: 1 edited files", saved)
            self.assertNotIn("Official Docs", saved)
            self.assertNotIn("Parked Idea", saved)
            self.assertNotIn("\u2014", saved)
            self.assertNotIn("\u2013", saved)


if __name__ == "__main__":
    unittest.main()
