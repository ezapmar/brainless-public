"""Dreaming: judge parsing, picking, proposals, replies, the registry and the kill rule.

A stub judge and a stub Buzz outbox stand in for the model and the relay.

Run: python3 -m unittest tools.tests.test_dreaming -v
"""
import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

OWNER = "o" * 64


class Client:
    def channel(self, name, identity):
        return "cid-" + name


class Box:
    """The Outbox surface dreaming uses: enqueue, deliver, record, client."""
    def __init__(self):
        self.client, self.sent, self.n = Client(), {}, 0

    def enqueue(self, identity, channel, body, *, key=None, parent=None):
        if key in self.sent and self.sent[key]["body"] != body:
            raise ValueError("Outbox key reused with different content")
        self.sent.setdefault(key, {"body": body, "parent": parent, "event": None})
        return key

    def deliver(self, key):
        if not self.sent[key]["event"]:
            self.n += 1
            self.sent[key]["event"] = f"{self.n:064x}"

    def record(self, key):
        return self.sent.get(key)


def msg(content, parent, n=900):
    return {"id": f"{n:064x}", "pubkey": OWNER, "content": content,
            "tags": [["e", parent, "", "reply"]]}


class Dreaming(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("BRAINLESS_VAULT", self.old) if self.old
                        else os.environ.pop("BRAINLESS_VAULT", None))
        self.root = Path(self.tmp.name)
        for rel in ("summaries/meeting", "projects/Project", "summaries/article", "summaries/article_raw",
                    "summaries/x", "summaries/y"):
            p = self.root / ".wiki" / f"{rel}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"---\nsummary_en: {rel}\n---\n# {rel}\n\nBody of {rel}.\n\n## Bağlantılar\n")
        import compile_resources
        import dreaming
        importlib.reload(compile_resources)
        self.d = importlib.reload(dreaming)
        w = lambda r: f".wiki/{r}.md"
        self.sugg = {"orphan_homes": [{"a": w("summaries/meeting"), "b": w("projects/Project"), "sim": .96}],
                     "new_links": [{"a": w("summaries/x"), "b": w("summaries/y"), "sim": .95}],
                     "near_identical": [{"a": w("summaries/article"), "b": w("summaries/article_raw"), "sim": .98}]}
        orig_pick = self.d.pick
        self.d.pick = lambda state, n=5, now=None, suggestions=None: orig_pick(state, n, now, self.sugg)
        self.verdicts = {"meeting": "link", "x": "none", "article": "duplicate"}
        self.judge = lambda a, b, vault: {"verdict": self.verdicts[Path(a).stem], "reason": "aynı konu"}
        self.box = Box()

    def run_(self, **kw):
        return self.d.run(box=self.box, judge_fn=self.judge, **kw)

    def proposal(self, stem):
        import json
        state = json.loads(self.d.STATE.read_text())
        return next(p for p in state["proposals"].values() if Path(p["a"]).stem == stem)

    def test_parse_takes_the_json_out_of_noise(self):
        r = self.d.parse('<think>hmm {"verdict":"none"}</think> Sure: {"verdict": "Link", "reason": "  aynı   toplantı "}')
        self.assertEqual(r, {"verdict": "link", "reason": "aynı toplantı"})
        self.assertIsNone(self.d.parse('{"verdict": "maybe"}'))
        self.assertIsNone(self.d.parse(None))

    def test_run_proposes_links_and_duplicates_and_remembers_none(self):
        c = self.run_()
        self.assertEqual((c["proposed"], c["none"]), (2, 1))
        self.assertEqual(len(self.box.sent), 2)
        again = self.run_()
        self.assertEqual(again["judged"], 0)  # nothing new to ask

    def test_dry_run_asks_nothing(self):
        self.run_(dry=True)
        self.assertEqual(self.box.sent, {})

    def test_yes_writes_registry_and_both_pages(self):
        self.run_()
        p = self.proposal("meeting")
        self.assertTrue(self.d.handle(msg("evet", p["event"]), "cid-dreaming", OWNER, box=self.box))
        reg = self.d.REGISTRY.read_text()
        self.assertIn("| .wiki/summaries/meeting.md | .wiki/projects/Project.md | link | aynı konu |", reg)
        self.assertIn("- [[Project]]: aynı konu", (self.root / ".wiki/summaries/meeting.md").read_text())
        self.assertIn("- [[meeting]]: aynı konu", (self.root / ".wiki/projects/Project.md").read_text())
        self.assertEqual(self.proposal("meeting")["status"], "approved")

    def test_approved_link_survives_a_rewrite(self):
        self.run_()
        self.d.handle(msg("evet", self.proposal("meeting")["event"]), "cid-dreaming", OWNER, box=self.box)
        page = self.root / ".wiki/summaries/meeting.md"
        page.write_text("---\nsummary_en: new\n---\n# meeting\n\nRewritten.\n\n## Bağlantılar\n")
        import compile_resources
        self.assertEqual(compile_resources.apply_link_registry(), 1)
        self.assertIn("- [[Project]]: aynı konu", page.read_text())

    def test_duplicate_yes_is_a_merge_and_deletes_nothing(self):
        self.run_()
        self.d.handle(msg("evet", self.proposal("article")["event"]), "cid-dreaming", OWNER, box=self.box)
        self.assertIn("| merge |", self.d.REGISTRY.read_text())
        self.assertTrue((self.root / ".wiki/summaries/article_raw.md").exists())

    def test_no_is_never_asked_again(self):
        self.run_()
        self.d.handle(msg("hayır", self.proposal("meeting")["event"]), "cid-dreaming", OWNER, box=self.box)
        self.assertIn("| rejected |", self.d.REGISTRY.read_text())
        self.assertNotIn("[[Project]]", (self.root / ".wiki/summaries/meeting.md").read_text())
        import json
        state = json.loads(self.d.STATE.read_text())
        self.assertEqual(self.d.pick(state), [])

    def test_a_second_answer_changes_nothing(self):
        self.run_()
        ev = self.proposal("meeting")["event"]
        self.d.handle(msg("hayır", ev, n=901), "cid-dreaming", OWNER, box=self.box)
        self.d.handle(msg("evet", ev, n=902), "cid-dreaming", OWNER, box=self.box)
        self.assertNotIn("| link |", self.d.REGISTRY.read_text())

    def test_other_messages_are_not_claimed(self):
        self.run_()
        self.assertFalse(self.d.handle(msg("evet", "f" * 64), "cid-dreaming", OWNER, box=self.box))
        self.assertFalse(self.d.handle(msg("evet", self.proposal("meeting")["event"]), "cid-ops", OWNER, box=self.box))

    def test_one_duplicate_a_night_the_rest_wait_unjudged(self):
        w = lambda r: f".wiki/{r}.md"
        for i in range(3):
            (self.root / ".wiki/summaries" / f"dup{i}.md").write_text("---\nsummary_en: d\n---\nd\n")
            (self.root / ".wiki/summaries" / f"dup{i}_raw.md").write_text("---\nsummary_en: d\n---\nd\n")
        self.sugg["orphan_homes"] = [{"a": w(f"summaries/dup{i}"), "b": w(f"summaries/dup{i}_raw"), "sim": .97}
                                     for i in range(3)] + self.sugg["orphan_homes"]
        self.verdicts.update({f"dup{i}": "duplicate" for i in range(3)})
        calls = []
        judge = self.judge
        self.judge = lambda a, b, v: calls.append(a) or judge(a, b, v)
        self.run_()
        import json
        state = json.loads(self.d.STATE.read_text())
        dups = [p for p in state["proposals"].values() if p["verdict"] == "duplicate"]
        self.assertEqual(len(dups), 1)
        self.assertEqual(len(state["waiting"]), 3)  # two dup pairs and the article
        before = len(calls)
        self.run_()
        state = json.loads(self.d.STATE.read_text())
        self.assertEqual(sum(p["verdict"] == "duplicate" for p in state["proposals"].values()), 2)
        self.assertEqual(len(state["waiting"]), 2)
        self.assertEqual(len(calls), before)  # the waiting one was not judged again

    def test_kill_rule_stops_and_says_so(self):
        import json
        now = time.time()
        props = {f"k{i}": {"a": "a", "b": "b", "status": "rejected" if i < 8 else "approved",
                           "decided_at": now, "created": now} for i in range(10)}
        self.d.STATE.parent.mkdir(parents=True, exist_ok=True)
        self.d.STATE.write_text(json.dumps({"proposals": props, "judged": {}}))
        c = self.run_()
        self.assertEqual(c["judged"], 0)
        self.assertIn("stopped", json.loads(self.d.STATE.read_text()))
        self.assertTrue(any(k.startswith("dreaming:stopped") for k in self.box.sent))


if __name__ == "__main__":
    unittest.main()
