#!/usr/bin/env python3
"""Keep the originals out of git and the knowledge in.

Git is a good home for text and a bad one for a 40 MB scan. Binaries never
diff, they only ever grow the repository, and deleting one later does not
delete it: it stays in history until somebody rewrites it, which this vault has
now done twice. So the rule is that the markdown is the record and the original
lives somewhere a file store is good at.

Three cases, in the owner's words:

  1. The file is in Google Drive. Do not push it to git. Write down where it is.
  2. Only part of it matters. Keep that part as markdown.
  3. The whole thing matters. Convert it with markitdown and keep the markdown.

This tool does case 1 and case 3 mechanically, and leaves case 2 to a human,
because deciding which part matters is not a thing a script should guess.

For each tracked binary it finds the markdown conversion beside it (converting
when there is none), stamps a provenance block into that markdown saying what
the original was and where it lives, and untracks the binary. Nothing is
deleted from disk, ever: `git rm --cached` only stops git following it.

    python3 tools/lighten_vault.py                 # report, changes nothing
    python3 tools/lighten_vault.py --apply         # stamp and untrack
    python3 tools/lighten_vault.py --apply --convert   # also convert the gaps

Drive lookup is by exact byte size against the local Drive mount, with the file
name as a tie-break. Size is the right key here because the vault renames its
documents to a dated convention while Drive keeps the name they arrived with,
so matching on name alone would miss almost everything.
"""
import argparse
import collections
import hashlib
import os
import subprocess
import sys
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT")
             or Path(__file__).resolve().parents[1])
# The Drive mount is per account, so there is no useful default: set
# BRAINLESS_DRIVE_ROOT to the "My Drive" folder inside ~/Library/CloudStorage.
DRIVE_ROOT = Path(os.environ.get(
    "BRAINLESS_DRIVE_ROOT",
    os.path.expanduser("~/Library/CloudStorage/My Drive")))

BINARY_SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx", ".jpg", ".jpeg",
                   ".png", ".heic", ".ppt", ".doc", ".xls"}
# Engine assets are text-adjacent and small, and the README needs them.
KEEP_PREFIXES = ("docs/assets/", ".wiki/assets/")
# The marker makes the stamp idempotent: a second run updates it in place
# rather than growing the file.
# Where moved originals land in Drive. A single container rather than Work/ and
# Personal/ at the Drive root, so the vault's tree is recognisable and the move
# is reversible without untangling it from everything else in My Drive.
MIRROR_ROOT = "brainless-vault"
# Attachments are embedded in notes with ![[...]]; moving one to Drive leaves a
# broken image in the note, so they are never moved automatically.
NEVER_MOVE_PREFIXES = ("_attachments/",)

MARK_START = "<!-- brainless:source -->"
MARK_END = "<!-- /brainless:source -->"


def git(*args, **kw):
    return subprocess.run(["git", *args], cwd=VAULT, capture_output=True,
                          text=True, **kw)


def tracked_binaries():
    out = git("ls-files", "-z").stdout
    for rel in out.split("\0"):
        if not rel or rel.startswith(KEEP_PREFIXES):
            continue
        if Path(rel).suffix.lower() in BINARY_SUFFIXES:
            yield rel


def drive_index():
    """Byte size to Drive paths. Metadata only, so the streamed mount is cheap."""
    index = collections.defaultdict(list)
    if not DRIVE_ROOT.is_dir():
        return index
    for root, dirs, files in os.walk(DRIVE_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if Path(name).suffix.lower() not in BINARY_SUFFIXES:
                continue
            p = Path(root) / name
            try:
                index[p.stat().st_size].append(p)
            except OSError:
                continue
    return index


def strip_suffix(path: Path) -> Path:
    """Drop only the final extension.

    Path.with_suffix is wrong here: the vault names documents
    2026.03.23-Kind-Issuer.pdf, and with_suffix treats ".03.23-Kind-Issuer" as
    the suffix, so it returns 2026.md and the conversion is never found.
    """
    return path.parent / path.name.rsplit(".", 1)[0]


def find_markdown(rel: str) -> Path | None:
    """The conversion beside a binary: <base>.md, or <base>/<name>_raw.md."""
    stem = strip_suffix(VAULT / rel)
    candidates = [stem.parent / f"{stem.name}.md", stem / f"{stem.name}_raw.md"]
    for c in candidates:
        if c.is_file():
            return c
    return None


def convert(rel: str) -> Path | None:
    """Make the missing markdown with the vault's own converter."""
    sys.path.insert(0, str(VAULT / "tools"))
    try:
        from markitdown_native import convert_to_file
    except ImportError:
        return None
    src = VAULT / rel
    stem = strip_suffix(src)
    dst = stem.parent / f"{stem.name}.md"
    try:
        convert_to_file(str(src), str(dst))
    except Exception:
        return None
    # An empty conversion is worse than none: it would untrack the original and
    # leave a file that says nothing about it.
    if not dst.is_file() or not dst.read_text(errors="replace").strip():
        if dst.is_file():
            dst.unlink()
        return None
    return dst


def verified_match(local: Path, candidates) -> Path | None:
    """A Drive candidate whose bytes actually match, or None.

    Equal size is a hint, not proof: two screenshots can weigh the same, and a
    wrong match would delete the local original while pointing the note at
    somebody else's file. Worse, a Drive file can be a stream placeholder that
    reads as empty or times out, which looks like a hash mismatch rather than
    an unreadable file. Either way the answer is the same: not verified, so
    treat it as absent and make our own copy.
    """
    try:
        want = digest(local)
    except OSError:
        return None
    for c in candidates:
        try:
            if digest(c) == want:
                return c
        except OSError:
            continue
    return None


def move_to_drive(rel: str) -> Path | None:
    """Copy the original into Drive under the same path, verified. -> new path.

    Order matters: copy, then verify the bytes, and only then let the caller
    remove the local file. A move that trusts the copy is how a file that
    existed nowhere else stops existing anywhere.
    """
    import shutil
    src = VAULT / rel
    dst = DRIVE_ROOT / MIRROR_ROOT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        return dst  # already there from an earlier run
    try:
        shutil.copy2(src, dst)
    except OSError:
        return None
    try:
        if dst.stat().st_size != src.stat().st_size or digest(dst) != digest(src):
            return None
    except OSError:
        return None
    return dst


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def stamp(md: Path, rel: str, size: int, sha: str, drive: Path | None) -> None:
    """Write (or refresh) the provenance block at the top of the markdown.

    Without this the conversion is an orphan: text with no way back to what it
    came from. With it, the note says what the original was, how big, which
    exact bytes, and whether a copy exists anywhere other than this machine.
    """
    original = Path(rel).name
    if drive is not None:
        try:
            where = f"Google Drive, `{drive.relative_to(DRIVE_ROOT)}`"
        except ValueError:
            where = f"Google Drive, `{drive}`"
    else:
        where = ("yalnız bu makinede, `" + str(Path(rel).parent) + "/` "
                 "(Drive'da kopyası bulunamadı)")
    block = (f"{MARK_START}\n"
             f"> **Kaynak dosya:** `{original}` ({human(size)}, sha `{sha}`)\n"
             f"> **Nerede:** {where}\n"
             f"> **Not:** Orijinal git'e alınmaz, bu markdown kayıt nüshasıdır. "
             f"Kural: `docs/method.md`.\n"
             f"{MARK_END}\n")
    text = md.read_text(errors="replace")
    if MARK_START in text and MARK_END in text:
        head, rest = text.split(MARK_START, 1)
        _, tail = rest.split(MARK_END, 1)
        md.write_text(head + block + tail.lstrip("\n"))
        return
    md.write_text(block + "\n" + text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="stamp and untrack")
    ap.add_argument("--convert", action="store_true",
                    help="convert binaries that have no markdown yet")
    ap.add_argument("--limit", type=int, default=0, help="stop after N files")
    ap.add_argument("--move-to-drive", action="store_true",
                    help="for binaries with no Drive copy: mirror the vault path "
                         "under MIRROR_ROOT in Drive, copy the file there, verify "
                         "it byte for byte, then untrack and remove the local "
                         "original. Nothing is removed before the copy verifies.")
    ap.add_argument("--only-drive", action="store_true",
                    help="untrack only the binaries that have a copy in Drive. "
                         "Untracking a file that exists nowhere else removes its "
                         "only backup, so that case waits for a human.")
    args = ap.parse_args()

    print("indexing Drive ...", flush=True)
    index = drive_index()
    print(f"  {sum(len(v) for v in index.values())} documents in Drive\n")

    in_drive = local_only = converted = skipped = 0
    freed = 0
    for i, rel in enumerate(tracked_binaries()):
        if args.limit and i >= args.limit:
            break
        path = VAULT / rel
        if not path.is_file():
            continue
        size = path.stat().st_size
        if rel.startswith(NEVER_MOVE_PREFIXES):
            # Embedded in notes with ![[...]]; untracking one drops its backup
            # while the note still expects to render it. Leave it entirely.
            skipped += 1
            print(f"KEEP  embedded attachment, left tracked: {rel}")
            continue
        md = find_markdown(rel)
        if md is None:
            if not args.convert:
                skipped += 1
                print(f"SKIP  no markdown yet: {rel}")
                continue
            md = convert(rel)
            if md is None:
                skipped += 1
                print(f"SKIP  conversion failed: {rel}")
                continue
            converted += 1

        candidates = index.get(size, [])
        drive = None
        if candidates and verified_match(path, candidates):
            same_name = [c for c in candidates if c.name == Path(rel).name]
            drive = (same_name or candidates)[0]
            in_drive += 1
        elif args.move_to_drive and not rel.startswith(NEVER_MOVE_PREFIXES):
            if not args.apply:
                # A dry run must not write to Drive either. Report the intent.
                local_only += 1
                print(f"would copy to Drive then untrack: {rel}")
                continue
            moved = move_to_drive(rel)
            if moved is None:
                skipped += 1
                print(f"SKIP  copy to Drive failed, left alone: {rel}")
                continue
            drive = moved
            in_drive += 1
        else:
            local_only += 1
            if args.only_drive:
                print(f"hold  [local only, needs a Drive home] {rel}")
                continue
        freed += size

        if args.apply:
            stamp(md, rel, size, digest(path), drive)
            git("rm", "--cached", "-q", "--", rel)
            # Only now, with the copy verified and the note stamped, is the
            # local original redundant.
            if args.move_to_drive and drive is not None and drive != path:
                try:
                    path.unlink()
                except OSError:
                    pass
        mark = "drive" if drive else "local"
        print(f"{'untrack' if args.apply else 'would untrack'} [{mark}] {rel}")

    acted = in_drive if args.only_drive else in_drive + local_only
    verb = "untracked" if args.apply else "would untrack"
    print(f"\n{verb} {acted} binaries, {human(freed)} of new commit weight")
    print(f"  {in_drive} have a copy in Google Drive")
    print(f"  {local_only} exist ONLY in this vault; their markdown now says so, "
          f"but the original is backed up nowhere until it goes to Drive")
    if converted:
        print(f"  {converted} converted with markitdown on the way through")
    if skipped:
        print(f"  {skipped} skipped, no markdown (rerun with --convert)")
    if not args.apply:
        print("\nnothing was changed. Add --apply to stamp and untrack.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
