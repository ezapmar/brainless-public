#!/usr/bin/env python3
"""
compile_resources.py — human-tree → .wiki compiler.

Walks the human-owned homes and compiles .wiki/ artifacts via Claude CLI:
- Library/, Inbox/, Thinking/Daily/, Personal/, the company area (PROFILE.md) → .wiki/summaries/<slug>.md
- Work/<P>/notes.md, Personal/<P>/notes.md → .wiki/projects/{work,personal}/<P>.md
- Clusters summaries → .wiki/articles/<concept>.md
- Auto-derives .wiki/ideas/ from Thinking/Beliefs + Thinking/Decisions
- Regenerates .wiki/INDEX.md

Incremental by default: skips files where summary mtime >= source mtime.
Use --full-rebuild to force.

CRITICAL: cross-link personal↔work effects (rule in _Agent-Context/PROFILE.md).

Usage:
  python3 tools/compile_resources.py                  # incremental
  python3 tools/compile_resources.py --full-rebuild   # rebuild all
  python3 tools/compile_resources.py --dry-run        # show plan
  python3 tools/compile_resources.py --only summaries # phase: summaries|articles|projects|ideas|index
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless"))
WIKI = VAULT / ".wiki"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_bin import resolve_claude
from llm import run_prompt
from owner_profile import OWNER, LANG, lang_name, CROSS_LINK_RULE  # noqa: E402
from owner_profile import COMPANY_AREA, GENERIC_PRIVATE_SEGMENTS, PRIVATE_SEGMENTS as PROFILE_PRIVATE_SEGMENTS  # noqa: E402

CLAUDE = resolve_claude()
# Per-source prompt cap; smaller-context providers can shrink it (Phase 0 T5).
MAX_CHARS = int(os.environ.get("BRAINLESS_LLM_MAX_CHARS", "30000"))

# One-concept-one-home layout: the human tree lives at vault root; .wiki is the
# compiled (machine-owned) layer. Summaries are drawn from the reference/cognition
# homes plus the company area named in PROFILE.md (company_area).
SUMMARY_SOURCES = [
    VAULT / "Library",
    VAULT / "Inbox",
    VAULT / "Thinking" / "Daily",
    VAULT / "Personal",
    VAULT / COMPANY_AREA,
]

# Privacy guard: NEVER summarize gitignored / sensitive homes into the tracked
# .wiki layer. Mirrors .gitignore. A path is skipped if any segment matches.
# NFC normalizasyonu ZORUNLU: macOS dosya adlarini NFD ile dondururken bu
# dosyadaki string literalleri NFC; normalize edilmezse aksanli kisi adli
# Turkce/aksanli klasorler guard'a takilmaz ve hassas veri .wiki'ye sizar
# (2026-08-28 denetiminde 88 dosya bu yuzden GitHub'a dusmustu).
def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


PRIVATE_SEGMENTS = {_nfc(s) for s in (*GENERIC_PRIVATE_SEGMENTS, *PROFILE_PRIVATE_SEGMENTS)}
PRIVATE_SUFFIXES = (" - Health",)            # "<name> - Health" folders
PRIVATE_NAME_PARTS = tuple(_nfc(p).casefold() for p in
                           ("passport", "pasaport", "schengen", "kimlik", "nüfus", "nufus"))
# Tam alt-yol eslesmeleri (segment adlari cok jenerik olan hassas alanlar).
PRIVATE_PATH_PARTS = tuple(_nfc(p).casefold() for p in
                           (COMPANY_AREA.split("/")[-1] + "/Finance/Resources",))


def _is_private(path: Path) -> bool:
    for seg in path.parts:
        seg_n = _nfc(seg)
        if seg_n in PRIVATE_SEGMENTS or seg_n.endswith(PRIVATE_SUFFIXES):
            return True
    low = _nfc(str(path)).casefold()
    return (any(p in low for p in PRIVATE_NAME_PARTS)
            or any(p in low for p in PRIVATE_PATH_PARTS))


# CROSS_LINK_RULE comes from _Agent-Context/PROFILE.md ("## Cross-link rule") via owner_profile.
SUMMARY_HEADING = "## Özet (TR)" if LANG == "tr" else "## Summary"
STATUS_HEADING = "## Durum (TR)" if LANG == "tr" else "## Status"


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s/]+", "-", s)
    return s[:100]


def _claude_env() -> dict:
    """claude is a node script; under cron, node may not be on PATH. Ensure the
    resolved claude's own bin dir (which contains node for nvm installs) is."""
    env = os.environ.copy()
    node_bin = os.path.dirname(CLAUDE)
    if node_bin and node_bin not in env.get("PATH", ""):
        env["PATH"] = node_bin + os.pathsep + env.get("PATH", "")
    return env


_CLAUDE_CALLS = 0
_CLAUDE_FAILURES = 0


# Compile prompt'larina guvenilmeyen icerik (kaynak dosya govdeleri) giriyor;
# arac kullanimi kapali olmali. Kapali degilse model agentic'e kayip takilabiliyor
# (Visma/Talentics gibi zengin Work kaynaklarinda 150s+ timeout'un sebebi buydu),
# ayrica settings allowlist'i kod calistirma/dosya yazma yetkisi verir (C2 sinifi).
_DANGEROUS_TOOLS = ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
                    "WebFetch", "WebSearch", "Task"]


def call_claude(prompt: str, timeout: int = 300) -> str | None:
    """All compile prompts go through tools/llm.py (Phase 0 T3): same tool-deny
    list, same llm_status breadcrumb, and BRAINLESS_LLM_PROVIDER can swap the
    backend for an OpenAI-compatible endpoint without touching this file."""
    global _CLAUDE_CALLS, _CLAUDE_FAILURES
    _CLAUDE_CALLS += 1
    try:
        result = run_prompt(prompt.replace("\x00", ""), timeout=timeout)
    except Exception as exc:  # never let one source kill the batch
        print(f"[llm err] {exc}", file=sys.stderr)
        result = None
    if not result:
        _CLAUDE_FAILURES += 1
        return None
    return result.strip() or None


_FENCE_OPEN_RE = re.compile(r"^```(?:markdown|md)?[ \t]*\n")


def clean_markdown_output(out: str) -> str:
    """Unwrap Claude CLI output that arrives inside a ```markdown fence.

    The CLI sometimes wraps the whole document in a fence, optionally with
    chatter before/after ("Here is the summary...", "If you want it saved...").
    Written to disk verbatim, that produced nested double-frontmatter files
    (the bug that corrupted 155 summaries, repaired 2026-08-24). Returns the
    inner document; if the shape is ambiguous, returns the input untouched.
    """
    text = out.strip()
    m = _FENCE_OPEN_RE.match(text)
    if m:
        inner = text[m.end():]
    else:
        # fence preceded by preamble chatter; only trust it if the fenced
        # document carries its own frontmatter, so dropping chatter loses nothing
        m2 = re.search(r"\n```(?:markdown|md)?[ \t]*\n(?=---\n)", "\n" + text)
        if not m2:
            return text
        inner = ("\n" + text)[m2.end():]
    close = inner.rfind("\n```")
    if close == -1:
        return text
    body, trailer = inner[:close], inner[close + 4:]
    if "```" in trailer or body.count("```") % 2 != 0:
        return text
    return body.strip()


def sources_digest(paths) -> str:
    """Kaynak kumesinin icerik parmak izi (yola gore sirali sha256, 12 hane).

    Neden mtime yerine hash: iki makineli git kurulumunda `git pull/reset/checkout`
    dosya mtime'ini ISLEM anina cekiyor, icerik anina degil. Sonuc: senkron sonrasi
    uretilmis dosya her zaman "taze" gorunuyor ve BIR DAHA hic yeniden derlenmiyor
    (2026-08-28: Visma ve Talentics dossier'lari bu yuzden 24 Agustos seed'inde
    donmus kalmisti). Hash makineden ve senkrondan bagimsizdir.
    """
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: str(x)):
        try:
            h.update(str(p).encode())
            h.update(p.read_bytes())
        except OSError:
            continue
    return h.hexdigest()[:12]


_DIGEST_RE = re.compile(r"^sources_hash:\s*([0-9a-f]{6,})\s*$", re.M)


def stored_digest(dst: Path) -> str | None:
    """Uretilmis dosyanin frontmatter'indaki sources_hash (yoksa None)."""
    if not dst.exists():
        return None
    try:
        head = dst.read_text(errors="replace")[:2000]
    except OSError:
        return None
    m = _DIGEST_RE.search(head)
    return m.group(1) if m else None


_ZK_RE = re.compile(r"^zk:\s*(\d{8,14})\s*$", re.M)


def stored_zk(dst: Path) -> str | None:
    """Return the note's existing zk slip-id (Zettelkasten permanent address), or None.

    The zk id is stable: once assigned it must survive every recompile, even though
    the rest of the file is regenerated. Callers reuse this so the address never churns.
    """
    if not dst.exists():
        return None
    try:
        head = dst.read_text(errors="replace")[:2000]
    except OSError:
        return None
    m = _ZK_RE.search(head)
    return m.group(1) if m else None


def write_compiled(dst: Path, body: str, sources) -> None:
    """Derlenmis ciktiyi sources_hash damgasiyla yaz (atomik: once tmp, sonra replace).

    Damgayi biz basiyoruz, model degil; boylece hash guvenilir. Atomik yazim,
    yarim kalmis bir cagrinin dosyayi bozmasini engeller.
    """
    text = clean_markdown_output(body).rstrip("\n") + "\n"
    digest = sources_digest(sources)
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            text = text[:end] + f"\nsources_hash: {digest}" + text[end:]
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(dst)


def needs_rebuild(src: Path, dst: Path, full: bool) -> bool:
    if full or not dst.exists():
        return True
    stored = stored_digest(dst)
    if stored is not None:                      # damgali dosyalarda hash otoritedir
        return stored != sources_digest([src])
    return src.stat().st_mtime > dst.stat().st_mtime


def iter_sources(roots):
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.md"):
            if _is_private(p):
                continue
            yield p


# ─── Phase: summaries ───────────────────────────────────────────
def summarize_file(src: Path, dry: bool, full: bool) -> bool:
    rel = src.relative_to(VAULT)
    slug = slugify(str(rel).replace("/", "_").rsplit(".", 1)[0])
    dst = WIKI / "summaries" / f"{slug}.md"
    if not needs_rebuild(src, dst, full):
        return False
    if dry:
        print(f"[dry] summarize {rel} → {dst.relative_to(VAULT)}")
        return True
    try:
        body = src.read_text()[:MAX_CHARS]
    except Exception as e:
        print(f"[skip] {rel}: {e}")
        return False
    prompt = f"""You are compiling a personal knowledge wiki.

{CROSS_LINK_RULE}

SOURCE FILE: {rel}
SOURCE CONTENT:
---
{body}
---

Produce a concise wiki summary. Output ONLY markdown with this structure:

---
lang: tr
summary_en: <2-3 sentence English summary>
source: {rel}
compiled_at: {datetime.now().isoformat(timespec='seconds')}
status: seed
---
# {src.stem}

{SUMMARY_HEADING}
<3-6 sentences in {lang_name()}>

## Key Points
- bullet
- bullet

## Bağlantılar
<!-- Leave empty. Article backlinks are injected automatically by the articles phase.
     If you must reference another note, use its PLAIN title only — e.g. [[Calm is contagious]].
     NEVER use a path like [[.wiki/articles/...]] or [[wiki/...]]: those do not resolve in Obsidian. -->

## Cross-effects (varsa)
<personal↔work etkileri>
"""
    out = call_claude(prompt)
    if not out:
        print(f"[FAIL] summary {src.relative_to(VAULT)}: uretilemedi", file=sys.stderr)
        return False
    write_compiled(dst, out, [src])
    print(f"[ok] {dst.relative_to(VAULT)}")
    return True


def phase_summaries(dry: bool, full: bool):
    n = 0
    for src in iter_sources(SUMMARY_SOURCES):
        if summarize_file(src, dry, full):
            n += 1
    print(f"phase summaries: {n} file(s)")


# ─── Phase: projects mirror ─────────────────────────────────────
def phase_projects(dry: bool, full: bool):
    # A "project" is an immediate subfolder of Work/ or Personal/ that already
    # contains a notes.md. We never auto-create notes.md, so non-project homes
    # (company area, Official Docs, health folders, area notes) are skipped.
    n = 0
    for base, cat in ((VAULT / "Work", "work"), (VAULT / "Personal", "personal")):
        if not base.exists():
            continue
        for proj_dir in sorted(base.iterdir()):
            if not proj_dir.is_dir():
                continue
            if _is_private(proj_dir):
                continue
            notes = proj_dir / "notes.md"
            if not notes.exists():
                continue
            dst = WIKI / "projects" / cat / f"{proj_dir.name}.md"
            if not needs_rebuild(notes, dst, full):
                continue
            if dry:
                print(f"[dry] mirror {notes.relative_to(VAULT)} → {dst.relative_to(VAULT)}")
                n += 1
                continue
            # Aggregate all files in the project folder
            blob = ""
            for p in sorted(proj_dir.rglob("*.md")):
                try:
                    blob += f"\n--- {p.relative_to(proj_dir)} ---\n" + p.read_text()[:8000]
                except Exception:
                    pass
            prompt = f"""Compile a status mirror for project '{proj_dir.name}' (category: {cat}).

{CROSS_LINK_RULE}

PROJECT FILES:
{blob[:40000]}

Produce ONLY markdown:

---
lang: tr
summary_en: <2-3 sentences>
source: {proj_dir.relative_to(VAULT)}/
compiled_at: {datetime.now().isoformat(timespec='seconds')}
status: seed
---
# {proj_dir.name}

{STATUS_HEADING}
## Açık İşler / Open Threads
## Kararlar / Decisions
## Bağlantılar
## Cross-effects (personal↔work)
"""
            out = call_claude(prompt)
            if not out:
                print(f"[FAIL] project {proj_dir.name}: uretilemedi", file=sys.stderr)
                continue
            write_compiled(dst, out, [notes])
            print(f"[ok] {dst.relative_to(VAULT)}")
            n += 1
    print(f"phase projects: {n} mirror(s)")


def _inject_backlink(summary_stem: str, article_slug: str):
    """Add a resolvable backlink to an article into a member summary's Bağlantılar."""
    sp = WIKI / "summaries" / f"{summary_stem}.md"
    if not sp.exists():
        return
    txt = sp.read_text()
    link = f"- [[{article_slug}]]"
    if link in txt:
        return
    if "## Bağlantılar" in txt:
        txt = txt.replace("## Bağlantılar\n", f"## Bağlantılar\n{link}\n", 1)
    else:
        txt = txt.rstrip() + f"\n\n## Bağlantılar\n{link}\n"
    sp.write_text(txt)


# ─── Phase: articles (concept clustering) ───────────────────────
def phase_articles(dry: bool, full: bool):
    summaries_dir = WIKI / "summaries"
    if not summaries_dir.exists():
        print("phase articles: no summaries yet")
        return
    index = []
    for s in sorted(summaries_dir.glob("*.md")):
        try:
            head = s.read_text()[:600]
            index.append(f"### {s.stem}\n{head}\n")
        except Exception:
            pass
    if not index:
        return
    if dry:
        print(f"[dry] cluster {len(index)} summaries into wiki/articles/")
        return
    prompt = f"""You have {len(index)} summary files in .wiki/summaries/. Cluster them into 8-20 concept articles.

{CROSS_LINK_RULE}

SUMMARIES:
{chr(10).join(index)[:60000]}

Output a JSON object: {{"articles": [{{"slug": "kebab-case", "title": "Title", "members": ["summary-stem-1", ...], "outline": "<short outline>"}}, ...]}}.
Output ONLY valid JSON, no preamble.
"""
    out = call_claude(prompt, timeout=300)
    if not out:
        return
    import json
    try:
        m = re.search(r"\{.*\}", out, re.S)
        data = json.loads(m.group(0)) if m else json.loads(out)
    except Exception as e:
        print(f"[articles] parse fail: {e}")
        return
    written = 0
    for art in data.get("articles", []):
        slug = art["slug"]
        members = art.get("members", [])
        # Skip empty-member articles — an article with no summaries behind it is
        # an orphan that just creates broken navigation.
        if not members:
            continue
        dst = WIKI / "articles" / f"{slug}.md"
        # Member links use the summary's basename so Obsidian resolves them.
        members_md = "\n".join(f"- [[{m}]]" for m in members)
        body = f"""---
lang: tr
summary_en: {art.get('title','')}
compiled_at: {datetime.now().isoformat(timespec='seconds')}
status: seed
---
# {art.get('title', slug)}

## Outline
{art.get('outline','')}

## Members
{members_md}
"""
        dst.write_text(body)
        written += 1
        print(f"[ok] {dst.relative_to(VAULT)}")
        # Bidirectional graph: inject a backlink to this article into each member
        # summary, so links resolve both ways (the connective tissue of the wiki).
        for m in members:
            _inject_backlink(m, slug)
    print(f"phase articles: {written} article(s)")


# ─── Phase: ideas (auto-derived) ────────────────────────────────
def phase_ideas(dry: bool, full: bool):
    blob = ""
    for d in (VAULT / "Thinking" / "Beliefs", VAULT / "Thinking" / "Decisions"):
        if not d.exists():
            continue
        for p in d.rglob("*.md"):
            try:
                blob += f"\n--- {p.relative_to(VAULT)} ---\n" + p.read_text()[:5000]
            except Exception:
                pass
    if not blob.strip():
        print("phase ideas: nothing to derive from")
        return
    if dry:
        print("[dry] derive ideas from beliefs+decisions")
        return
    prompt = f"""Auto-derive 5-15 atomic ideas from these beliefs and decisions. Each idea = one note.

{CROSS_LINK_RULE}

SOURCE:
{blob[:60000]}

Output JSON: {{"ideas": [{{"slug": "kebab", "title": "Title", "tr": "<TR body>", "en": "<EN summary>"}}, ...]}}
Output ONLY valid JSON.
"""
    out = call_claude(prompt, timeout=240)
    if not out:
        return
    import json
    try:
        m = re.search(r"\{.*\}", out, re.S)
        data = json.loads(m.group(0)) if m else json.loads(out)
    except Exception as e:
        print(f"[ideas] parse fail: {e}")
        return
    now = datetime.now()
    for i, idea in enumerate(data.get("ideas", [])):
        slug = idea["slug"]
        dst = WIKI / "ideas" / f"{slug}.md"
        # zk is the note's permanent slip-id: reuse the existing one so it never
        # churns on recompile; only mint a fresh one for a genuinely new idea.
        # Consecutive new ideas get consecutive minutes so ids in one run never collide.
        zk = stored_zk(dst) or (now + timedelta(minutes=i)).strftime("%Y%m%d%H%M")
        body = f"""---
lang: tr
summary_en: {idea.get('en','')}
zk: {zk}
compiled_at: {datetime.now().isoformat(timespec='seconds')}
status: seed
---
# {idea.get('title', slug)}

{idea.get('tr','')}
"""
        dst.write_text(body)
        print(f"[ok] {dst.relative_to(VAULT)}")
    print(f"phase ideas: {len(data.get('ideas', []))} idea(s)")


# ─── Phase: index ───────────────────────────────────────────────
def phase_index(dry: bool, full: bool):
    if dry:
        print("[dry] regenerate wiki/INDEX.md")
        return
    sections = []
    for sub, label in [("articles", "Articles"), ("entities", "Entities"),
                       ("projects/work", "Projects · Work"),
                       ("projects/personal", "Projects · Personal"),
                       ("ideas", "Ideas"), ("moc", "Maps of Content"),
                       ("digests", "Digests"), ("summaries", "Summaries")]:
        d = WIKI / sub
        if not d.exists():
            continue
        items = sorted(d.rglob("*.md"))
        if not items:
            continue
        sections.append(f"## {label}\n")
        for p in items:
            # Basename wikilink so Obsidian resolves it (slugs are unique).
            sections.append(f"- [[{p.stem}]]")
        sections.append("")
    body = f"""---
lang: tr
summary_en: Auto-generated index of the LLM-owned wiki.
compiled_at: {datetime.now().isoformat(timespec='seconds')}
---
# Wiki INDEX

_Auto-generated by tools/compile_resources.py. Do not edit by hand._

""" + "\n".join(sections)
    (WIKI / "INDEX.md").write_text(body)
    print("[ok] wiki/INDEX.md")


# ─── Phase: entities (auto-maintained dossiers) ─────────────────
ENTITY_REGISTRY = VAULT / "_Agent-Context" / "entities.md"
# Varlik kaynagi taranan alanlar: toplanti korpusu, gunluk yakalama, digest'ler,
# ve insan proje alanlari (private guard her dosyada ayrica uygulanir).
ENTITY_SOURCE_ROOTS = [
    VAULT / "Inbox" / "Spiky",
    VAULT / "Thinking" / "Daily",
    VAULT / ".wiki" / "digests",
    VAULT / "Work",
    VAULT / "Personal",
]
ENTITY_MAX_SOURCES = 18   # en yeni N eslesme (maliyet siniri)
TASKS_FILE = VAULT / "_Agent-Context" / "TASKS.md"


def parse_entity_registry():
    """entities.md -> [(ad, tip, [takma adlar])]. Yorum/bos satir atlanir."""
    out = []
    if not ENTITY_REGISTRY.exists():
        return out
    for line in ENTITY_REGISTRY.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "|" not in s:
            continue
        parts = [p.strip() for p in s.split("|")]
        name = parts[0]
        etype = parts[1] if len(parts) > 1 and parts[1] else "entity"
        aliases = [a.strip() for a in (parts[2].split(",") if len(parts) > 2 else []) if a.strip()]
        if name:
            out.append((name, etype, aliases))
    return out


def _entity_matches(terms):
    """Terimlerden birini iceren kaynak dosyalari (private haric), en yeni once."""
    low_terms = [_nfc(t).casefold() for t in terms if t]
    hits = []
    for root in ENTITY_SOURCE_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*.md"):
            if _is_private(p) or p.name.startswith("."):
                continue
            try:
                text = _nfc(p.read_text(errors="replace")).casefold()
            except Exception:
                continue
            if any(t in text for t in low_terms):
                hits.append(p)
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[:ENTITY_MAX_SOURCES]


def _entity_tasks(terms):
    """TASKS.md'de bu varligi anan acik maddeler."""
    if not TASKS_FILE.exists():
        return []
    low = [t.casefold() for t in terms if t]
    out = []
    for line in TASKS_FILE.read_text().splitlines():
        s = line.strip()
        if s.startswith("- [ ]") and any(t in s.casefold() for t in low):
            out.append(s)
    return out[:15]


def phase_entities(dry: bool, full: bool):
    n = 0
    for name, etype, aliases in parse_entity_registry():
        terms = [name] + aliases
        dst = WIKI / "entities" / f"{name}.md"
        sources = _entity_matches(terms)
        if not sources:
            continue
        # Bayatlik testi hash ile (mtime git senkronunda yalan soyluyor, bkz.
        # sources_digest). Damgasiz eski seed'ler otomatik yeniden derlenir.
        if not (full or stored_digest(dst) != sources_digest(sources)):
            continue
        if dry:
            print(f"[dry] entity {name} ({etype}) <- {len(sources)} kaynak")
            n += 1
            continue
        blob = ""
        for p in sources:
            try:
                blob += f"\n--- {p.relative_to(VAULT)} ---\n" + p.read_text()[:5000]
            except Exception:
                pass
        tasks = _entity_tasks(terms)
        task_block = "\n".join(tasks) if tasks else "(TASKS.md'de acik madde yok)"
        alias_note = f"Takma adlar/yazim varyantlari (transkripsiyon hatasi olarak duzelt): {', '.join(aliases)}" if aliases else ""
        prompt = f"""'{name}' ({etype}) icin kendini gunceleyen bir varlik dosyasi (dossier) derle.
Kaynaklardaki bu kisi/sirket/proje ile ilgili her seyi tek canli sayfada topla.

{CROSS_LINK_RULE}

KURALLAR:
- SADECE kaynaklardaki bilgiyi kullan; uydurma yok. Emin olmadigin yeri yazma.
- {alias_note}
- Tarihli etkileşimleri "Zaman Çizgisi" altinda kronolojik ver (kaynak dosya adiyla).
- Iliskili kisi/sirket/proje/toplanti icin [[wikilink]] kullan.
- Em dash / en dash KULLANMA.
- SADECE markdown dondur.

TASKS.md ACIK MADDELER (bu varlikla ilgili):
{task_block}

KAYNAKLAR:
{blob[:40000]}

Ciktinin sablonu:

---
lang: tr
summary_en: <2-3 sentence English gist>
type: entity
entity_type: {etype}
compiled_at: {datetime.now().isoformat(timespec='seconds')}
status: seed
---
# {name}

## Özet
## Zaman Çizgisi
## Açık Maddeler
## İlişkiler
## Kaynaklar
"""
        out = call_claude(prompt)
        if not out and len(sources) > 4:
            # Tek deneme yetmezse yariya indirilmis kaynakla bir kez daha dene;
            # aksi halde bir timeout o varligi kalici bosluk birakiyor.
            print(f"[retry] {name} (kaynak {len(sources)} -> {len(sources)//2})")
            half = sources[:len(sources) // 2]
            small = "".join(f"\n--- {p.relative_to(VAULT)} ---\n" + p.read_text()[:3000]
                            for p in half)
            out = call_claude(prompt.split("KAYNAKLAR:")[0] + "KAYNAKLAR:\n" + small[:20000],
                              timeout=240)
            if out:
                sources = half          # damga gercekten kullanilan kumeyi yansitsin
        if not out:
            # Basarisiz varligi ADIYLA bildir: sessiz bosluk, bayat dosyayi
            # "guncel" sanmaya yol aciyordu. Mevcut dosyaya DOKUNULMAZ.
            print(f"[FAIL] entity {name}: uretilemedi, mevcut dosya korundu", file=sys.stderr)
            continue
        write_compiled(dst, out, sources)
        print(f"[ok] {dst.relative_to(VAULT)}")
        n += 1
    print(f"phase entities: {n} dossier")


# ─── Main ───────────────────────────────────────────────────────
PHASES = {
    "summaries": phase_summaries,
    "projects": phase_projects,
    "entities": phase_entities,
    "articles": phase_articles,
    "ideas": phase_ideas,
    "index": phase_index,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full-rebuild", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=list(PHASES.keys()))
    args = ap.parse_args()

    phases = [args.only] if args.only else list(PHASES.keys())
    for ph in phases:
        print(f"\n=== phase: {ph} ===")
        PHASES[ph](args.dry_run, args.full_rebuild)

    if not args.dry_run:
        print(f"\n=== claude: {_CLAUDE_CALLS} call(s), {_CLAUDE_FAILURES} failure(s) ===")
        # Loud signal: a silent compile-to-empty is exactly what hid the broken
        # pipeline for weeks. Alarm if calls were made but most/all failed.
        if _CLAUDE_CALLS and _CLAUDE_FAILURES >= max(1, _CLAUDE_CALLS // 2):
            msg = f"compile_resources: {_CLAUDE_FAILURES}/{_CLAUDE_CALLS} claude calls failed"
            print(f"[WARN] {msg}", file=sys.stderr)
            try:
                subprocess.run(["osascript", "-e",
                                f'display notification "{msg}" with title "brainless"'],
                               check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            sys.exit(1)


if __name__ == "__main__":
    main()
