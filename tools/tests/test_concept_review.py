"""Concept proposals decided by a Buzz reply.

A stub outbox stands in for the relay. Checks the announcement (once per
proposal, no title for a personal one), the replies (yes activates, no retires,
skip and unknown words change nothing), and the queue cap.

Run: python3 -m unittest tools.tests.test_concept_review -v
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

from tests.test_dreaming import Box, OWNER, msg  # noqa: E402

REGISTRY = """# Concept registry

Row format: `slug | Title | aliases | status | sensitivity | scope`.

## Concepts
nakit | Nakit | cash | active |  | cash discipline
takim | Takım Etkinliği | teams | proposed |  | real teams vs working groups
okul | Okul Geçişi |  | proposed | personal | school move
"""


class ConceptReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("BRAINLESS_VAULT", self.old) if self.old
                        else os.environ.pop("BRAINLESS_VAULT", None))
        self.root = Path(self.tmp.name)
        (self.root / "_Agent-Context").mkdir()
        self.registry = self.root / "_Agent-Context/concepts.md"
        self.registry.write_text(REGISTRY)
        import i18n
        import concept_review
        self.cr = importlib.reload(concept_review)
        self.cr.t = lambda key, **kw: i18n.t(key, lang="en", **kw)
        i18n_t = i18n.t
        self.addCleanup(setattr, i18n, "t", i18n_t)
        i18n.t = lambda key, lang=None, **kw: i18n_t(key, lang="en", **kw)
        self.catalogue = [
            {"stem": "why-teams", "source": "Library/Articles/Why Teams.md", "concepts": ["takim"]},
            {"stem": "why-teams_raw", "source": "Library/Articles/Why Teams_raw.md", "concepts": ["takim"]},
            {"stem": "discipline", "source": "Library/Articles/Discipline.md", "concepts": ["takim"]},
        ]
        self.box = Box()

    def announced(self):
        return json.loads(self.cr.STATE.read_text())["announced"]

    def status(self, slug):
        return next(r["status"] for r in self.cr.rows() if r["slug"] == slug)

    def test_announce_once_and_personal_shows_no_title(self):
        self.assertEqual(self.cr.announce(box=self.box, catalogue=self.catalogue), 2)
        self.assertEqual(self.cr.announce(box=self.box, catalogue=self.catalogue), 0)
        bodies = [v["body"] for v in self.box.sent.values()]
        team = next(b for b in bodies if "Takım" in b)
        self.assertIn("2 source(s)", team)          # the _raw twin is not a second source
        self.assertIn("Discipline.md", team)
        personal = next(b for b in bodies if "Takım" not in b)
        self.assertNotIn("Okul", personal)
        self.assertIn("line 8", personal)

    def test_yes_activates_no_retires(self):
        self.cr.announce(box=self.box, catalogue=self.catalogue)
        ev = self.announced()
        self.assertTrue(self.cr.handle(msg("evet", ev["takim"]["event"], n=901), "cid-dreaming", OWNER, box=self.box))
        self.assertTrue(self.cr.handle(msg("no", ev["okul"]["event"], n=902), "cid-dreaming", OWNER, box=self.box))
        self.assertEqual(self.status("takim"), "active")
        self.assertEqual(self.status("okul"), "retired")
        self.assertIn("cash discipline", self.registry.read_text())   # other rows untouched
        replies = [v["body"] for k, v in self.box.sent.items() if k.startswith("concept:reply:")]
        self.assertTrue(any("Active: Takım" in r for r in replies))
        self.assertFalse(any("Okul" in r for r in replies))
        # A later answer to a decided proposal changes nothing.
        self.cr.handle(msg("hayır", ev["takim"]["event"], n=903), "cid-dreaming", OWNER, box=self.box)
        self.assertEqual(self.status("takim"), "active")

    def test_skip_and_unknown_leave_it_waiting(self):
        self.cr.announce(box=self.box, catalogue=self.catalogue)
        ev = self.announced()["takim"]["event"]
        self.cr.handle(msg("sonra", ev, n=904), "cid-dreaming", OWNER, box=self.box)
        self.cr.handle(msg("belki", ev, n=905), "cid-dreaming", OWNER, box=self.box)
        self.assertEqual(self.status("takim"), "proposed")

    def test_other_messages_pass_through(self):
        self.cr.announce(box=self.box, catalogue=self.catalogue)
        ev = self.announced()["takim"]["event"]
        self.assertFalse(self.cr.handle(msg("evet", "f" * 64), "cid-dreaming", OWNER, box=self.box))
        self.assertFalse(self.cr.handle(msg("evet", ev), "cid-thinking", OWNER, box=self.box))
        stranger = dict(msg("evet", ev), pubkey="x" * 64)
        self.assertFalse(self.cr.handle(stranger, "cid-dreaming", OWNER, box=self.box))

    def test_room_is_the_cap_minus_waiting(self):
        self.assertEqual(self.cr.room(), self.cr.MAX_PENDING - 2)
        self.registry.write_text(REGISTRY + "".join(f"w{i} | W{i} |  | proposed |  | x\n" for i in range(3)))
        self.assertEqual(self.cr.room(), 0)


if __name__ == "__main__":
    unittest.main()
