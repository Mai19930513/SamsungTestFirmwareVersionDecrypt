import json
import os
import sqlite3
import ssl
import time
from contextlib import contextmanager

from .files import now_text
from .mysql_errors import CloudDatabaseError, mysql_error_summary


def mysql_connection():
    import pymysql

    if pymysql.VERSION[:3] < (1, 2, 3):
        raise CloudDatabaseError("[dependency] 请按 requirements.txt 升级 PyMySQL 至 1.2.3 或更高版本，以确保云连接强制使用 TLS")
    host = os.getenv("FW_MYSQL_HOST") or os.getenv("HOST")
    user = os.getenv("FW_MYSQL_USER") or os.getenv("DBUSER")
    database = os.getenv("FW_MYSQL_DATABASE") or os.getenv("DATABASE")
    password = os.getenv("FW_MYSQL_PASSWORD") or os.getenv("PASSWORD")
    if not all((host, user, database, password)):
        raise CloudDatabaseError("[configuration] MySQL 配置不完整；需要 FW_MYSQL_HOST、FW_MYSQL_USER、FW_MYSQL_PASSWORD、FW_MYSQL_DATABASE")
    host, user, database = host.strip(), user.strip(), database.strip()
    if not host or any(value in host for value in ("://", "/", "@")) or any(char.isspace() for char in host):
        raise CloudDatabaseError("[configuration] FW_MYSQL_HOST 只填写数据库主机名，不要填写连接 URI、用户名或路径")
    if not user or not database:
        raise CloudDatabaseError("[configuration] FW_MYSQL_USER 和 FW_MYSQL_DATABASE 不能只包含空白")
    try:
        port = int(os.getenv("FW_MYSQL_PORT", "3306"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise CloudDatabaseError("[configuration] FW_MYSQL_PORT 必须是 1–65535 的整数，并与 Aiven 服务实际端口一致") from None
    try:
        tls = ssl.create_default_context(cafile=os.getenv("FW_MYSQL_SSL_CA", "").strip() or None)
    except (OSError, ValueError):
        raise CloudDatabaseError("[tls_ca_file] 无法读取有效的 CA 证书；GitHub Secret FW_MYSQL_CA_PEM 必须是保留换行的完整 PEM 内容，不是文件路径") from None
    try:
        return pymysql.connect(host=host, user=user, password=password, database=database,
                               port=port, charset="utf8mb4",
                               connect_timeout=10, read_timeout=30, write_timeout=30,
                               ssl=tls, autocommit=False, cursorclass=pymysql.cursors.DictCursor)
    except Exception as error:
        raise CloudDatabaseError("云数据库连接失败：" + mysql_error_summary(error)) from None


class Store:
    def __init__(self, settings):
        self.mysql = settings.db_backend == "mysql"
        if self.mysql:
            self.connection = mysql_connection()
        else:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            database = settings.data_dir / "firmware.sqlite3"
            self.connection = sqlite3.connect(database, timeout=30)
            os.chmod(database, 0o600)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA busy_timeout=30000")
        text_type = "LONGTEXT" if self.mysql else "TEXT"
        definitions = {
            "firmware_settings": "name VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL",
            "firmware_devices": f"model VARCHAR(32), region VARCHAR(3), snapshot {text_type} NOT NULL, updated_at VARCHAR(32), PRIMARY KEY(model, region)",
            "firmware_versions": "model VARCHAR(32), region VARCHAR(3), hash VARCHAR(64), algorithm VARCHAR(16), version VARCHAR(140), ap VARCHAR(32), csc VARCHAR(32), cp VARCHAR(32), build_year INTEGER, build_month INTEGER, build_number INTEGER, bootloader VARCHAR(1), first_seen VARCHAR(32), last_seen VARCHAR(32), active INTEGER, PRIMARY KEY(model, region, hash)",
            "firmware_hashes": "model VARCHAR(32), region VARCHAR(3), hash VARCHAR(64), algorithm VARCHAR(16), first_seen VARCHAR(32), last_seen VARCHAR(32), active INTEGER, PRIMARY KEY(model, region, hash)",
            "firmware_search_progress": f"model VARCHAR(32), region VARCHAR(3), result {text_type} NOT NULL, updated_at DOUBLE, PRIMARY KEY(model, region)",
            "firmware_search_cache": f"signature VARCHAR(64) PRIMARY KEY, result {text_type} NOT NULL, completed_at DOUBLE",
        }
        try:
            for table, definition in definitions.items():
                suffix = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4" if self.mysql else ""
                self.execute(f"CREATE TABLE IF NOT EXISTS {table} ({definition}){suffix}")
            self.connection.commit()
        except Exception as error:
            self.connection.close()
            if self.mysql:
                raise CloudDatabaseError("云数据库初始化数据表失败：" + mysql_error_summary(error)) from None
            raise

    def execute(self, statement, parameters=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(statement.replace("?", "%s") if self.mysql else statement, parameters)
            return [dict(row) for row in cursor.fetchall()] if cursor.description else []
        finally:
            cursor.close()

    def upsert_many(self, table, columns, keys, rows, preserve=()):
        if not rows:
            return
        placeholders = ",".join("?" for column in columns)
        updates = [column for column in columns if column not in keys and column not in preserve]
        statement = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
        if self.mysql:
            statement += " ON DUPLICATE KEY UPDATE " + ",".join(f"{column}=VALUES({column})" for column in updates)
            statement = statement.replace("?", "%s")
        else:
            statement += f" ON CONFLICT ({','.join(keys)}) DO UPDATE SET " + ",".join(f"{column}=excluded.{column}" for column in updates)
        cursor = self.connection.cursor()
        try:
            cursor.executemany(statement, rows)
        finally:
            cursor.close()

    @contextmanager
    def transaction(self):
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def get_setting(self, name):
        rows = self.execute("SELECT value FROM firmware_settings WHERE name=?", (name,))
        return rows[0]["value"] if rows else None

    def set_setting(self, name, value):
        self.upsert_many("firmware_settings", ("name", "value"), ("name",), [(name, value)])

    def snapshots(self):
        result = {}
        for row in self.execute("SELECT model, region, snapshot FROM firmware_devices"):
            result.setdefault(row["model"], {})[row["region"]] = json.loads(row["snapshot"])
        return result

    def search_result(self, signature, hours):
        rows = self.execute("SELECT result, completed_at FROM firmware_search_cache WHERE signature = ?", (signature,))
        if rows and time.time() - rows[0]["completed_at"] < hours * 3600:
            return json.loads(rows[0]["result"])
        return None

    def search_progress(self, model, region, hours):
        rows = self.execute("SELECT result, updated_at FROM firmware_search_progress WHERE model=? AND region=?", (model, region))
        if rows:
            result = json.loads(rows[0]["result"])
            if result["stats"]["status"] == "bounded" or time.time() - rows[0]["updated_at"] < hours * 3600:
                return result
        return None

    def save_progress(self, model, region, result):
        if result["stats"]["status"] in {"bounded", "exhausted", "matched"}:
            self.upsert_many("firmware_search_progress", ("model", "region", "result", "updated_at"),
                             ("model", "region"), [(model, region, json.dumps(result, ensure_ascii=False), time.time())])

    def save_search(self, signature, result):
        if result["stats"]["status"] in {"exhausted", "matched"}:
            self.upsert_many("firmware_search_cache", ("signature", "result", "completed_at"), ("signature",),
                             [(signature, json.dumps(result, ensure_ascii=False), time.time())])

    def save_device(self, model, region, snapshot, observed_hashes=None, observed_at=None):
        timestamp = observed_at or snapshot.get("上次检查时间") or now_text()
        self.upsert_many("firmware_devices", ("model", "region", "snapshot", "updated_at"),
                         ("model", "region"), [(model, region, json.dumps(snapshot, ensure_ascii=False), timestamp)])
        observed = set(observed_hashes) if observed_hashes is not None else None
        if observed is not None:
            for table in ("firmware_hashes", "firmware_versions"):
                self.execute(f"UPDATE {table} SET active=0 WHERE model=? AND region=?", (model, region))
            hash_rows = [(model, region, digest, "md5" if len(digest) == 32 else "hmac-sha256", timestamp, timestamp, 1) for digest in observed]
            self.upsert_many("firmware_hashes", ("model", "region", "hash", "algorithm", "first_seen", "last_seen", "active"),
                             ("model", "region", "hash"), hash_rows, preserve=("first_seen",))
        previous_seen = {row["hash"]: row["last_seen"] for row in self.execute(
            "SELECT hash, last_seen FROM firmware_versions WHERE model=? AND region=?", (model, region))}
        version_rows = []
        for digest, version in snapshot.get("版本号", {}).items():
            ap, csc, cp = version.split("/")[:3]
            year = 2001 + ord(ap[-3]) - ord("A") if ap[-3].isalpha() else None
            month = ord(ap[-2]) - ord("A") + 1 if ap[-2] in "ABCDEFGHIJKL" else None
            count = int(ap[-1], 36)
            version_rows.append((model, region, digest, "md5" if len(digest) == 32 else "hmac-sha256", version,
                                 ap, csc, cp, year, month, count, ap[-5], timestamp,
                                 timestamp if observed is not None and digest in observed else previous_seen.get(digest, timestamp),
                                 int(digest in observed) if observed is not None else None))
        columns = ("model", "region", "hash", "algorithm", "version", "ap", "csc", "cp", "build_year", "build_month", "build_number", "bootloader", "first_seen", "last_seen", "active")
        preserve = ("first_seen",) if observed is not None else ("first_seen", "active")
        self.upsert_many("firmware_versions", columns, ("model", "region", "hash"), version_rows, preserve=preserve)

    def prune_cache(self, days=30):
        self.execute("DELETE FROM firmware_search_cache WHERE completed_at < ?", (time.time() - days * 86400,))

    def close(self):
        self.connection.close()
