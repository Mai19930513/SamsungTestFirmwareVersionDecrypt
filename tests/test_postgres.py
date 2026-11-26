import os
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from samsung_firmware.config import Settings
from samsung_firmware.databases import Databases
from samsung_firmware.mysql_errors import CloudDatabaseError
from samsung_firmware.postgres import postgres_options, postgres_error_summary
from samsung_firmware.storage import Store


class PostgresOptionsTests(unittest.TestCase):
    def test_explicit_parameters_and_safe_defaults(self):
        values = dict(FW_PG_HOST='test.aivencloud.com', FW_PG_USER='app', FW_PG_PASSWORD='password with spaces')
        options = postgres_options(values)
        self.assertEqual(options['dbname'], 'Sam_FW')
        self.assertEqual(options['sslmode'], 'verify-full')
        self.assertEqual(options['password'], values['FW_PG_PASSWORD'])
        self.assertIn('search_path=public,pg_catalog', options['options'])
        for changes in ({'FW_PG_HOST':'postgres://secret@host'}, {'FW_PG_PORT':'bad'},
                        {'FW_PG_PORT':'0'}, {'FW_PG_SSLMODE':'prefer'}, {'FW_PG_SSLMODE':'disable'}):
            with self.subTest(changes=changes), self.assertRaises(CloudDatabaseError):
                postgres_options({**values, **changes})
        self.assertEqual(postgres_options({**values, 'FW_PG_HOST':'127.0.0.1', 'FW_PG_SSLMODE':'disable'})['sslmode'], 'disable')

    def test_does_not_reuse_mysql_or_libpq_credentials(self):
        with self.assertRaises(CloudDatabaseError):
            postgres_options({'HOST':'legacy', 'DATABASE':'defaultdb', 'PASSWORD':'old',
                              'PGHOST':'ambient', 'PGUSER':'ambient', 'PGPASSWORD':'ambient'})

    def test_errors_never_print_driver_details(self):
        class Error(Exception):
            sqlstate = '28P01'
        self.assertIn('postgres_28P01', postgres_error_summary(Error('secret password and row')))
        self.assertNotIn('secret', postgres_error_summary(OSError('secret password')))

    def test_postgres_backend_defaults_to_cloud(self):
        with patch.dict(os.environ, {'FW_DB_BACKEND':'postgres', 'FW_MODEL_SOURCE':'postgres'}, clear=True):
            settings = Settings.from_env(Path('.'))
        self.assertEqual(settings.storage_target, 'cloud')
        self.assertEqual(settings.model_source, 'postgres')

    def test_cloud_driver_and_queue_are_isolated(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            base = Settings.from_env(Path(directory))
            opened = []
            def fake_store(settings):
                opened.append(settings.db_backend)
                return Store(replace(settings, db_backend='sqlite', data_dir=settings.data_dir / settings.db_backend))
            with patch('samsung_firmware.databases.Store', side_effect=fake_store):
                pg = Databases(replace(base, db_backend='postgres', storage_target='both'))
                old = Databases(replace(base, storage_target='both'))
            try:
                self.assertEqual(pg.cloud_backend, 'postgres')
                self.assertEqual(old.cloud_backend, 'mysql')
                self.assertNotEqual(pg.pending_path, old.pending_path)
                self.assertIn('postgres', opened)
            finally:
                pg.close()
                old.close()

    def test_postgres_failure_keeps_both_mode_local_store(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            settings = replace(Settings.from_env(Path(directory)), db_backend='postgres', storage_target='both')
            with patch('samsung_firmware.storage.postgres_connection', side_effect=CloudDatabaseError('offline')):
                database = Databases(settings)
            try:
                self.assertIsNone(database.cloud)
                self.assertTrue(database.cloud_error)
                self.assertIsNotNone(database.local)
            finally:
                database.close()


if __name__ == '__main__':
    unittest.main()
