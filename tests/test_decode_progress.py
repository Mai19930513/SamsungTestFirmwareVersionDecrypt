import hashlib
import hmac
import itertools
import os
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from samsung_firmware.actions_state import pack_state, unpack_state
from samsung_firmware.config import Settings
from samsung_firmware.databases import Databases
from samsung_firmware.decode import candidate_groups, decode_device, indexed_product
from samsung_firmware.storage import Store

STABLE = 'S9380ZCUECZJ1/S9380CHCECZJ1/S9380ZCUECZJ1'


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {}, clear=True):
            self.settings = replace(Settings.from_env(Path(self.temp.name)),
                                    year_codes='Z', month_codes='JK', bootloader_codes='E',
                                    update_codes='CZ', serial_codes='123', max_candidates=7)

    def decode(self, hashes, known=None, settings=None, progress=None, metadata_only=False):
        return decode_device('SM-S9380', 'CHC', STABLE, hashes, known or {},
                             settings or self.settings, metadata_only, progress)

    def test_product_seeks_match_full_iteration(self):
        axes = ('AB', '123', 'xy')
        full = list(itertools.product(*axes))
        for offset in range(len(full) + 2):
            self.assertEqual(list(indexed_product(axes, offset)), full[offset:])
        self.assertEqual(list(indexed_product(('AB', ''), 0)), [])

    def test_group_seeks_match_full_iteration(self):
        groups = list(candidate_groups('SM-S9380', 'CHC', STABLE, [], self.settings))
        for offset in range(len(groups) + 1):
            self.assertEqual(list(candidate_groups('SM-S9380', 'CHC', STABLE, [], self.settings, offset)), groups[offset:])

    def test_slices_equal_full_search_with_discoveries(self):
        candidates = [prefix + modem for prefix, modems in candidate_groups('SM-S9380', 'CHC', STABLE, [], self.settings) for modem in modems]
        hashes = ['0' * 32, '0' * 64]
        for version in (candidates[1], candidates[30], candidates[-2]):
            hashes.extend((hashlib.md5(version.encode()).hexdigest(), hmac.digest(self.settings.hmac_key.encode(), version.encode(), 'sha256').hex()))
        full = self.decode(hashes, settings=replace(self.settings, max_candidates=100000))
        result = None
        known = {}
        total = 0
        for iteration in range(1000):
            result = self.decode(hashes, known, progress=result)
            total += result['stats']['candidates']
            self.assertEqual(result['stats']['resumed'], iteration > 0)
            known = result['versions']
            if result['stats']['status'] != 'bounded':
                break
        else:
            self.fail('search did not advance')
        self.assertEqual(result['versions'], full['versions'])
        self.assertEqual(result['unresolved'], full['unresolved'])
        self.assertEqual(total, full['stats']['candidates'])
        self.assertEqual(result['stats']['candidates_total'], total)

    def test_checkpoint_invalidates_on_changed_inputs_not_budget(self):
        first = self.decode(['0' * 32])
        self.assertTrue(self.decode(['0' * 32], settings=replace(self.settings, max_candidates=2), progress=first)['stats']['resumed'])
        self.assertFalse(self.decode(['1' * 32], progress=first)['stats']['resumed'])
        self.assertFalse(self.decode(['0' * 32], settings=replace(self.settings, month_codes='J'), progress=first)['stats']['resumed'])
        version = STABLE.replace('ZJ1', 'ZJ9')
        known = {hashlib.md5(version.encode()).hexdigest(): version}
        self.assertFalse(self.decode(['0' * 32], known, progress=first)['stats']['resumed'])
        self.assertFalse(self.decode(['0' * 32], settings=replace(self.settings, hmac_key='different-protocol-key'), progress=first)['stats']['resumed'])
        changed_stable = decode_device('SM-S9380', 'CHC', STABLE.replace('ZJ1', 'ZJ2'), ['0' * 32], {}, self.settings, progress=first)
        self.assertFalse(changed_stable['stats']['resumed'])
        self.assertFalse(self.decode(['0' * 32], progress=first, metadata_only=True)['stats']['resumed'])
        self.assertFalse(self.decode(['0' * 32])['stats']['resumed'])

    def test_new_server_hash_restarts_and_finds_already_passed_candidate(self):
        candidates = [prefix + modem for prefix, modems in candidate_groups('SM-S9380', 'CHC', STABLE, [], self.settings) for modem in modems]
        late_version = candidates[0]
        late_hashes = [hashlib.md5(late_version.encode()).hexdigest(),
                       hmac.digest(self.settings.hmac_key.encode(), late_version.encode(), 'sha256').hex()]
        # Run 1 passed this candidate, but Samsung had not listed its hashes yet.
        first = self.decode(['0' * 32])
        self.assertEqual(first['stats']['candidates'], 7)
        # Run 2 sees new server hashes and must search from the beginning.
        second = self.decode(['0' * 32, *late_hashes], progress=first)
        self.assertFalse(second['stats']['resumed'])
        for digest in late_hashes:
            self.assertEqual(second['versions'][digest], late_version)
        # Removing/re-adding a hash also invalidates the old plan.
        removed = self.decode(['0' * 32], second['versions'], progress=second)
        self.assertFalse(removed['stats']['resumed'])
        restored = self.decode(['0' * 32, *late_hashes], removed['versions'], progress=removed)
        self.assertFalse(restored['stats']['resumed'])
        self.assertTrue(all(digest in restored['versions'] for digest in late_hashes))

    def test_completed_cache_expires_without_sliding_on_reads(self):
        full = self.decode(['0' * 32], settings=replace(self.settings, max_candidates=100000))
        with patch('samsung_firmware.storage.time.time', return_value=100):
            store = Store(self.settings)
            self.addCleanup(store.close)
            with store.transaction():
                store.save_progress('SM-S9380', 'CHC', full)
        with patch('samsung_firmware.storage.time.time', return_value=200):
            previous = store.search_progress('SM-S9380', 'CHC', 1)
            cached = self.decode(['0' * 32], progress=previous)
            self.assertEqual(cached['stats']['status'], 'cached')
            self.assertEqual(cached['stats']['candidates'], 0)
            with store.transaction():
                store.save_progress('SM-S9380', 'CHC', cached)
        with patch('samsung_firmware.storage.time.time', return_value=3701):
            previous = store.search_progress('SM-S9380', 'CHC', 1)
            self.assertIsNone(previous)
            fresh = self.decode(['0' * 32], progress=previous)
            self.assertFalse(fresh['stats']['resumed'])
            self.assertEqual(fresh['stats']['candidates'], 7)

    def test_mid_group_cursor_and_time_budget(self):
        settings = replace(self.settings, max_candidates=1)
        first = self.decode(['0' * 32], settings=settings)
        self.assertEqual(first['progress']['cursor'], {'group': 0, 'modem': 1})
        second = self.decode(['0' * 32], settings=settings, progress=first)
        self.assertEqual(second['stats']['candidates_total'], 2)
        with patch('samsung_firmware.decode.time.perf_counter', side_effect=[0, 1000, 1000]):
            timed = self.decode(['0' * 32], progress=first)
        self.assertEqual(timed['stats']['candidates'], 0)
        self.assertEqual(timed['progress']['cursor'], first['progress']['cursor'])

    def test_sqlite_restart_and_encrypted_artifact(self):
        first = self.decode(['0' * 32])
        databases = Databases(self.settings)
        databases.save_progress('SM-S9380', 'CHC', first)
        databases.close()
        archive = Path(self.temp.name) / 'state.enc'
        key = bytes(range(32)); associated = b'repo/branch'
        pack_state(self.settings.data_dir, archive, key, associated)
        restored = self.settings.data_dir / 'restored'
        unpack_state(archive.read_bytes(), restored, key, associated)
        store = Store(replace(self.settings, data_dir=restored))
        self.addCleanup(store.close)
        previous = store.search_progress('SM-S9380', 'CHC', 0)
        second = self.decode(['0' * 32], progress=previous)
        self.assertTrue(second['stats']['resumed'])
        self.assertEqual(second['stats']['candidates_total'], 14)
        with store.transaction():
            store.execute('UPDATE firmware_search_progress SET updated_at=0')
        self.assertIsNotNone(store.search_progress('SM-S9380', 'CHC', 0))
        completed = self.decode(['0' * 32], settings=replace(self.settings, max_candidates=100000))
        with store.transaction():
            store.save_progress('SM-S9380', 'CHC', completed)
        self.assertIsNone(store.search_progress('SM-S9380', 'CHC', 0))

    def test_wifi_candidates_resume_with_empty_cp(self):
        stable = 'X920ZCU1AZJ1/X920CHC1AZJ1/'
        settings = replace(self.settings, max_candidates=1)
        first = decode_device('SM-X920', 'CHN', stable, ['0' * 32], {}, settings)
        second = decode_device('SM-X920', 'CHN', stable, ['0' * 32], {}, settings, progress=first)
        self.assertTrue(second['stats']['resumed'])
        self.assertEqual(second['stats']['candidates_total'], 2)

    def test_changed_hashes_retain_checkpoint_discoveries_after_interruption(self):
        prefix, modems = next(candidate_groups('SM-S9380', 'CHC', STABLE, [], self.settings))
        version = prefix + modems[0]
        digest = hashlib.md5(version.encode()).hexdigest()
        first = self.decode(['0' * 32, digest])
        self.assertEqual(first['versions'][digest], version)
        # JSON was never exported, and the server has since removed the hash.
        second = self.decode(['0' * 32], progress=first)
        self.assertFalse(second['stats']['resumed'])
        self.assertEqual(second['versions'][digest], version)

    def test_cloud_mode_keeps_local_checkpoint(self):
        def fake_store(settings):
            directory = settings.data_dir / 'fake-cloud' if settings.db_backend == 'mysql' else settings.data_dir
            return Store(replace(settings, db_backend='sqlite', data_dir=directory))
        with patch('samsung_firmware.databases.Store', fake_store):
            databases = Databases(replace(self.settings, storage_target='cloud'))
            first = self.decode(['0' * 32])
            databases.save_progress('SM-S9380', 'CHC', first)
            databases.close()
            databases = Databases(replace(self.settings, storage_target='cloud'))
            self.addCleanup(databases.close)
            self.assertEqual(databases.search_progress('SM-S9380', 'CHC'), first)
            self.assertEqual(databases.snapshots(), {})


if __name__ == '__main__':
    unittest.main()
