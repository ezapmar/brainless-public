#!/usr/bin/env python3
"""privacy_scan.py: personal data and secrets in the vault's git-bound text.

Two jobs, one set of patterns:

  scan   (default) count findings in .wiki/, the compiled layer that is pushed
         every night. --json for machines, --record to store the count as the
         baseline health_check compares against.
  gate   (--staged) look only at lines ADDED in the staged diff. A file whose
         added lines carry a finding is unstaged, so tonight's commit goes out
         without it and the leak stays on the Mac. Everything else is backed
         up as usual. The result lands in .agents/state/privacy_gate.json.

Scope: personal data (e-mail, phone, IBAN, TC kimlik) is checked in .wiki/
only, since the human folders hold contact notes on purpose. Secrets (keys,
tokens, private key blocks) are checked in every staged file.

Not covered yet: person names (that is pseudonymisation, P0.2) and street
addresses or postcodes (too many public school and office postcodes to gate on).

--fix redacts the findings in .wiki/ in place. It is not a cure: the night's
compile writes the same text back from the source until the source is
redacted, so fix the ingest first (see
.wiki/digests/queries/2026-11-21-improvements-privacy-first-vault.md, P0.2).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

from paths import vault_root

VAULT = vault_root()
STATE = os.path.join(VAULT, ".agents", "state")
BASELINE_FILE = os.path.join(STATE, "privacy_baseline.json")
GATE_FILE = os.path.join(STATE, "privacy_gate.json")
WIKI = ".wiki/"

from redact import (EMAIL, IBAN, PHONE, REDACT, SECRETS, TCKN,  # noqa: F401  re-exported
                    find_personal, find_secrets, redact, tckn_valid)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=VAULT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    ).stdout


def _tracked(prefix: str) -> set[str]:
    return {p for p in _git("ls-files", "-z", "--", prefix).split("\0") if p}


def scan_wiki() -> dict:
    """Personal and secret findings in every .md under .wiki/ on disk."""
    tracked = _tracked(WIKI)
    files, kinds = {}, {}
    root = os.path.join(VAULT, WIKI)
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, VAULT)
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            hits = find_personal(text) + find_secrets(text)
            if hits:
                counts = {}
                for kind, _v, _s in hits:
                    counts[kind] = counts.get(kind, 0) + 1
                    kinds[kind] = kinds.get(kind, 0) + 1
                files[rel] = {"tracked": rel in tracked, "counts": counts}
    return {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "files": len(files),
            "tracked_files": sum(1 for f in files.values() if f["tracked"]),
            "kinds": dict(sorted(kinds.items())), "by_file": dict(sorted(files.items()))}


def added_lines(diff: str) -> dict[str, list[str]]:
    """{path: [added line, ...]} from a `git diff -U0` text."""
    out: dict[str, list[str]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            path = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
            continue
        if path and line.startswith("+") and not line.startswith("+++"):
            out.setdefault(path, []).append(line[1:])
    return out


def gate_findings(added: dict[str, list[str]]) -> dict[str, list[str]]:
    """{path: [kind, ...]} for staged files that must not be committed."""
    held = {}
    for path, lines in added.items():
        text = "\n".join(lines)
        hits = find_secrets(text)
        if path.startswith(WIKI):
            hits += find_personal(text)
        if hits:
            held[path] = sorted({k for k, _v, _s in hits})
    return held


def run_gate(unstage: bool = True) -> dict:
    diff = _git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--text")
    held = gate_findings(added_lines(diff))
    if unstage and held:
        subprocess.run(["git", "reset", "--quiet", "--", *held], cwd=VAULT, check=False)
    result = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "held": held}
    _write(GATE_FILE, result)
    return result


def _write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def fix_wiki() -> int:
    total = 0
    for rel in scan_wiki()["by_file"]:
        path = os.path.join(VAULT, rel)
        text = open(path, encoding="utf-8").read()
        new, n = redact(text)
        if n:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            total += n
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="Personal data and secrets in .wiki/ and the staged diff.")
    ap.add_argument("--json", action="store_true", help="full report as JSON")
    ap.add_argument("--record", action="store_true", help="store this scan as the health baseline")
    ap.add_argument("--staged", action="store_true", help="gate the staged diff: unstage leaking files")
    ap.add_argument("--dry-run", action="store_true", help="with --staged: report, unstage nothing")
    ap.add_argument("--fix", action="store_true", help="redact personal findings in .wiki/ in place")
    a = ap.parse_args()

    if a.staged:
        res = run_gate(unstage=not a.dry_run)
        for path, kinds in res["held"].items():
            print(f"held back: {path} ({', '.join(kinds)})", file=sys.stderr)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        return 1 if res["held"] else 0
    if a.fix:
        print(f"redacted {fix_wiki()} findings in {WIKI}")
        return 0
    rep = scan_wiki()
    if a.record:
        _write(BASELINE_FILE, {k: rep[k] for k in ("date", "files", "tracked_files", "kinds")})
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"{WIKI}: {rep['files']} files with findings ({rep['tracked_files']} tracked)")
        for kind, n in rep["kinds"].items():
            print(f"  {kind:<20} {n}")
    return 1 if rep["files"] else 0


if __name__ == "__main__":
    sys.exit(main())
