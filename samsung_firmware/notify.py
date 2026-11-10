import hashlib
import os
import re
import time
from copy import deepcopy

import requests

from .config import env_bool, env_int
from .events import fus_ahead_version, render_event, render_message
from .files import atomic_json, load_json
from .messages import digest_counts, digest_pages, render_digest_page, split_message, split_text
from .versions import newer_release, normalize_version, same_release, test_candidates


def recipient_config(group_id=None):
    token = os.getenv("TG_BOT_TOKEN", "").strip()
    personal = os.getenv("TG_USER_ID", "").strip()
    recipients = {personal} if re.fullmatch(r"[1-9][0-9]*", personal) else set()
    if env_bool("FW_NOTIFY_GROUPS", True) and group_id:
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
        if event.get("schema") not in (2, 3):
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


def event_pairs(event):
    return {pair_key(item) for item in event.get("entries", [event])}


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
        targets = {recipient_key(token, recipient): 0 for recipient in recipients}
        if not targets:
            continue
        siblings = [item for item in queue.values()
                    if item.get("schema") in (2, 3) and pair_key(event) in event_pairs(item)]
        pending = next((item for item in siblings if item.get("schema") == 2
                        and not completed(item) and not item.get("started_at")
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
                pending["stable"] = deepcopy(event["stable"])
        pending["members"].append(event["id"])
        pending["text"] = render_event(pending)
        seen.add(event["id"])
    atomic_json(queue_path(settings), queue, private=True)


def revalidate_stable(event, record):
    """Recheck queued/retried FUS alerts; defer if current evidence is unavailable."""
    stable = event.get("stable")
    if not stable:
        return True
    if record is None or record.get("抓取状态", {}).get("stable") != "ok":
        return False
    fota = normalize_version(record.get("最新正式版", ""))
    if not fota:
        return False
    ahead = newer_release(stable["current"], fota)
    if ahead and record.get("最新固件下载", {}).get("status") != "available":
        return False
    valid = ahead and same_release(stable["current"], fus_ahead_version(record))
    partial = any(event["targets"].values())
    if valid and (not partial or stable.get("rule") == "fus_ahead_fota"):
        if not partial:
            stable.update(earlier=fota, source="FUS", rule="fus_ahead_fota", rollback=False)
        return True
    if partial:
        # Keep chunk offsets intact; never resume an obsolete stable announcement.
        event["suppressed_at"] = time.time()
    else:
        event["stable"] = None
        event.pop("started_at", None)
        event["text"] = render_event(event)
    return True


def prepare_digest(queue, token, recipients, group_id, data, last_sent, cooldown):
    """Freeze all due updates together, keeping per-recipient delivery progress."""
    now = time.time()
    busy = {pair for item in queue.values() if item.get("schema") in (2, 3)
            and not completed(item) and item.get("started_at") for pair in event_pairs(item)}
    selected = []
    for event in queue.values():
        if event.get("schema") != 2 or completed(event) or event.get("started_at"):
            continue
        allowed = {recipient_key(token, recipient) for recipient in recipients}
        event["targets"] = {key: count for key, count in event["targets"].items() if key in allowed}
        if not event["targets"]:
            event["suppressed_at"] = now
            continue
        record = (data or {}).get(event["model"], {}).get(event["region"])
        if not revalidate_stable(event, record) or completed(event):
            continue
        if record is not None:
            active = set(test_candidates(record))
            event["versions"] = [v for v in event.get("versions", []) if v in active]
        if not event.get("versions") and not event.get("stable"):
            event["suppressed_at"] = now
            continue
        pair = pair_key(event)
        due = max(event["ready_at"], event.get("retry_at", 0), last_sent.get(pair, 0) + cooldown)
        if pair in busy or now < due:
            continue
        selected.append(event)
        busy.add(pair)
    if not selected:
        return
    targets = {key: 0 for event in selected for key in event["targets"]}
    group_target = recipient_key(token, group_id) if group_id else None
    entries = [{field: deepcopy(event[field])
                for field in ("id", "model", "region", "name", "versions", "stable", "targets")}
               for event in selected]
    digest = {"schema": 3, "kind": "firmware_digest", "id": selected[0]["id"],
              "created_at": min(event["created_at"] for event in selected), "started_at": now,
              "members": [member for event in selected for member in event["members"]],
              "entries": entries, "targets": targets, "group_target": group_target, "group_stable": True,
              "pages": {key: digest_pages(entries, key, group_target) for key in targets}}
    # Keep the first queue position and all member IDs for deduplication/cooldown.
    queue[digest["id"]] = digest
    for event in selected[1:]:
        del queue[event["id"]]


def revalidate_digest(event, data):
    """Remove obsolete FUS fragments without dropping other updates on that page."""
    waiting, changed = set(), False
    # Digests frozen before group FUS support must retain their test-only layout.
    group_stable = event.get("group_stable", False)
    for entry in event["entries"]:
        if not entry.get("stable"):
            continue
        record = (data or {}).get(entry["model"], {}).get(entry["region"])
        probe = {**entry, "stable": deepcopy(entry["stable"]),
                 "targets": {key: count for key, count in event["targets"].items()
                             if key in entry["targets"] and (group_stable or key != event["group_target"])}}
        if not revalidate_stable(probe, record):
            waiting.add(entry["id"])
            continue
        stable = None if probe.get("suppressed_at") else probe["stable"]
        if stable != entry["stable"]:
            entry["stable"] = stable
            changed = True
    if changed:
        valid = {entry["id"] for entry in event["entries"] if entry.get("stable")}
        for key, count in event["targets"].items():
            if not count:
                event["pages"][key] = digest_pages(event["entries"], key, event["group_target"],
                                                  group_stable=group_stable)
            else:
                # Retain the page positions already acknowledged by Telegram.
                # Only remove obsolete FUS blocks from the remaining pages.
                for index in range(count, len(event["pages"][key])):
                    event["pages"][key][index] = [block for block in event["pages"][key][index]
                                                if block["kind"] != "stable" or block["entry"] in valid]
        if not any(entry.get("stable") or entry.get("versions") for entry in event["entries"]):
            event["suppressed_at"] = time.time()
    return waiting


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
        if item.get("schema") in (2, 3) and item.get("delivered_at"):
            for pair in event_pairs(item):
                last_sent[pair] = max(last_sent.get(pair, 0), item["delivered_at"])
    prepare_digest(queue, token, recipients, group_id, data, last_sent, cooldown)
    atomic_json(path, queue, private=True)
    with requests.Session() as session:
        for event in queue.values():
            if event.get("schema") not in (2, 3) or completed(event):
                continue
            digest = event.get("schema") == 3
            allowed = {recipient_key(token, recipient) for recipient in recipients}
            event["targets"] = {key: count for key, count in event["targets"].items() if key in allowed}
            if not event["targets"]:
                event["suppressed_at"] = time.time()
                atomic_json(path, queue, private=True)
                continue
            if digest:
                waiting = revalidate_digest(event, data)
            else:
                # Finish already-started schema-2 messages using their original
                # layout so saved chunk offsets still refer to the same content.
                record = (data or {}).get(event["model"], {}).get(event["region"])
                if not revalidate_stable(event, record):
                    continue
                if not event.get("stable") and not event.get("versions"):
                    event["suppressed_at"] = time.time()
            atomic_json(path, queue, private=True)
            if completed(event) or not event.get("started_at") or time.time() < event.get("retry_at", 0):
                continue
            finished = True
            for recipient in sorted(recipients):
                key = recipient_key(token, recipient)
                if key not in event["targets"]:
                    continue
                if digest:
                    pages = event["pages"][key]
                    counts = digest_counts([block for page in pages for block in page])
                    chunks = [render_digest_page(page, counts, index, len(pages))
                              for index, page in enumerate(pages, 1)]
                else:
                    # Only pre-upgrade, already-started schema-2 messages reach
                    # here; adding FUS would invalidate their saved chunk offsets.
                    content = {**event, "stable": None} if recipient == group_id else event
                    chunks = split_message(**render_message(content))
                for index in range(event["targets"][key], len(chunks)):
                    if digest and any(block["kind"] == "stable" and block["entry"] in waiting
                                      for block in pages[index]):
                        finished = False
                        break
                    if chunks[index]["text"]:
                        try:
                            with session.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                              json={"chat_id": recipient, **chunks[index], "disable_web_page_preview": True},
                                              timeout=settings.timeout, allow_redirects=False) as response:
                                payload = response.json()
                                if response.status_code != 200 or not payload.get("ok"):
                                    retry_after = payload.get("parameters", {}).get("retry_after", 60)
                                    delay = retry_after if type(retry_after) is int and retry_after > 0 else 60
                                    event["retry_at"] = time.time() + delay
                                    if response.status_code == 429:
                                        for item in queue.values():
                                            if item.get("schema") in (2, 3) and not completed(item):
                                                item["retry_at"] = event["retry_at"]
                                    atomic_json(path, queue, private=True)
                                    print("Telegram 发送失败，通知保留在本地队列，下次重试。")
                                    return
                        except (requests.RequestException, ValueError):
                            print("Telegram 网络错误，通知保留在本地队列，下次重试。")
                            return
                    event["targets"][key] = index + 1
                    atomic_json(path, queue, private=True)
                    if chunks[index]["text"]:
                        time.sleep(1.1)
            if finished:
                event["delivered_at"] = time.time()
                for pair in event_pairs(event):
                    last_sent[pair] = event["delivered_at"]
                atomic_json(path, queue, private=True)
