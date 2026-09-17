"""Route only explicitly addressed Today replies and revision-bound buttons."""
import json
import re

from i18n import t, t_list
from today_queue import TodayQueue, keyboard


def handle(token, msg, chat_id, api, transcribe, queue=None):
    queue = queue or TodayQueue()
    callback = msg.get("_today_callback")
    text = (msg.get("text") or "").strip()
    if text == "/today":
        from today_queue import send_queue
        send_queue(queue)
        return True
    if not callback and not msg.get("reply_to_message"):
        return False
    if not queue.state_path.exists():
        return bool(callback)
    try:
        initial = json.loads(queue.state_path.read_text())
    except (OSError, json.JSONDecodeError):
        # Do not let a damaged Today state block ordinary Telegram capture.
        return bool(callback)
    if not isinstance(initial, dict):
        return bool(callback)
    reply_id = (msg.get("reply_to_message") or {}).get("message_id")
    item_id, revision, action = None, None, None
    if callback:
        match = re.fullmatch(r"today:([a-f0-9]{12}):(\d+):(apply|edit|defer|dismiss)", callback["data"])
        if not match:
            return True
        item_id, revision, action = match.groups()
        revision = int(revision)
    else:
        item_id = next((key for key, item in initial.get("records", {}).items()
                        if reply_id in item.get("messages", []) and item.get("chat_id") == str(chat_id)), None)
    if item_id is None:
        return False
    with queue.locked() as state:
        queue.finish_apply(state)
        item = state["records"].get(item_id)
        if not item or item.get("chat_id") != str(chat_id):
            return True
        if callback and msg.get("message_id") not in item.get("messages", []):
            return True
        event_id = "callback:" + callback["id"] if callback else "message:" + str(msg["message_id"])
        if event_id in state.get("handled", []):
            return True
        if callback:
            api(token, "answerCallbackQuery", {"callback_query_id": callback["id"]})
        if action is None:
            for verb in ("apply", "edit", "defer", "dismiss"):
                if text.casefold() in t_list("today_queue." + verb + "_words"):
                    action = verb
                    break
        try:
            if callback and revision != item["revision"]:
                raise ValueError(t("today_queue.expired"))
            if not callback and action == "apply" and reply_id != item.get("approval_message"):
                raise ValueError(t("today_queue.expired"))
            if action in ("defer", "dismiss"):
                if item["status"] in ("applied", "deferred", "dismissed"):
                    raise ValueError(t("today_queue.already_handled"))
                item["revision"] += 1
                item["awaiting"] = action
                message = t("today_queue.date_required" if action == "defer" else "today_queue.reason_required")
            elif action in ("apply", "edit"):
                message = queue.act(state, item_id, action, revision=revision)
                item.pop("awaiting", None)
            else:
                from telegram_capture import audio_file_id
                audio = audio_file_id(msg)
                if audio:
                    text = transcribe(token, audio[0]) or ""
                    if not text.strip():
                        raise ValueError(t("today_queue.transcribe_failed"))
                action = item.get("awaiting", "answer")
                message = queue.act(state, item_id, action, text=text)
                item.pop("awaiting", None)
        except ValueError as exc:
            message = str(exc)
        if item["status"] == "drafted" and item.get("draft") and message != queue.render_item(item):
            message += "\n\n" + queue.render_item(item)
        params = {"chat_id": chat_id, "text": message,
                  "reply_to_message_id": msg["message_id"]}
        if item["status"] in ("open", "drafted"):
            params["reply_markup"] = json.dumps(keyboard(item))
        response = api(token, "sendMessage", params)
        mid = response.get("result", {}).get("message_id")
        if not response.get("ok") or not mid:
            raise RuntimeError("Telegram did not acknowledge the Today reply")
        item["messages"].append(mid)
        item["messages"] = item["messages"][-20:]
        item["approval_message"] = mid
        state["handled"] = (state.get("handled", []) + [event_id])[-200:]
    return True
