import hashlib

from .config import env_bool
from .decode import version_key
from .files import now_text
from .fus import public_download
from .network import valid_version


def normalize_version(value):
    return "/".join(value.split("/")[:3]) if valid_version(value) else ""


def server_version(record, previous):
    download = record.get("最新固件下载", {})
    if download.get("status") == "available" and valid_version(download.get("version", "")):
        return normalize_version(download["version"]), "FUS"
    stable = normalize_version(record.get("最新正式版", ""))
    old_stable = normalize_version(previous.get("最新正式版", ""))
    if record.get("抓取状态", {}).get("stable") == "ok" and stable and stable != old_stable:
        earlier = normalize_version(previous.get("最新服务器正式版", ""))
        if earlier and previous.get("正式版来源") == "FUS" and version_key(stable) <= version_key(earlier):
            return earlier, "FUS"
        return stable, "FOTA"
    return (previous.get("最新服务器正式版") or old_stable or stable,
            previous.get("正式版来源", "FOTA"))


def firmware_events(old_data, data, pairs):
    events = []
    initial = env_bool("FW_NOTIFY_INITIAL", False)
    attach_download = env_bool("FW_NOTIFY_DOWNLOAD_LINK", True)
    for model, region in pairs:
        previous = old_data.get(model, {}).get(region, {})
        record = data[model][region]
        current, source = server_version(record, previous)
        earlier = normalize_version(previous.get("最新服务器正式版") or previous.get("最新正式版", ""))
        record["最新服务器正式版"] = current or "暂无正式版"
        record["正式版来源"] = source
        if current and current != earlier:
            record["正式版首次发现时间"] = now_text()
        if current and current != earlier and (earlier or initial):
            rollback = bool(earlier and version_key(current) < version_key(earlier))
            title = "三星正式版服务器回滚" if rollback else "三星正式版服务器上线"
            lines = [title, f"{record.get('机型', model)} · {model} · {region}",
                     f"原版本：{earlier or '首次发现'}", f"新版本：{current}",
                     f"发现时间：{record['正式版首次发现时间']}", f"来源：{source}",
                     "服务器已提供此版本；手机 OTA 到达时间取决于分批推送。"]
            download = public_download(record.get("最新固件下载"))
            if (attach_download and download.get("status") == "available" and download.get("url")
                    and normalize_version(download.get("version", "")) == current):
                lines.extend(["下载地址（需要 FUS 会话认证，非免认证直链）：", download["url"]])
            elif attach_download:
                lines.append("下载地址：本次尚未获取。")
            lines.append(f"更新说明：https://doc.samsungmobile.com/{model}/{region}/doc.html")
            events.append({"kind": "stable", "text": "\n".join(lines), "key": f"stable/{model}/{region}/{earlier}/{current}"})
        for digest in sorted(set(record.get("版本号", {})) - set(previous.get("版本号", {}))):
            events.append({"kind": "test", "text": f"三星新增测试固件\n{model} · {region}\n{record['版本号'][digest]}\n哈希：{digest}", "key": f"test/{model}/{region}/{digest}"})
    for event in events:
        event["id"] = hashlib.sha256(event.pop("key").encode()).hexdigest()
    return events
