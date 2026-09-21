import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import writing_index
from buzz_delivery import ROUTES, HARNESS_CHANNELS
import buzz_interactions


class WritingIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        w = self.vault / "Writings"
        (w / "HBR").mkdir(parents=True)
        (w / "HBR" / "Published piece.md").write_text("---\ntitle: The Piece\n---\n\n# The Piece\n\nsome words here\n")
        (w / "Drafts").mkdir()
        (w / "Drafts" / "2026-09-21 pitch-x.md").write_text("---\ntitle: Pitch X\nstatus: idea\n---\n\n# Pitch X\n")
        (w / "Editor" / "Rehberler").mkdir(parents=True)
        (w / "Editor" / "README.md").write_text("# Editor\n")
        (w / "Editor" / "editor_lint.py").write_text("print(1)\n")
        (w / "Secret Person").mkdir()
        (w / "Secret Person" / "hidden.md").write_text("# Hidden\n")
        c = self.vault / "raw" / "Medium Posts"
        c.mkdir(parents=True)
        (c / "2024-01-02_Hello-world--abc123.md").write_text("hi\n")
        (c / "2025-03-04_Second-post--def456.md").write_text("hi\n")
        (self.vault / ".agents" / "state").mkdir(parents=True)
        (self.vault / ".agents" / "state" / "writing_pitches.json").write_text(json.dumps(
            {"pitches": [{"title": "Open pitch", "venue": "hbr", "date": "2026-09-21", "path": "Writings/Drafts/p.md", "status": "open"},
                         {"title": "Closed pitch", "venue": "hbr", "date": "2026-07-01", "path": "x", "status": "done"}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_map_lists_pieces_drafts_corpus_and_hides_private(self):
        text = writing_index.build(self.vault, corpus_dirs=("raw/Medium Posts",), private_segments=("Secret Person",), lang="en")
        self.assertIn("[[Published piece]]", text)
        self.assertIn("Writings/HBR/Published piece.md", text)
        self.assertIn("Pitch X", text)
        self.assertIn("idea", text)
        self.assertIn("2 pieces; by year: 2024: 1, 2025: 1", text)
        self.assertIn("Open pitch", text)
        self.assertNotIn("Closed pitch", text)
        self.assertNotIn("hidden", text)
        self.assertNotIn("Secret Person", text)
        self.assertIn("editor_lint.py", text)
        self.assertNotIn("—", text)

    def test_reply_worker_leaves_harness_channels_alone(self):
        self.assertIn("writing", ROUTES)
        self.assertIn("narratives", ROUTES)
        polled = buzz_interactions.polled_routes()
        for name in HARNESS_CHANNELS:
            self.assertNotIn(name, polled)
        self.assertIn("tasks", polled)


if __name__ == "__main__":
    unittest.main()
