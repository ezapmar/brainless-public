"""Concept pages: the pure half of the concept layer.

A summary records what one source said. A concept page records what the vault
knows about one idea, and it improves as sources arrive instead of growing a
new summary beside the old ones. That is the whole point of the LLM wiki
pattern, and the reason this layer exists: the old articles phase re-clustered
from scratch every run, saw 16% of the summaries and kept no memory.

The rules that make a concept page trustworthy live here as code, not only as
prompt text, because a prompt instruction is a preference and a validator is a
boundary. A page that silently drops a superseded claim or a cited source has
lost the history of what was believed and why, and nothing would error.

compile_resources.py owns the LLM calls and the phases; everything here is
deterministic so it can be tested without a model.
"""
import json
import re
import unicodedata
from pathlib import Path

STATUSES = {"active", "proposed", "retired"}
CONFIDENCE = ("primary", "secondary", "self-reported", "unverified")

# Summaries whose source sits under these path parts never feed a concept.
# People and deals belong to the entity layer, stale Spiky copies to the archive,
# and finance-pack material to the Finance Data tiers in AGENT-RULES-PRIVATE.
EXCLUDE_PARTS = (
    "about people", "library/people", "hiring",
    "inbox/", "archive/",
    "investor relations", "investor update", "yatırımcı",
)
# Finance is excluded inside the company area only: a finance book in Library is
# knowledge, a finance pack in the company area is Finance Data.
COMPANY_EXCLUDE = ("finance/",)
# Knowledge roots. The company area comes from PROFILE.md and is added by the caller.
INCLUDE_ROOTS = ("Library/", "Thinking/", "Personal/")

# Family, health and legal-family material may feed a concept, but the concept
# is then marked personal: no titles or claims on Buzz, never exported. The
# generic markers live here; names come from PROFILE.md (personal_markers).
GENERIC_PERSONAL_MARKERS = ("health", "sağlık", "saglik", "hukuk", "legal", "pets/",
                            "aile", "family", "medical", "tıbbi", "personal/finance")
try:
    from owner_profile import PERSONAL_MARKERS as _OWNER_MARKERS
except ImportError:  # the module is importable on its own in tests
    _OWNER_MARKERS = ()
PERSONAL_MARKERS = tuple(unicodedata.normalize("NFC", m).casefold()
                         for m in (*GENERIC_PERSONAL_MARKERS, *_OWNER_MARKERS))

_DASHES = ("\u2014", "\u2013")


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


# ─── Registry ───────────────────────────────────────────────────
def parse_registry(text: str) -> list[dict]:
    """concepts.md -> rows. A row is `slug | Title | aliases | status | sensitivity | scope`.

    Like entities.md, the file explains its own format in prose with pipes in it,
    so only a line whose status column is a known status counts as a row."""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(">") or "|" not in s:
            continue
        parts = [p.strip() for p in s.split("|")]
        parts += [""] * (6 - len(parts))
        slug, title, aliases, status, sensitivity, scope = parts[:6]
        if not slug or status not in STATUSES or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
            continue
        rows.append({
            "slug": slug,
            "title": title or slug,
            "aliases": [a.strip() for a in aliases.split(",") if a.strip()],
            "status": status,
            "sensitivity": "personal" if sensitivity == "personal" else "",
            "scope": scope,
        })
    return rows


def registry_row(row: dict) -> str:
    return " | ".join([row["slug"], row["title"], ", ".join(row.get("aliases", [])),
                       row["status"], row.get("sensitivity", ""), row.get("scope", "")])


def set_status(text: str, slug: str, status: str) -> str:
    """Change one row's status in concepts.md, leaving every other line as it is.
    Raises KeyError when the slug has no row."""
    if status not in STATUSES:
        raise ValueError(status)
    out, hit = [], False
    for line in text.splitlines(keepends=True):
        parts = [p.strip() for p in line.split("|")]
        if not hit and len(parts) >= 4 and parts[0] == slug and parts[3] in STATUSES:
            parts += [""] * (6 - len(parts))
            row = {"slug": parts[0], "title": parts[1],
                   "aliases": [a.strip() for a in parts[2].split(",") if a.strip()],
                   "status": status, "sensitivity": parts[4], "scope": " | ".join(parts[5:]).strip(" |")}
            line = registry_row(row) + ("\n" if line.endswith("\n") else "")
            hit = True
        out.append(line)
    if not hit:
        raise KeyError(slug)
    return "".join(out)


def known_names(rows) -> set[str]:
    """Every slug, title and alias, casefolded, for deduping new proposals."""
    out = set()
    for r in rows:
        for n in [r["slug"], r["title"], *r["aliases"]]:
            out.add(_nfc(n).casefold())
            out.add(slugify(n))
    return out


def slugify(s: str) -> str:
    s = _nfc(s).casefold()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return re.sub(r"-{2,}", "-", s)[:80]


# ─── Scope ──────────────────────────────────────────────────────
def in_scope(source: str, company_area: str) -> bool:
    """True when a summary's source may feed a concept page."""
    if not source:
        return False
    src = _nfc(source)
    low = src.casefold()
    area = _nfc(company_area.rstrip("/") + "/")
    if any(p in low for p in EXCLUDE_PARTS):
        return False
    if any(low.startswith(area.casefold() + p) for p in COMPANY_EXCLUDE):
        return False
    roots = INCLUDE_ROOTS + (area,)
    return src.startswith(roots)


def is_personal(source: str) -> bool:
    low = _nfc(source).casefold()
    return any(m in low for m in PERSONAL_MARKERS)


# ─── Frontmatter ────────────────────────────────────────────────
_FM_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


def split_frontmatter(text: str) -> tuple[str, str]:
    m = _FM_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():]


def fm_value(fm: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", fm, re.M)
    if not m:
        return None
    v = m.group(1).strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


_YAML_INDICATORS = tuple("-?:,[]{}#&*!|>'\"%@`")


def yaml_scalar(value) -> str:
    """One frontmatter value that strict YAML (and Obsidian) parses back as the
    same string. Plain when it can be, double-quoted when a colon-space, a
    comment marker or a leading indicator would otherwise break the block.
    Newlines fold to one space (every reader here matches `key: value` per line);
    other whitespace is kept, since a `source:` path can hold a double space."""
    v = re.sub(r"[ \t]*[\r\n]+[ \t]*", " ", str(value)).strip()
    if (not v or v.startswith(_YAML_INDICATORS) or ": " in v or " #" in v
            or v.endswith(":")):
        return json.dumps(v, ensure_ascii=False)
    return v


def read_concepts_key(text: str) -> list[str] | None:
    """The `concepts:` list stamped on a summary. None = never assigned."""
    fm, _ = split_frontmatter(text)
    v = fm_value(fm, "concepts")
    if v is None:
        return None
    v = v.strip()
    if v.startswith("["):
        try:
            return [str(x) for x in json.loads(v)]
        except ValueError:
            return [x.strip().strip("'\"") for x in v.strip("[]").split(",") if x.strip()]
    return [v] if v else []


_FM_LINE_RE = re.compile(r"^([A-Za-z_][\w-]*):[ \t]+(\S.*?)[ \t]*$")
_BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?\d*$")


def quote_frontmatter(text: str) -> str:
    """Quote the top-level frontmatter values of a model-written page that would
    break strict YAML, the way yaml_scalar does for values we write ourselves.
    Lists, block scalars and values already quoted are left as they are, so a
    second pass changes nothing."""
    m = _FM_RE.match(text)
    if not m:
        return text
    out = []
    for line in m.group(1).split("\n"):
        lm = _FM_LINE_RE.match(line)
        if lm:
            key, val = lm.groups()
            quoted = len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'"
            if not (quoted or val[0] in "[{" or _BLOCK_SCALAR_RE.match(val)):
                line = f"{key}: {yaml_scalar(val)}"
        out.append(line)
    return text[:m.start(1)] + "\n".join(out) + text[m.end(1):]


def set_fm_key(text: str, key: str, value: str) -> str:
    """Replace or add one frontmatter line. A file without frontmatter gets one."""
    fm, body = split_frontmatter(text)
    line = f"{key}: {value}"
    if not fm and not text.startswith("---\n"):
        return f"---\n{line}\n---\n{text}"
    if re.search(rf"^{re.escape(key)}:", fm, re.M):
        fm = re.sub(rf"^{re.escape(key)}:.*$", line.replace("\\", "\\\\"), fm, count=1, flags=re.M)
    else:
        fm = fm + "\n" + line
    return f"---\n{fm}\n---\n{body}"


def read_members(text: str) -> dict[str, str]:
    """`members:` on a concept page: {summary_stem: sources_hash} at integration time."""
    fm, _ = split_frontmatter(text)
    v = fm_value(fm, "members")
    if not v:
        return {}
    try:
        data = json.loads(v)
    except ValueError:
        return {}
    return {str(k): str(h) for k, h in data.items()} if isinstance(data, dict) else {}


def members_value(members: dict[str, str]) -> str:
    return json.dumps(dict(sorted(members.items())), ensure_ascii=False)


# ─── Delta ──────────────────────────────────────────────────────
def delta(assigned: dict[str, str], integrated: dict[str, str]) -> list[str]:
    """Stems to integrate: assigned now, and either new or changed since the page last saw them.

    `assigned` and `integrated` map summary stem -> that summary's sources_hash."""
    return sorted(s for s, h in assigned.items() if integrated.get(s) != h)


# ─── LLM output parsing ─────────────────────────────────────────
def parse_json_object(out: str) -> dict | None:
    if not out:
        return None
    try:
        m = re.search(r"\{.*\}", out, re.S)
        data = json.loads(m.group(0) if m else out)
    except (ValueError, AttributeError):
        return None
    return data if isinstance(data, dict) else None


def parse_assignment(out: str, stems: set[str], slugs: set[str]) -> tuple[dict, list]:
    """Model output -> ({stem: [slugs]}, [proposals]).

    Unknown stems are dropped (the model cannot invent a summary); unknown slugs
    are dropped from assignments (proposals are the only way to add a concept).
    Stems the model skipped are assigned [] so they are not asked about again."""
    data = parse_json_object(out)
    if data is None:
        return {}, []
    raw = data.get("assign") if isinstance(data.get("assign"), dict) else {}
    assign = {}
    for stem in stems:
        got = raw.get(stem, [])
        if isinstance(got, str):
            got = [got]
        assign[stem] = sorted({g for g in got if isinstance(g, str) and g in slugs})
    proposals = []
    for p in data.get("proposals", []) or []:
        if not isinstance(p, dict) or not str(p.get("title", "")).strip():
            continue
        members = [m for m in p.get("members", []) if m in stems]
        proposals.append({
            "slug": slugify(p.get("slug") or p["title"]),
            "title": str(p["title"]).strip(),
            "aliases": [str(a).strip() for a in p.get("aliases", []) if str(a).strip()],
            "scope": str(p.get("scope", "")).strip().replace("|", "/"),
            "members": members,
        })
    return assign, proposals


# ─── Source filter ──────────────────────────────────────────────
# One document often reaches the vault as several files. Counting them as
# sources inflated concept proposals (25/09/2026: one book was three
# "sources"). These rules are deterministic on purpose. A magazine file is not
# a table of contents even when its frontmatter lists the articles: the 47
# checked on 25/09 were whole issues, 270 to 450 KB each.
BOOK_SUMMARY = "Summary.md"
READING_SHEET = "Fiche_de_Lecture.md"


def _norm(rel: str) -> str:
    return _nfc(str(rel)).replace("\\", "/")


def skip_reason(rel: str, exists) -> str | None:
    """Why a source file should not be compiled, or None. `exists(rel)` answers
    for a vault-relative path.

      raw-twin           X_raw.md beside X.md: the same text before cleanup
      reading-sheet      Fiche_de_Lecture.md beside Summary.md: a French rewrite
                         of the same book summary, framed by an old pipeline
    """
    rel = _norm(rel)
    folder, _, name = rel.rpartition("/")
    sib = (folder + "/") if folder else ""
    if name.endswith("_raw.md") and exists(sib + name[:-len("_raw.md")] + ".md"):
        return "raw-twin"
    if name == READING_SHEET and exists(sib + BOOK_SUMMARY):
        return "reading-sheet"
    return None


def source_group(rel: str, exists) -> str:
    """Files that are one document share a key. A folder holding a book summary
    is one book, whatever else sits in it, and so is `X.md` beside a folder `X/`
    that holds one (the converted book next to its summaries); otherwise X and
    X_raw are one."""
    rel = _norm(rel)
    folder, _, name = rel.rpartition("/")
    if folder and exists(folder + "/" + BOOK_SUMMARY):
        return folder + "/"
    beside = rel[:-len(".md")] if rel.endswith(".md") else ""
    if beside and exists(beside + "/" + BOOK_SUMMARY):
        return beside + "/"
    return (folder + "/" if folder else "") + re.sub(r"_raw\.md$", ".md", name)


def source_rank(rel: str) -> int:
    """Lower is the better stand-in for its group. A book summary covers the
    whole book, where the compiler reads only the first MAX_CHARS of the book
    itself; a cleaned file beats its raw conversion."""
    name = _norm(rel).rsplit("/", 1)[-1]
    if name == BOOK_SUMMARY:
        return 0
    if name == READING_SHEET:
        return 3
    return 2 if name.endswith("_raw.md") else 1


def source_count(stems) -> int:
    """Distinct sources behind summary stems: `X` and `X_raw` are one document
    converted twice, not two sources agreeing."""
    return len({re.sub(r"_raw$", "", s) for s in stems})


def new_proposals(proposals, rows, min_members: int = 2) -> list[dict]:
    """Proposals worth a registry row: not a known name, backed by enough summaries.

    One summary is a paragraph, not a concept; the guide's test for a page is
    whether the idea recurs across sources."""
    seen = known_names(rows)
    out = []
    for p in proposals:
        names = {p["slug"], _nfc(p["title"]).casefold(), slugify(p["title"]),
                 *(_nfc(a).casefold() for a in p["aliases"])}
        if names & seen or source_count(p["members"]) < min_members:
            continue
        seen |= names
        out.append(p)
    return out


# ─── Validator ──────────────────────────────────────────────────
_STRUCK_RE = re.compile(r"~~[^~\n]+~~")
_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def struck_claims(text: str) -> list[str]:
    return _STRUCK_RE.findall(text)


def cited_links(text: str) -> set[str]:
    _, body = split_frontmatter(text)
    return {m.strip() for m in _LINK_RE.findall(body)}


def validate_update(old: str, new: str, *, min_ratio: float = 0.7,
                    retracted: frozenset = frozenset()) -> list[str]:
    """Reasons the new page must not replace the old one (empty list = accept).

    The model rewrites the whole page, so everything the page promised to keep
    is checked mechanically: superseded claims, every cited page, the shape.
    `retracted` names withdrawn sources (tools/retract_source.py): their links
    must go, and only theirs may."""
    problems = []
    try:
        import output_guard
        problems += output_guard.problems(new)
    except ImportError:
        pass
    fm, body = split_frontmatter(new)
    if not fm:
        problems.append("no frontmatter")
    elif fm_value(fm, "summary_en") in (None, ""):
        problems.append("frontmatter lacks summary_en")
    if any(d in new for d in _DASHES):
        problems.append("contains an em or en dash")
    if not old:
        return problems
    lost_struck = [s for s in struck_claims(old) if s not in new]
    if lost_struck:
        problems.append(f"dropped {len(lost_struck)} superseded claim(s): {lost_struck[0][:60]}")
    kept = cited_links(new) & set(retracted)
    if kept:
        problems.append(f"still cites retracted {', '.join(sorted(kept)[:3])}")
    lost_links = sorted(cited_links(old) - cited_links(new) - set(retracted))
    if lost_links:
        problems.append(f"dropped {len(lost_links)} cited link(s): {', '.join(lost_links[:3])}")
    _, old_body = split_frontmatter(old)
    if old_body.strip() and len(body.strip()) < min_ratio * len(old_body.strip()):
        problems.append(f"shrank to {len(body.strip())}/{len(old_body.strip())} chars")
    return problems


def strip_dashes(text: str) -> str:
    """Last-resort repair for the one rule models break most: an em or en dash
    between words becomes a comma, a range dash becomes a hyphen."""
    text = re.sub("\\s*\u2014\\s*", ", ", text)
    text = re.sub("(\\d)\\s*\u2013\\s*(\\d)", r"\1-\2", text)
    return re.sub("\\s*\u2013\\s*", ", ", text)


def read_page(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""
