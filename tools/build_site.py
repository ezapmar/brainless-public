#!/usr/bin/env python3
"""Build the public site's generated pages from CHANGELOG.md.

Writes docs/whats-new.html (every release, newest first) and docs/feed.xml (Atom, one
entry per released version) so people can follow releases without watching GitHub.
docs/index.html is written by hand; this only restamps its site.css and site.js links
with a content hash, so a changed file gets a new URL past the Pages cache.

    python3 tools/build_site.py            # rebuild both files
    python3 tools/build_site.py --check    # exit 1 if the files are stale
    python3 tools/build_site.py --fragment docs/index.html   # print the page body for an artifact

The changelog format this reads: `## <version> (<YYYY-MM-DD>)` or `## Unreleased`,
an optional lead paragraph, then `- **Bold lead.** text` bullets that may wrap onto
indented lines. Inline `code`, **bold** and [links](url) are rendered; a relative link
is pointed at the file on GitHub.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
DOCS = ROOT / "docs"
REPO = "https://github.com/ezapmar/brainless-public"
SITE = "https://ezapmar.github.io/brainless-public"

HEADING = re.compile(r"^## (?P<version>\S+)(?: \((?P<date>\d{4}-\d{2}-\d{2})\))?\s*$")
BULLET = re.compile(r"^(?P<indent>\s*)[-*] (?P<text>.*)$")
NUMBERED = re.compile(r"^(?P<indent>\s*)\d+\. (?P<text>.*)$")



ASSET_LINK = re.compile(r'(href|src)="(site\.(?:css|js))(?:\?v=[0-9a-f]+)?"')


def asset_version() -> str:
    """Short hash of site.css and site.js, appended to their links so a changed file
    gets a new URL. GitHub Pages serves everything with a ten-minute cache."""
    h = hashlib.sha1()
    for name in ("site.css", "site.js"):
        h.update((DOCS / name).read_bytes())
    return h.hexdigest()[:8]


def stamp(text: str, version: str) -> str:
    return ASSET_LINK.sub(lambda m: f'{m.group(1)}="{m.group(2)}?v={version}"', text)


class Release:
    def __init__(self, version: str, date: str | None):
        self.version = version
        self.date = date
        self.lead: list[str] = []
        self.items: list[str] = []
        self.ordered = False

    @property
    def anchor(self) -> str:
        return "v" + self.version.replace(".", "-") if self.version != "Unreleased" else "unreleased"


def parse(text: str) -> list[Release]:
    releases: list[Release] = []
    current: Release | None = None
    in_lead = False
    for raw in text.splitlines():
        line = raw.rstrip()
        m = HEADING.match(line)
        if m:
            current = Release(m.group("version"), m.group("date"))
            releases.append(current)
            in_lead = True
            continue
        if current is None:
            continue
        if not line.strip():
            if current.items:
                in_lead = False
            continue
        b = BULLET.match(line) or NUMBERED.match(line)
        if b and not (b.group("indent") and current.items):
            current.items.append(b.group("text").strip())
            if NUMBERED.match(line):
                current.ordered = True
            in_lead = False
            continue
        if line.startswith((" ", "\t")) and current.items:
            current.items[-1] += " " + line.strip()
            continue
        if line.startswith("#"):
            continue
        if in_lead or not current.items:
            current.lead.append(line.strip())
        else:
            current.items[-1] += " " + line.strip()
    return releases


def inline(md: str) -> str:
    """Escape HTML, then render `code`, **bold** and [text](url)."""
    out: list[str] = []
    pos = 0
    token = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)]+)\)")
    for m in token.finditer(md):
        out.append(html.escape(md[pos:m.start()]))
        if m.group(1) is not None:
            out.append(f"<code>{html.escape(m.group(1))}</code>")
        elif m.group(2) is not None:
            out.append(f"<strong>{inline(m.group(2))}</strong>")
        else:
            href = m.group(4)
            if not href.startswith(("http://", "https://", "#", "mailto:")):
                href = f"{REPO}/blob/main/{href.lstrip('./')}"
            out.append(f'<a href="{html.escape(href, quote=True)}">{inline(m.group(3))}</a>')
        pos = m.end()
    out.append(html.escape(md[pos:]))
    return "".join(out)


def long_date(date: str | None) -> str:
    if not date:
        return ""
    d = dt.date.fromisoformat(date)
    return f"{d.day} {d.strftime('%b %Y')}"


def release_html(r: Release) -> str:
    unreleased = r.version == "Unreleased"
    version_class = "version unreleased" if unreleased else "version"
    label = "unreleased" if unreleased else r.version
    when = (f'<time datetime="{r.date}">{long_date(r.date)}</time>' if r.date
            else '<span class="when">on main, not yet tagged</span>')
    tag_link = "" if unreleased else f'<a class="when" href="{REPO}/releases/tag/v{r.version}">release notes</a>'
    heading = inline(r.lead[0]) if r.lead else ("What is on main" if unreleased else f"Version {r.version}")
    rest = "".join(f"<p>{inline(p)}</p>" for p in r.lead[1:])
    tag = "ol" if r.ordered else "ul"
    items = "".join(f"<li>{inline(i)}</li>" for i in r.items)
    body = f"<{tag}>{items}</{tag}>" if items else ""
    return (
        f'<article class="release" id="{r.anchor}">\n'
        f'  <div class="meta"><span class="{version_class}">{html.escape(label)}</span>{when}{tag_link}</div>\n'
        f'  <div class="body"><h2>{heading}</h2>{rest}{body}</div>\n'
        f"</article>\n"
    )


def page_html(releases: list[Release], version: str) -> str:
    chips = "".join(
        f'<a href="#{r.anchor}">{"main" if r.version == "Unreleased" else r.version}</a>' for r in releases
    )
    body = "".join(release_html(r) for r in releases)
    latest = next((r for r in releases if r.version != "Unreleased"), None)
    latest_line = (f"Latest release {latest.version}, {long_date(latest.date)}." if latest else "")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="What changed in each brainless release, in plain words, with a feed to follow.">
<link rel="icon" href="assets/favicon.ico">
<link rel="alternate" type="application/atom+xml" title="brainless releases" href="feed.xml">
<title>brainless: what's new</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Outfit:wght@500;700;800&family=Hanken+Grotesk:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<link rel="stylesheet" href="site.css?v={version}">
</head>
<body>
<header class="site-nav">
  <div class="wrap nav">
    <a class="brand" href="index.html">
      <img src="assets/brainless-logo-64.png" alt="" width="40" height="40">
      <span>brainless</span>
    </a>
    <nav class="nav-links" aria-label="Sections">
      <a href="index.html#loop">The loop</a>
      <a href="index.html#voices">Six voices</a>
      <a href="index.html#writing">Writing</a>
      <a href="index.html#install">Install</a>
      <a href="whats-new.html" aria-current="page">What's new</a>
      <a href="{REPO}">GitHub</a>
    </nav>
  </div>
</header>

<div class="wrap page-head">
  <span class="eyebrow">What's new</span>
  <h1>Every release, in plain words.</h1>
  <p class="lede">{latest_line} Each entry says what changed and why it mattered, written the day it shipped. Follow it in a feed reader, or watch the repository on GitHub.</p>
  <div class="follow">
    <a href="feed.xml">Atom feed</a>
    <a href="{REPO}/releases.atom">GitHub releases feed</a>
    <a href="{REPO}/releases">Releases on GitHub</a>
    <a href="{REPO}/blob/main/CHANGELOG.md">CHANGELOG.md</a>
  </div>
  <div class="versions" aria-label="Jump to a version">{chips}</div>
</div>

<div class="wrap releases">
{body}</div>

<footer>
  <div class="wrap">
    <div class="foot-bottom">
      <span>brainless · MIT licence</span>
      <span><a href="index.html" style="color:inherit">Back to the front page</a></span>
    </div>
  </div>
</footer>

<script src="site.js?v={version}"></script>
</body>
</html>
"""


def feed_xml(releases: list[Release]) -> str:
    released = [r for r in releases if r.version != "Unreleased" and r.date]
    updated = f"{released[0].date}T00:00:00Z" if released else "1970-01-01T00:00:00Z"
    entries = []
    for r in released:
        content = ""
        if r.lead:
            content += "".join(f"<p>{inline(p)}</p>" for p in r.lead)
        if r.items:
            tag = "ol" if r.ordered else "ul"
            content += f"<{tag}>" + "".join(f"<li>{inline(i)}</li>" for i in r.items) + f"</{tag}>"
        entries.append(
            "  <entry>\n"
            f"    <title>brainless {html.escape(r.version)}</title>\n"
            f'    <link href="{SITE}/whats-new.html#{r.anchor}"/>\n'
            f"    <id>tag:brainless.ezapmar,{r.date}:v{html.escape(r.version)}</id>\n"
            f"    <updated>{r.date}T00:00:00Z</updated>\n"
            f'    <content type="html">{html.escape(content)}</content>\n'
            "  </entry>\n"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        "  <title>brainless releases</title>\n"
        "  <subtitle>What changed in each release of the brainless engine.</subtitle>\n"
        f'  <link href="{SITE}/whats-new.html"/>\n'
        f'  <link rel="self" href="{SITE}/feed.xml"/>\n'
        f"  <id>tag:brainless.ezapmar,2026:releases</id>\n"
        f"  <updated>{updated}</updated>\n"
        "  <author><name>Tunca Üçer</name></author>\n"
        + "".join(entries)
        + "</feed>\n"
    )


def fragment(path: Path) -> str:
    """The page body of a hand-written full document, for publishing as an artifact:
    the head's title, stylesheet and font links, then everything inside <body>."""
    text = path.read_text(encoding="utf-8")
    head = re.search(r"<head>(.*?)</head>", text, re.S).group(1)
    body = re.search(r"<body>(.*?)</body>", text, re.S).group(1)
    keep = re.findall(r"<title>.*?</title>|<link rel=\"stylesheet\"[^>]*>", head, re.S)
    return "\n".join(keep) + "\n" + body.strip() + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the generated files are stale")
    ap.add_argument("--fragment", type=Path, help="print an artifact fragment of this page and exit")
    args = ap.parse_args(argv)
    if args.fragment:
        sys.stdout.write(fragment(args.fragment))
        return 0
    releases = parse(CHANGELOG.read_text(encoding="utf-8"))
    if not releases:
        print("no releases found in CHANGELOG.md", file=sys.stderr)
        return 1
    version = asset_version()
    index = DOCS / "index.html"
    outputs = {
        DOCS / "whats-new.html": page_html(releases, version),
        DOCS / "feed.xml": feed_xml(releases),
        index: stamp(index.read_text(encoding="utf-8"), version),
    }
    stale = [p for p, s in outputs.items() if not p.exists() or p.read_text(encoding="utf-8") != s]
    if args.check:
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}")
        return 1 if stale else 0
    for p, s in outputs.items():
        if p in stale:
            p.write_text(s, encoding="utf-8")
            print(f"wrote {p.relative_to(ROOT)} ({len(s)} bytes)")
    print(f"asset version {version}")
    print(f"{len(releases)} sections, latest {releases[1].version if releases[0].version == 'Unreleased' else releases[0].version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
