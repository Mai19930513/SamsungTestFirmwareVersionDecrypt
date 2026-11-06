import hashlib

from .config import env_bool
from .files import now_text
from .fus import public_download
from .records import refresh_test_fields
from .versions import normalize_version, same_release, test_candidates, version_key


def server_version(record, previous):
    download = record.get("最新固件下载", {})
    if download.get("status") == "available" and normalize_version(download.get("version", "")):
        return normalize_version(download["version"]), "FUS"
    stable = normalize_version(record.get("最新正式版", ""))
    earlier = normalize_version(previous.get("最新服务器正式版", ""))
    if record.get("抓取状态", {}).get("stable") == "ok" and stable:
        if earlier and previous.get("正式版来源") == "FUS" and version_key(stable) <= version_key(earlier):
            return earlier, "FUS"
        return stable, "FOTA"
    return (earlier or normalize_version(previous.get("最新正式版", "")) or stable,
            previous.get("正式版来源", "FOTA"))


def render_event(event):
    lines = ["三星固件更新", f"{event['name']} · {event['model']} · {event['region']}"]
    stable = event.get("stable")
    if stable:
        title = "正式版服务器回滚" if stable["rollback"] else "正式版服务器上线"
        lines.extend(["", title, f"原版本：{stable['earlier'] or '首次发现'}", f"新版本：{stable['current']}",
                      f"发现时间：{stable['discovered_at']} · 来源：{stable['source']}",
                      "服务器已提供此版本；设备 OTA 到达时间取决于分批推送。"])
        if stable.get("url"):
            lines.extend(["下载地址（需要 FUS 会话认证）：", stable["url"]])
        lines.append(f"更新说明：https://doc.samsungmobile.com/{event['model']}/{event['region']}/doc.html")
    versions = event.get("versions", [])
    if versions:
        lines.extend(["", f"新增测试固件解密结果：{len(versions)} 个版本"])
        for index, version in enumerate(sorted(set(versions), key=lambda v: (version_key(v), v), reverse=True), 1):
            lines.append(f"{index}. {version}")
        lines.append("仅列出高于正式版的当前测试固件；解密发现时间不代表上传时间。")
    return "\n".join(lines)


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
        if changed and (earlier or initial):
            stable = {"earlier": earlier, "current": current, "source": source,
                      "rollback": bool(earlier and version_key(current) < version_key(earlier)),
                      "discovered_at": record["正式版首次发现时间"]}
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
        keys = [model, region, earlier if stable else "", current if stable else "", *sorted(versions)]
        event["id"] = hashlib.sha256("\0".join(keys).encode()).hexdigest()
        event["text"] = render_event(event)
        events.append(event)
    return events
