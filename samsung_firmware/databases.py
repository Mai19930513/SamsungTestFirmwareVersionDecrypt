import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import replace

from .files import atomic_json, load_json, now_text
from .storage import Store


class Databases:
    def __init__(self, settings):
        self.settings = settings
        self.local = None
        self.cloud = None
        self.cloud_error = False
        identity = [os.getenv("FW_MYSQL_HOST") or os.getenv("HOST", ""),
                    os.getenv("FW_MYSQL_PORT", "3306"),
                    os.getenv("FW_MYSQL_DATABASE") or os.getenv("DATABASE", "")]
        fingerprint = hashlib.sha256("\0".join(identity).encode()).hexdigest()[:16]
        self.pending_path = settings.data_dir / f"cloud-pending-{fingerprint}.private.json"
        if settings.storage_target in {"local", "both"}:
            self.local = Store(replace(settings, db_backend="sqlite"))
        if settings.storage_target in {"cloud", "both"}:
            try:
                self.cloud = Store(replace(settings, db_backend="mysql"))
            except Exception:
                self.cloud_error = True
                if not self.local:
                    raise RuntimeError("云数据库连接失败，请检查 MySQL 参数、端口和 TLS 证书") from None
                print("云数据库暂不可用，将保存本地数据和待同步队列。", flush=True)

        # Cloud-only runs also retain local checkpoints for encrypted Actions state.
        self.progress = self.local or Store(replace(settings, db_backend="sqlite"))

    def search_progress(self, model, region):
        return self.progress.search_progress(model, region, self.settings.search_cache_hours)

    def save_progress(self, model, region, result):
        with self.progress.transaction():
            self.progress.save_progress(model, region, result)

    def snapshots(self):
        result = {}
        local_path = self.settings.data_dir / "firmware.sqlite3"
        if self.local is None and local_path.is_file():
            with closing(sqlite3.connect(local_path.resolve().as_uri() + "?mode=ro", uri=True)) as database:
                for model, region, snapshot in database.execute("SELECT model, region, snapshot FROM firmware_devices"):
                    result.setdefault(model, {})[region] = json.loads(snapshot)
        for store in (self.cloud, self.local):
            if store is None:
                continue
            try:
                snapshots = store.snapshots()
            except Exception:
                if store is self.cloud and self.local:
                    self.cloud_error = True
                    print("云数据库读取失败，继续使用本地数据。", flush=True)
                    continue
                raise
            for model, regions in snapshots.items():
                for region, record in regions.items():
                    previous = result.setdefault(model, {}).get(region, {})
                    winner = record if record.get("上次检查时间", "") >= previous.get("上次检查时间", "") else previous
                    result[model][region] = {**winner, "版本号": {**previous.get("版本号", {}), **record.get("版本号", {})}}
        return result

    def search_result(self, signature, hours):
        store = self.local or self.cloud
        return store.search_result(signature, hours)

    def persist(self, records, observations=None, search_updates=()):
        observations = observations or {}
        pending = load_json(self.pending_path) if self.settings.storage_target in {"cloud", "both"} else {}
        for model, regions in records.items():
            if not isinstance(regions, dict):
                continue
            for region, record in regions.items():
                key = f"{model}/{region}"
                prior = pending.get(key, {})
                prior_record = prior.get("record", {})
                newer = record.get("上次检查时间", "") >= prior_record.get("上次检查时间", "")
                winner = record if newer else prior_record
                merged = {**winner, "版本号": {**prior_record.get("版本号", {}), **record.get("版本号", {})}}
                observed = observations.get((model, region), prior.get("hashes")) if newer else prior.get("hashes")
                observed_at = (record.get("上次检查时间") or now_text()) if newer and (model, region) in observations else prior.get("observed_at")
                pending[key] = {"model": model, "region": region, "record": merged,
                                "observed_at": observed_at,
                                "hashes": sorted(observed) if observed is not None else None}

        def write(store, entries):
            with store.transaction():
                for entry in entries:
                    store.save_device(entry["model"], entry["region"], entry["record"], entry["hashes"], entry.get("observed_at"))
                for signature, result in search_updates:
                    store.save_search(signature, result)
                store.prune_cache()

        if self.local:
            current = [entry for entry in pending.values() if entry["region"] in records.get(entry["model"], {})]
            write(self.local, current)
        if self.settings.storage_target in {"cloud", "both"}:
            atomic_json(self.pending_path, pending, private=True)
            if self.cloud:
                try:
                    write(self.cloud, pending.values())
                    self.pending_path.unlink(missing_ok=True)
                    self.cloud_error = False
                except Exception:
                    self.cloud_error = True
                    print("云数据库写入失败，待同步数据已安全保留，下次选择 cloud/both 时重试。", flush=True)
                    if not self.local:
                        raise RuntimeError("云数据库写入失败；待同步队列已保存在本地") from None

    def close(self):
        for store in {self.local, self.cloud, self.progress}:
            if store is not None:
                store.close()
