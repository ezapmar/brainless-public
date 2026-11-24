"""Unit tests for .agents/scripts/gmail_brain_intake.py."""
import email.message
import tempfile
import unittest
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest import mock

import gmail_brain_intake as gbi


def _make_plain(mid: str, subject: str, body: str, sender: str = "Ada <ada@example.com>") -> bytes:
    msg = email.message.EmailMessage()
    msg["Message-ID"] = mid
    msg["Subject"] = subject
    msg["From"] = sender
    msg["Date"] = "Mon, 23 Nov 2026 14:30:00 +0300"
    msg.set_content(body)
    return msg.as_bytes()


def _make_html_with_attachment(mid: str, subject: str, html: str, filename: str) -> bytes:
    msg = MIMEMultipart()
    msg["Message-ID"] = mid
    msg["Subject"] = subject
    msg["From"] = "Partner <partner@example.com>"
    msg["Date"] = "Mon, 23 Nov 2026 16:00:00 +0300"
    msg.attach(MIMEText(html, "html", "utf-8"))
    att = MIMEApplication(b"%PDF-1.4 mock", Name=filename)
    att["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(att)
    return msg.as_bytes()


class GmailBrainIntakeTest(unittest.TestCase):
    def test_idempotent_ingest_writes_once_and_skips_on_second_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "Inbox" / "Mail"
            state_path = root / ".agents" / "state" / "gmail_intake_state.json"
            review_path = root / "_Agent-Context" / "GMAIL-REVIEW.md"
            now = datetime(2026, 11, 23, 23, 0, tzinfo=timezone.utc)

            msgs = [
                _make_plain("<m1@example.com>", "Annual Renewal Schedule",
                            "Please confirm the target renewal date for December 2026."),
                _make_plain("<m2@example.com>", "Q4 Board Deck Notes",
                            "Attached are the talking points for our Q4 board alignment."),
            ]

            first = gbi.ingest_messages(
                msgs, label="Brain", limit=5, dry_run=False,
                out_dir=out_dir, state_path=state_path, review_path=review_path,
                vault_root=root, now=now,
            )
            self.assertEqual(first, {"written": 2, "skipped": 0, "flagged": 0})
            saved = sorted(out_dir.glob("*.md"))
            self.assertEqual(len(saved), 2)
            text = saved[0].read_text(encoding="utf-8")
            self.assertIn("privacy: personal", text)
            self.assertIn("status_checked:", text)
            self.assertNotIn("\u2014", text)
            self.assertNotIn("\u2013", text)
            self.assertFalse(review_path.exists())

            # Second run over identical messages must create zero duplicates.
            second = gbi.ingest_messages(
                msgs, label="Brain", limit=5, dry_run=False,
                out_dir=out_dir, state_path=state_path, review_path=review_path,
                vault_root=root, now=now,
            )
            self.assertEqual(second, {"written": 0, "skipped": 2, "flagged": 0})
            self.assertEqual(len(list(out_dir.glob("*.md"))), 2)

    def test_attachments_and_html_body_flag_review_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "Inbox" / "Mail"
            state_path = root / ".agents" / "state" / "gmail_intake_state.json"
            review_path = root / "_Agent-Context" / "GMAIL-REVIEW.md"
            now = datetime(2026, 11, 23, 23, 5, tzinfo=timezone.utc)

            raw = _make_html_with_attachment(
                "<m3@example.com>",
                "Signed Contract Draft",
                "<html><body><p>Hello Ada,</p><p>Please review the signed PDF attached.</p></body></html>",
                "agreement-v3.pdf",
            )
            counts = gbi.ingest_messages(
                [raw], label="Brain", limit=5, dry_run=False,
                out_dir=out_dir, state_path=state_path, review_path=review_path,
                vault_root=root, now=now,
            )
            self.assertEqual(counts, {"written": 1, "skipped": 0, "flagged": 1})
            self.assertTrue(review_path.exists())
            review_text = review_path.read_text(encoding="utf-8")
            self.assertIn("agreement-v3.pdf", review_text)
            self.assertNotIn("partner@example.com", review_text)
            note_text = next(out_dir.glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("Please review the signed PDF attached.", note_text)
            self.assertIn("needs_review: true", note_text)

    def test_limit_and_dry_run_leave_disk_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "Inbox" / "Mail"
            state_path = root / ".agents" / "state" / "gmail_intake_state.json"
            review_path = root / "_Agent-Context" / "GMAIL-REVIEW.md"
            msgs = [
                _make_plain(f"<m{i}@example.com>", f"Note {i}", "Long enough body text for testing limit.")
                for i in range(4)
            ]
            res = gbi.ingest_messages(
                msgs, label="Brain", limit=2, dry_run=True,
                out_dir=out_dir, state_path=state_path, review_path=review_path,
                vault_root=root,
            )
            self.assertEqual(res["written"], 2)
            self.assertFalse(out_dir.exists())
            self.assertFalse(state_path.exists())

    def test_missing_label_exits_zero_cleanly(self):
        with mock.patch.object(gbi, "_read_secret", side_effect=["a@b.com", "secret"]), \
             mock.patch.object(gbi, "fetch_label_messages", return_value=(False, [])):
            rc = gbi.main(["--label", "Brain"])
            self.assertEqual(rc, 0)

    def test_imap_label_quoting_rejects_crlf_and_escapes_quotes(self):
        self.assertEqual(gbi.quote_imap_mailbox("Brain"), '"Brain"')
        self.assertEqual(gbi.quote_imap_mailbox('My "Brain"\\Box'), '"My \\"Brain\\"\\\\Box"')
        for bad in ("", "Brain\r\nLOGOUT", "Brain\nNOOP", "Brain\x00"):
            with self.assertRaises(ValueError):
                gbi.quote_imap_mailbox(bad)

    def test_oversized_body_is_truncated(self):
        huge = _make_plain("<huge@example.com>", "Huge Mail", "A" * (gbi.MAX_BODY_CHARS + 500))
        rec = gbi.parse_message(huge)
        self.assertIn("[truncated at 100000 characters]", rec["body"])
        self.assertLessEqual(len(rec["body"]), gbi.MAX_BODY_CHARS + 64)


if __name__ == "__main__":
    unittest.main()
