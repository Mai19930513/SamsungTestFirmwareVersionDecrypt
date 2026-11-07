import hashlib
import os
import unittest
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from samsung_firmware.config import Settings
from samsung_firmware.decode import candidate_groups
from samsung_firmware.files import atomic_json, atomic_write, load_json
from samsung_firmware.pipeline import execute_run
from samsung_firmware.storage import Store

STABLE = 'S9380ZCUECZJ1/S9380CHCECZJ1/S9380ZCUECZJ1'


class Client:
    def __init__(self, settings):
        pass

    def fetch_device(self, model, region, force):
        return {'errors': {}, 'stable': {'version': STABLE, 'android': '16'}, 'test': ['0' * 32]}

    def close(self):
        pass


class PipelineProgressTests(unittest.TestCase):
    def test_notifications_use_only_currently_scanned_devices(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            settings = replace(Settings.from_env(Path(directory)), downloads=False, timeline=False,
                               notifications=True, cpu_workers=1)
            atomic_write(settings.models_file, 'Galaxy S25 Ultra,SM-S9380,CHC|TGY\n')
            atomic_json(settings.output_dir / 'firmware.json', {'SM-S9380': {'TGY': {
                '最新正式版': STABLE, '抓取状态': {'stable': 'ok'}}}})
            args = Namespace(model=['SM-S9380'], region=['CHC'], force=False,
                             metadata_only=True, migrate_only=False)
            with patch('samsung_firmware.pipeline.HttpClient', Client), patch('samsung_firmware.notify.notify') as send:
                self.assertEqual(execute_run(settings, args), 0)
            current = send.call_args.args[2]
            self.assertEqual(set(current), {'SM-S9380'})
            self.assertEqual(set(current['SM-S9380']), {'CHC'})
            self.assertIn('TGY', load_json(settings.output_dir / 'firmware.json')['SM-S9380'])

    def test_metadata_force_and_precommit_failure_preserve_progress(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            settings = replace(Settings.from_env(Path(directory)), downloads=False, timeline=False,
                               notifications=False, cpu_workers=1, max_candidates=3,
                               year_codes='Z', month_codes='J', bootloader_codes='E',
                               update_codes='C', serial_codes='123')
            atomic_write(settings.models_file, 'Galaxy S25 Ultra,SM-S9380,CHC\n')
            args = Namespace(model=None, region=None, force=False, metadata_only=False, migrate_only=False)

            def progress():
                store = Store(settings)
                try:
                    return store.search_progress('SM-S9380', 'CHC', 24)
                finally:
                    store.close()

            with patch('samsung_firmware.pipeline.HttpClient', Client), patch('samsung_firmware.pipeline.ProcessPoolExecutor', ThreadPoolExecutor):
                self.assertEqual(execute_run(settings, args), 0)
                first = progress()
                self.assertEqual(first['stats']['candidates_total'], 3)
                args.metadata_only = True
                self.assertEqual(execute_run(settings, args), 0)
                self.assertEqual(progress(), first)
                args.metadata_only = False
                self.assertEqual(execute_run(settings, args), 0)
                self.assertEqual(progress()['stats']['candidates_total'], 6)
                with patch('samsung_firmware.pipeline.get_download', side_effect=OSError('interrupted before final save')):
                    with self.assertRaises(OSError):
                        execute_run(settings, args)
                self.assertEqual(progress()['stats']['candidates_total'], 9)
                self.assertEqual(execute_run(settings, args), 0)
                self.assertEqual(progress()['stats']['candidates_total'], 12)
                args.force = True
                self.assertEqual(execute_run(settings, args), 0)
                self.assertEqual(progress()['stats']['candidates_total'], 3)
                self.assertFalse(progress()['stats']['resumed'])
                # Publish a hash from before the saved cursor in the next server response.
                prefix, modems = next(candidate_groups('SM-S9380', 'CHC', STABLE, [], settings))
                version = prefix + modems[0]
                digest = hashlib.md5(version.encode()).hexdigest()
                with patch.object(Client, 'fetch_device', return_value={'errors': {}, 'stable': {'version': STABLE, 'android': '16'}, 'test': ['0' * 32, digest]}):
                    args.force = False
                    self.assertEqual(execute_run(settings, args), 0)
                result = load_json(settings.output_dir / 'firmware.json')['SM-S9380']['CHC']
                self.assertFalse(result['解密统计']['resumed'])
                self.assertEqual(result['版本号'][digest], version)


if __name__ == '__main__':
    unittest.main()
