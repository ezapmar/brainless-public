#!/usr/bin/env python3
"""brainless watchdog (worker'de saatlik calisir).

Sessiz kirilmalari Telegram'dan haber verir. Kontroller:
  1. Mac sessizligi: origin/master'da worker imzasiz son commit > 26 saat eski
     (dikkat: bu GitHub'a ULASAN son commit'tir; Mac lokalde commit atip
      push'u dusuruyor olabilir, 2026-09-01/02'de oldugu gibi)
  2. HEALTH.md kirmizi: committed HEALTH.md "Durum: KIRMIZI" gosteriyor
  3. Linux job'lari: systemctl --user failed durumda brainless-* birimi var
  4. LLM auth: bu makinedeki son LLM cagrisi auth hatasi vermis

Ayni konu 12 saatte bir kez bildirilir (state: .agents/state/watchdog_state.json,
gitignore altinda). Vault'a yazmaz, push'lamaz; sadece okur ve mesaj atar.
"""
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
import sys  # noqa: E402
sys.path.insert(0, os.path.join(VAULT, "tools"))
from owner_profile import WORKER  # noqa: E402
CONF_DIR = os.path.expanduser("~/.config/brainless")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "watchdog_state.json")
MAC_SILENCE_HOURS = 26
DEDUPE_HOURS = 12


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=VAULT, **kw)


# Buzz (Katman 1): Telegram'a giden her mesaj, cagiran script'e gore bir Buzz
# kanalina da dusur. Kimlik ve kanal eslemesi; buzz_post.sh yoksa sessiz gecer.
BUZZ_ROUTES = {
    "watchdog.py": ("watchdog", "ops"),
    "update_check.py": ("watchdog", "ops"),
    "task_reminder.py": ("gorev", "tasks"),
    "meeting_brief.py": ("gorev", "tasks"),
    "relationship_radar.py": ("radar", "radar"),
    "thinker_digest.py": ("radar", "radar"),
    "content_engine.py": ("icerik", "content"),
}


def buzz_mirror(text, route=None):
    """Best effort: post text to the Buzz channel mapped to the calling script."""
    import sys
    route = route or BUZZ_ROUTES.get(os.path.basename(sys.argv[0] or ""))
    script = os.path.join(VAULT, ".agents", "scripts", "buzz_post.sh")
    if not route or not os.access(script, os.X_OK):
        return False
    try:
        subprocess.run([script, route[0], route[1]], input=text, text=True,
                       capture_output=True, timeout=45, cwd=VAULT)
        return True
    except Exception as exc:  # never break the Telegram path
        log(f"buzz mirror skipped: {exc}")
        return False


def send_telegram(text):
    buzz_mirror(text)
    token = read_file(os.path.join(CONF_DIR, "telegram_token"))
    chat = read_file(os.path.join(CONF_DIR, "telegram_chat_id"))
    if not (token and chat):
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30)
    return True


def check_mac_silence(issues):
    run(["git", "fetch", "-q", "origin"])
    r = run(["git", "log", "origin/master", "--format=%ct\t%s", "-100"])
    now = time.time()
    for line in r.stdout.splitlines():
        ct, _, subject = line.partition("\t")
        if not subject.rstrip().endswith(f"({WORKER})"):
            hours = (now - int(ct)) / 3600
            if hours > MAC_SILENCE_HOURS:
                issues["mac-silent"] = (
                    f"Mac'in GitHub'a ulastirdigi son commit {hours:.0f} saat once "
                    f"(esik {MAC_SILENCE_HOURS} saat). Bu 'commit atilmadi' demek degil. "
                    "Sirayla bak: 1) push dusuyor mu (Mac'te logs/vault_backup.log), "
                    "2) backup job'i calisiyor mu, 3) makine kapali mi.")
            return
    issues["mac-silent"] = "Son 100 commit'te Mac imzali commit yok."


def check_health_red(issues):
    r = run(["git", "show", "origin/master:_Agent-Context/HEALTH.md"])
    for line in r.stdout.splitlines():
        if line.startswith("**Durum:") and "🔴" in line:
            issues["health-red"] = f"HEALTH.md kirmizi: {line.strip('* ')}"
            return


def check_failed_units(issues):
    r = subprocess.run(
        ["systemctl", "--user", "--failed", "--no-legend", "--plain"],
        capture_output=True, text=True)
    failed = [l.split()[0] for l in r.stdout.splitlines()
              if "brainless-" in l or "buzz-" in l]
    if failed:
        issues["units-failed"] = "Linux'ta hata veren job'lar: " + ", ".join(failed)


def check_dialectic(issues):
    """The dialectic round (12:30 and 21:20) writes one line per run into
    DIALECTIC-STATUS.md (idle included). No noon line after 14:00 or no evening
    line after 22:30 means the round did not run."""
    # Motor kurulmadan alarm verme: timer yoksa bu ozellik henuz acik degil
    # (install_personas.sh calismamis). Hic acilmamis bir tur icin uyarma.
    if not os.path.exists(os.path.expanduser(
            "~/.config/systemd/user/brainless-dialectic.timer")):
        return
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    text = read_file(os.path.join(VAULT, "_Agent-Context", "DIALECTIC-STATUS.md")) or ""
    lines = [l for l in text.splitlines() if l.startswith(f"- {today} ")]
    have = {l.split()[2].rstrip(":") for l in lines}
    errors = [l for l in lines if ": error," in l]
    missing = []
    if now.hour * 60 + now.minute >= 14 * 60 and "noon" not in have:
        missing.append("noon (12:30)")
    if now.hour * 60 + now.minute >= 22 * 60 + 30 and "evening" not in have:
        missing.append("evening (21:20)")
    if missing:
        issues["dialectic-missing"] = ("Dialectic round did not run: " + ", ".join(missing) +
                                       "; journalctl --user -u brainless-dialectic")
    if errors:
        issues["dialectic-error"] = "Dialectic round failed: " + errors[-1][2:120]


def check_llm_auth(issues):
    s = read_file(os.path.join(VAULT, ".agents", "state", "llm_status"))
    if s and "\tauth\t" in s:
        # Ham CLI stderr'i Telegram'a tasima; env/argv/kimlik sizabilir.
        # Yalniz zaman damgasini (ilk alan) bildir, detay alanini birak.
        stamp = s.split("\t", 1)[0]
        issues["llm-auth"] = f"Linux'ta LLM auth hatasi (son cagri {stamp}); llm_status'a bak"


def main():
    issues = {}
    for check in (check_mac_silence, check_health_red, check_failed_units, check_llm_auth,
                  check_dialectic):
        try:
            check(issues)
        except Exception as e:
            log(f"{check.__name__} hata: {e}")

    try:
        state = json.loads(read_file(STATE_FILE) or "{}")
    except ValueError:
        state = {}
    now = time.time()
    fresh = {k: v for k, v in issues.items()
             if now - state.get(k, 0) > DEDUPE_HOURS * 3600}

    if fresh:
        text = "🐶 brainless watchdog:\n\n" + "\n".join(f"- {v}" for v in fresh.values())
        if send_telegram(text):
            log(f"Uyari gonderildi: {list(fresh)}")
            state.update({k: now for k in fresh})
    else:
        log(f"Temiz ({len(issues)} bilinen konu)" if issues else "Temiz")

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh)


if __name__ == "__main__":
    main()
