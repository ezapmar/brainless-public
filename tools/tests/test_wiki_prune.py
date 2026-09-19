"""The pile drain, tested on a throwaway vault.

The rules move files, so they are tested from the side that costs: a page that
should stay must stay. A filed research note never ages out, a linked page is
never an orphan, a summary the compiler would rebuild tomorrow is not moved
today, and a Spiky report leaves Inbox only once it has a summary and two weeks
behind it. Then the one thing the owner asked for on top: every move is logged,
in both places, once.
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


def fm(**kw):
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in kw.items()) + "\n---\n"


def days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


class PruneVault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        os.environ["BRAINLESS_VAULT"] = str(self.vault)
        for d in (".wiki/digests/queries", ".wiki/summaries", ".wiki/_commands", ".wiki/articles",
                  "Inbox/Spiky", "Archive/Daily-Captures", "Thinking/Decisions", "Thinking/Beliefs",
                  "Thinking/Ideas", "Thinking/Daily", "_Agent-Context", ".agents/state", "Work/Acme"):
            (self.vault / d).mkdir(parents=True, exist_ok=True)
        import lint_wiki
        import wiki_prune
        self.lint = importlib.reload(lint_wiki)
        self.wp = importlib.reload(wiki_prune)
        self.now = datetime.now()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def query(self, name, command, age, body="Some analysis.\n"):
        d = days_ago(age)
        return self.write(f".wiki/digests/queries/{d}-{command}-{name}.md",
                          fm(lang="en", summary_en="x", command=command, title=name,
                             compiled_at=f"{d}T10:00:00") + body)

    def rules(self, only=None):
        return {m.rule: m for m in self.wp.candidates(self.now, only)}, \
               [m.src.name for m in self.wp.candidates(self.now, only)]

    # ---- queries

    def test_old_unlinked_review_query_goes_and_research_stays(self):
        old_review = self.query("plan-a", "review", 45)
        self.query("plan-b", "research", 45)
        self.query("plan-c", "review", 10)
        _, names = self.rules(["query"])
        self.assertEqual(names, [old_review.name])

    def test_an_inbound_link_protects_a_query(self):
        q = self.query("kept", "review", 45)
        self.write(".wiki/articles/hub.md", fm(lang="en", summary_en="x") + f"See [[{q.stem}]].\n")
        _, names = self.rules(["query"])
        self.assertEqual(names, [])

    # ---- summaries and orphans

    def test_summary_of_an_archived_source_goes_and_a_live_one_stays(self):
        self.write("Work/Acme/live.md", "# live\n")
        gone = self.write(".wiki/summaries/gone.md",
                          fm(lang="en", summary_en="x", source="Archive/Spiky/2025-08/old.md",
                             compiled_at=f"{days_ago(90)}T00:00:00") + "old\n")
        self.write(".wiki/summaries/live.md",
                   fm(lang="en", summary_en="x", source="Work/Acme/live.md",
                      compiled_at=f"{days_ago(90)}T00:00:00") + "live\n")
        _, names = self.rules(["summary", "orphan"])
        self.assertEqual(names, [gone.name])

    def test_orphan_digest_older_than_sixty_days_goes_and_a_young_one_stays(self):
        old = self.write(f".wiki/digests/{days_ago(70)}.md", fm(lang="en", summary_en="x") + "quiet day\n")
        self.write(f".wiki/digests/{days_ago(5)}.md", fm(lang="en", summary_en="x") + "quiet day\n")
        _, names = self.rules(["orphan"])
        self.assertEqual(names, [old.name])

    def test_archived_pages_are_out_of_the_graph(self):
        self.write(f".wiki/_archive/digests/{days_ago(90)}.md", fm(lang="en", summary_en="x") + "gone\n")
        self.assertEqual(self.wp.candidates(self.now, ["orphan"]), [])
        self.assertNotIn("_archive", {p.parent.name for p in self.lint.all_wiki_files()})

    # ---- spiky

    def test_spiky_report_moves_only_with_a_summary_and_two_weeks_in_inbox(self):
        d = days_ago(40)
        summarised = self.write(f"Inbox/Spiky/{d} Weekly.md", "# Weekly\n")
        self.write(f"Inbox/Spiky/{days_ago(41)} Unsummarised.md", "# x\n")
        self.write(f"Inbox/Spiky/{days_ago(3)} Fresh.md", "# x\n")
        self.write(".wiki/summaries/inbox_spiky_weekly.md",
                   fm(lang="en", summary_en="x", source=f"Inbox/Spiky/{d} Weekly.md",
                      compiled_at=f"{days_ago(39)}T00:00:00") + "s\n")
        self.write(".wiki/summaries/inbox_spiky_fresh.md",
                   fm(lang="en", summary_en="x", source=f"Inbox/Spiky/{days_ago(3)} Fresh.md",
                      compiled_at=f"{days_ago(2)}T00:00:00") + "s\n")
        moves = self.wp.candidates(self.now, ["spiky"])
        self.assertEqual([m.src.name for m in moves], [summarised.name])
        self.assertEqual(moves[0].dst, self.vault / "Archive" / "Spiky" / d[:7] / summarised.name)

    # ---- apply and the log

    def test_apply_moves_logs_every_move_twice_and_retargets_the_summary(self):
        d = days_ago(40)
        self.write(f"Inbox/Spiky/{d} Weekly.md", "# Weekly\n")
        s = self.write(".wiki/summaries/inbox_spiky_weekly.md",
                       fm(lang="en", summary_en="x", source=f"Inbox/Spiky/{d} Weekly.md",
                          compiled_at=f"{days_ago(39)}T00:00:00") + "s\n")
        q = self.query("plan-a", "review", 45)
        moves = self.wp.candidates(self.now)
        rows, skipped = self.wp.apply(moves, self.now)

        self.assertEqual(len(rows), 2)
        self.assertEqual(skipped, [])
        self.assertFalse(q.exists())
        self.assertTrue((self.vault / ".wiki/_archive/queries" / q.name).exists())
        self.assertTrue((self.vault / "Archive/Spiky" / d[:7] / f"{d} Weekly.md").exists())
        self.assertIn(f"source: Archive/Spiky/{d[:7]}/{d} Weekly.md", s.read_text())

        jsonl = (self.vault / ".agents/state/prune_log.jsonl").read_text().splitlines()
        self.assertEqual(len(jsonl), 2)
        self.assertEqual({json.loads(x)["rule"] for x in jsonl}, {"query", "spiky"})
        log_md = (self.vault / ".wiki/_archive/LOG.md").read_text()
        self.assertEqual(log_md.count(f"| {days_ago(0)} |"), 2)
        self.assertTrue((self.vault / ".wiki/_archive/README.md").exists())

        # A second apply of the same list finds nothing: the sources are gone.
        self.assertEqual(self.wp.candidates(self.now), [])

    # ---- count

    def test_count_writes_a_machine_readable_line_and_the_beliefs_criterion(self):
        self.write(f"Inbox/Spiky/{days_ago(30)} Old.md", "# x\n")
        self.write(f"Inbox/Spiky/{days_ago(2)} New.md", "# x\n")
        self.write("Thinking/Decisions/Decision - A.md",
                   fm(date=days_ago(5), type="decision") + "# A\n\n## Outcome\nWent well.\n")
        self.write("Thinking/Decisions/Decision - B.md",
                   fm(date=days_ago(100), type="decision") + "# B\n\n## Outcome\n_Pending review._\n")
        self.write("Thinking/Beliefs/Belief.md", fm(type="belief", last_challenged=days_ago(3)) + "# b\n")
        self.write(f"Archive/Daily-Captures/{days_ago(1)}/{days_ago(1)}-note.md", "# n\n")
        self.write("_Agent-Context/TASKS.md",
                   f"# T\n\n## Promises\n\n- [ ] old | [[x]] | {days_ago(90)}\n- [ ] new | [[x]] | {days_ago(1)}\n")
        c = self.wp.count(self.now)
        self.wp.write_scorecard(c)
        text = (self.vault / "_Agent-Context/PILE-SCORECARD.md").read_text()
        line = next(ln for ln in text.splitlines() if ln.startswith("<!-- pile: "))
        data = json.loads(line[len("<!-- pile: "):-len(" -->")])
        self.assertEqual(data["inbox_stale"], 1)
        self.assertEqual(data["decisions"], 2)
        self.assertEqual(data["graded"], 1)
        self.assertEqual(data["decisions_30"], 1)
        self.assertEqual(data["beliefs_30"], 1)
        self.assertEqual(data["captures_30"], 1)
        self.assertEqual((data["tasks_open"], data["tasks_stale"]), (2, 1))
        self.assertNotIn("\u2014", text)

    def test_run_history_rolls_and_replaces_the_same_day(self):
        c = self.wp.count(self.now)
        self.wp.write_scorecard(c)
        self.wp.write_scorecard(c)
        text = (self.vault / "_Agent-Context/PILE-SCORECARD.md").read_text()
        self.assertEqual(len(self.wp.previous_rows(text)), 1)


if __name__ == "__main__":
    unittest.main()
