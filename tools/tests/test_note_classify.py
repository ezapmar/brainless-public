"""The epic gate: research must trigger rarely, and only on real weight.

These tests exist because the failure mode is asymmetric. Missing an epic costs
one skipped research pass. Calling an ordinary meeting an epic costs a week of
tokens and puts noise in the wiki, which is the exact "malumatfuruş" outcome the
feature is meant to prevent. So the gate is tested from the over-triggering side.
"""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import note_classify as nc  # noqa: E402


def classify(text, *, projects=(), people=(), peers=(), weight=1.0, name="note.md"):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_text(text)
        f = nc.features(path, text, list(projects), list(people), list(peers))
        total, fired, parts = nc.score(f, weight)
        persistent = (f["recurrence_days"] >= nc.EPIC_MIN_RECURRENCE
                      or f["projects"] >= nc.EPIC_MIN_PROJECTS
                      or f["links"] >= nc.EPIC_MIN_LINKS)
        candidate = (total >= nc.EPIC_SCORE_MIN and fired >= nc.EPIC_MIN_SIGNALS
                     and persistent)
        return {"score": total, "signals": fired, "parts": parts,
                "features": f, "candidate": candidate}


TASK = "# Sunum\n\nYarın Aylin'e dashboard örneklerini gönder.\n\n- [ ] Örnekleri gönder\n"

MEETING = """# Haftalık Review
Tarih: 2026-09-17
Katılımcılar: Orhan Tekin, Nil Akdoğan Sezer, Zeynep Ilgaz Toprak
Brief Summary
Anket sistemi üzerinden talep toplama stratejisi kararlaştırıldı. Eylül sonuna
kadar teknik altyapı hazırlanıp Ekim-Aralık döneminde aktif hale getirilecek.
Important Action Items
* Zeynep: Anket formatını hazırlamak
* Orhan: Teknik ekiple görüşmek
"""

EPIC = """# Ürün ve mühendislik dönüşümü: ekip yapısı ve öncelik yönetimi

## Nereden başladık
[[Şirket]] tarafında üç ekip vardı; biri [[Bordro]], biri [[Core]], biri de
bugları yürütüyordu. Kişilerden biri ayrılınca iki ekibe düştük ve Core ekibi
bugları da devraldı. Bu yapı neden tıkandı, ekipleri nasıl bölmeliyiz? En çok
duyulan şikayet şu: bir ekibin yoğunluğu çok fazla ve her iş sonunda oraya
bağlanıyor. Bu bağımlılığı azaltmadan hiçbir öncelik sırası tutmuyor.

## Çerçeve
[[Product Engineering Framework]] üzerine konuşuyoruz. Önümüzdeki çeyrek için
yol haritası, mimari katmanlar ve sahiplik sınırları netleşmeli. Burada asıl
karar şu: ekipleri ürün hattına göre mi yoksa müşteri segmentine göre mi
böleceğiz? İki seçeneğin de maliyeti var ve ikisi de geri dönüşü zor kararlar.
Bir de üçüncü bir alternatif var: mevcut yapıyı koruyup sadece öncelik
yönetimini değiştirmek, ama bunun bağımlılığı gerçekten çözüp çözmediği
belirsiz ve ekip içinde tartışmalı.

## Risk
Bağımlılığı minimize etmezsek strateji tutmaz, uzun vadede teknik borç büyür.
Belirsizlik yüksek; hangi ölçüyü izleyeceğimize de karar vermedik. Kaygım şu:
yapıyı değiştirirken teslim hızını düşürürsek, kazanacağımız esneklik
kaybettiğimiz momentumu karşılamayabilir.
"""


class TaskAndStory(unittest.TestCase):
    def test_short_actionable_note_is_not_epic(self):
        r = classify(TASK)
        self.assertFalse(r["candidate"])
        self.assertLess(r["score"], nc.STORY_SCORE_MIN)

    def test_checkboxes_pull_score_down(self):
        with_box = classify(TASK)["score"]
        without = classify(TASK.replace("- [ ] Örnekleri gönder", ""))["score"]
        self.assertLess(with_box, without)


class MeetingDumps(unittest.TestCase):
    """Spiky reports look structurally heavy but are usually one afternoon."""

    def test_meeting_without_wikilinks_cannot_be_epic(self):
        people = ["Orhan Tekin", "Nil Akdoğan Sezer", "Zeynep Ilgaz Toprak"]
        r = classify(MEETING, people=people, weight=nc.SOURCE_WEIGHTS["Inbox/Spiky"])
        self.assertEqual(r["features"]["links"], 0)
        self.assertFalse(r["candidate"], "a meeting dump reached the epic gate")

    def test_spiky_damper_is_applied(self):
        full = classify(MEETING, weight=1.0)["score"]
        damped = classify(MEETING, weight=nc.SOURCE_WEIGHTS["Inbox/Spiky"])["score"]
        self.assertLess(damped, full)


class Epics(unittest.TestCase):
    def test_connected_multi_cycle_note_is_a_candidate(self):
        r = classify(EPIC)
        self.assertTrue(r["candidate"], f"score {r['score']} signals {r['signals']}")
        self.assertGreaterEqual(r["features"]["links"], nc.EPIC_MIN_LINKS)

    def test_structural_gate_blocks_a_high_score_without_breadth(self):
        # Same thinking, no links, no projects, no recurrence: it scores well but
        # nothing shows it spans cycles or workstreams, so it stays a story.
        flat = EPIC.replace("[[", "").replace("]]", "")
        r = classify(flat)
        self.assertGreaterEqual(r["score"], nc.AMBIGUOUS_BAND[0])
        self.assertFalse(r["candidate"])

    def test_recurrence_alone_can_satisfy_the_structural_gate(self):
        flat = EPIC.replace("[[", "").replace("]]", "")
        today = datetime.now()
        peers = [(Path(f"/x/peer{i}.md"), today - timedelta(days=i * 3),
                  nc.significant_tokens(flat)) for i in range(1, 4)]
        r = classify(flat, peers=peers)
        self.assertGreaterEqual(r["features"]["recurrence_days"], nc.EPIC_MIN_RECURRENCE)
        self.assertTrue(r["candidate"])


class ModelCannotPromote(unittest.TestCase):
    """Stage B may demote a candidate but never promote a rejected note.

    Otherwise a persuasively written note could talk itself into triggering a
    research pass, which is exactly the injection-shaped risk worth closing.
    """

    def test_ambiguous_note_capped_at_story_even_if_model_says_epic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "n.md"
            path.write_text(EPIC)
            real = nc.adjudicate
            nc.adjudicate = lambda *a, **k: "epic"
            try:
                # Force the ambiguous branch: score below the gate, breadth present.
                saved = nc.EPIC_SCORE_MIN
                nc.EPIC_SCORE_MIN = 99.0
                nc.AMBIGUOUS_BAND = (0.0, 99.0)
                rec = nc.classify(path, EPIC, [], [], [], 1.0, use_llm=True)
            finally:
                nc.adjudicate = real
                nc.EPIC_SCORE_MIN = saved
                nc.AMBIGUOUS_BAND = (3.0, 3.6)
        self.assertNotEqual(rec["label"], "epic")


if __name__ == "__main__":
    unittest.main()
