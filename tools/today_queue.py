#!/usr/bin/env python3
"""A bounded daily queue. Selection is local; source writes require explicit apply."""
import argparse
from contextlib import contextmanager
from datetime import date, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from build_dashboard import fm
from compile_resources import _is_private
from i18n import t, t_list
from owner_profile import LANG

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
CATEGORIES = ("decision", "commitment", "evidence")


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".today-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def as_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class TodayQueue:
    def __init__(self, vault=VAULT, today=None):
        self.vault = Path(vault).resolve()
        self.today = today or date.today()
        self.state_path = self.vault / ".agents/state/today_queue.json"
        self.surface = self.vault / "_Agent-Context/TODAY.md"

    @contextmanager
    def locked(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                # A truncated state file should not strand the queue; source notes remain authoritative.
                state = {}
            if not isinstance(state, dict):
                state = {}
            if not isinstance(state.get("records"), dict):
                state["records"] = {}
            if not isinstance(state.get("history"), list):
                state["history"] = []
            yield state
            atomic_write(self.state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
            atomic_write(self.surface, self.render(state))

    def source(self, relative):
        path = self.vault / relative
        resolved = path.resolve()
        if (not resolved.is_relative_to(self.vault) or _is_private(path) or _is_private(resolved)
                or not path.is_file()):
            raise ValueError(t("today_queue.source_missing"))
        rel = resolved.relative_to(self.vault)
        if rel.parts[0] not in ("Thinking", "Work", "Personal", "Library") and str(rel) != "_Agent-Context/TASKS.md":
            raise ValueError(t("today_queue.source_missing"))
        return resolved

    def candidate(self, category, relative, mode, title=None, line=None):
        path = self.source(relative)
        text = path.read_text(encoding="utf-8")
        identity = f"{category}\0{relative}\0{line or mode}"
        return {"id": digest(identity)[:12], "category": category, "source": relative,
                "mode": mode, "title": (title or path.stem)[:180], "line": line,
                "fingerprint": digest(line or text),
                "excerpt": re.sub(r"^---\n.*?\n---\n", "", line or text, flags=re.S)[:600]}

    def candidates(self):
        out = []
        for path in sorted((self.vault / "Thinking/Decisions").glob("*.md")):
            try:
                relative = path.relative_to(self.vault).as_posix()
                text = self.source(relative).read_text()
                meta = fm(text)
                status = meta.get("status", "").lower()
                review = as_date(meta.get("review") or meta.get("revisit"))
                mode = None
                if status == "pending" and (not review or review <= self.today + timedelta(days=14)):
                    mode = "decide"
                elif status == "deferred" and review and review <= self.today:
                    mode = "decide"
                elif status == "decided" and review and review <= self.today and not meta.get("graded"):
                    mode = "grade"
                if mode:
                    item = self.candidate("decision", relative, mode)
                    item["rank"] = (review or self.today).isoformat()
                    out.append(item)
            except (OSError, ValueError):
                continue
        ledger = self.vault / "_Agent-Context/TASKS.md"
        if ledger.exists():
            section = None
            aliases = {h for key in ("promises", "waiting") for h in t_list(f"gtasks_sync.section_{key}")}
            for line in ledger.read_text().splitlines():
                if line.startswith("## "):
                    section = line[3:].strip()
                if section not in aliases or not line.startswith("- [ ] "):
                    continue
                parts = line[6:].split(" | ")
                captured = as_date(parts[2]) if len(parts) > 2 else None
                if captured and captured > self.today - timedelta(days=2):
                    continue
                item = self.candidate("commitment", "_Agent-Context/TASKS.md", "complete", parts[0], line)
                item["rank"] = (captured or self.today).isoformat()
                out.append(item)
        evidence = []
        resurfaced = self.vault / "_Agent-Context/RESURFACE.md"
        if resurfaced.exists():
            text = resurfaced.read_text()
            stamp = re.search(r"\d{4}-\d{2}-\d{2}", text)
            when = as_date(stamp.group()) if stamp else None
            if when and 0 <= (self.today - when).days < 7:
                evidence += [m.group(1) + ".md" for m in re.finditer(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]", text)]
        evidence += [p.relative_to(self.vault).as_posix() for p in sorted((self.vault / "Thinking/Beliefs").glob("*.md"))]
        for index, relative in enumerate(dict.fromkeys(evidence)):
            try:
                item = self.candidate("evidence", relative, "review")
                item["rank"] = f"{index:06d}"
                out.append(item)
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda x: (x["rank"], x["id"]))

    def build(self, state):
        # A day's slots are fixed. Completing one does not produce another nudge.
        if state.get("day") == self.today.isoformat():
            return
        selected = []
        candidates = self.candidates()
        for category in CATEGORIES:
            for item in candidates:
                if item["category"] != category:
                    continue
                previous = state["records"].get(item["id"], {})
                until = as_date(previous.get("until"))
                if until and until > self.today:
                    continue
                if previous.get("status") == "dismissed" and previous.get("fingerprint") == item["fingerprint"]:
                    continue
                if (previous.get("status") == "applied" and previous.get("resolution") == "review"
                        and previous.get("fingerprint") == item["fingerprint"]):
                    continue
                count = previous.get("deferrals", 0)
                record = {**item, "status": "open", "deferrals": count,
                          "adaptive": count >= 2, "messages": previous.get("messages", []),
                          "chat_id": previous.get("chat_id"), "revision": previous.get("revision", 0) + 1}
                # Keep unanswered previews when the input has not changed.
                if (previous.get("draft") and previous.get("status") in ("open", "drafted", "deferred")
                        and previous.get("fingerprint") == item["fingerprint"]
                        and (count < 2 or previous.get("draft_mode") == "blocker")):
                    record.update({k: previous[k] for k in ("draft", "draft_mode")})
                    record["status"] = "drafted"
                state["records"][item["id"]] = record
                selected.append(item["id"])
                break
        state.update(day=self.today.isoformat(), selected=selected)

    def render_item(self, item):
        question = t("today_queue.blocker") if item.get("adaptive") else t("today_queue.q_" + item["mode"])
        text = f"{t('today_queue.' + item['category'])}: {item['title']}\n{item['source'][:300]}\n\n{question}"
        if item["category"] != "commitment":
            text += "\n\n" + item["excerpt"]
        if item.get("draft"):
            text += "\n\n" + t("today_queue.preview") + "\n" + item["draft"]
            text += "\n\n" + t("today_queue.effect_" + item["draft_mode"])
        return text

    def render(self, state):
        lines = [f"# {t('today_queue.title')}", "", state.get("day", self.today.isoformat()), ""]
        for key in state.get("selected", []):
            item = state["records"][key]
            lines += [f"## {item['title']}", f"`{key}` | {item['status']}",
                      f"[[{item['source'].removesuffix('.md')}]]", "",
                      "\n".join("> " + line for line in self.render_item(item).splitlines()), ""]
        if not state.get("selected"):
            lines.append(t("today_queue.empty"))
        counts = {mode: sum(1 for event in state["history"] if event.get("action") == "apply"
                           and event.get("mode") == mode and as_date(event["date"]) >= self.today - timedelta(days=6))
                  for mode in ("decide", "grade", "complete", "review", "blocker")}
        lines += ["", t("today_queue.week", **counts), ""]
        return "\n".join(lines)

    def act(self, state, key, action, text="", revision=None):
        if key not in state.get("selected", []):
            raise ValueError(t("today_queue.expired"))
        item = state["records"][key]
        if revision is not None and revision != item["revision"]:
            raise ValueError(t("today_queue.expired"))
        if item["status"] in ("applied", "deferred", "dismissed"):
            return t("today_queue.already_handled")
        if action == "edit":
            item.pop("draft", None)
            item.pop("draft_mode", None)
            item["status"] = "open"
            item["revision"] += 1
            return t("today_queue.edit_prompt")
        if action == "defer":
            until = as_date(text)
            if not until or until <= self.today:
                raise ValueError(t("today_queue.date_required"))
            item.update(status="deferred", until=until.isoformat(), deferrals=item["deferrals"] + 1)
        elif action == "dismiss":
            if not text.strip():
                raise ValueError(t("today_queue.reason_required"))
            item.update(status="dismissed", reason=text.strip()[:1000])
        elif action in ("answer", "apply"):
            path = self.source(item["source"])
            current = path.read_text()
            valid = current.splitlines().count(item["line"]) == 1 if item["line"] else digest(current) == item["fingerprint"]
            if not valid:
                raise ValueError(t("today_queue.source_changed"))
            if action == "answer":
                if not text.strip() or len(text) > 1800:
                    raise ValueError(t("today_queue.answer_required"))
                item.update(status="drafted", draft=text.strip(), revision=item["revision"] + 1,
                            draft_mode="blocker" if item["adaptive"] else ("revise" if item["mode"] == "complete" else item["mode"]))
                return self.render_item(item)
            mode = item.get("draft_mode", item["mode"])
            if not item.get("draft") and (item["mode"] != "complete" or item["adaptive"]):
                return t("today_queue.answer_required")
            answer = item.get("draft", "")
            updated = current
            if mode in ("complete", "revise"):
                if mode == "revise" and ("\n" in answer or " | " in answer):
                    raise ValueError(t("today_queue.task_single_line"))
                replacement = item["line"].replace("- [ ]", "- [x]", 1)
                if mode == "revise":
                    fields = item["line"].split(" | ")
                    replacement = " | ".join(["- [ ] " + answer, *fields[1:]])
                updated = "".join(replacement + ("\n" if line.endswith("\n") else "")
                                  if line.rstrip("\n") == item["line"] else line for line in current.splitlines(keepends=True))
            elif mode in ("decide", "grade"):
                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".agents/scripts"))
                from thinking_loop import set_fm
                updated = set_fm(current, "status", "decided") if mode == "decide" else set_fm(current, "graded", self.today.isoformat())
                heading = "Decision" if mode == "decide" else "Outcome"
                updated = updated.rstrip() + f"\n\n## {heading} ({self.today})\n\n{answer}\n"
            writes = [{"relative": item["source"], "before": current, "after": updated}]
            if mode == "grade":
                calibration = self.vault / "Thinking/Calibration.md"
                if calibration.exists():
                    calibration = self.source("Thinking/Calibration.md")
                    before = calibration.read_text()
                    rows = []
                    for row in before.splitlines(keepends=True):
                        if row.startswith("|") and f"[[{path.stem}]]" in row:
                            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
                            if len(cells) >= 6:
                                cells[4] = " ".join(answer.replace("|", "/").split())[:160]
                                row = "| " + " | ".join(cells) + " |\n"
                        rows.append(row)
                    writes.append({"relative": "Thinking/Calibration.md", "before": before, "after": "".join(rows)})
            # A prepared operation can be replayed after a process interruption.
            receipt = self.vault / ".wiki/digests/queries" / f"{self.today}-today-{key}-{item['revision']}.md"
            body = (f"---\nlang: {LANG}\nsummary_en: Owner-approved Today queue entry.\n"
                    f"command: today\nstatus: seed\nmode: {mode}\n---\n\n# {item['title']}\n\n"
                    f"[[{item['source'].removesuffix('.md')}]]\n\n{answer or t('today_queue.completed')}\n")
            operation = {"writes": writes, "receipt": receipt.relative_to(self.vault).as_posix(),
                         "body": body, "key": key, "mode": mode, "date": self.today.isoformat()}
            state["pending_apply"] = operation
            atomic_write(self.state_path, json.dumps(state, ensure_ascii=False))
            self.finish_apply(state)
            return t("today_queue.applied")
        else:
            raise ValueError(t("today_queue.unknown_action"))
        item["revision"] += 1
        state["history"].append({"id": key, "action": action, "date": self.today.isoformat(), "detail": text[:1000]})
        return t("today_queue." + action + "_saved")

    def finish_apply(self, state):
        op = state.get("pending_apply")
        if not op:
            return
        item = state["records"][op["key"]]
        pending = []
        for write in op["writes"]:
            path = self.source(write["relative"])
            current = path.read_text()
            if current not in (write["before"], write["after"]):
                raise ValueError(t("today_queue.source_changed"))
            if current != write["after"]:
                pending.append((path, write["after"]))
        receipt = (self.vault / op["receipt"]).resolve()
        if not receipt.is_relative_to(self.vault / ".wiki/digests/queries"):
            raise ValueError(t("today_queue.source_missing"))
        for path, after in pending:
            atomic_write(path, after)
        atomic_write(receipt, op["body"])
        until = (as_date(op["date"]) + timedelta(days=7)).isoformat() if op["mode"] == "blocker" else None
        item.update(status="applied", resolution=op["mode"], until=until)
        item["revision"] += 1
        state["history"].append({"id": op["key"], "action": "apply", "mode": op["mode"], "date": op["date"]})
        del state["pending_apply"]


def keyboard(item):
    return {"inline_keyboard": [[{"text": t("today_queue." + verb),
                                 "callback_data": f"today:{item['id']}:{item['revision']}:{verb}"}
                                for verb in ("apply", "edit")],
                               [{"text": t("today_queue." + verb),
                                 "callback_data": f"today:{item['id']}:{item['revision']}:{verb}"}
                                for verb in ("defer", "dismiss")]]}


def send_queue(queue=None, *, config=None, api=None):
    config = Path(config) if config else Path.home() / ".config/brainless"
    try:
        token = (config / "telegram_token").read_text().strip()
        chat = (config / "telegram_chat_id").read_text().strip()
    except OSError:
        return False
    if not token or not chat:
        return False
    if api is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".agents/scripts"))
        from telegram_capture import api
    queue = queue or TodayQueue()
    with queue.locked() as state:
        queue.finish_apply(state)
        queue.build(state)
        for key in state["selected"]:
            item = state["records"][key]
            if item.get("sent_on") == state["day"] or item["status"] not in ("open", "drafted"):
                continue
            response = api(token, "sendMessage", {"chat_id": chat, "text": queue.render_item(item),
                           "reply_markup": json.dumps(keyboard(item))})
            mid = response.get("result", {}).get("message_id")
            if not response.get("ok") or not mid:
                raise RuntimeError("Telegram did not acknowledge the Today item")
            item.update(sent_on=state["day"], chat_id=str(chat), approval_message=mid)
            item["messages"].append(mid)
            atomic_write(queue.state_path, json.dumps(state, ensure_ascii=False))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="persist the queue and refresh TODAY.md")
    parser.add_argument("--send", action="store_true", help="send today's items to configured Telegram chat")
    parser.add_argument("--action", nargs=2, metavar=("ID", "ACTION"))
    parser.add_argument("--text", default="", help="answer, dismissal reason, or defer date")
    args = parser.parse_args()
    queue = TodayQueue()
    if args.send:
        send_queue(queue)
    elif args.build or args.action:
        with queue.locked() as state:
            queue.finish_apply(state)
            queue.build(state)
            if args.action:
                print(queue.act(state, *args.action, text=args.text))
            else:
                print(queue.render(state))
    else:
        try:
            state = json.loads(queue.state_path.read_text()) if queue.state_path.exists() else {"records": {}, "history": []}
        except (OSError, json.JSONDecodeError):
            state = {"records": {}, "history": []}
        if not isinstance(state, dict):
            state = {"records": {}, "history": []}
        queue.build(state)
        print(queue.render(state))


if __name__ == "__main__":
    main()
