"""Graph health and merge proposals on a throwaway wiki.

The numbers are only useful if they mean what they say, so they are checked on
a graph small enough to count by hand: two clusters joined by one bridge page,
one orphan, a duplicate pair, a name clash.

Run: python3 -m unittest tools.tests.test_wiki_graph -v
"""
import importlib
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


def fm(**kw):
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in kw.items()) + "\n---\n"


class Graph(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.root = Path(self.tmp.name)
        self.addCleanup(self.restore)
        # pricing, packaging, bridge, hiring-plan, onboarding in a chain, and a lone page
        self.page("concepts/pricing", "[[packaging]]")
        self.page("concepts/packaging", "[[bridge]]")
        self.page("concepts/bridge", "[[hiring-plan]]", compiled_at="2026-01-01T00:00:00")
        self.page("entities/hiring-plan", "[[onboarding]]")
        self.page("entities/onboarding", "")
        self.page("summaries/lonely", "nothing links here")
        import lint_wiki
        import wiki_metrics
        import wiki_dedupe
        importlib.reload(lint_wiki)
        self.wm = importlib.reload(wiki_metrics)
        self.wd = importlib.reload(wiki_dedupe)

    def restore(self):
        if self.old is None:
            os.environ.pop("BRAINLESS_VAULT", None)
        else:
            os.environ["BRAINLESS_VAULT"] = self.old

    def page(self, rel, body, **extra):
        p = self.root / ".wiki" / f"{rel}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = {"lang": "en", "summary_en": "x", "compiled_at": "2026-09-20T00:00:00", **extra}
        p.write_text(fm(**meta) + f"# {p.stem}\n\n{body}\n")
        return p

    def test_metrics_count_what_is_there(self):
        m = self.wm.compute(datetime(2026, 9, 22))
        self.assertEqual(m["pages"], 6)
        self.assertAlmostEqual(m["orphan_rate"], round(1 / 6, 3))
        self.assertEqual(m["avg_degree"], round(2 * 4 / 6, 2))
        self.assertEqual(m["components"], 2)
        self.assertAlmostEqual(m["main_share"], round(5 / 6, 3))
        self.assertEqual(m["stale_concepts"], "1/3")
        self.assertEqual(m["bridges"][0], "bridge")

    def test_verdicts_follow_the_guide_thresholds(self):
        v = dict((k, s) for k, s, _ in self.wm.verdicts(
            {"orphan_rate": 0.2, "avg_degree": 1.5, "main_share": 0.9}))
        self.assertEqual(v, {"orphan_rate": "RED", "avg_degree": "WARN", "main_share": "OK"})

    def test_gephi_export_carries_type_colour_size_and_filters(self):
        import graph_export
        ge = importlib.reload(graph_export)
        nodes, edges = ge.build()
        by = {n["label"]: n for n in nodes}
        self.assertEqual((len(nodes), len(edges)), (6, 4))
        self.assertEqual((by["bridge"]["type"], by["lonely"]["orphan"]), ("concept", True))
        text = ge.gexf(nodes, edges)
        self.assertIn('<viz:color r="230" g="159" b="0"/>', text)     # concept colour
        import xml.dom.minidom
        xml.dom.minidom.parseString(text)
        xml.dom.minidom.parseString(ge.graphml(nodes, edges))
        main, _ = ge.build(no_summaries=True, main_only=True)
        self.assertEqual(sorted(n["label"] for n in main),
                         ["bridge", "hiring-plan", "onboarding", "packaging", "pricing"])

    def test_dedupe_finds_alias_title_and_clash_but_not_strangers(self):
        self.page("entities/Deniz", "", aliases='["Deniz A"]')
        self.page("entities/Deniz Korkmaz", "", aliases='["Deniz"]')
        self.page("concepts/pricing-strategy", "")
        self.page("concepts/pricing-strategies", "")
        self.page("projects/work/onboarding", "")
        signals = {(p["signal"], Path(p["a"]).stem, Path(p["b"]).stem) for p in self.wd.proposals()}
        self.assertIn(("alias", "Deniz Korkmaz", "Deniz"), {(s, *sorted((a, b), reverse=True)) for s, a, b in signals})
        self.assertIn("title", {s for s, _, _ in signals})
        self.assertIn("clash", {s for s, _, _ in signals})
        self.assertFalse(any({a, b} == {"pricing", "hiring-plan"} for _, a, b in signals))


if __name__ == "__main__":
    unittest.main()
