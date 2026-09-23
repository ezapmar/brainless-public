#!/usr/bin/env python3
"""vault_archive.py: a monthly encrypted copy of the vault that sync cannot reach.

Sync is not backup. Two machines and GitHub all follow each other, so an
automation that quietly rewrites two hundred pages, or a bad history rewrite,
reaches every copy within minutes. The failure this protects against is not
disk death; it is noticing, three weeks later, that something went wrong.

  create   one archive: the working tree (private homes included, they exist
           nowhere else) plus a git bundle of the full history, streamed through
           `age` to a public key, so no plaintext archive ever touches disk and
           the machine that makes backups cannot read them. Written to the
           backup folder (a Google Drive for desktop folder), newest six kept.
  verify   the restore test: decrypt with the private key, unpack to a temp
           folder, check every file in the manifest is there at its size, the
           git bundle verifies, and the wiki's links resolve. An untested
           backup fails at a reliably bad moment.
  status   last archive and last verified restore.

Usage:
  python3 tools/vault_archive.py create [--if-older-days 30]
  python3 tools/vault_archive.py verify --identity ~/Downloads/brainless-backup.key [archive]
  python3 tools/vault_archive.py status

Config (PROFILE.md): backup_dir, backup_recipient (an age public key, age1...).
The private key lives in the password manager, never on this machine.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(Path(__file__).resolve().parent))
STATE = VAULT / ".agents" / "state" / "backup.json"
KEEP = 6
PREFIX = "brainless-"
SUFFIX = ".tar.gz.age"
# Rebuildable or machine-local: never worth a backup, sometimes large.
EXCLUDE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                "logs", ".obsidian/cache", ".trash", ".claude/worktrees"}
EXCLUDE_SUFFIXES = (".pyc", ".tmp", ".DS_Store")


def config() -> tuple[Path | None, str]:
    try:
        from owner_profile import _fm
    except Exception:
        _fm = {}
    # A set environment variable wins even when empty, so a test or a one-off run
    # can switch the profile value off.
    pick = lambda env, key: os.environ[env] if env in os.environ else _fm.get(key, "")
    d = pick("BRAINLESS_BACKUP_DIR", "backup_dir")
    r = pick("BRAINLESS_BACKUP_RECIPIENT", "backup_recipient")
    return (Path(os.path.expanduser(d)) if d else None), r.strip()


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(**kw):
    s = {**load_state(), **kw}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1))


def files(root: Path):
    """Every file to archive, relative to the vault, sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames
                             if d not in EXCLUDE_DIRS and str(rel_dir / d) not in EXCLUDE_DIRS)
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if f.endswith(EXCLUDE_SUFFIXES) or p.is_symlink():
                continue
            out.append(p.relative_to(root))
    return out


def age_bin() -> str:
    exe = shutil.which("age") or ("/opt/homebrew/bin/age" if Path("/opt/homebrew/bin/age").exists() else "")
    if not exe:
        raise RuntimeError("age is not installed (brew install age / pacman -S age)")
    return exe


def create(if_older_days: int | None = None, now: datetime | None = None) -> Path | None:
    now = now or datetime.now()
    last = load_state().get("created")
    if if_older_days and last and datetime.fromisoformat(last) > now - timedelta(days=if_older_days):
        return None
    dest_dir, recipient = config()
    if not dest_dir or not recipient.startswith("age1"):
        raise RuntimeError("backup_dir and backup_recipient (age1...) must be set in PROFILE.md")
    if not dest_dir.parent.exists():
        raise RuntimeError(f"backup folder's parent is missing (Drive not mounted?): {dest_dir.parent}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{PREFIX}{now.strftime('%Y-%m-%d-%H%M')}{SUFFIX}"
    final, part = dest_dir / name, dest_dir / (name + ".part")
    listing = files(VAULT)
    manifest = {"created": now.isoformat(timespec="seconds"), "vault": str(VAULT), "files": {}}
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "history.bundle"
        subprocess.run(["git", "-C", str(VAULT), "bundle", "create", str(bundle), "--all"],
                       check=True, capture_output=True)
        with open(part, "wb") as out:
            proc = subprocess.Popen([age_bin(), "-r", recipient], stdin=subprocess.PIPE, stdout=out)
            try:
                with tarfile.open(fileobj=proc.stdin, mode="w|gz") as tar:
                    for rel in listing:
                        p = VAULT / rel
                        try:
                            manifest["files"][str(rel)] = p.stat().st_size
                            tar.add(p, arcname=f"vault/{rel}", recursive=False)
                        except (OSError, tarfile.TarError):
                            manifest["files"].pop(str(rel), None)   # vanished mid-run
                    tar.add(bundle, arcname="history.bundle")
                    data = json.dumps(manifest, ensure_ascii=False).encode()
                    info = tarfile.TarInfo("manifest.json")
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            finally:
                proc.stdin.close()
                rc = proc.wait()
    if rc != 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"age exited {rc}")
    part.replace(final)
    h = hashlib.sha256()
    with open(final, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    digest = h.hexdigest()
    save_state(created=now.isoformat(timespec="seconds"), archive=str(final), sha256=digest,
               files=len(manifest["files"]), bytes=final.stat().st_size,
               seconds=round(time.monotonic() - t0))
    prune(dest_dir)
    return final


def prune(dest_dir: Path, keep: int = KEEP) -> list[Path]:
    """Keep the newest archives. Only this tool's own files are ever touched."""
    ours = sorted(p for p in dest_dir.glob(f"{PREFIX}*{SUFFIX}") if p.is_file())
    gone = ours[:-keep] if len(ours) > keep else []
    for p in gone:
        p.unlink()
    return gone


def verify(identity: Path, archive: Path | None = None) -> dict:
    """Restore into a temp folder and check it. Returns the findings."""
    if not identity.expanduser().is_file():
        raise RuntimeError(f"key file not found: {identity}. Export it from the password manager, "
                           f"run verify, then delete it again.")
    identity = identity.expanduser()
    if archive is None:
        dest_dir, _ = config()
        found = sorted(dest_dir.glob(f"{PREFIX}*{SUFFIX}")) if dest_dir else []
        if not found:
            raise RuntimeError("no archive to verify")
        archive = found[-1]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dec = subprocess.Popen([age_bin(), "-d", "-i", str(identity), str(archive)], stdout=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=dec.stdout, mode="r|gz") as tar:
                tar.extractall(root, filter="data")
        except tarfile.TarError:
            dec.stdout.close()
            dec.wait()
            raise RuntimeError("age could not decrypt the archive (wrong key, or not this vault's key?)")
        dec.stdout.close()
        if dec.wait() != 0:
            raise RuntimeError("age could not decrypt the archive (wrong key?)")
        manifest = json.loads((root / "manifest.json").read_text())
        missing = [f for f, size in manifest["files"].items()
                   if not (root / "vault" / f).exists() or (root / "vault" / f).stat().st_size != size]
        # A clone is the real restore of the history, and it checks every object.
        bundle = subprocess.run(["git", "clone", "-q", "--mirror", str(root / "history.bundle"),
                                 str(root / "history.git")], capture_output=True, text=True)
        links = _link_check(root / "vault")
    result = {"archive": archive.name, "files": len(manifest["files"]), "missing": len(missing),
              "missing_sample": missing[:5], "bundle_ok": bundle.returncode == 0, **links}
    result["ok"] = not missing and result["bundle_ok"]
    if result["ok"]:
        save_state(verified=datetime.now().isoformat(timespec="seconds"), verified_archive=archive.name)
    return result


def _link_check(vault: Path) -> dict:
    """Links resolve in the restored copy the way they do in the live one."""
    code = (f"import json,sys; sys.path.insert(0, {str(Path(__file__).resolve().parent)!r}); import lint_wiki as L;"
            "fs=L.all_wiki_files(); g=L.link_graph(fs);"
            "print(json.dumps({'wiki_pages': len(fs), 'broken_links': len(g['broken'])}))")
    r = subprocess.run([sys.executable, "-c", code], cwd=vault, capture_output=True, text=True,
                       env={**os.environ, "BRAINLESS_VAULT": str(vault)}, timeout=300)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"wiki_pages": 0, "broken_links": -1}


def status() -> dict:
    s = load_state()
    now = datetime.now()
    age = lambda k: (now - datetime.fromisoformat(s[k])).days if s.get(k) else None
    return {**s, "created_days_ago": age("created"), "verified_days_ago": age("verified")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--if-older-days", type=int)
    v = sub.add_parser("verify")
    v.add_argument("--identity", required=True, type=Path)
    v.add_argument("archive", nargs="?", type=Path)
    sub.add_parser("status")
    args = ap.parse_args(argv)
    if args.cmd == "create":
        try:
            path = create(args.if_older_days)
        except RuntimeError as e:
            print(f"backup not made: {e}", file=sys.stderr)
            print("RUNLOG archived=0 status=fail")
            return 1
        if path is None:
            print("RUNLOG archived=0")        # not due yet
            return 0
        s = load_state()
        print(f"[ok] {path.name}: {s['files']} files, {s['bytes'] / 1e6:.0f} MB, {s['seconds']}s")
        print(f"RUNLOG archived=1 files={s['files']} mb={s['bytes'] // 1_000_000}")
    elif args.cmd == "verify":
        try:
            res = verify(args.identity, args.archive)
        except RuntimeError as e:
            print(f"restore test not run: {e}", file=sys.stderr)
            return 1
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
