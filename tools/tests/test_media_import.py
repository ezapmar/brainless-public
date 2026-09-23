"""YouTube and podcast import, without the network, yt-dlp or Whisper.

The parts that decide what lands in the vault are pure: which links count,
which job a link is, how rolling captions collapse into text, where the
timestamps go, which episode an Apple link means. Those are tested here; the
external tools are exercised by a real run on the worker.

Run: python3 -m unittest tools.tests.test_media_import -v
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

VTT = """WEBVTT
Kind: captions

00:00:00.000 --> 00:00:02.000 align:start
<c>welcome</c> to the show

00:00:02.000 --> 00:00:04.000
welcome to the show
today we talk pricing

00:03:05.500 --> 00:03:07.000
second part&nbsp;starts
"""

WHISPER = """[00:00:00.000 --> 00:00:04.000]   Merhaba, bugün fiyatlamayı konuşuyoruz.
[00:03:10.000 --> 00:03:14.000]   İkinci bölüm.
[00:03:14.000 --> 00:03:15.000]
"""

LOOKUP = {"results": [
    {"wrapperType": "track", "kind": "podcast", "collectionName": "Show", "feedUrl": "https://x/feed"},
    {"trackId": 111, "trackName": "Other", "episodeUrl": "https://cdn.example.com/1.mp3"},
    {"trackId": 1000712345678, "trackName": "Pricing, part 2", "collectionName": "Show",
     "episodeUrl": "https://cdn.example.com/2.mp3", "releaseDate": "2025-03-04T05:00:00Z",
     "trackTimeMillis": 3600000, "description": "About pricing."},
]}


class Media(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("BRAINLESS_VAULT", self.old) if self.old
                        else os.environ.pop("BRAINLESS_VAULT", None))
        import media_import
        self.m = importlib.reload(media_import)

    def test_links_are_classified(self):
        c = self.m.classify
        self.assertEqual(c("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10"), "youtube")
        self.assertEqual(c("https://youtu.be/dQw4w9WgXcQ"), "youtube")
        self.assertEqual(c("https://youtube.com/shorts/dQw4w9WgXcQ"), "youtube")
        self.assertEqual(c("https://podcasts.apple.com/tr/podcast/the-show/id123456?i=1000712345678"), "podcast")
        self.assertIsNone(c("https://example.com/watch?v=dQw4w9WgXcQ"))
        self.assertIsNone(c("https://www.youtube.com/@channel"))

    def test_the_same_video_twice_is_one_job(self):
        a, new_a = self.m.enqueue("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "listen")
        b, new_b = self.m.enqueue("https://youtu.be/dQw4w9WgXcQ?si=share")
        self.assertEqual((a == b, new_a, new_b), (True, True, False))
        with self.assertRaises(ValueError):
            self.m.enqueue("https://example.com/")

    def test_rolling_captions_collapse_and_tags_go(self):
        cues = self.m.parse_vtt(VTT)
        self.assertEqual([t for _, t in cues], ["welcome to the show", "today we talk pricing", "second part starts"])
        self.assertEqual(cues[2][0], 185.5)

    def test_markers_every_few_minutes_and_chapter_headings(self):
        text = self.m.paragraphs(self.m.parse_vtt(VTT), chapters=[(0, "Intro"), (180, "Pricing")])
        self.assertEqual(text, "## Intro\n\n[00:00] welcome to the show today we talk pricing\n\n"
                               "## Pricing\n\n[03:05] second part starts")

    def test_whisper_output_parses_and_skips_empty_lines(self):
        cues = self.m.parse_whisper(WHISPER)
        self.assertEqual(cues, [(0.0, "Merhaba, bugün fiyatlamayı konuşuyoruz."), (190.0, "İkinci bölüm.")])

    def test_apple_link_resolves_to_its_own_episode(self):
        info = self.m.apple_lookup("123456", "1000712345678", fetch=lambda url: LOOKUP)
        self.assertEqual((info["title"], info["audio"], info["published"], info["duration"]),
                         ("Pricing, part 2", "https://cdn.example.com/2.mp3", "2025-03-04", 3600))
        with self.assertRaises(RuntimeError):
            self.m.apple_lookup("123456", "999", fetch=lambda url: LOOKUP)

    def test_a_show_link_without_an_episode_is_refused(self):
        with self.assertRaises(RuntimeError) as e:
            self.m.podcast("https://podcasts.apple.com/tr/podcast/the-show/id123456", Path(self.tmp.name))
        self.assertIn("episode", str(e.exception))

    def test_a_permanent_failure_leaves_the_queue_and_is_logged(self):
        self.m.enqueue("https://podcasts.apple.com/tr/podcast/the-show/id123456?i=999")
        self.m.apple_lookup = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("episode not found"))
        self.m.notify = lambda *a, **k: None
        done, failed = self.m.run()
        self.assertEqual((done, failed), (0, 1))
        self.assertEqual(list(self.m.QUEUE.glob("*.json")), [])
        self.assertEqual(json.loads(self.m.DONE_LOG.read_text())["status"], "failed")

    def test_render_has_no_dashes_and_keeps_the_comment(self):
        info = {"kind": "podcast", "title": f"A {chr(0x2014)} B", "author": "Show", "url": "u",
                "published": "2025-03-04", "duration": 65, "method": "whisper"}
        out = self.m.render(info, "[00:00] text", {"comment": "listen for pricing"})
        self.assertNotIn(chr(0x2014), out)
        self.assertIn("> listen for pricing", out)
        self.assertIn("duration: 01:05", out)


if __name__ == "__main__":
    unittest.main()
