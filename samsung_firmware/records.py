from .decode import version_key
from .files import now_text
from .models import COUNTRIES
from .network import valid_version
from .fus import public_download


PUBLIC_FIELDS = {"版本号", "常规更新测试版", "大版本测试版", "最新正式版", "正式版安卓版本", "测试版安卓版本",
                 "最新测试版上传时间", "最新版本号说明", "解密百分比", "解密数量", "地区", "机型",
                 "服务器固件数量", "当前已解密数量", "未解密哈希", "公共标记", "解密统计", "抓取状态",
                 "上次检查时间", "最新固件下载", "最新固件下载链接", "测试版下载状态", "当前服务器版本号",
                 "最新服务器正式版", "正式版来源", "正式版首次发现时间"}


def public_record(record):
    result = {key: value for key, value in record.items() if key in PUBLIC_FIELDS}
    if "最新固件下载" in result:
        result["最新固件下载"] = public_download(result["最新固件下载"])
        result["最新固件下载链接"] = result["最新固件下载"].get("url")
    for key, allowed in (("解密统计", {"status", "candidates", "seconds", "target_count", "matched_count", "resumed", "candidates_total", "candidates_per_second"}),
                         ("抓取状态", {"stable", "test"})):
        if key in result:
            result[key] = {name: value for name, value in result[key].items() if name in allowed}
    return result


def update_record(model, region, name, old, fetched, decoded):
    record = public_record(old)
    record.update({"机型": name, "地区": COUNTRIES.get(region, region), "上次检查时间": now_text(),
                   "抓取状态": {"stable": fetched["errors"].get("stable", "ok"), "test": fetched["errors"].get("test", "ok")},
                   "测试版下载状态": "not_provided_by_public_fota"})
    stable_data = fetched.get("stable")
    if stable_data is not None:
        record["最新正式版"] = stable_data["version"] or "暂无正式版"
        record["正式版安卓版本"] = stable_data["android"]
    record.setdefault("最新正式版", "暂无正式版")
    record.setdefault("正式版安卓版本", "未知")
    stable = record["最新正式版"]
    if decoded is not None:
        versions = dict(sorted(decoded["versions"].items(), key=lambda item: version_key(item[1])))
        record["版本号"] = versions
        record["解密数量"] = len(versions)
        record["未解密哈希"] = decoded["unresolved"]
        record["公共标记"] = decoded["markers"]
        stats = decoded["stats"]
        record["解密统计"] = stats
        record["服务器固件数量"] = stats["target_count"]
        record["当前已解密数量"] = stats["matched_count"]
        record["解密百分比"] = f'{stats["matched_count"] / stats["target_count"] * 100:.2f}%' if stats["target_count"] else "0.00%"
        current_hashes = set(fetched.get("test", []))
        current = {digest: version for digest, version in versions.items() if digest in current_hashes}
        record["当前服务器版本号"] = current
    else:
        versions = record.setdefault("版本号", {})
        current = record.get("当前服务器版本号", versions)
    candidates = list(current.values())
    stable_major = stable.split("/")[0][-4] if valid_version(stable) else None
    minor = [version for version in candidates if stable_major is None or version.split("/")[0][-4] == stable_major]
    major = [version for version in candidates if version.split("/")[0][-4] == "Z" or (stable_major is not None and version.split("/")[0][-4] > stable_major)]
    record["常规更新测试版"] = max(minor, key=version_key, default="暂无常规测试版")
    record["大版本测试版"] = max(major, key=version_key, default="暂无大版本测试版")
    record["测试版安卓版本"] = "未知"
    record["最新版本号说明"] = "暂无"
    latest = max(candidates, key=version_key, default="")
    if latest:
        ap = latest.split("/")[0]
        if ap[-3].isalpha() and ap[-2] in "ABCDEFGHIJKL":
            record["最新版本号说明"] = f"{2001 + ord(ap[-3]) - 65}年{ord(ap[-2]) - 64}月第{int(ap[-1], 36)}个测试版"
    new_hashes = set(versions) - set(old.get("版本号", {}))
    record["最新测试版上传时间"] = now_text() if new_hashes else old.get("最新测试版上传时间", "暂无")
    if decoded is None:
        for field in ("常规更新测试版", "大版本测试版", "最新版本号说明", "测试版安卓版本"):
            if field in old:
                record[field] = old[field]
    return record
