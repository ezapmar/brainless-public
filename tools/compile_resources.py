#!/usr/bin/env python3
"""
compile_resources.py: human-tree → .wiki compiler.

Walks the human-owned homes and compiles .wiki/ artifacts via Claude CLI:
- Library/, Inbox/, Thinking/Daily/, Personal/, the company area (PROFILE.md) → .wiki/summaries/<slug>.md
- Work/<P>/notes.md, Personal/<P>/notes.md → .wiki/projects/{work,personal}/<P>.md
- Assigns summaries to concepts (_Agent-Context/concepts.md) and updates
  .wiki/concepts/<slug>.md in place (see tools/concepts.py)
- Auto-derives .wiki/ideas/ from Thinking/Beliefs + Thinking/Decisions
- Regenerates .wiki/INDEX.md

Incremental by default: skips files where summary mtime >= source mtime.
Use --full-rebuild to force.

CRITICAL: cross-link personal↔work effects (rule in _Agent-Context/PROFILE.md).

Usage:
  python3 tools/compile_resources.py                  # incremental
  python3 tools/compile_resources.py --full-rebuild   # rebuild all
  python3 tools/compile_resources.py --dry-run        # show plan
  python3 tools/compile_resources.py --only summaries # phase: summaries|concept-assign|concepts|projects|entities|ideas|index
  python3 tools/compile_resources.py --only concept-propose  # one-shot: propose a starting concept set
  python3 tools/compile_resources.py --only concept-migrate  # one-off: articles -> concepts
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless"))
WIKI = VAULT / ".wiki"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import run_prompt
from owner_profile import LANG, lang_name, output_lang_directive, CROSS_LINK_RULE  # noqa: E402
from owner_profile import COMPANY_AREA, GENERIC_PRIVATE_SEGMENTS, PRIVATE_SEGMENTS as PROFILE_PRIVATE_SEGMENTS  # noqa: E402
from owner_profile import PRIVATE_NAME_PARTS as PROFILE_PRIVATE_NAME_PARTS  # noqa: E402
from i18n import t, t_list  # noqa: E402
import concepts as C  # noqa: E402
import output_guard  # noqa: E402

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
# NFC normalisation is MANDATORY: macOS returns file names in NFD while the
# string literals in this file are NFC; without normalising, accented folder
# names (people's names, Turkish letters) slip past the guard and sensitive data
# leaks into .wiki (the 2026-08-28 audit found 88 files on GitHub for this reason).
def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


PRIVATE_SEGMENTS = {_nfc(s) for s in (*GENERIC_PRIVATE_SEGMENTS, *PROFILE_PRIVATE_SEGMENTS)}
PRIVATE_SUFFIXES = (" - Health",)            # "<name> - Health" folders
# Sensitive name fragments: the current language's list merged with English (locale data).
PRIVATE_NAME_PARTS = tuple(_nfc(p).casefold() for p in
                           (*t_list("compile_resources.private_name_parts"), *PROFILE_PRIVATE_NAME_PARTS))
# Full sub-path matches (sensitive areas whose segment names are too generic on their own).
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
SUMMARY_HEADING = t("compile_resources.summary_heading")
STATUS_HEADING = t("compile_resources.status_heading")
LINKS_HEADING = t("compile_resources.links_heading")


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s/]+", "-", s)
    return s[:100]


_CLAUDE_CALLS = 0
_CLAUDE_FAILURES = 0
_COUNTS = {}   # per-phase work done this run, for the RUNLOG line (tools/run_log.py)

# Wall-clock budget. The phases run in a fixed order and make serial LLM calls
# of up to 300s each, so a backlog at the front ate the whole night: the nightly
# wrapper's hard timeout killed the run mid-phase (Sep 15, 17, 19, 21, 2026) and
# every cheap phase behind it, the index included, never ran at all. With a
# budget the run stops itself between items, still reaches the index, and says
# what is left for tomorrow. None = no budget, which is what a manual run gets.
_DEADLINE = None
_BUDGET_NOTED = set()


def out_of_time(phase: str, *, need: int = 0) -> bool:
    """True when the budget is spent. `need` reserves the seconds a phase's own
    call would take, so we never start a call that the budget cannot finish."""
    if _DEADLINE is None:
        return False
    if time.monotonic() + need < _DEADLINE:
        return False
    if phase not in _BUDGET_NOTED:
        _BUDGET_NOTED.add(phase)
        print(f"[budget] out of time in phase {phase}: the remainder is left "
              f"for the next run", file=sys.stderr)
    return True


# Untrusted content (source file bodies) enters the compile prompts, so tool use
# must be off. If it is not, the model can drift into agentic mode and get stuck
# (this was the cause of the 150s+ timeouts on rich Work sources),
# and the settings allowlist would grant code execution / file writes (class C2).
_DANGEROUS_TOOLS = ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
                    "WebFetch", "WebSearch", "Task"]


def call_claude(prompt: str, timeout: int = 300, *, concept: bool = False,
                aliases: bool = False) -> str | None:
    """All compile prompts go through tools/llm.py (Phase 0 T3): same tool-deny
    list, same llm_status breadcrumb, and BRAINLESS_LLM_PROVIDER can swap the
    backend for an OpenAI-compatible endpoint without touching this file."""
    global _CLAUDE_CALLS, _CLAUDE_FAILURES
    _CLAUDE_CALLS += 1
    try:
        if aliases:
            result = run_prompt(prompt.replace("\x00", ""), timeout=timeout, lane="compile-aliases")
        elif concept:
            result = run_prompt(prompt.replace("\x00", ""), timeout=timeout, lane="compile-concept")
        else:
            result = run_prompt(prompt.replace("\x00", ""), timeout=timeout, lane="compile")
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


def sources_digest(paths, *, scope="") -> str:
    """Content fingerprint of a source set (sha256 over path-sorted files, 12 hex digits).

    Why a hash instead of mtime: in a two-machine git setup, `git pull/reset/checkout`
    sets the file mtime to the moment of the OPERATION, not of the content. Result:
    after a sync the generated file always looks "fresh" and is NEVER recompiled again
    (2026-08-28: two company dossiers stayed frozen at the 24 August seed
    for this reason). A hash is independent of machine and sync.
    """
    h = hashlib.sha256()
    if scope:
        h.update(scope.encode() + b"\0")
    for p in sorted(paths, key=lambda x: str(x)):
        try:
            h.update(str(p).encode())
            h.update(p.read_bytes())
        except OSError:
            continue
    return h.hexdigest()[:12]


_DIGEST_RE = re.compile(r"^sources_hash:\s*([0-9a-f]{6,})\s*$", re.M)


def stored_digest(dst: Path) -> str | None:
    """The sources_hash in the generated file's frontmatter (None when absent)."""
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


def write_compiled(dst: Path, body: str, sources, *, scope="") -> bool:
    """Write the compiled output with a sources_hash stamp (atomic: tmp first, then replace).

    We stamp it ourselves, not the model, so the hash is trustworthy. The atomic
    write keeps a half-finished call from corrupting the file. Output that is the
    model talking about its tools, or a page wrapped around a copy of itself
    (tools/output_guard.py), is refused: the existing page stays, unstamped
    output never lands, and the next run tries again. False when refused.
    """
    text = clean_markdown_output(body).rstrip("\n") + "\n"
    bad = output_guard.problems(text)
    if bad:
        print(f"[reject] {dst.relative_to(VAULT)}: {'; '.join(bad)}", file=sys.stderr)
        return False
    digest = sources_digest(sources, scope=scope)
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            text = text[:end] + f"\nsources_hash: {digest}" + text[end:]
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(dst)
    return True


def needs_rebuild(src: Path, dst: Path, full: bool) -> bool:
    if full or not dst.exists():
        return True
    stored = stored_digest(dst)
    if stored is not None:                      # for stamped files the hash is the authority
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
# Promoted chat logs (tools/chat_import.py) record the owner thinking, so the
# useful extraction is rarely the assistant's answer.
CHAT_RULE = """
THIS SOURCE IS ONE OF THE OWNER'S OWN PAST AI CONVERSATIONS. Build the summary
around what the owner was trying to work out and what they concluded, not
around the assistant's explanations. Note where the owner changed position and
what changed it. Keep the conversation date on every conclusion: the owner's
view may have moved since.
"""

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
    chat_rule = CHAT_RULE if str(rel).startswith("Library/Chats/") else ""
    prompt = f"""You are compiling a personal knowledge wiki.

{CROSS_LINK_RULE}
{chat_rule}
SOURCE FILE: {rel}
SOURCE CONTENT:
---
{body}
---

Produce a concise wiki summary. Output ONLY markdown with this structure:

---
lang: {LANG}
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

{LINKS_HEADING}
<!-- Leave empty. Concept backlinks are injected automatically by the concepts phase.
     If you must reference another note, use its PLAIN title only, e.g. [[Calm is contagious]].
     NEVER use a path like [[.wiki/articles/...]] or [[wiki/...]]: those do not resolve in Obsidian. -->

{t("compile_resources.cross_effects_heading")}
<personal↔work effects>

{output_lang_directive()}
"""
    out = call_claude(prompt)
    if not out:
        print(f"[FAIL] summary {src.relative_to(VAULT)}: not produced", file=sys.stderr)
        return False
    if not write_compiled(dst, out, [src]):
        return False
    print(f"[ok] {dst.relative_to(VAULT)}")
    return True


def phase_summaries(dry: bool, full: bool):
    n = 0
    for src in iter_sources(SUMMARY_SOURCES):
        if not dry and out_of_time("summaries"):
            break
        if summarize_file(src, dry, full):
            n += 1
    _COUNTS["summaries"] = n
    print(f"phase summaries: {n} file(s)")


# ─── Phase: projects mirror ─────────────────────────────────────
def _project_dst(cat: str, name: str, dry: bool = False) -> tuple[Path, str | None]:
    """A mirror that shares its name with an entity page is named '<Name> (project)'
    and links to the entity: two files with one stem make every [[Name]] link
    ambiguous (wiki_dedupe 'clash'), and the dossier is the page a link means.
    An existing bare-name mirror is renamed so its hash, and the work, survive."""
    dst = WIKI / "projects" / cat / f"{name}.md"
    if name not in {n for n, _, _ in parse_entity_registry()}:
        return dst, None
    new = dst.with_name(f"{name} (project).md")
    if dst.exists() and not new.exists() and not dry:
        r = subprocess.run(["git", "-C", str(VAULT), "mv", str(dst), str(new)], capture_output=True)
        if r.returncode != 0:
            dst.replace(new)
    return new, name


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
            if not notes.exists() or _is_private(notes):
                continue
            if not dry and out_of_time("projects"):
                break
            sources = sorted(iter_sources([proj_dir]))
            dst, entity = _project_dst(cat, proj_dir.name, dry)
            if entity and not dry:
                _inject_link(dst, entity)
            # Version the dependency policy so old notes-only mirrors are also
            # rebuilt when their other inputs are now excluded for privacy.
            scope = "project-files-v1"
            if not full and stored_digest(dst) == sources_digest(sources, scope=scope):
                continue
            if dry:
                print(f"[dry] mirror {notes.relative_to(VAULT)} → {dst.relative_to(VAULT)}")
                n += 1
                continue
            # Read exactly the permitted files used by the freshness check.
            blob = ""
            try:
                for p in sources:
                    blob += f"\n--- {p.relative_to(proj_dir)} ---\n" + p.read_text()[:8000]
            except (OSError, UnicodeError) as e:
                print(f"[FAIL] project {proj_dir.name}: {e}", file=sys.stderr)
                continue
            prompt = f"""Compile a status mirror for project '{proj_dir.name}' (category: {cat}).

{CROSS_LINK_RULE}

PROJECT FILES:
{blob[:40000]}

Produce ONLY markdown:

---
lang: {LANG}
summary_en: <2-3 sentences>
source: {proj_dir.relative_to(VAULT)}/
compiled_at: {datetime.now().isoformat(timespec='seconds')}
status: seed
---
# {proj_dir.name}

{STATUS_HEADING}
{t("compile_resources.open_threads_heading")}
{t("compile_resources.decisions_heading")}
{LINKS_HEADING}
## Cross-effects (personal↔work)

{output_lang_directive()}
"""
            out = call_claude(prompt)
            if not out:
                print(f"[FAIL] project {proj_dir.name}: not produced", file=sys.stderr)
                continue
            if not write_compiled(dst, out, sources, scope=scope):
                continue
            if entity:
                _inject_link(dst, entity)       # a rewrite drops injected links
            print(f"[ok] {dst.relative_to(VAULT)}")
            n += 1
    print(f"phase projects: {n} mirror(s)")


def _inject_backlink(summary_stem: str, article_slug: str):
    """Add a resolvable backlink to an article into a member summary's links section."""
    _inject_link(WIKI / "summaries" / f"{summary_stem}.md", article_slug)


def _inject_link(sp: Path, target: str, note: str = "") -> bool:
    """Add `- [[target]]` under the page's links heading, with `: note` after
    it when given (an approved dreaming link carries its one-sentence reason).
    True when added."""
    if not sp.exists():
        return False
    txt = sp.read_text()
    if f"- [[{target}]]" in txt:
        return False
    link = f"- [[{target}]]" + (f": {note}" if note else "")
    # Accept the heading in the current language or in English (existing vaults).
    heading = next((h for h in t_list("compile_resources.links_heading") if h in txt), None)
    if heading:
        txt = txt.replace(f"{heading}\n", f"{heading}\n{link}\n", 1)
    else:
        txt = txt.rstrip() + f"\n\n{LINKS_HEADING}\n{link}\n"
    sp.write_text(txt)
    return True


# ─── Phase: concepts (pages that update in place) ───────────────
# See tools/concepts.py for why this layer replaced the articles phase.
CONCEPT_REGISTRY = VAULT / "_Agent-Context" / "concepts.md"
CONCEPTS_DIR = WIKI / "concepts"
CONCEPT_ARCHIVE = WIKI / "_archive" / "articles"
CONCEPT_ASSIGN_BATCH = 80       # summaries per assignment call (summary_en lines only)
CONCEPT_MAX_PER_RUN = 6         # concept pages updated per run, most stale first
CONCEPT_MAX_DELTA = 8           # new summaries folded into one page per call
CONCEPT_SOURCE_CHARS = 4000     # per summary, inside the update prompt


def concept_rows():
    return C.parse_registry(C.read_page(CONCEPT_REGISTRY))


def _active_rev(rows) -> str:
    """Fingerprint of the active concept set. A summary stamped with an older
    one is assigned again, so a concept the owner activates collects the
    summaries that were filed before it existed. Proposals do not change it,
    or every batch that proposes would trigger a full reassignment."""
    active = sorted(r["slug"] for r in rows if r["status"] == "active")
    return hashlib.sha256("\n".join(active).encode()).hexdigest()[:8]


def summary_catalogue():
    """In-scope summaries as dicts: stem, path, source, summary_en, hash, concepts, rev."""
    out = []
    d = WIKI / "summaries"
    if not d.exists():
        return out
    for p in sorted(d.glob("*.md")):
        text = C.read_page(p)
        fm, _ = C.split_frontmatter(text)
        source = C.fm_value(fm, "source") or ""
        if not C.in_scope(source, COMPANY_AREA):
            continue
        out.append({
            "stem": p.stem, "path": p, "source": source,
            "summary_en": (C.fm_value(fm, "summary_en") or "")[:400],
            "hash": C.fm_value(fm, "sources_hash") or "",
            "concepts": C.read_concepts_key(text),
            "rev": C.fm_value(fm, "concepts_rev") or "",
        })
    return out


def _stamp_concepts(path: Path, slugs, rev: str):
    text = C.read_page(path)
    text = C.set_fm_key(text, "concepts", json.dumps(sorted(set(slugs)), ensure_ascii=False))
    text = C.set_fm_key(text, "concepts_rev", rev)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _append_proposals(props, catalogue):
    """Add new proposals to the registry as `proposed` rows. Returns the rows added."""
    by_stem = {s["stem"]: s for s in catalogue}
    rows = []
    for p in props:
        personal = any(C.is_personal(by_stem[m]["source"]) for m in p["members"] if m in by_stem)
        rows.append({"slug": p["slug"], "title": p["title"], "aliases": p["aliases"],
                     "status": "proposed", "sensitivity": "personal" if personal else "",
                     "scope": p["scope"]})
    if rows:
        text = C.read_page(CONCEPT_REGISTRY).rstrip("\n")
        text += "\n" + "\n".join(C.registry_row(r) for r in rows) + "\n"
        CONCEPT_REGISTRY.write_text(text)
    return rows


def _ping_proposals(rows):
    """One Buzz line when the compiler proposes concepts. Personal ones are only
    counted: their titles alone can say too much about family or health."""
    if not rows:
        return
    public = [r["title"] for r in rows if r["sensitivity"] != "personal"]
    personal = len(rows) - len(public)
    lines = [t("compile_resources.concept_ping_intro").format(n=len(rows))]
    lines += [f"- {title}" for title in public]
    if personal:
        lines.append(t("compile_resources.concept_ping_personal").format(n=personal))
    lines.append(t("compile_resources.concept_ping_howto"))
    key = "concept-proposals:" + hashlib.sha256(
        "\n".join(sorted(r["slug"] for r in rows)).encode()).hexdigest()[:16]
    try:
        from buzz_delivery import send
        send("thinking", "\n".join(lines), key=key)
        print(f"[buzz] {len(rows)} concept proposal(s) announced")
    except Exception as e:  # a failed ping must never cost the compile
        print(f"[buzz] concept proposal ping failed: {e}", file=sys.stderr)


def _assign_prompt(rows, batch, *, propose_only=False):
    reg = "\n".join(f"- {r['slug']}: {r['title']}"
                    + (f" (aka {', '.join(r['aliases'])})" if r["aliases"] else "")
                    + (f". {r['scope']}" if r["scope"] else "")
                    for r in rows if r["status"] != "retired") or "(none yet)"
    items = "\n".join(f"- {s['stem']} [{s['source']}]: {s['summary_en']}" for s in batch)
    task = ("Propose concepts only; leave assign empty." if propose_only else
            "Assign each summary to the existing concepts it genuinely informs (0 to 3). "
            "Most summaries inform none; an empty list is the normal answer.")
    return f"""You maintain the concept layer of a personal knowledge wiki. A concept is one
idea, method or recurring question that several sources inform, named so that it
can be explained without referring back to any single source.

EXISTING CONCEPTS:
{reg}

SUMMARIES (stem [source]: gist):
{items}

{task}
Propose a NEW concept only when at least two of these summaries share an idea
that no existing concept covers. Do not propose people, companies, meetings,
deals or one-off events; those are entities or projects, not concepts. Titles
are singular and in the language the owner writes in; slugs are kebab-case ASCII.

Output ONLY JSON:
{{"assign": {{"<stem>": ["<slug>", ...]}},
 "proposals": [{{"slug": "...", "title": "...", "aliases": ["..."], "scope": "<one line>", "members": ["<stem>", ...]}}]}}
"""


def phase_concept_assign(dry: bool, full: bool, *, propose_only: bool = False):
    rows = concept_rows()
    rev = _active_rev(rows)
    slugs = {r["slug"] for r in rows if r["status"] != "retired"}
    catalogue = summary_catalogue()
    todo = catalogue if (full or propose_only) else \
        [s for s in catalogue if s["concepts"] is None or s["rev"] != rev]
    label = "concept-propose" if propose_only else "concept-assign"
    if not todo:
        print(f"phase {label}: nothing to assign")
        return
    if dry:
        print(f"[dry] {label}: {len(todo)} summaries in {-(-len(todo) // CONCEPT_ASSIGN_BATCH)} call(s)")
        return
    added, assigned = [], 0
    for i in range(0, len(todo), CONCEPT_ASSIGN_BATCH):
        if out_of_time(label, need=240):
            break
        batch = todo[i:i + CONCEPT_ASSIGN_BATCH]
        stems = {s["stem"] for s in batch}
        out = call_claude(_assign_prompt(concept_rows(), batch, propose_only=propose_only),
                          timeout=300, concept=True)
        assign, props = C.parse_assignment(out or "", stems, slugs)
        if not assign:
            print(f"[FAIL] {label}: batch {i // CONCEPT_ASSIGN_BATCH + 1} unparsable, left for next run",
                  file=sys.stderr)
            continue
        fresh = C.new_proposals(props, concept_rows())
        added += _append_proposals(fresh, catalogue)
        for p in fresh:                  # a proposal's evidence is assigned to it right away
            slugs.add(p["slug"])
            for m in p["members"]:
                assign[m] = sorted(set(assign.get(m, [])) | {p["slug"]})
        if propose_only:
            continue
        for s in batch:
            _stamp_concepts(s["path"], assign.get(s["stem"], []), rev)
            assigned += bool(assign.get(s["stem"]))
    _ping_proposals(added)
    print(f"phase {label}: {len(todo)} summaries seen, {assigned} assigned, "
          f"{len(added)} concept(s) proposed")


def phase_concept_propose(dry: bool, full: bool):
    """One-shot: read every in-scope gist and propose a starting set of concepts."""
    phase_concept_assign(dry, full, propose_only=True)


def _date_key(source: str) -> str:
    """Oldest source first, so a later source updates what an earlier one said
    rather than every old source arriving as a contradiction to the newest."""
    m = re.search(r"(20\d\d)[-.](\d\d)(?:[-.](\d\d))?", source)
    return "".join(g or "00" for g in m.groups()) if m else "99999999"


def _concept_prompt(row, old, members, prior, personal):
    blob = ""
    for s in members:
        body = C.read_page(s["path"])[:CONCEPT_SOURCE_CHARS]
        blob += f"\n--- [[{s['stem']}]] (source: {s['source']}) ---\n{body}\n"
    existing = old or "(no page yet: write the first version from these sources)"
    prior_block = (f"\nPRIOR ARTICLE (context from the retired articles layer; may be outdated, "
                   f"cite only what the sources support):\n{prior[:6000]}\n" if prior else "")
    aliases = json.dumps(row["aliases"], ensure_ascii=False)
    return f"""You maintain one concept page in a personal knowledge wiki: '{row['title']}'.
Scope: {row['scope'] or '(none given)'}

The page explains one idea and gets better as sources arrive. It is not a list
of what each source said. Fold the NEW SOURCES into the EXISTING PAGE.

{CROSS_LINK_RULE}

RULES (each exists because breaking it destroys what the page is for):
- Integrate, do not append summaries. Update the explanation and claims so the page reads as one account.
- Never overwrite a disagreement. When a new source disagrees with a claim on the page, keep both under
  "{t('compile_resources.concept_contested_heading').lstrip('# ')}", each attributed ([[source]]) and dated,
  and say what would settle it. The history of what was believed and why is the asset.
- Never resolve a contradiction by picking the newer source. Recency is not evidence.
- When a source shows an old claim is out of date (a changed fact, not a disagreement), move it to
  "{t('compile_resources.concept_superseded_heading').lstrip('# ')}" as: ~~old claim~~ superseded YYYY-MM: why ([[source]]).
  Never delete a claim, and keep every existing ~~struck~~ line exactly as it is.
- Keep every [[link]] already on the page.
- Every claim cites its source as [[summary-stem]] with a date when one is known, and one confidence
  label: {' | '.join(C.CONFIDENCE)}. Self-reported numbers keep that label forever.
- Only record a relationship the source states. Do not infer.
- Link the first mention of any entity or concept with [[wikilink]].
- Use ONLY the sources and the existing page. Nothing from general knowledge.
- Do NOT use em dashes or en dashes.
- {output_lang_directive()}
{prior_block}
EXISTING PAGE:
{existing}

NEW SOURCES:
{blob[:40000]}

Return ONLY the full updated page in this shape:

---
lang: {LANG}
summary_en: <2-3 sentence English gist of the idea>
type: concept
aliases: {aliases}
compiled_at: {datetime.now().isoformat(timespec='seconds')}
status: seed
---
# {row['title']}

{t("compile_resources.concept_explanation_heading")}
{t("compile_resources.concept_position_heading")}
{t("compile_resources.concept_contested_heading")}
{t("compile_resources.concept_superseded_heading")}
{t("compile_resources.concept_open_heading")}
{t("compile_resources.concept_related_heading")}
{t("compile_resources.cross_effects_heading")}
"""


def _finish_concept(out, row, members_map, personal):
    text = C.strip_dashes(clean_markdown_output(out)).rstrip("\n") + "\n"
    if not text.startswith("---\n"):
        # A sentence of preamble before the page ("Here is the updated page:")
        # cost a whole concept its night on 2026-09-22. Drop it when a real
        # frontmatter block follows; otherwise the validator refuses the page.
        m = re.search(r"(?m)^---\n(?=[a-z_]+:)", text)
        if not m:
            return text
        text = text[m.start():]
    for key, value in (("type", "concept"),
                       ("aliases", json.dumps(row["aliases"], ensure_ascii=False)),
                       ("members", C.members_value(members_map))):
        text = C.set_fm_key(text, key, value)
    if personal:
        text = C.set_fm_key(text, "sensitivity", "personal")
    return text


def phase_concepts(dry: bool, full: bool):
    rows = [r for r in concept_rows() if r["status"] == "active"]
    catalogue = summary_catalogue()
    work = []
    for row in rows:
        members = [s for s in catalogue if row["slug"] in (s["concepts"] or [])]
        dst = CONCEPTS_DIR / f"{row['slug']}.md"
        old = C.read_page(dst)
        integrated = {} if full else C.read_members(old)
        todo = C.delta({s["stem"]: s["hash"] for s in members}, integrated)
        if not todo:
            continue
        fm, _ = C.split_frontmatter(old)
        work.append((C.fm_value(fm, "compiled_at") or "", row, members, todo, dst, old, integrated))
    work.sort(key=lambda w: w[0])            # never compiled, then oldest compile first
    n = 0
    for _, row, members, todo, dst, old, integrated in work[:CONCEPT_MAX_PER_RUN]:
        by_stem = {s["stem"]: s for s in members}
        batch = sorted((by_stem[s] for s in todo), key=lambda s: _date_key(s["source"]))
        batch = batch[:CONCEPT_MAX_DELTA]
        if dry:
            print(f"[dry] concept {row['slug']} <- {len(batch)} of {len(todo)} new summaries")
            n += 1
            continue
        if out_of_time("concepts", need=300):
            break
        if full:
            old = ""
        personal = row["sensitivity"] == "personal" or any(C.is_personal(s["source"]) for s in members)
        prior = "" if old else C.read_page(CONCEPT_ARCHIVE / f"{row['slug']}.article.md")
        text = None
        for attempt in (batch, batch[:max(1, len(batch) // 2)]):
            out = call_claude(_concept_prompt(row, old, attempt, prior, personal), concept=True)
            if not out:
                continue
            stamped = dict(integrated, **{s["stem"]: s["hash"] for s in attempt})
            candidate = _finish_concept(out, row, stamped, personal)
            problems = C.validate_update(old, candidate)
            if not problems:
                text, batch = candidate, attempt
                break
            print(f"[reject] concept {row['slug']}: {'; '.join(problems)}", file=sys.stderr)
            if len(attempt) == 1:
                break
        if text is None:
            print(f"[FAIL] concept {row['slug']}: not produced, existing page kept", file=sys.stderr)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".md.tmp")
        tmp.write_text(text)
        tmp.replace(dst)
        # Backlinks make the page reachable from every source behind it.
        for s in batch:
            _inject_backlink(s["stem"], row["slug"])
        print(f"[ok] {dst.relative_to(VAULT)} (+{len(batch)}, {len(todo) - len(batch)} left)")
        n += 1
    left = len(work) - n
    _COUNTS["concepts"] = n
    print(f"phase concepts: {n} page(s) updated" + (f", {left} waiting" if left > 0 else ""))


def migrate_articles(dry: bool):
    """One-off: the articles become active concepts under the same slugs, so the
    [[slug]] backlinks already in summaries keep resolving. Their member lists
    seed the assignment; the article itself is archived as prior context."""
    src_dir = WIKI / "articles"
    if not src_dir.exists():
        print("migrate: no .wiki/articles")
        return
    rows = concept_rows()
    known = {r["slug"] for r in rows}
    catalogue = {s["stem"]: s for s in summary_catalogue()}
    new_rows, seeds = [], {}
    for p in sorted(src_dir.glob("*.md")):
        text = C.read_page(p)
        fm, body = C.split_frontmatter(text)
        m = re.search(r"^# (.+)$", body, re.M)
        title = m.group(1).strip() if m else p.stem
        members = re.findall(r"^- \[\[([^\]|]+)\]\]", body.split("## Members", 1)[-1], re.M)
        for stem in members:
            if stem in catalogue:
                seeds.setdefault(stem, set()).add(p.stem)
        if p.stem not in known:
            new_rows.append({"slug": p.stem, "title": title, "aliases": [], "status": "active",
                             "sensitivity": "", "scope": (C.fm_value(fm, "summary_en") or "")[:160]
                             .replace("|", "/")})
        print(f"[migrate] {p.stem}: {len(members)} member(s), "
              f"{sum(1 for s in members if s in catalogue)} in scope")
    if dry:
        return
    if new_rows:
        text = C.read_page(CONCEPT_REGISTRY).rstrip("\n")
        CONCEPT_REGISTRY.write_text(text + "\n" + "\n".join(C.registry_row(r) for r in new_rows) + "\n")
    rev = _active_rev(concept_rows())
    for stem, slugs in seeds.items():
        s = catalogue[stem]
        _stamp_concepts(s["path"], set(s["concepts"] or []) | slugs, rev)
    CONCEPT_ARCHIVE.mkdir(parents=True, exist_ok=True)
    log = WIKI / "_archive" / "LOG.md"
    stamp = datetime.now().strftime("%Y-%m-%d")
    lines = []
    for p in sorted(src_dir.glob("*.md")):
        dst = CONCEPT_ARCHIVE / f"{p.stem}.article.md"
        subprocess.run(["git", "-C", str(VAULT), "mv", str(p), str(dst)], check=False)
        if p.exists():                  # not tracked: a plain move keeps it anyway
            p.replace(dst)
        lines.append(f"| {stamp} | concept-migrate | {p.relative_to(VAULT)} | "
                     f"{dst.relative_to(VAULT)} | article became concept [[{p.stem}]] |")
    if lines and log.exists():
        text = log.read_text()
        head, sep, rest = text.partition("|---")
        if sep:
            first_nl = rest.find("\n")
            text = head + sep + rest[:first_nl + 1] + "\n".join(lines) + "\n" + rest[first_nl + 1:]
        else:
            text = text.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
        log.write_text(text)
    try:
        src_dir.rmdir()
    except OSError:
        pass
    print(f"migrate: {len(new_rows)} registry row(s), {len(seeds)} summary seed(s), "
          f"{len(lines)} article(s) archived")


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
    if out_of_time("ideas", need=240):
        return
    prompt = f"""Auto-derive 5-15 atomic ideas from these beliefs and decisions. Each idea = one note.

{CROSS_LINK_RULE}

SOURCE:
{blob[:60000]}

Output JSON: {{"ideas": [{{"slug": "kebab", "title": "Title", "body": "<idea body in {lang_name()}>", "en": "<English summary>"}}, ...]}}
Output ONLY valid JSON. For the body: {output_lang_directive()}
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
lang: {LANG}
summary_en: {idea.get('en','')}
zk: {zk}
compiled_at: {datetime.now().isoformat(timespec='seconds')}
status: seed
---
# {idea.get('title', slug)}

{idea.get('body','')}
"""
        dst.write_text(body)
        print(f"[ok] {dst.relative_to(VAULT)}")
    print(f"phase ideas: {len(data.get('ideas', []))} idea(s)")


# ─── Phase: index ───────────────────────────────────────────────
# The index is what an agent reads first: one line per page says whether the
# page is worth opening, so a question costs three cheap reads instead of a
# full-text sweep (Second Brain OS, "index.md and log.md"). Small sections sit
# in INDEX.md itself; the long ones (summaries by home, digests, filed queries)
# get a topic index of their own in .wiki/_index/, one level down and no more,
# because summaries of summaries drift from what they describe.
INDEX_DIR = WIKI / "_index"
INDEX_INLINE = ("concepts", "entities", "projects/work", "projects/personal", "ideas", "moc")
INDEX_LABELS = {"concepts": "Concepts", "entities": "Entities", "projects/work": "Projects, work",
                "projects/personal": "Projects, personal", "ideas": "Ideas", "moc": "Maps of Content"}
INDEX_DESC_CHARS = 150


def index_line(p: Path) -> str:
    """'- [[stem]]: first sentence of summary_en (aka aliases)'."""
    fm, _ = C.split_frontmatter(C.read_page(p))
    desc = C.fm_value(fm, "summary_en") or ""
    desc = C.strip_dashes(re.split(r"(?<=[.!?])\s", desc.strip(), maxsplit=1)[0])
    if len(desc) > INDEX_DESC_CHARS:
        desc = desc[:INDEX_DESC_CHARS].rsplit(" ", 1)[0] + "..."
    aliases = []
    raw = C.fm_value(fm, "aliases") or ""
    if raw.startswith("["):
        try:
            aliases = [str(a) for a in json.loads(raw)]
        except ValueError:
            aliases = []
    aka = f" (aka {', '.join(aliases[:4])})" if aliases else ""
    return f"- [[{p.stem}]]: {desc}{aka}" if desc else f"- [[{p.stem}]]{aka}"


def _topic_of(p: Path) -> str:
    """Topic index a long-tail page belongs to."""
    rel = p.relative_to(WIKI).parts
    if rel[0] == "digests":
        return "Filed queries" if len(rel) > 2 and rel[1] == "queries" else "Daily digests"
    fm, _ = C.split_frontmatter(C.read_page(p))
    src = (C.fm_value(fm, "source") or "").strip("/")
    depth = len(COMPANY_AREA.split("/")) + 1 if src.startswith(COMPANY_AREA + "/") else 2
    parts = src.split("/")[:depth]
    if parts and parts[-1].endswith(".md"):
        parts = parts[:-1]
    return "Summaries, " + ("/".join(parts) or "other")


def phase_index(dry: bool, full: bool):
    if dry:
        print("[dry] regenerate wiki/INDEX.md and wiki/_index/")
        return
    stamp = datetime.now().isoformat(timespec='seconds')
    head = f"lang: {LANG}\ncompiled_at: {stamp}\n"
    sections = []
    for sub in INDEX_INLINE:
        d = WIKI / sub
        items = sorted(d.glob("*.md")) if d.exists() else []
        if items:
            sections += [f"## {INDEX_LABELS[sub]}", ""] + [index_line(p) for p in items] + [""]
    topics = {}
    for sub in ("summaries", "digests"):
        d = WIKI / sub
        for p in sorted(d.rglob("*.md")) if d.exists() else []:
            topics.setdefault(_topic_of(p), []).append(p)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    wanted = set()
    sections += ["## Topic indexes", ""]
    for topic, pages in sorted(topics.items()):
        stem = "index-" + C.slugify(topic.replace("/", " "))
        wanted.add(f"{stem}.md")
        lines = [f"---\n{head}summary_en: Topic index for {topic}, one line per page.\n---",
                 f"# {topic}", "", "_Auto-generated by tools/compile_resources.py. Back to [[INDEX]]._", ""]
        order = reversed(pages) if topic in ("Daily digests", "Filed queries") else pages
        lines += [index_line(p) for p in order]
        (INDEX_DIR / f"{stem}.md").write_text("\n".join(lines) + "\n")
        sections.append(f"- [[{stem}]]: {topic} ({len(pages)} pages)")
    for old in INDEX_DIR.glob("*.md"):          # a topic that emptied out
        if old.name not in wanted:
            old.unlink()
    body = (f"---\n{head}summary_en: Auto-generated index of the LLM-owned wiki, one line per page.\n---\n"
            "# Wiki INDEX\n\n_Auto-generated by tools/compile_resources.py. Do not edit by hand. "
            "Read this first, open the pages that fit, follow links from there._\n\n"
            + "\n".join(sections) + "\n")
    (WIKI / "INDEX.md").write_text(body)
    print(f"[ok] wiki/INDEX.md + {len(wanted)} topic index(es)")


# ─── Phase: entities (auto-maintained dossiers) ─────────────────
ENTITY_REGISTRY = VAULT / "_Agent-Context" / "entities.md"
# Areas scanned for entity sources: meeting corpus, daily capture, digests,
# and the human project areas (the private guard is applied per file as well).
ENTITY_SOURCE_ROOTS = [
    VAULT / "Inbox" / "Spiky",
    VAULT / "Thinking" / "Daily",
    VAULT / ".wiki" / "digests",
    VAULT / "Work",
    VAULT / "Personal",
]
ENTITY_MAX_SOURCES = 18   # newest N matches (cost cap)
TASKS_FILE = VAULT / "_Agent-Context" / "TASKS.md"


ENTITY_TYPES = {"person", "company", "project"}


def parse_entity_registry():
    """entities.md -> [(name, type, [aliases])]. Comment/blank lines are skipped."""
    out = []
    if not ENTITY_REGISTRY.exists():
        return out
    for line in ENTITY_REGISTRY.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "|" not in s:
            continue
        parts = [p.strip() for p in s.split("|")]
        name = parts[0]
        etype = parts[1] if len(parts) > 1 else ""
        # The file's own prose explains the format with a "|" in it; only a row
        # whose second column is a known type is an entity, the rest is comment.
        if not name or etype not in ENTITY_TYPES:
            continue
        aliases = [a.strip() for a in (parts[2].split(",") if len(parts) > 2 else []) if a.strip()]
        out.append((name, etype, aliases))
    return out


_ENTITY_CORPUS = None


def _entity_corpus():
    """Every candidate source read once per run as (path, casefolded text).

    The registry holds dozens of entities and the roots hold ~90 MB of notes;
    walking and reading them per entity multiplied the I/O by the entity count."""
    global _ENTITY_CORPUS
    if _ENTITY_CORPUS is None:
        corpus = []
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
                corpus.append((p, text))
        _ENTITY_CORPUS = corpus
    return _ENTITY_CORPUS


def _entity_matches(terms):
    """Source files containing any of the terms (private excluded), newest first."""
    low_terms = [_nfc(t).casefold() for t in terms if t]
    hits = [p for p, text in _entity_corpus() if any(t in text for t in low_terms)]
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[:ENTITY_MAX_SOURCES]


def _entity_tasks(terms):
    """Open items in TASKS.md that mention this entity."""
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
        if not dry and out_of_time("entities"):
            break
        terms = [name] + aliases
        dst = WIKI / "entities" / f"{name}.md"
        sources = _entity_matches(terms)
        if not sources:
            continue
        # Staleness test by hash (mtime lies after a git sync, see sources_digest).
        # Old unstamped seeds are recompiled automatically.
        if not (full or stored_digest(dst) != sources_digest(sources)):
            continue
        if dry:
            print(f"[dry] entity {name} ({etype}) <- {len(sources)} source(s)")
            n += 1
            continue
        blob = ""
        for p in sources:
            try:
                blob += f"\n--- {p.relative_to(VAULT)} ---\n" + p.read_text()[:5000]
            except Exception:
                pass
        tasks = _entity_tasks(terms)
        task_block = "\n".join(tasks) if tasks else "(no open items in TASKS.md)"
        alias_note = f"Aliases/spelling variants (fix them as transcription errors): {', '.join(aliases)}" if aliases else ""
        timeline_heading = t("compile_resources.entity_timeline_heading").lstrip("# ")
        prompt = f"""Compile a self-updating entity file (dossier) for '{name}' ({etype}).
Gather everything in the sources about this person/company/project into one living page.

{CROSS_LINK_RULE}

RULES:
- Use ONLY information from the sources; no invention. Leave out anything you are not sure about.
- {alias_note}
- List dated interactions chronologically under "{timeline_heading}" (with the source file name).
- Use [[wikilink]] for related people/companies/projects/meetings.
- Do NOT use em dashes or en dashes.
- Return ONLY markdown.
- {output_lang_directive()}

TASKS.md OPEN ITEMS (related to this entity):
{task_block}

SOURCES:
{blob[:40000]}

Output template:

---
lang: {LANG}
summary_en: <2-3 sentence English gist>
type: entity
entity_type: {etype}
compiled_at: {datetime.now().isoformat(timespec='seconds')}
status: seed
---
# {name}

{t("compile_resources.entity_summary_heading")}
{t("compile_resources.entity_timeline_heading")}
{t("compile_resources.entity_open_items_heading")}
{t("compile_resources.entity_relations_heading")}
{t("compile_resources.entity_sources_heading")}
"""
        out = call_claude(prompt)
        if not out and len(sources) > 4:
            # If one attempt is not enough, try once more with the sources halved;
            # otherwise a timeout leaves that entity as a permanent gap.
            print(f"[retry] {name} (sources {len(sources)} -> {len(sources)//2})")
            half = sources[:len(sources) // 2]
            small = "".join(f"\n--- {p.relative_to(VAULT)} ---\n" + p.read_text()[:3000]
                            for p in half)
            out = call_claude(prompt.split("SOURCES:")[0] + "SOURCES:\n" + small[:20000],
                              timeout=240)
            if out:
                sources = half          # the stamp must reflect the set actually used
        if not out:
            # Report the failed entity BY NAME: a silent gap led to mistaking a stale
            # file for a "current" one. The existing file is NOT touched.
            print(f"[FAIL] entity {name}: not produced, existing file kept", file=sys.stderr)
            continue
        if not write_compiled(dst, out, sources):
            continue
        print(f"[ok] {dst.relative_to(VAULT)}")
        n += 1
    print(f"phase entities: {n} dossier")


# ─── Phase: aliases ──────────────────────────────────────────────
# Search matches words, and the owner asks in words the page does not use:
# "moving office" for a project called Workspace, "ISO 27001" for a security page. The guide's
# first fix for that is aliases, before any embedding index. They live in
# _Agent-Context/aliases.md, one row per page, editable by hand; the model only
# fills rows for pages that have none, and every run stamps the rows into the
# pages' frontmatter (a recompile drops them, the next run puts them back).
ALIAS_FILE = VAULT / "_Agent-Context" / "aliases.md"
ALIAS_FOLDERS = ("entities", "projects", "concepts", "ideas")
ALIAS_BATCH = 30
ALIAS_HEADER = """# Aliases

Other names for wiki pages: the words someone would use to ask about the page
without using its title. `tools/compile_resources.py` (phase aliases) proposes a
row for every new entity, project, concept and idea page, and stamps the rows
into each page's `aliases:` frontmatter, which is what search and Obsidian
links match. Edit freely: a row you change is never regenerated. Delete a row
to have it proposed again.

Format: `page stem | alias one, alias two, ...`

## Rows
"""


def alias_pages():
    return [p for f in ALIAS_FOLDERS if (WIKI / f).exists() for p in sorted((WIKI / f).rglob("*.md"))]


def parse_alias_rows(text: str) -> dict[str, list[str]]:
    rows = {}
    for line in text.split("## Rows", 1)[-1].splitlines():
        if "|" not in line or line.lstrip().startswith(("#", "Format")):
            continue
        stem, _, names = line.partition("|")
        if stem.strip():
            rows[stem.strip()] = [n.strip() for n in names.split(",") if n.strip()]
    return rows


def _registry_aliases() -> dict[str, list[str]]:
    out = {name: aliases for name, _, aliases in parse_entity_registry()}
    for row in concept_rows():
        out.setdefault(row["slug"], []).extend([row["title"], *row["aliases"]])
    return out


def _alias_prompt(batch):
    items = "\n".join(f"- {p.stem} [{p.parent.name}]: {(C.fm_value(C.split_frontmatter(C.read_page(p))[0], 'summary_en') or '')[:300]}"
                      for p in batch)
    return f"""For each wiki page below, give 3 to 5 aliases: the other names and short
phrases the owner would use when asking about it without using its title.
The owner writes Turkish and English; give both where natural. Include common
misspellings of names and the plain-language description of the thing
(for a project called "Workspace": "ofis taşınması", "office move").
Do NOT use generic words ("proje", "toplantı", "strategy"), and do NOT use the
name of a different person, company or project.
Do NOT put personal data in an alias: no birth dates, ages, ID or registration
numbers, phone numbers, addresses, salaries or amounts. An alias is a name.
Do NOT use em dashes or en dashes.

PAGES (stem [folder]: gist):
{items}

Output ONLY JSON: {{"<stem>": ["alias", ...], ...}}
"""


# Aliases land in frontmatter, the index and search, so personal data in one
# spreads everywhere. The prompt forbids it; this is the boundary behind the
# prompt (the first run proposed a birth date as an alias for a person).
_ALIAS_DATA = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{5,}|\+?\d[\d ]{8,}\d|[₺$€£]\s?\d|\d\s?(?:tl|try|gbp|usd|eur)\b",
                         re.IGNORECASE)


def clean_alias(a: str) -> str | None:
    a = C.strip_dashes(str(a)).replace("|", "/").strip()
    # Standard names are names ("ISO 27001", "BS 7858"), not data.
    probe = re.sub(r"\b(?:ISO|IEC|BS|EN|SOC)\s?[\d:/-]+", "", a, flags=re.IGNORECASE)
    if not a or len(a) > 60 or _ALIAS_DATA.search(probe):
        return None
    return a


def phase_aliases(dry: bool, full: bool):
    pages = alias_pages()
    rows = parse_alias_rows(C.read_page(ALIAS_FILE))
    todo = [p for p in pages if full or p.stem not in rows]
    if dry:
        print(f"[dry] aliases: {len(todo)} page(s) to propose, {len(pages)} to stamp")
        return
    added = 0
    for i in range(0, len(todo), ALIAS_BATCH):
        if out_of_time("aliases", need=180):
            break
        batch = todo[i:i + ALIAS_BATCH]
        data = C.parse_json_object(call_claude(_alias_prompt(batch), timeout=240, aliases=True) or "")
        if not data:
            print(f"[FAIL] aliases: batch {i // ALIAS_BATCH + 1} unparsable", file=sys.stderr)
            continue
        for p in batch:
            got = data.get(p.stem)
            if isinstance(got, list):
                rows[p.stem] = [a for a in map(clean_alias, got) if a][:5]
                added += 1
    if added:
        body = ALIAS_HEADER + "".join(f"{stem} | {', '.join(names)}\n" for stem, names in sorted(rows.items()))
        ALIAS_FILE.write_text(body)
    reg = _registry_aliases()
    stamped = 0
    for p in pages:
        names = []
        # Rows are hand-editable, so the data filter applies to them too.
        for n in [*map(clean_alias, rows.get(p.stem, [])), *reg.get(p.stem, [])]:
            if n and n.casefold() != p.stem.casefold() and n not in names:
                names.append(n)
        if not names:
            continue
        text = C.read_page(p)
        value = json.dumps(names, ensure_ascii=False)
        fm, _ = C.split_frontmatter(text)
        if fm and C.fm_value(fm, "aliases") != value:
            p.write_text(C.set_fm_key(text, "aliases", value))
            stamped += 1
    _COUNTS["aliases"] = added
    print(f"phase aliases: {added} proposed, {stamped} page(s) stamped")


# ─── Phase: link (deterministic linker, no model) ───────────────
# The guide's "linker" role: first mentions of a known entity or active concept
# become links, so a summary joins the graph through what it is about. Summaries
# are written with an empty links section and used to connect only when an
# article or concept linked back, which left 454 pages with no working link.
# It adds links and nothing else: no page is created, no text is rewritten.
LINK_PAGES = ("summaries", "digests/queries")
LINK_MAX_PER_PAGE = 8
_WORD = "0-9A-Za-zÇĞİÖŞÜçğıöşü_"


def link_targets():
    """[(page stem, compiled regex)] for entities with a page and active concepts."""
    out = []
    for name, _, aliases in parse_entity_registry():
        if (WIKI / "entities" / f"{name}.md").exists():
            out.append((name, [name, *aliases]))
    for row in concept_rows():
        if row["status"] == "active" and (CONCEPTS_DIR / f"{row['slug']}.md").exists():
            out.append((row["slug"], [row["title"], *row["aliases"]]))
    compiled = []
    for stem, names in out:
        # Short names ("ACME" stays, "Ozan" stays) need an exact word; under
        # three letters is noise.
        alts = sorted({_nfc(n) for n in names if len(n) >= 3}, key=len, reverse=True)
        if alts:
            rx = re.compile(rf"(?<![{_WORD}])(?:{'|'.join(re.escape(a) for a in alts)})(?![{_WORD}])",
                            re.IGNORECASE)
            compiled.append((stem, rx, {a.casefold() for a in alts}))
    return compiled


_NEXT_NAME = re.compile(r"\s+([A-ZÇĞİÖŞÜ][a-zçğıöşü]+)")


def mentions(rx, names, body: str) -> bool:
    """A real mention, not the first half of someone else's full name: "Deniz"
    is an alias of one person, and "Deniz Kaya" is another person."""
    for m in rx.finditer(body):
        nxt = _NEXT_NAME.match(body, m.end())
        if nxt and f"{m.group(0)} {nxt.group(1)}".casefold() not in names \
                and not any(n.startswith(f"{m.group(0)} {nxt.group(1)}".casefold()) for n in names):
            continue
        return True
    return False


def phase_link(dry: bool, full: bool):
    targets = link_targets()
    pages = [p for sub in LINK_PAGES if (WIKI / sub).exists() for p in sorted((WIKI / sub).glob("*.md"))]
    added = touched = 0
    for p in pages:
        _, body = C.split_frontmatter(C.read_page(p))
        body = _nfc(body)
        hits = [stem for stem, rx, names in targets if stem != p.stem and mentions(rx, names, body)
                and f"[[{stem}]]" not in body and f"[[{stem}|" not in body][:LINK_MAX_PER_PAGE]
        if not hits:
            continue
        if dry:
            added += len(hits)
            touched += 1
            continue
        n = sum(_inject_link(p, stem) for stem in hits)
        added += n
        touched += bool(n)
    approved = 0 if dry else apply_link_registry()
    _COUNTS["links"] = added + approved
    print(f"phase link: {added} link(s) on {touched} page(s), {approved} from the registry"
          + (" [dry]" if dry else ""))


# Links the owner approved in Buzz #dreaming (tools/dreaming.py) live in a
# registry, not only in the pages: a summary is rewritten whole when its source
# changes, and the link would go with it. Every compile puts them back.
LINK_REGISTRY = VAULT / "_Agent-Context" / "links.md"
_REG_ROW = re.compile(r"^\|\s*(?P<a>[^|]+?)\s*\|\s*(?P<b>[^|]+?)\s*\|\s*(?P<d>[a-z-]+)\s*\|"
                      r"\s*(?P<reason>[^|]*?)\s*\|\s*(?P<date>[0-9-]+)\s*\|\s*$")


def link_registry(path: Path = LINK_REGISTRY) -> list[dict]:
    """Rows of the registry: a, b (vault-relative paths), decision, reason, date."""
    try:
        text = path.read_text()
    except OSError:
        return []
    return [m.groupdict() for m in map(_REG_ROW.match, text.splitlines()) if m and m["a"] != "a"]


def apply_link_registry(rows=None) -> int:
    """Both directions of every approved link or merge, where missing. A merge
    is linked too until the owner merges: the two pages should know of each other."""
    added = 0
    for row in link_registry() if rows is None else rows:
        if row["d"] not in ("link", "merge"):
            continue
        for x, y in ((row["a"], row["b"]), (row["b"], row["a"])):
            added += _inject_link(VAULT / x, Path(y).stem, row["reason"])
    return added


# ─── Main ───────────────────────────────────────────────────────
# Concepts run right after summaries and before the heavier dossiers, with a
# per-run cap, so they are never starved the way the articles phase was.
PHASES = {
    "summaries": phase_summaries,
    "concept-assign": phase_concept_assign,
    "concepts": phase_concepts,
    "projects": phase_projects,
    "entities": phase_entities,
    "aliases": phase_aliases,
    "link": phase_link,
    "ideas": phase_ideas,
    "index": phase_index,
}


# Run only when asked for by name: a one-shot proposal pass and the articles migration.
ON_DEMAND = {
    "concept-propose": phase_concept_propose,
    "concept-migrate": lambda dry, full: migrate_articles(dry),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full-rebuild", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=list(PHASES.keys()) + list(ON_DEMAND.keys()))
    ap.add_argument("--budget-seconds", type=int,
                    default=int(os.environ.get("BRAINLESS_COMPILE_BUDGET", "0")),
                    help="wall-clock budget in seconds; 0 = unlimited. The run "
                         "stops between items and still rebuilds the index.")
    args = ap.parse_args()

    global _DEADLINE
    if args.budget_seconds > 0 and not args.dry_run:
        _DEADLINE = time.monotonic() + args.budget_seconds
        print(f"=== budget: {args.budget_seconds}s ===")

    phases = [args.only] if args.only else list(PHASES.keys())
    for ph in phases:
        print(f"\n=== phase: {ph} ===")
        {**PHASES, **ON_DEMAND}[ph](args.dry_run, args.full_rebuild)

    if not args.dry_run:
        if _BUDGET_NOTED:
            print(f"\n=== budget spent in: {', '.join(sorted(_BUDGET_NOTED))}; "
                  f"run again to drain the rest ===")
        print(f"\n=== claude: {_CLAUDE_CALLS} call(s), {_CLAUDE_FAILURES} failure(s) ===")
        print("RUNLOG " + " ".join(f"{k}={v}" for k, v in
                                   {**_COUNTS, "calls": _CLAUDE_CALLS, "failures": _CLAUDE_FAILURES}.items()))
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
