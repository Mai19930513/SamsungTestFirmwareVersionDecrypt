import hashlib

from .config import env_bool
from .files import now_text
from .fus import public_download
from .records import refresh_test_fields
from .versions import newer_release, normalize_version, same_release, test_candidates, version_key


def server_version(record, previous):
    fus = fus_ahead_version(record)
    if fus:
        return fus, "FUS"
    stable = normalize_version(record.get("最新正式版", ""))
    earlier = normalize_version(previous.get("最新服务器正式版", ""))
    if record.get("抓取状态", {}).get("stable") == "ok" and stable:
        # Keep a previously observed FUS build through a transient FUS failure while
        # FOTA is still behind it; do not treat an equal/older FUS build as current.
        if (earlier and previous.get("正式版来源") == "FUS"
                and newer_release(earlier, stable)):
            return earlier, "FUS"
        return stable, "FOTA"
    return (earlier or normalize_version(previous.get("最新正式版", "")) or stable,
            previous.get("正式版来源", "FOTA"))


def fus_ahead_version(record):
    """Only a successful FUS lookup strictly ahead of current FOTA is news."""
    stable = normalize_version(record.get("最新正式版", ""))
    download = record.get("最新固件下载", {})
    current = normalize_version(download.get("version", ""))
    if (record.get("抓取状态", {}).get("stable") == "ok"
            and download.get("status") == "available" and newer_release(current, stable)):
        return current
    return ""


def render_message(event, *, header=None):
    lines = ([header] if header is not None else
             ["三星固件更新", f"{event['name']} · {event['model']} · {event['region']}"])
    entities = []
    stable = event.get("stable")
    if stable:
        if header is None:
            lines.extend(["", "FUS 新版本上线"])
        lines.extend([f"FOTA 当前正式版：{stable['earlier'].split('/')[0]}",
                      f"FUS 版本：{stable['current'].split('/')[0]}",
                      f"脚本发现时间：{stable['discovered_at']} · 来源：{stable['source']}"])
        links = []
        if stable.get("url"):
            links.append(("下载地址", stable["url"]))
        links.append(("更新说明", f"https://doc.samsungmobile.com/{event['model']}/{event['region']}/doc.html"))
        offset = len(("\n".join(lines) + "\n").encode("utf-16-le")) // 2
        labels = []
        for label, url in links:
            if labels:
                offset += len(" · ")
            length = len(label.encode("utf-16-le")) // 2
            entities.append({"type": "text_link", "offset": offset, "length": length, "url": url})
            labels.append(label)
            offset += length
        lines.append(" · ".join(labels))
    versions = {version.split("/")[0] for version in event.get("versions", [])}
    if versions:
        lines.extend(["", f"新增测试固件解密结果：{len(versions)} 个版本"])
        lines.extend(sorted(versions, key=lambda v: (version_key(v), v), reverse=True))
    return {"text": "\n".join(lines), "entities": entities}


def render_event(event):
    return render_message(event)["text"]


def firmware_events(old_data, data, pairs):
    events = []
    initial = env_bool("FW_NOTIFY_INITIAL", False)
    attach_download = env_bool("FW_NOTIFY_DOWNLOAD_LINK", True)
    for model, region in pairs:
        previous = old_data.get(model, {}).get(region, {})
        record = data[model][region]
        current, source = server_version(record, previous)
        earlier = normalize_version(previous.get("最新服务器正式版") or previous.get("最新正式版", ""))
        changed = bool(current and not same_release(current, earlier))
        record["最新服务器正式版"] = current or "暂无正式版"
        record["正式版来源"] = source
        if changed:
            record["正式版首次发现时间"] = now_text()
        refresh_test_fields(record)
        stable = None
        ahead = fus_ahead_version(record)
        seen = normalize_version(previous.get("已记录FUS领先版本", ""))
        if "已记录FUS领先版本" not in previous and previous.get("正式版来源") == "FUS":
            old_fus = normalize_version(previous.get("最新服务器正式版", ""))
            old_fota = normalize_version(previous.get("最新正式版", ""))
            seen = old_fus if newer_release(old_fus, old_fota) else fus_ahead_version(previous)
        # Persist even for a silent initial scan; fetch failures must not reset deduplication.
        record["已记录FUS领先版本"] = ahead or seen
        if ahead and not same_release(ahead, seen) and (previous or initial):
            stable = {"earlier": normalize_version(record["最新正式版"]), "current": ahead,
                      "source": "FUS", "rule": "fus_ahead_fota", "rollback": False,
                      "discovered_at": now_text()}
            download = public_download(record.get("最新固件下载"))
            if (attach_download and download.get("status") == "available" and download.get("url")
                    and same_release(download.get("version", ""), current)):
                stable["url"] = download["url"]
        known = {normalize_version(v) for v in previous.get("版本号", {}).values()}
        versions = [v for v in test_candidates(record) if normalize_version(v) not in known] if previous or initial else []
        if not stable and not versions:
            continue
        event = {"schema": 2, "kind": "firmware", "model": model, "region": region,
                 "name": record.get("机型", model), "stable": stable, "versions": versions}
        keys = [model, region, "fus_ahead_fota" if stable else "", ahead if stable else "", *sorted(versions)]
        event["id"] = hashlib.sha256("\0".join(keys).encode()).hexdigest()
        event["text"] = render_event(event)
        events.append(event)
    return events
