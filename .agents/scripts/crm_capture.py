#!/usr/bin/env python3
"""CRM snapshot (Pipedrive first provider; hourly on the laptop, daily on the worker).

Pulls the organisations and open deals assigned to the owner from the CRM API and
produces four outputs, deterministically and without an LLM:

  _Agent-Context/CRM.md           : status block the briefing copies (flags, one
                                    table per pipeline, changes in the last 24h).
                                    Rewritten only when the body changes.
  Inbox/CRM/<Organisation>.md     : event log per organisation. A line is appended
                                    ONLY when an event happens; a quiet run touches
                                    nothing (every touch under Inbox triggers an LLM
                                    summary at compile time).
  .agents/state/crm_snapshot.json : previous state for the delta (gitignored).
  .agents/state/crm_status        : trace read by health_check
                                    (<iso>\t<ok|auth|error>\t<detail>).

Person data (name, e-mail, phone) is DELIBERATELY not fetched: the provider mapping
passes organisation and deal fields only. This boundary stays in code until the
customer data policy is settled.

Provider seam: Provider base + PipedriveProvider. For another CRM add a class that
implements the same five methods; callers never see provider fields.

Credentials and settings (outside the repo, ~/.config/brainless/, mode 0600):
  pipedrive_api_token : required; without it the script exits silently (exit 0,
                        same behaviour as telegram_capture). The company domain
                        is read from the API, never hard-coded.
  crm_provider        : provider name (default pipedrive)
  crm_pipelines       : one pipeline name per line; empty = all
  crm_owner           : numeric user id (default: the token's owner)

Usage:
  --dry-run             fetch, compute, print, write nothing
  --self-test           unit test with a fixed fixture (no network)
  --no-activity-days N  threshold for the no-activity flag (14)
  --stage-days M        threshold for the same-stage flag (30)
  --lang tr|en          output language (default: PROFILE.md output_lang)

Documentation with a Pipedrive example: docs/addons/crm-pipedrive.md
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from owner_profile import LANG, OWNER  # noqa: E402

CONF_DIR = os.path.expanduser("~/.config/brainless")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "crm_snapshot.json")
STATUS_FILE = os.path.join(VAULT, ".agents", "state", "crm_status")
SNAPSHOT_FILE = os.path.join(VAULT, "_Agent-Context", "CRM.md")
ORG_DIR = os.path.join(VAULT, "Inbox", "CRM")
BOOTSTRAP_URL = "https://api.pipedrive.com/v1"
EVENT_KEEP_DAYS = 90
DEFAULT_NO_ACTIVITY_DAYS = 14
DEFAULT_STAGE_DAYS = 30
MAX_PAGES = 20
PAGE_SIZE = 500

LABELS = {
    "tr": {
        "title": "# CRM Durumu",
        "status": "Durum",
        "updated": "güncelleme",
        "source": "kaynak",
        "owner": "sahip",
        "counts": "{orgs} şirket, {deals} açık anlaşma, {flags} bayrak, son 24 saatte {changes} değişiklik",
        "flags": "## Bayraklar",
        "changes": "## Değişenler (son 24 saat)",
        "idle_orgs": "## Anlaşması olmayan şirketler",
        "none": "- yok",
        "baseline_note": "- ilk çalışma: temel çizgi alındı, delta bir sonraki çalışmadan itibaren",
        "cols": ["Şirket", "Anlaşma", "Aşama", "Değer", "Son aktivite", "Sonraki adım", "Bayrak"],
        "total": "Toplam",
        "deal_word": "anlaşma",
        "no_org": "(şirket yok)",
        "day_short": "g",
        "last_activity": "son aktivite",
        "footer": ("> Kişi adı, e-posta ve telefon bilerek yok (şirket ve anlaşma seviyesi). "
                   "LLM kullanılmaz. Üretici: .agents/scripts/crm_capture.py"),
        "flag_no_activity": "{d} gündür aktivite yok",
        "flag_next_overdue": "sonraki adım gecikmiş ({date})",
        "flag_close_passed": "beklenen kapanış geçti ({date})",
        "flag_stage_stale": "{d} gündür aynı aşamada",
        "code_no_activity": "AKTIVITE>{n}g",
        "code_next_overdue": "SONRAKI GECIKTI",
        "code_close_passed": "KAPANIS GECTI",
        "code_stage_stale": "ASAMA>{n}g",
        "ev_baseline": "{org}: izlemeye alındı ({detail})",
        "ev_deal_new": "{org}: \"{title}\" yeni anlaşma ({detail})",
        "ev_stage": "{org}: \"{title}\" aşama {detail}",
        "ev_value": "{org}: \"{title}\" değer {detail}",
        "ev_won": "{org}: \"{title}\" kazanıldı",
        "ev_lost": "{org}: \"{title}\" kaybedildi",
        "ev_deleted": "{org}: \"{title}\" silindi",
        "ev_reassigned": "{org}: \"{title}\" başkasına atandı",
        "ev_gone": "{org}: \"{title}\" listeden çıktı",
        "ev_org_new": "{org}: izlemeye alındı",
        "ev_org_gone": "{org}: sahiplikten çıktı",
        "ev_org_renamed": "{org}: yeniden adlandırıldı ({detail})",
        "log_header": "# {org}\n\nKaynak: CRM ({provider}), otomatik olay günlüğü. Kişi verisi yok.\n\n---\n\n",
        "open_deals": "{n} açık anlaşma",
    },
    "en": {
        "title": "# CRM Status",
        "status": "Status",
        "updated": "updated",
        "source": "source",
        "owner": "owner",
        "counts": "{orgs} organisations, {deals} open deals, {flags} flags, {changes} changes in the last 24h",
        "flags": "## Flags",
        "changes": "## Changes (last 24h)",
        "idle_orgs": "## Organisations without open deals",
        "none": "- none",
        "baseline_note": "- first run: baseline taken, deltas start with the next run",
        "cols": ["Organisation", "Deal", "Stage", "Value", "Last activity", "Next step", "Flags"],
        "total": "Total",
        "deal_word": "deals",
        "no_org": "(no organisation)",
        "day_short": "d",
        "last_activity": "last activity",
        "footer": ("> No person names, e-mails or phones by design (organisation and deal level). "
                   "No LLM. Producer: .agents/scripts/crm_capture.py"),
        "flag_no_activity": "no activity for {d} days",
        "flag_next_overdue": "next step overdue ({date})",
        "flag_close_passed": "expected close passed ({date})",
        "flag_stage_stale": "same stage for {d} days",
        "code_no_activity": "NO ACTIVITY>{n}d",
        "code_next_overdue": "NEXT OVERDUE",
        "code_close_passed": "CLOSE PASSED",
        "code_stage_stale": "STAGE>{n}d",
        "ev_baseline": "{org}: tracking started ({detail})",
        "ev_deal_new": "{org}: \"{title}\" new deal ({detail})",
        "ev_stage": "{org}: \"{title}\" stage {detail}",
        "ev_value": "{org}: \"{title}\" value {detail}",
        "ev_won": "{org}: \"{title}\" won",
        "ev_lost": "{org}: \"{title}\" lost",
        "ev_deleted": "{org}: \"{title}\" deleted",
        "ev_reassigned": "{org}: \"{title}\" reassigned",
        "ev_gone": "{org}: \"{title}\" left the list",
        "ev_org_new": "{org}: tracking started",
        "ev_org_gone": "{org}: no longer owned",
        "ev_org_renamed": "{org}: renamed ({detail})",
        "log_header": "# {org}\n\nSource: CRM ({provider}), automatic event log. No person data.\n\n---\n\n",
        "open_deals": "{n} open deals",
    },
}


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def _plain(text):
    """Saglayici metnini ev stiline indir: tire yasagi, tablo ve wikilink kacisi."""
    text = str(text or "")
    text = text.replace("\u2014", "-").replace("\u2013", "-")
    text = text.replace("|", "/").replace("[[", "(").replace("]]", ")")
    return re.sub(r"\s+", " ", text).strip()


def safe_name(title):
    title = title.replace("\u2014", "-").replace("\u2013", "-")
    return re.sub(r"[^\w\sÇçĞğİıÖöŞşÜü&.-]", "", title).strip()[:80] or "Sirket"


def _id_of(value):
    """Pipedrive v1 iliskili alanlari bazen {value, name} sozlugu olarak dondurur."""
    if isinstance(value, dict):
        value = value.get("value") or value.get("id")
    return str(value) if value not in (None, "") else None


def _d(value):
    """'YYYY-MM-DD' veya 'YYYY-MM-DD HH:MM:SS' -> date; bos/bozuk -> None."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------- saglayici

class AuthError(Exception):
    pass


class Provider:
    """Normalize sozlukler dondurur; saglayici alanlari bu sinifin disina cikmaz.

    deal: id, title, org_id, org_name, pipeline_id, stage_id, value, currency,
          expected_close_date, last_activity_date, next_activity_date,
          stage_change_time, add_time, update_time, label, status, owner_id
    org : id, name, open_deals_count, last_activity_date, next_activity_date
    """
    name = "base"

    def me(self):
        raise NotImplementedError

    def pipelines_and_stages(self):
        raise NotImplementedError

    def owner_organizations(self, owner_id):
        raise NotImplementedError

    def open_deals(self, owner_id):
        raise NotImplementedError

    def deal(self, deal_id):
        raise NotImplementedError


class PipedriveProvider(Provider):
    """Pipedrive REST v1 (x-api-token). v1 secildi: anlasma nesnesi last/next
    activity ve stage_change_time tasiyor, v2 bunlari Activities API'sine tasidi."""
    name = "pipedrive"

    def __init__(self, token):
        self.token = token
        self.base = BOOTSTRAP_URL

    def _get(self, path, params=None, _retry=True):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "x-api-token": self.token,
            "Accept": "application/json",
            "User-Agent": "brainless-crm/1",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError(f"HTTP {e.code}")
            if e.code == 429 and _retry:
                wait = e.headers.get("Retry-After") or "2"
                try:
                    wait = min(int(wait), 30)
                except ValueError:
                    wait = 2
                log(f"429, {wait}s bekleniyor: {path}")
                time.sleep(wait)
                return self._get(path, params, _retry=False)
            raise RuntimeError(f"HTTP {e.code} {path}")
        if payload.get("success") is False:
            raise RuntimeError(f"API hata {path}: {str(payload.get('error'))[:80]}")
        return payload

    def _paged(self, path, params=None):
        params = dict(params or {})
        start, out = 0, []
        for _ in range(MAX_PAGES):
            params.update(start=start, limit=PAGE_SIZE)
            payload = self._get(path, params)
            out.extend(payload.get("data") or [])
            pag = (payload.get("additional_data") or {}).get("pagination") or {}
            if not pag.get("more_items_in_collection"):
                break
            start = pag.get("next_start") or start + PAGE_SIZE
        return out

    def me(self):
        data = self._get("/users/me").get("data") or {}
        domain = data.get("company_domain")
        if domain:
            self.base = f"https://{domain}.pipedrive.com/api/v1"
        return {"id": str(data.get("id")), "name": data.get("name") or "", "company": domain or ""}

    def pipelines_and_stages(self):
        pipes = {}
        for p in self._paged("/pipelines"):
            pipes[str(p["id"])] = {"name": _plain(p.get("name")) or str(p["id"]), "stages": {}}
        for s in self._paged("/stages"):
            pid = str(s.get("pipeline_id"))
            pipes.setdefault(pid, {"name": pid, "stages": {}})
            pipes[pid]["stages"][str(s["id"])] = _plain(s.get("name")) or str(s["id"])
        return pipes

    def owner_organizations(self, owner_id):
        return [self._norm_org(o) for o in self._paged("/organizations", {"user_id": owner_id})]

    def open_deals(self, owner_id):
        return [self._norm_deal(d) for d in self._paged("/deals", {"user_id": owner_id, "status": "open"})]

    def deal(self, deal_id):
        try:
            data = self._get(f"/deals/{deal_id}").get("data")
        except RuntimeError:
            return None
        return self._norm_deal(data) if data else None

    @staticmethod
    def _norm_deal(raw):
        # Kisi alanlari (person_id, person_name, cc_email, ...) bilerek eslenmez.
        org = raw.get("org_id")
        org_name = raw.get("org_name") or (org.get("name") if isinstance(org, dict) else "") or ""
        return {
            "id": str(raw["id"]),
            "title": _plain(raw.get("title")),
            "org_id": _id_of(org),
            "org_name": _plain(org_name),
            "pipeline_id": str(raw.get("pipeline_id")),
            "stage_id": str(raw.get("stage_id")),
            "value": raw.get("value") or 0,
            "currency": raw.get("currency") or "",
            "expected_close_date": raw.get("expected_close_date"),
            "last_activity_date": raw.get("last_activity_date"),
            "next_activity_date": raw.get("next_activity_date"),
            "stage_change_time": raw.get("stage_change_time"),
            "add_time": raw.get("add_time"),
            "update_time": raw.get("update_time"),
            "label": str(raw["label"]) if raw.get("label") not in (None, "") else None,
            "status": raw.get("status") or "open",
            "owner_id": _id_of(raw.get("user_id")),
        }

    @staticmethod
    def _norm_org(raw):
        return {
            "id": str(raw["id"]),
            "name": _plain(raw.get("name")),
            "open_deals_count": raw.get("open_deals_count") or 0,
            "last_activity_date": raw.get("last_activity_date"),
            "next_activity_date": raw.get("next_activity_date"),
        }


PROVIDERS = {"pipedrive": PipedriveProvider}


def get_provider(conf_dir):
    """Token yoksa None (sessiz cikis). Bilinmeyen saglayici adi -> exit 2."""
    name = (read_file(os.path.join(conf_dir, "crm_provider")) or "pipedrive").lower()
    cls = PROVIDERS.get(name)
    if cls is None:
        log(f"bilinmeyen crm_provider: {name} (desteklenen: {', '.join(PROVIDERS)})")
        sys.exit(2)
    token = read_file(os.path.join(conf_dir, f"{name}_api_token"))
    if not token:
        return None
    return cls(token)


class Config:
    def __init__(self, conf_dir, args):
        raw = read_file(os.path.join(conf_dir, "crm_pipelines")) or ""
        self.pipelines = {ln.strip().casefold() for ln in raw.splitlines() if ln.strip()}
        owner = read_file(os.path.join(conf_dir, "crm_owner")) or ""
        self.owner_id = owner if owner.isdigit() else None
        self.no_activity_days = args.no_activity_days
        self.stage_days = args.stage_days
        self.lang = args.lang


# --------------------------------------------------------------------------- hesap

def compute_flags(item, today, cfg, is_deal=True):
    """[(kod, deger, seviye)] ; seviye WARN|RED."""
    flags = []
    last = _d(item.get("last_activity_date")) or _d(item.get("add_time"))
    if last:
        days = (today - last).days
        if days > cfg.no_activity_days:
            # Anlasmasi olmayan sirket en fazla sari: kirmizi anlasmalara ayrilir.
            sev = "RED" if is_deal and days > 2 * cfg.no_activity_days else "WARN"
            flags.append(("no_activity", days, sev))
    if not is_deal:
        return flags
    nxt = _d(item.get("next_activity_date"))
    if nxt and nxt < today:
        flags.append(("next_overdue", nxt.isoformat(), "RED"))
    close = _d(item.get("expected_close_date"))
    if close and close < today:
        flags.append(("close_passed", close.isoformat(), "WARN"))
    since = _d(item.get("stage_change_time")) or _d(item.get("add_time"))
    if since:
        days = (today - since).days
        if days > cfg.stage_days:
            flags.append(("stage_stale", days, "WARN"))
    return flags


def stage_name(pipes, deal):
    pipe = pipes.get(deal.get("pipeline_id")) or {}
    return (pipe.get("stages") or {}).get(deal.get("stage_id")) or deal.get("stage_id") or "?"


def _ev(now_iso, typ, org_id, org_name, title="", detail="", deal_id=None):
    return {"ts": now_iso, "type": typ, "deal_id": deal_id, "org_id": org_id,
            "org_name": org_name, "title": title, "detail": detail}


def diff(prev, deals, orgs, pipes, provider, owner_id, now_iso, L):
    """Onceki durumla karsilastir; olay listesi dondur. Ilk kosu = temel cizgi."""
    events = []
    prev_deals = prev.get("deals") or {}
    prev_orgs = prev.get("orgs") or {}
    prev_pipes = prev.get("pipelines") or pipes
    cur_deal_ids = {d["id"] for d in deals}
    cur_org_ids = {o["id"] for o in orgs}

    if not prev:
        by_org = {}
        for d in deals:
            by_org.setdefault(d["org_id"], []).append(d)
        for o in orgs:
            ds = by_org.get(o["id"], [])
            detail = L["open_deals"].format(n=len(ds))
            if ds:
                detail += ": " + ", ".join(f"\"{d['title']}\" ({stage_name(pipes, d)})" for d in ds)
            events.append(_ev(now_iso, "baseline", o["id"], o["name"], detail=detail))
        for oid, ds in by_org.items():
            if oid and oid not in cur_org_ids:
                detail = L["open_deals"].format(n=len(ds)) + ": " + ", ".join(
                    f"\"{d['title']}\" ({stage_name(pipes, d)})" for d in ds)
                events.append(_ev(now_iso, "baseline", oid, ds[0]["org_name"], detail=detail))
        return events

    for d in deals:
        old = prev_deals.get(d["id"])
        if old is None:
            events.append(_ev(now_iso, "deal_new", d["org_id"], d["org_name"], d["title"],
                              stage_name(pipes, d), d["id"]))
            continue
        if old.get("stage_id") != d["stage_id"]:
            events.append(_ev(now_iso, "stage", d["org_id"], d["org_name"], d["title"],
                              f"{stage_name(prev_pipes, old)} -> {stage_name(pipes, d)}", d["id"]))
        if (old.get("value"), old.get("currency")) != (d["value"], d["currency"]):
            events.append(_ev(now_iso, "value", d["org_id"], d["org_name"], d["title"],
                              f"{fmt_money(old.get('value'), old.get('currency'), L)} -> "
                              f"{fmt_money(d['value'], d['currency'], L)}", d["id"]))
    for did, old in prev_deals.items():
        if did in cur_deal_ids:
            continue
        typ = "gone"
        fresh = provider.deal(did) if provider else None
        if fresh:
            if fresh["status"] in ("won", "lost", "deleted"):
                typ = fresh["status"]
            elif fresh.get("owner_id") and str(fresh["owner_id"]) != str(owner_id):
                typ = "reassigned"
        events.append(_ev(now_iso, typ, old.get("org_id"), old.get("org_name"), old.get("title"), "", did))
    for o in orgs:
        old = prev_orgs.get(o["id"])
        if old is None:
            events.append(_ev(now_iso, "org_new", o["id"], o["name"]))
        elif old.get("name") and old["name"] != o["name"]:
            events.append(_ev(now_iso, "org_renamed", o["id"], o["name"], detail=f"{old['name']} -> {o['name']}"))
    for oid, old in prev_orgs.items():
        if oid not in cur_org_ids:
            events.append(_ev(now_iso, "org_gone", oid, old.get("name")))
    return events


def assign_files(prev_files, events):
    """Sirket id -> Inbox/CRM dosya adi. Ad cakismasinda id soneki."""
    files = dict(prev_files or {})
    for ev in events:
        oid = ev.get("org_id")
        if not oid or oid in files:
            continue
        base = safe_name(ev.get("org_name") or oid)
        name = f"{base}.md"
        if name in files.values():
            name = f"{base} (crm {oid}).md"
        files[oid] = name
    return files


def event_text(ev, L):
    key = "ev_" + ev["type"]
    tmpl = L.get(key) or "{org}: {title} {detail}"
    return tmpl.format(org=ev.get("org_name") or L["no_org"], title=ev.get("title") or "",
                       detail=ev.get("detail") or "").strip()


def fmt_money(value, currency, L):
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        num = 0.0
    text = f"{num:,.0f}"
    if L is LABELS["tr"]:
        text = text.replace(",", ".")
    return f"{text} {currency}".strip()


def flag_text(code, val, L):
    if code == "no_activity":
        return L["flag_no_activity"].format(d=val)
    if code == "next_overdue":
        return L["flag_next_overdue"].format(date=val)
    if code == "close_passed":
        return L["flag_close_passed"].format(date=val)
    return L["flag_stage_stale"].format(d=val)


def flag_code(code, cfg, L):
    if code == "no_activity":
        return L["code_no_activity"].format(n=cfg.no_activity_days)
    if code == "stage_stale":
        return L["code_stage_stale"].format(n=cfg.stage_days)
    return L["code_" + code]


def render(state, deals, orgs, deal_flags, org_flags, events, pipes, cfg, now, L, baseline):
    today = now.date()
    cutoff = (now - timedelta(hours=24)).isoformat(timespec="minutes")
    recent = [e for e in events if e["ts"] >= cutoff and e["type"] != "baseline"]
    all_flags = [(d, f) for d in deals for f in deal_flags.get(d["id"], [])]
    all_flags += [(o, f) for o in orgs if o["id"] in org_flags for f in org_flags[o["id"]]]
    worst = "OK"
    for _, (_, _, sev) in all_flags:
        if sev == "RED":
            worst = "RED"
            break
        worst = "WARN"
    icon = {"OK": "🟢", "WARN": "🟡", "RED": "🔴"}[worst]
    n_flags = len(all_flags)
    lines = [
        L["title"], "",
        f"**{L['status']}: {icon} {worst}** ({L['updated']}: {now.strftime('%Y-%m-%d %H:%M')}, "
        f"{L['source']}: {state['provider']}, {L['owner']}: {OWNER})", "",
        "- " + L["counts"].format(orgs=len(orgs), deals=len(deals), flags=n_flags, changes=len(recent)),
        "", L["flags"],
    ]
    flagged = {}
    for item, (code, val, sev) in all_flags:
        key = item["id"]
        entry = flagged.setdefault(key, {"item": item, "sev": "WARN", "parts": []})
        entry["parts"].append(flag_text(code, val, L))
        if sev == "RED":
            entry["sev"] = "RED"
    if flagged:
        order = sorted(flagged.values(), key=lambda e: (e["sev"] != "RED", e["item"].get("org_name") or e["item"].get("name") or ""))
        for e in order:
            item = e["item"]
            mark = "🔴" if e["sev"] == "RED" else "🟡"
            if "title" in item:
                who = f"{item.get('org_name') or L['no_org']}: \"{item['title']}\""
            else:
                who = item["name"]
            lines.append(f"- {mark} {who} {', '.join(e['parts'])}")
    else:
        lines.append(L["none"])

    by_pipe = {}
    for d in deals:
        by_pipe.setdefault(d["pipeline_id"], []).append(d)
    for pid in list(pipes) + [p for p in by_pipe if p not in pipes]:
        ds = by_pipe.get(pid)
        if not ds:
            continue
        pname = (pipes.get(pid) or {}).get("name") or pid
        lines += ["", f"## {pname}", "| " + " | ".join(L["cols"]) + " |", "|" + "---|" * len(L["cols"])]
        ds.sort(key=lambda d: (not any(f[2] == "RED" for f in deal_flags.get(d["id"], [])),
                               not deal_flags.get(d["id"]), d.get("org_name") or ""))
        totals = {}
        for d in ds:
            since = _d(d.get("stage_change_time")) or _d(d.get("add_time"))
            age = f" ({(today - since).days}{L['day_short']})" if since else ""
            codes = ", ".join(flag_code(c, cfg, L) for c, _, _ in deal_flags.get(d["id"], []))
            lines.append("| " + " | ".join([
                d.get("org_name") or L["no_org"], d["title"], stage_name(pipes, d) + age,
                fmt_money(d["value"], d["currency"], L), (d.get("last_activity_date") or "")[:10],
                (d.get("next_activity_date") or "")[:10], codes or "-"]) + " |")
            if d["currency"]:
                totals[d["currency"]] = totals.get(d["currency"], 0) + float(d["value"] or 0)
        tot = " + ".join(fmt_money(v, c, L) for c, v in sorted(totals.items()))
        lines += ["", f"{L['total']}: {len(ds)} {L['deal_word']}" + (f", {tot}" if tot else "")]

    lines += ["", L["changes"]]
    if baseline:
        lines.append(L["baseline_note"])
    elif recent:
        for e in sorted(recent, key=lambda e: e["ts"], reverse=True):
            lines.append(f"- {e['ts'][:16].replace('T', ' ')} {event_text(e, L)}")
    else:
        lines.append(L["none"])

    idle = [o for o in orgs if o["id"] in org_flags]
    lines += ["", L["idle_orgs"]]
    if idle:
        for o in sorted(idle, key=lambda o: o["name"]):
            codes = ", ".join(flag_code(c, cfg, L) for c, _, _ in org_flags[o["id"]])
            last = (o.get("last_activity_date") or "?")[:10]
            lines.append(f"- {o['name']} ({L['last_activity']} {last}" + (f", {codes}" if codes else "") + ")")
    else:
        lines.append(L["none"])
    lines += ["", L["footer"], ""]
    return "\n".join(lines), worst


def analyse(provider, prev, cfg, now):
    """Saf hesap: ag disinda yan etkisi yok; main() ve self_test() bunu cagirir."""
    L = LABELS[cfg.lang]
    today = now.date()
    now_iso = now.isoformat(timespec="minutes")
    me = provider.me()
    owner_id = cfg.owner_id or me["id"]
    pipes = provider.pipelines_and_stages()
    deals = provider.open_deals(owner_id)
    orgs = provider.owner_organizations(owner_id)
    if cfg.pipelines:
        wanted = {pid for pid, p in pipes.items() if p["name"].casefold() in cfg.pipelines}
        if wanted:
            deals = [d for d in deals if d["pipeline_id"] in wanted]
        else:
            log(f"crm_pipelines hicbir boru hattiyla eslesmedi ({', '.join(sorted(cfg.pipelines))}); hepsi alindi")
    deal_flags = {d["id"]: compute_flags(d, today, cfg) for d in deals}
    with_deals = {d["org_id"] for d in deals if d["org_id"]}
    org_flags = {o["id"]: compute_flags(o, today, cfg, is_deal=False) for o in orgs if o["id"] not in with_deals}
    baseline = not prev
    events = diff(prev, deals, orgs, pipes, provider, owner_id, now_iso, L)
    files = assign_files(prev.get("files"), events)
    keep_after = (now - timedelta(days=EVENT_KEEP_DAYS)).isoformat(timespec="minutes")
    kept = [e for e in (prev.get("events") or []) if e.get("ts", "") >= keep_after] + events
    state = {
        "version": 1, "provider": provider.name, "owner_id": owner_id, "updated_at": now_iso,
        "pipelines": pipes,
        "orgs": {o["id"]: dict(o, file=files.get(o["id"])) for o in orgs},
        "deals": {d["id"]: d for d in deals},
        "files": files, "events": kept,
    }
    text, worst = render(state, deals, orgs, deal_flags, org_flags, kept, pipes, cfg, now, L, baseline)
    return {"text": text, "state": state, "events": events, "files": files, "worst": worst,
            "counts": (len(orgs), len(deals), sum(len(v) for v in deal_flags.values()) + sum(len(v) for v in org_flags.values()))}


# --------------------------------------------------------------------------- yazma

def load_state():
    try:
        with open(STATE_FILE) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) and data.get("version") == 1 else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def write_status(outcome, detail=""):
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w") as fh:
        fh.write(f"{datetime.now().isoformat(timespec='seconds')}\t{outcome}\t{_plain(detail)[:160]}\n")


def append_org_logs(events, files, provider_name, L):
    """Sadece olayi olan sirketlerin dosyasina dokunur."""
    grouped = {}
    for ev in events:
        if ev.get("org_id") and ev["org_id"] in files:
            grouped.setdefault(ev["org_id"], []).append(ev)
    written = 0
    for oid, evs in grouped.items():
        path = os.path.join(ORG_DIR, files[oid])
        os.makedirs(ORG_DIR, exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a") as fh:
            if new:
                fh.write(L["log_header"].format(org=evs[0].get("org_name") or oid, provider=provider_name))
            for ev in evs:
                fh.write(f"- {ev['ts'][:16].replace('T', ' ')} {event_text(ev, L)}\n")
        written += 1
    return written


_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def write_snapshot(text):
    """Govde (zaman damgasi haric) degismediyse dosyaya dokunma: saatlik commit gurultusu olmasin."""
    old = read_file(SNAPSHOT_FILE) or ""
    if _TS_RE.sub("", old).strip() == _TS_RE.sub("", text).strip():
        return False
    os.makedirs(os.path.dirname(SNAPSHOT_FILE), exist_ok=True)
    with open(SNAPSHOT_FILE, "w") as fh:
        fh.write(text)
    return True


# --------------------------------------------------------------------------- self-test

def _fixture():
    return {
        "me": {"id": "7", "name": "Owner", "company": "example"},
        "pipelines": {
            "1": {"name": "Enterprise", "stages": {"10": "Discovery", "11": "Proposal", "12": "Negotiation"}},
            "2": {"name": "Business Development", "stages": {"20": "Idea", "21": "Contact"}},
        },
        "orgs": [
            {"id": "100", "name": "Acme Holding", "open_deals_count": 1, "last_activity_date": "2026-08-15", "next_activity_date": None},
            {"id": "101", "name": "Beta Lojistik", "open_deals_count": 1, "last_activity_date": "2026-09-08", "next_activity_date": "2026-09-15"},
            {"id": "102", "name": "Gamma Enerji", "open_deals_count": 0, "last_activity_date": "2026-07-01", "next_activity_date": None},
        ],
        "deals": [
            {"id": "500", "title": "Acme HRIS", "org_id": "100", "org_name": "Acme Holding", "pipeline_id": "1", "stage_id": "11",
             "value": 12000, "currency": "EUR", "expected_close_date": "2026-09-01", "last_activity_date": "2026-08-15",
             "next_activity_date": "2026-09-05", "stage_change_time": "2026-07-20 09:00:00", "add_time": "2026-06-01 09:00:00",
             "update_time": "2026-08-15 09:00:00", "label": None, "status": "open", "owner_id": "7"},
            {"id": "501", "title": "Beta bordro", "org_id": "101", "org_name": "Beta Lojistik", "pipeline_id": "1", "stage_id": "10",
             "value": 300000, "currency": "TRY", "expected_close_date": "2026-12-01", "last_activity_date": "2026-09-08",
             "next_activity_date": "2026-09-15", "stage_change_time": "2026-09-01 09:00:00", "add_time": "2026-09-01 09:00:00",
             "update_time": "2026-09-08 09:00:00", "label": None, "status": "open", "owner_id": "7"},
            {"id": "502", "title": "Kanal ortakligi", "org_id": None, "org_name": "", "pipeline_id": "2", "stage_id": "20",
             "value": 0, "currency": "", "expected_close_date": None, "last_activity_date": "2026-09-09",
             "next_activity_date": None, "stage_change_time": "2026-09-09 09:00:00", "add_time": "2026-09-09 09:00:00",
             "update_time": "2026-09-09 09:00:00", "label": None, "status": "open", "owner_id": "7"},
        ],
    }


class _FakeProvider(Provider):
    name = "fake"

    def __init__(self, fx):
        self.fx = fx
        self.closed = {}

    def me(self):
        return self.fx["me"]

    def pipelines_and_stages(self):
        return self.fx["pipelines"]

    def owner_organizations(self, owner_id):
        return [dict(o) for o in self.fx["orgs"]]

    def open_deals(self, owner_id):
        return [dict(d) for d in self.fx["deals"]]

    def deal(self, deal_id):
        return self.closed.get(deal_id)


def self_test():
    class Args:
        no_activity_days, stage_days, lang = DEFAULT_NO_ACTIVITY_DAYS, DEFAULT_STAGE_DAYS, "tr"
    cfg = Config("/nonexistent", Args)
    now = datetime(2026, 9, 10, 10, 0)
    fx = _fixture()
    prov = _FakeProvider(fx)

    # 1) bayraklar
    f500 = {c for c, _, _ in compute_flags(fx["deals"][0], now.date(), cfg)}
    assert f500 == {"no_activity", "next_overdue", "close_passed", "stage_stale"}, f500
    assert compute_flags(fx["deals"][1], now.date(), cfg) == []
    f102 = compute_flags(fx["orgs"][2], now.date(), cfg, is_deal=False)
    assert f102 and f102[0][2] == "WARN", f102

    # 2) ilk kosu = temel cizgi
    r1 = analyse(prov, {}, cfg, now)
    assert {e["type"] for e in r1["events"]} == {"baseline"}, r1["events"]
    assert set(r1["files"]) == {"100", "101", "102"}, r1["files"]
    assert len(r1["state"]["deals"]) == 3 and r1["worst"] == "RED"
    assert "## Enterprise" in r1["text"] and "## Business Development" in r1["text"]
    assert "(şirket yok)" in r1["text"] and "Gamma Enerji" in r1["text"]
    assert "\u2014" not in r1["text"] and "\u2013" not in r1["text"]

    # 3) ikinci kosu: asama degisti, bir anlasma kazanildi, yeni anlasma, sirket gitti
    fx["deals"][0]["stage_id"] = "12"
    won = dict(fx["deals"][1], status="won")
    prov.closed["501"] = won
    fx["deals"][1] = {**fx["deals"][2], "id": "503", "title": "Beta ek modul", "org_id": "101",
                      "org_name": "Beta Lojistik", "pipeline_id": "1", "stage_id": "10"}
    fx["orgs"].pop()
    r2 = analyse(prov, r1["state"], cfg, now + timedelta(hours=1))
    types = sorted(e["type"] for e in r2["events"])
    assert types == ["deal_new", "org_gone", "stage", "won"], types
    stage_ev = next(e for e in r2["events"] if e["type"] == "stage")
    assert stage_ev["detail"] == "Proposal -> Negotiation", stage_ev
    assert "## Değişenler (son 24 saat)\n- 2026-09-10 11:00" in r2["text"]
    assert len(r2["state"]["events"]) == len(r1["events"]) + len(r2["events"])

    # 4) sessiz kosu: olay yok
    r3 = analyse(prov, r2["state"], cfg, now + timedelta(hours=2))
    assert r3["events"] == [], r3["events"]

    # 5) ad cakismasi ve metin temizligi
    files = assign_files({"1": "Acme.md"}, [_ev("t", "org_new", "2", "Acme")])
    assert files["2"] == "Acme (crm 2).md", files
    assert _plain("A \u2014 B | [[C]]") == "A - B / (C)"
    assert LABELS["tr"] is LABELS["tr"] and fmt_money(12000, "EUR", LABELS["tr"]) == "12.000 EUR"
    print("OK")


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch and print, write nothing")
    ap.add_argument("--self-test", action="store_true", help="unit test with a fixture (no network)")
    ap.add_argument("--no-activity-days", type=int, default=DEFAULT_NO_ACTIVITY_DAYS, metavar="N")
    ap.add_argument("--stage-days", type=int, default=DEFAULT_STAGE_DAYS, metavar="M")
    ap.add_argument("--lang", choices=sorted(LABELS), default=LANG if LANG in LABELS else "en")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return

    provider = get_provider(CONF_DIR)
    if provider is None:
        return  # kimlik yoksa sessizce cik
    cfg = Config(CONF_DIR, args)
    L = LABELS[cfg.lang]
    prev = load_state()
    try:
        result = analyse(provider, prev, cfg, datetime.now())
    except AuthError as e:
        log(f"kimlik reddedildi: {e}")
        if not args.dry_run:
            write_status("auth", str(e))
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        log(f"hata: {type(e).__name__}: {e}")
        if not args.dry_run:
            write_status("error", f"{type(e).__name__}: {e}")
        sys.exit(1)

    n_orgs, n_deals, n_flags = result["counts"]
    if args.dry_run:
        print(result["text"])
        for ev in result["events"]:
            print(f"EVENT {ev['ts']} {event_text(ev, L)}")
        log(f"dry-run: {n_orgs} org, {n_deals} deal, {n_flags} bayrak, {len(result['events'])} olay (yazilmadi)")
        return

    save_state(result["state"])
    logged = append_org_logs(result["events"], result["files"], provider.name, L)
    changed = write_snapshot(result["text"])
    write_status("ok", f"{n_orgs} org, {n_deals} deal, {n_flags} bayrak")
    log(f"{n_orgs} org, {n_deals} deal, {n_flags} bayrak, {len(result['events'])} olay, "
        f"{logged} sirket gunlugu, CRM.md {'yazildi' if changed else 'degismedi'}")


if __name__ == "__main__":
    main()
