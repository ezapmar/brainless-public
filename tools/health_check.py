#!/usr/bin/env python3
"""Pipeline heartbeat for the brainless vault.

Checks the plumbing (backups, processors, logs) and writes a compact status
block to _Agent-Context/HEALTH.md. Briefings must surface this block.
Fires a macOS notification when any check crosses the 2-day red-flag line.
Runs hourly from cron_wrapper.sh; cheap by design (no LLM calls).
"""
import os
import subprocess
import time
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from owner_profile import WORKER  # noqa: E402
HEALTH_FILE = os.path.join(VAULT, "_Agent-Context", "HEALTH.md")
RED_FLAG_SECONDS = 2 * 24 * 3600

CHECKS = []  # (label, status, detail) with status in {OK, WARN, RED}


def add(label, status, detail):
    CHECKS.append((label, status, detail))


def age_str(seconds):
    if seconds < 3600:
        return f"{int(seconds // 60)} dk"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} saat"
    return f"{seconds / 86400:.1f} gün"


def check_git():
    now = time.time()
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct"], cwd=VAULT,
            capture_output=True, text=True, timeout=30,
        )
        last_commit = int(out.stdout.strip())
        age = now - last_commit
        status = "RED" if age > RED_FLAG_SECONDS else ("WARN" if age > 86400 else "OK")
        add("Son commit", status, f"{age_str(age)} önce")
    except Exception as e:
        add("Son commit", "RED", f"okunamadı: {e}")

    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], cwd=VAULT,
            capture_output=True, text=True, timeout=30,
        )
        dirty = len([l for l in out.stdout.splitlines() if l.strip()])
        add("Commit bekleyen değişiklik", "OK" if dirty < 20 else "WARN", f"{dirty} dosya")
    except Exception:
        pass

    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", "origin/master..master"], cwd=VAULT,
            capture_output=True, text=True, timeout=30,
        )
        ahead = int(out.stdout.strip())
        paused = os.path.exists(os.path.join(VAULT, ".agents", "state", "no_push"))
        if paused:
            add("Push bekleyen commit", "WARN" if ahead else "OK",
                f"{ahead} commit (push bilinçli duraklatıldı, no_push bayrağı aktif)")
        else:
            add("Push bekleyen commit", "OK" if ahead == 0 else ("WARN" if ahead < 10 else "RED"),
                f"{ahead} commit")
    except Exception:
        pass


def check_log(label, path, red_after, hint):
    """Freshness of a log file: its mtime is the job's last sign of life."""
    full = os.path.join(VAULT, path)
    if not os.path.exists(full):
        add(label, "RED", "log dosyası yok")
        return
    age = time.time() - os.path.getmtime(full)
    status = "RED" if age > red_after else "OK"
    add(label, status, f"son iz {age_str(age)} önce ({hint})")


def check_llm_auth():
    """LLM backend auth canary.

    Reads the breadcrumb llm.py drops on every real call. Freshness checks see
    a script that ran and logged; they cannot see that its LLM call 401'd. This
    catches a silent token/API-key expiry that would otherwise show green.
    """
    path = os.path.join(VAULT, ".agents", "state", "llm_status")
    if not os.path.exists(path):
        add("LLM erişimi", "WARN", "henüz kayıt yok (bir otomasyon LLM çağrısı bekleniyor)")
        return
    try:
        with open(path, errors="replace") as fh:
            parts = fh.read().strip().split("\t")
        outcome = parts[1] if len(parts) > 1 else "error"
        detail = parts[2] if len(parts) > 2 else ""
    except Exception:
        add("LLM erişimi", "WARN", "durum dosyası okunamadı")
        return
    age = age_str(time.time() - os.path.getmtime(path))
    if outcome == "ok":
        add("LLM erişimi", "OK", f"son çağrı başarılı ({age} önce)")
    elif outcome == "auth":
        add("LLM erişimi", "RED",
            f"kimlik doğrulama hatası ({age} önce): {detail[:70]}. 'claude' ile yeniden giriş yap")
    elif outcome == "timeout":
        add("LLM erişimi", "WARN", f"son çağrı zaman aşımı ({age} önce)")
    else:
        add("LLM erişimi", "WARN", f"son çağrı hatası ({age} önce): {detail[:70]}")


def check_log_errors():
    """Recent error lines in the processor logs."""
    # nightly log lives on the worker now; the stale Mac file must not WARN.
    for label, path in [("smart_processor hataları", "logs/smart_processor.log")]:
        full = os.path.join(VAULT, path)
        if not os.path.exists(full):
            continue
        try:
            with open(full, errors="replace") as fh:
                tail = fh.readlines()[-50:]
            # Yalnizca SON basarili calisma ozetinden ("... backed off.") sonraki
            # hatalari say. Boylece cozulmus/gecici hatalar (ssh kesintisi, eski
            # kod bug'i) sonraki temiz kosu gelince WARN uretmeyi birakir; ancak
            # ozetsiz cokmus son kosunun hatalari isaretlenmeye devam eder.
            last_run = max((i for i, l in enumerate(tail)
                            if "backed off" in l.lower()), default=-1)
            errs = [l.strip() for l in tail[last_run + 1:]
                    if any(k in l.lower() for k in ("error", "failed", "err]", "exception"))
                    and "0 failed" not in l]
            if errs:
                add(label, "WARN", f"{len(errs)} hata satırı, son: {errs[-1][:120]}")
            else:
                add(label, "OK", "son 50 satır temiz")
        except Exception:
            pass


def check_worker():
    """The nightly family runs on the always-on worker (PROFILE.md worker_name).
    The evidence the primary machine can see is git: the age of the last commit
    signed "(<worker>)". Unit-level failures are caught by the worker's watchdog;
    buradaki gosterge toplu nabizdir. Esik: 30h WARN, 52h RED (2 gun kurali)."""
    try:
        out = subprocess.run(
            ["git", "log", "--format=%ct\t%s", "-100"],
            capture_output=True, text=True, cwd=VAULT, timeout=30).stdout
        for line in out.splitlines():
            ct, _, subject = line.partition("\t")
            if subject.rstrip().endswith(f"({WORKER})"):
                age = time.time() - int(ct)
                status = "RED" if age > 52 * 3600 else ("WARN" if age > 30 * 3600 else "OK")
                add(f"{WORKER} worker", status, f"son {WORKER} commit {age_str(age)} önce")
                return
        add(f"{WORKER} worker", "RED", f"son 100 commit'te {WORKER} izi yok")
    except Exception:
        pass


def check_dialectic():
    """Critical dialectic rounds (worker, 12:30 and 21:20) write one status
    line per run into _Agent-Context/DIALECTIC-STATUS.md, idle days included.
    The freshest copy is on origin; the Mac working tree may lag a day."""
    try:
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=VAULT,
                       capture_output=True, timeout=30)
        text = subprocess.run(
            ["git", "show", "origin/master:_Agent-Context/DIALECTIC-STATUS.md"],
            cwd=VAULT, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        text = ""
    if not text.strip():
        try:
            with open(os.path.join(VAULT, "_Agent-Context", "DIALECTIC-STATUS.md"),
                      errors="replace") as fh:
                text = fh.read()
        except OSError:
            add("Dialectic round", "WARN", "no status record yet")
            return
    runs = [l for l in text.splitlines() if l.startswith("- 20")]
    if not runs:
        add("Dialectic round", "WARN", "status file empty")
        return
    last = runs[-1]
    try:
        day = datetime.strptime(last[2:12], "%Y-%m-%d")
        slot = last.split()[2].rstrip(":")
        hour = 12 if slot == "noon" else 21
        age = time.time() - day.replace(hour=hour, minute=30).timestamp()
    except Exception:
        add("Dialectic round", "WARN", f"unparsable line: {last[:60]}")
        return
    result = last.split(":", 1)[1].strip().split(",")[0] if ":" in last else "?"
    status = "RED" if age > 36 * 3600 else ("WARN" if age > 14 * 3600 else "OK")
    if result == "error":
        status = "RED" if status == "OK" else status
    add("Dialectic round", status, f"last run {last[2:12]} {slot} ({result}, {age_str(max(age, 0))} önce)")


def main():
    check_git()
    # smart_processor runs hourly; 3h of silence means the cron line is dead.
    check_log("Saatlik işlemci", "logs/smart_processor.log", 3 * 3600, "saatlik cron")
    # Night jobs and telegram moved to the worker on 2026-08-26; no Mac log trace
    # birakmazlar. Toplu nabiz asagida, ayrinti Linux watchdog'unda.
    check_worker()
    check_dialectic()
    check_llm_auth()
    check_log_errors()

    worst = "OK"
    for _, status, _ in CHECKS:
        if status == "RED":
            worst = "RED"
            break
        if status == "WARN":
            worst = "WARN"

    icon = {"OK": "🟢", "WARN": "🟡", "RED": "🔴"}[worst]
    lines = [
        "# Sistem Sağlığı",
        "",
        f"**Durum: {icon} {worst}** (güncelleme: {datetime.now().strftime('%Y-%m-%d %H:%M')})",
        "",
    ]
    for label, status, detail in CHECKS:
        mark = {"OK": "🟢", "WARN": "🟡", "RED": "🔴"}[status]
        lines.append(f"- {mark} {label}: {detail}")
    lines.append("")
    lines.append("> 2 günden uzun sessizlik = kırmızı bayrak. Bu blok her sabah brifinginde yer almalı.")

    os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
    with open(HEALTH_FILE, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    if worst == "RED":
        reds = "; ".join(f"{l}: {d}" for l, s, d in CHECKS if s == "RED")[:180]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{reds}" with title "brainless KIRMIZI BAYRAK"'],
            check=False,
        )


if __name__ == "__main__":
    main()
