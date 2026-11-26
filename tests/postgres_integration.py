"""Smoke-test the real application against models imported by the migration fixture.

Invoked by ../database-migration/tests/integration.py. Never loads dotenv, performs
Samsung requests or sends Telegram messages. Only accepts the fixture database.
"""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from samsung_firmware.config import Settings
from samsung_firmware.databases import Databases
from samsung_firmware.models import load_models
from samsung_firmware.postgres_check import main as check_connection
from samsung_firmware.storage import Store


def main():
    cfg = json.loads(Path(sys.argv[1]).read_text())
    job = next(j for j in cfg['jobs'] if j['name'] == 'samsung')
    assert cfg['postgres']['host'] == '127.0.0.1'
    assert job['target_database'].startswith('Sam_FW_test_')
    env = {'FW_DB_BACKEND':'postgres', 'FW_MODEL_SOURCE':'postgres', 'FW_STORAGE':'cloud',
           'FW_PG_HOST':'127.0.0.1', 'FW_PG_PORT':str(cfg['postgres']['port']),
           'FW_PG_USER':cfg['postgres']['user'], 'FW_PG_PASSWORD':cfg['postgres']['password'],
           'FW_PG_DATABASE':job['target_database'], 'FW_PG_SSLMODE':'disable', 'FW_NOTIFICATIONS':'false'}
    with TemporaryDirectory() as directory, patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env(Path(directory))
        assert check_connection() == 0
        models = load_models(settings, {})
        assert models['SM-S9380']['CC'] == ['CHC','TGY']
        assert models['SM-S9380']['name'] == 'Galaxy S25 Ultra 中文 😀'
        store = Store(settings)
        try:
            with store.transaction():
                store.set_setting('integration', 'first')
                store.set_setting('integration', '第二次 😀')
            assert store.get_setting('integration') == '第二次 😀'
            try:
                with store.transaction():
                    store.set_setting('rollback', 'must disappear')
                    store.execute('INSERT INTO firmware_settings(name,value) VALUES (?,?)', ('integration','duplicate'))
            except Exception:
                pass
            else:
                raise AssertionError('Expected primary key conflict')
            assert store.get_setting('rollback') is None
            version = 'S9380ZCUECZJ1/S9380CHCECZJ1/S9380ZCUECZJ1'
            digest = '1' * 32
            snapshot = {'机型':'Galaxy S25 Ultra', '版本号':{digest:version}, '上次检查时间':'2026-11-26 10:00:00'}
            search = {'stats':{'status':'matched'}, 'versions':{digest:version}}
            with store.transaction():
                store.save_device('SM-S9380', 'CHC', snapshot, [digest], '2026-11-26 10:00:00')
                store.save_progress('SM-S9380','CHC',search)
                store.save_search('test-signature',search)
            with store.transaction():
                store.save_device('SM-S9380', 'CHC', snapshot, [], '2026-11-26 11:00:00')
            rows = store.execute('SELECT active,first_seen,last_seen FROM firmware_versions WHERE hash=?', (digest,))
            assert rows == [{'active':0, 'first_seen':'2026-11-26 10:00:00', 'last_seen':'2026-11-26 10:00:00'}]
            assert store.snapshots()['SM-S9380']['CHC'] == snapshot
            assert store.search_progress('SM-S9380','CHC',24) == search
            assert store.search_result('test-signature',24) == search
            with store.transaction():
                store.execute('UPDATE firmware_search_cache SET completed_at=0')
                store.prune_cache()
            assert store.search_result('test-signature',24) is None
        finally:
            store.close()
        database = Databases(settings)
        try:
            assert database.cloud.backend == 'postgres'
            database.persist({'SM-S9380': {'CHC':snapshot}}, {('SM-S9380','CHC'):{digest}})
            assert not database.pending_path.exists()
            assert database.cloud.snapshots()['SM-S9380']['CHC'] == snapshot
            # Intentional write failure retains queue; the next transaction can retry.
            original = database.cloud.save_device
            database.cloud.save_device = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('synthetic offline'))
            try:
                database.persist({'SM-S9380': {'CHC':snapshot}})
            except RuntimeError:
                pass
            assert database.pending_path.exists()
            database.cloud.save_device = original
            database.persist({})
            assert not database.pending_path.exists()
            assert load_models(settings, {}) == models  # Store never writes the models table.
            # Historical MySQL identifiers may use CC/Name while queries use lowercase.
            with database.cloud.transaction():
                database.cloud.execute('ALTER TABLE public.models RENAME COLUMN cc TO "CC"')
            assert load_models(settings, {}) == models
            assert check_connection() == 0
            with database.cloud.transaction():
                database.cloud.execute('ALTER TABLE public.models RENAME COLUMN "CC" TO cc')
        finally:
            database.close()
    print('PASS: migrated models read, PostgreSQL schema, upsert, rollback recovery, snapshots, versions, caches, queues')


if __name__ == '__main__':
    main()
