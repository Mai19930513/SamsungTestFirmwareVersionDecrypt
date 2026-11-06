import hashlib
import os
import re
import time

import requests

from .files import atomic_json, load_json


def split_text(text, limit=3900):
    chunks = []
    current = []
    units = 0
    for character in text:
        width = 2 if ord(character) > 65535 else 1
        if units + width > limit:
            chunks.append("".join(current))
            current, units = [], 0
        current.append(character)
        units += width
    if current:
        chunks.append("".join(current))
    return chunks


def recipient_config(kind=None):
    token = os.getenv("TG_BOT_TOKEN", "")
    recipients = {value for value in (os.getenv("TG_USER_ID"), os.getenv("TG_CHAT_ID")) if value}
    if kind == "stable":
        personal = os.getenv("TG_USER_ID", "").strip()
        recipients = {personal} if re.fullmatch(r"[1-9][0-9]*", personal) else set()
    return token, recipients


def recipient_key(token, recipient):
    return hashlib.sha256(f"{token}:{recipient}".encode()).hexdigest()


def enqueue(settings, events):
    token, recipients = recipient_config()
    if not token or not recipients:
        return
    path = settings.data_dir / "telegram-outbox.private.json"
    queue = load_json(path)
    queue = {key: event for key, event in queue.items() if not event.get("delivered_at") or time.time() - event["delivered_at"] < 30 * 86400}
    for event in events:
        token, targets = recipient_config(event["kind"])
        if not targets:
            print("正式版通知需要有效的个人 TG_USER_ID，未发送到群组。")
            continue
        queue.setdefault(event["id"], {"kind": event["kind"], "text": event["text"], "created_at": time.time(),
                                      "targets": {recipient_key(token, recipient): 0 for recipient in targets}})
    atomic_json(path, queue, private=True)


def notify(settings):
    token, recipients = recipient_config()
    if not token or not recipients:
        print("通知已启用，但 Telegram 配置不完整。")
        return
    path = settings.data_dir / "telegram-outbox.private.json"
    queue = load_json(path)
    with requests.Session() as session:
        for event in queue.values():
            if event.get("delivered_at"):
                continue
            kind = event.get("kind")
            if kind is None and event["text"].startswith(("三星正式版服务器上线", "三星正式版服务器回滚")):
                kind = "stable"
            token, recipients = recipient_config(kind)
            if kind == "stable":
                allowed = {recipient_key(token, recipient) for recipient in recipients}
                event["targets"] = {key: count for key, count in event["targets"].items() if key in allowed}
                event["kind"] = kind
                atomic_json(path, queue, private=True)
                if not event["targets"]:
                    continue
            chunks = split_text(event["text"])
            for recipient in sorted(recipients):
                key = recipient_key(token, recipient)
                if key not in event["targets"]:
                    continue
                for index in range(event["targets"][key], len(chunks)):
                    try:
                        with session.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                          json={"chat_id": recipient, "text": chunks[index], "disable_web_page_preview": True},
                                          timeout=settings.timeout, allow_redirects=False) as response:
                            if response.status_code != 200 or not response.json().get("ok"):
                                print("Telegram 发送失败，通知保留在本地队列，下次重试。")
                                return
                    except (requests.RequestException, ValueError):
                        print("Telegram 网络错误，通知保留在本地队列，下次重试。")
                        return
                    event["targets"][key] = index + 1
                    atomic_json(path, queue, private=True)
                    time.sleep(0.05)
            if all(count == len(chunks) for count in event["targets"].values()):
                event["delivered_at"] = time.time()
                atomic_json(path, queue, private=True)
