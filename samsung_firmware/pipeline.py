from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from copy import deepcopy

from .decode import decode_device, search_signature, target_hashes, verified_versions
from .files import atomic_json, atomic_write, load_json, now_text
from .fus import empty_download, get_download
from .models import load_models
from .network import HttpClient
from .records import public_record, refresh_test_fields, update_record
from .databases import Databases
from .events import firmware_events


FIRMWARE_FILE = "firmware.json"
HASH_FILE = "MD5编码后的固件版本号.json"


def load_state(settings, store):
    existing = load_json(settings.output_dir / FIRMWARE_FILE)
    database = store.snapshots()
    for model, regions in database.items():
        for region, record in regions.items():
            current = existing.setdefault(model, {}).get(region)
            merged_versions = {**record.get("版本号", {}), **(current or {}).get("版本号", {})}
            if current is None or record.get("上次检查时间", "") > current.get("上次检查时间", ""):
                existing[model][region] = {**record, "版本号": merged_versions}
            else:
                current["版本号"] = merged_versions
    for model, regions in existing.items():
        if isinstance(regions, dict):
            for region, record in regions.items():
                if not isinstance(record, dict):
                    raise ValueError("Invalid firmware record")
                cleaned = public_record(record)
                cleaned["版本号"] = verified_versions(cleaned.get("版本号", {}), settings.hmac_key)
                cleaned["解密数量"] = len(cleaned["版本号"])
                cleaned.setdefault("最新固件下载", empty_download("not_checked", cleaned.get("最新正式版", "")))
                cleaned["最新固件下载链接"] = cleaned["最新固件下载"].get("url")
                cleaned["测试版下载状态"] = "not_provided_by_public_fota"
                refresh_test_fields(cleaned)
                regions[region] = cleaned
    return existing


def export_state(settings, data, hashes):
    data = {key: data[key] for key in sorted(data)}
    mini = deepcopy(data)
    for regions in mini.values():
        if isinstance(regions, dict):
            for record in regions.values():
                for key in ("版本号", "当前服务器版本号", "未解密哈希"):
                    record.pop(key, None)
    atomic_json(settings.output_dir / FIRMWARE_FILE, data)
    atomic_json(settings.output_dir / "firmware_mini.json", mini)
    atomic_json(settings.output_dir / HASH_FILE, hashes)
    markdown = []
    for model, regions in data.items():
        if not isinstance(regions, dict):
            continue
        for region, record in regions.items():
            markdown.append(f"#### {record.get('机型', model)} {region}\n\n- 正式版: {record.get('最新正式版', '暂无')}\n- 常规测试版: {record.get('常规更新测试版', '暂无')}\n- 大版本测试版: {record.get('大版本测试版', '暂无')}\n- 下载查询状态: {record['最新固件下载']['status']}\n")
    atomic_write(settings.output_dir / "各机型最新版本.md", "\n".join(markdown))


def log_changes(settings, old_data, data, old_hashes, hashes, pairs):
    messages = []
    hash_messages = []
    for model, region in pairs:
        before = old_data.get(model, {}).get(region, {})
        after = data.get(model, {}).get(region, {})
        versions = after.get("版本号", {})
        for digest in sorted(set(versions) - set(before.get("版本号", {}))):
            messages.append(f"{model} {region} 新增测试固件: {versions[digest]}，哈希: {digest}")
        stable = after.get("最新正式版")
        if stable and stable != before.get("最新正式版"):
            messages.append(f"{model} {region} 正式版变动: {before.get('最新正式版', '暂无')} → {stable}")
        previous = set(old_hashes.get(model, {}).get(region, {}).get("版本号", []))
        current = set(hashes.get(model, {}).get(region, {}).get("版本号", []))
        for label, values in (("新增", current - previous), ("移除", previous - current)):
            hash_messages.extend(f"{model} {region} {label}哈希: {digest}" for digest in sorted(values))
    for filename, lines in (("测试版新增日志.txt", messages), ("测试版固件变动信息.txt", hash_messages)):
        if lines:
            with (settings.output_dir / filename).open("a", encoding="utf-8") as handle:
                handle.write(f"*****记录时间:{now_text()}*****\n" + "\n".join(lines) + "\n")
    return messages


def execute_run(settings, args):
    store = Databases(settings)
    client = HttpClient(settings)
    try:
        group_id = store.telegram_group_id()
        if getattr(args, "sync_notification_config", False):
            print("Telegram 群组配置已保存至数据库；未发送消息。" if group_id else "未配置 Telegram 群组 ID；未发送消息。")
            return 3 if store.cloud_error else 0
        data = load_state(settings, store)
        hashes = load_json(settings.output_dir / HASH_FILE)
        old_data, old_hashes = deepcopy(data), deepcopy(hashes)
        if args.migrate_only:
            store.persist(data)
            export_state(settings, data, hashes)
            print("历史 JSON 已导入数据库，并补齐下载信息字段。")
            return 3 if store.cloud_error else 0
        models = load_models(settings, data)
        pairs = [(model, region) for model, entry in sorted(models.items()) for region in entry["CC"]
                 if (not args.model or model in args.model) and (not args.region or region in args.region)]
        if not pairs:
            raise ValueError("No configured models match the filters")
        fetched = {}
        print(f"开始查询 {len(pairs)} 个机型/地区；网络并发 {settings.io_workers}，计算进程 {settings.cpu_workers}。", flush=True)
        with ThreadPoolExecutor(max_workers=settings.io_workers) as pool:
            futures = {pool.submit(client.fetch_device, model, region, args.force): (model, region) for model, region in pairs}
            for future in as_completed(futures):
                pair = futures[future]
                fetched[pair] = future.result()
                print(f"FOTA {pair[0]} {pair[1]}: {'partial' if fetched[pair]['errors'] else 'ok'}", flush=True)
        decoded = {}
        search_updates = []
        executor = ThreadPoolExecutor if args.metadata_only else ProcessPoolExecutor
        with executor(max_workers=settings.cpu_workers) as pool:
            futures = {}
            for model, region in pairs:
                snapshot = fetched[model, region]
                if "test" not in snapshot:
                    continue
                previous = data.get(model, {}).get(region, {})
                stable = snapshot.get("stable", {}).get("version", previous.get("最新正式版", ""))
                known = previous.get("版本号", {})
                signature = search_signature(model, region, stable, snapshot["test"], known, settings)
                cached = None if args.force or args.metadata_only else store.search_result(signature, settings.search_cache_hours)
                if cached:
                    cached["stats"] = {**cached["stats"], "status": "cached", "candidates": 0, "seconds": 0, "candidates_per_second": 0}
                    decoded[model, region] = cached
                else:
                    progress = None if args.force or args.metadata_only else store.search_progress(model, region)
                    future = pool.submit(decode_device, model, region, stable, snapshot["test"], known, settings, args.metadata_only, progress)
                    futures[future] = (model, region, signature)
            for future in as_completed(futures):
                model, region, signature = futures[future]
                result = future.result()
                if not args.metadata_only:
                    store.save_progress(model, region, result)
                decoded[model, region] = result
                search_updates.append((signature, result))
                print(f"匹配 {model} {region}: {result['stats']['matched_count']}/{result['stats']['target_count']} ({result['stats']['status']}, 本轮 {result['stats']['candidates']}，累计 {result['stats'].get('candidates_total', 0)}，续搜 {result['stats'].get('resumed', False)})", flush=True)
        for model, region in pairs:
            snapshot = fetched[model, region]
            previous = data.get(model, {}).get(region, {})
            data.setdefault(model, {})[region] = update_record(model, region, models[model]["name"], previous, snapshot, decoded.get((model, region)))
            if "test" in snapshot:
                targets, markers = target_hashes(snapshot["test"], settings.hmac_key)
                hashes.setdefault(model, {})[region] = {"版本号": snapshot["test"], "固件数量": len(targets), "公共标记": markers}
        with ThreadPoolExecutor(max_workers=min(4, settings.io_workers)) as pool:
            futures = {}
            for model, region in pairs:
                record = data[model][region]
                if "stable" in fetched[model, region]:
                    future = pool.submit(get_download, settings, model, region, record["最新正式版"], record.get("最新固件下载"), args.force)
                    futures[future] = (model, region)
                else:
                    previous = record.get("最新固件下载") or empty_download("error")
                    record["最新固件下载"] = {**previous, "status": "stale" if previous.get("url") else "error", "error": "stable_fetch_failed"}
            for future in as_completed(futures):
                model, region = futures[future]
                data[model][region]["最新固件下载"] = future.result()
        data["上次更新时间"] = now_text()
        hashes["上次更新时间"] = now_text()
        observations = {}
        for model, region in pairs:
            record = data[model][region]
            record["最新固件下载链接"] = record["最新固件下载"].get("url")
            if "test" in fetched[model, region]:
                observations[model, region] = target_hashes(fetched[model, region]["test"], settings.hmac_key)[0]
        events = firmware_events(old_data, data, pairs)
        if settings.notifications:
            from .notify import enqueue

            enqueue(settings, events, group_id)
        store.persist(data, observations, search_updates)
        export_state(settings, data, hashes)
        log_changes(settings, old_data, data, old_hashes, hashes, pairs)
        if settings.notifications:
            from .notify import notify

            notify(settings, group_id, data)
        if settings.timeline:
            from .timeline import update_timeline

            update_timeline(settings, client, models, pairs, args.force)
        failures = sum(bool(snapshot["errors"]) for snapshot in fetched.values())
        available = sum(data[model][region]["最新固件下载"]["status"] == "available" for model, region in pairs)
        print(f"已保存数据库和 JSON；FOTA 部分失败 {failures}，可用下载元数据 {available}/{len(pairs)}。", flush=True)
        return 3 if store.cloud_error else (2 if failures else 0)
    finally:
        client.close()
        store.close()
