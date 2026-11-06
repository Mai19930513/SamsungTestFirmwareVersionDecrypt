import hashlib
import os
import re
import time
from copy import deepcopy

import requests

from .config import env_bool, env_int
from .events import render_event
from .files import atomic_json, load_json
from .versions import same_release, test_candidates, version_key


def split_text(text, limit=3900):
    """Prefer whole lines/versions; Telegram limits UTF-16 units."""
    chunks, current, units = [], "", 0
    for line in text.splitlines(keepends=True):
        width = len(line.encode("utf-16-le")) // 2
        if units and units + width > limit:
            chunks.append(current.rstrip())
            current, units = "", 0
        for character in line:
            size = 2 if ord(character) > 65535 else 1
            if units + size > limit:
                chunks.append(current.rstrip())
                current, units = "", 0
            current += character
            units += size
    if current:
        chunks.append(current.rstrip())
    return chunks


def recipient_config(group_id=None):
    token = os.getenv("TG_BOT_TOKEN", "").strip()
    personal = os.getenv("TG_USER_ID", "").strip()
    recipients = {personal} if re.fullmatch(r"[1-9][0-9]*", personal) else set()
    if env_bool("FW_NOTIFY_GROUPS", False) and group_id:
        recipients.add(group_id)
    return token, recipients


def recipient_key(token, recipient):
    return hashlib.sha256(f"{token}:{recipient}".encode()).hexdigest()


def queue_path(settings):
    return settings.data_dir / "telegram-outbox.private.json"


def load_queue(settings):
    queue = load_json(queue_path(settings))
    now = time.time()
    result = {}
    for key, event in queue.items():
        # Legacy per-hash messages may describe historical builds or CP fill-ins.
        # Never flush this obsolete backlog after upgrading.
        if event.get("schema") != 2:
            if not event.get("delivered_at"):
                event.setdefault("suppressed_at", now)
            completed_at = event.get("delivered_at") or event.get("suppressed_at", now)
            if now - completed_at < 30 * 86400:
                result[key] = event
            continue
        completed = event.get("delivered_at") or event.get("suppressed_at")
        if not completed or now - completed < 30 * 86400:
            result[key] = event
    return result


def pair_key(event):
    return event.get("model"), event.get("region")


def completed(event):
    return event.get("delivered_at") or event.get("suppressed_at")


def enqueue(settings, events, group_id=None):
    queue = load_queue(settings)
    token, recipients = recipient_config(group_id)
    now = time.time()
    window = env_int("FW_NOTIFY_BATCH_SECONDS", 300, 0, 86400)
    cooldown = env_int("FW_NOTIFY_COOLDOWN_SECONDS", 1800, 0, 86400)
    seen = {member for item in queue.values() for member in item.get("members", [])}
    for event in events:
        if event["id"] in seen or not token:
            continue
        targets = {recipient_key(token, recipient): 0 for recipient in recipients
                   if recipient != group_id or event.get("versions")}
        if not targets:
            continue
        siblings = [item for item in queue.values() if item.get("schema") == 2 and pair_key(item) == pair_key(event)]
        pending = next((item for item in siblings if not completed(item) and not item.get("started_at")
                        and set(item["targets"]) == set(targets)), None)
        if pending is None:
            pending = deepcopy(event)
            last_sent = max((item.get("delivered_at", 0) for item in siblings), default=0)
            pending.update({"created_at": now, "ready_at": max(now + window, last_sent + cooldown),
                            "targets": targets, "members": []})
            queue[event["id"]] = pending
        else:
            pending["versions"] = sorted(set(pending.get("versions", [])) | set(event.get("versions", [])))
            if event.get("stable"):
                first = pending.get("stable")
                pending["stable"] = deepcopy(event["stable"])
                if first:
                    pending["stable"]["earlier"] = first["earlier"]
                pending["stable"]["rollback"] = bool(pending["stable"]["earlier"] and version_key(pending["stable"]["current"]) < version_key(pending["stable"]["earlier"]))
                if same_release(pending["stable"]["earlier"], pending["stable"]["current"]):
                    pending["stable"] = None
        pending["members"].append(event["id"])
        pending["text"] = render_event(pending)
        seen.add(event["id"])
    atomic_json(queue_path(settings), queue, private=True)


def notify(settings, group_id=None, data=None):
    queue = load_queue(settings)
    token, recipients = recipient_config(group_id)
    path = queue_path(settings)
    # Persist legacy suppression even when there is no valid destination.
    atomic_json(path, queue, private=True)
    if not token or not recipients:
        return
    cooldown = env_int("FW_NOTIFY_COOLDOWN_SECONDS", 1800, 0, 86400)
    last_sent = {}
    for item in queue.values():
        if item.get("schema") == 2 and item.get("delivered_at"):
            pair = pair_key(item)
            last_sent[pair] = max(last_sent.get(pair, 0), item["delivered_at"])
    with requests.Session() as session:
        for event in queue.values():
            if event.get("schema") != 2 or completed(event):
                continue
            allowed = {recipient_key(token, recipient) for recipient in recipients
                       if recipient != group_id or event.get("versions")}
            event["targets"] = {key: count for key, count in event["targets"].items() if key in allowed}
            if not event["targets"]:
                event["suppressed_at"] = time.time()
                atomic_json(path, queue, private=True)
                continue
            pair = pair_key(event)
            if time.time() < event.get("retry_at", 0):
                continue
            if not event.get("started_at"):
                # A later scan can promote a queued test build to stable or remove it.
                record = (data or {}).get(event["model"], {}).get(event["region"])
                if record is not None:
                    active = set(test_candidates(record))
                    event["versions"] = [v for v in event.get("versions", []) if v in active]
                if not event.get("versions") and not event.get("stable"):
                    event["suppressed_at"] = time.time()
                    atomic_json(path, queue, private=True)
                    continue
                due = max(event["ready_at"], last_sent.get(pair, 0) + cooldown)
                if time.time() < due:
                    continue
                event["started_at"] = time.time()
                event["text"] = render_event(event)
                atomic_json(path, queue, private=True)
            for recipient in sorted(recipients):
                key = recipient_key(token, recipient)
                if key not in event["targets"]:
                    continue
                content = event
                if recipient == group_id:
                    # Stable events remain personal, even in a combined digest.
                    content = {**event, "stable": None}
                    if not content.get("versions"):
                        event["targets"].pop(key, None)
                        continue
                chunks = split_text(render_event(content))
                for index in range(event["targets"][key], len(chunks)):
                    try:
                        with session.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                          json={"chat_id": recipient, "text": chunks[index], "disable_web_page_preview": True},
                                          timeout=settings.timeout, allow_redirects=False) as response:
                            payload = response.json()
                            if response.status_code != 200 or not payload.get("ok"):
                                retry_after = payload.get("parameters", {}).get("retry_after", 60)
                                delay = retry_after if type(retry_after) is int and retry_after > 0 else 60
                                event["retry_at"] = time.time() + delay
                                if response.status_code == 429:
                                    for item in queue.values():
                                        if item.get("schema") == 2 and not completed(item):
                                            item["retry_at"] = event["retry_at"]
                                atomic_json(path, queue, private=True)
                                print("Telegram 发送失败，通知保留在本地队列，下次重试。")
                                return
                    except (requests.RequestException, ValueError):
                        print("Telegram 网络错误，通知保留在本地队列，下次重试。")
                        return
                    event["targets"][key] = index + 1
                    atomic_json(path, queue, private=True)
                    time.sleep(1.1)
            event["delivered_at"] = time.time()
            last_sent[pair] = event["delivered_at"]
            atomic_json(path, queue, private=True)
