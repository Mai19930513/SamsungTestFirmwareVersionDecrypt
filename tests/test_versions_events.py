import hashlib
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from samsung_firmware.events import firmware_events
from samsung_firmware.records import public_record, refresh_test_fields, update_record
from samsung_firmware.versions import newer_release, parse_build, same_release, version_key

STABLE = 'F9260TBSHKZH2/F9260ZACHKZH2/F9260ZCSHKZH2'
OLD_BETA = 'F9260ZCU1ZVK3/F9260CHC1ZVK3/F9260ZCU1DVK3'
NEW_TEST = 'F9260ZCSHKZI1/F9260CHCHKZI1/F9260ZCSHKZI1'
NEW_BETA = 'F9260ZCUHZZJ1/F9260CHCHZZJ1/F9260ZCUHKZJ1'


def record(versions=(), stable=STABLE):
    values = {hashlib.md5(v.encode()).hexdigest(): v for v in versions}
    return {'机型': 'Z Fold3', '最新正式版': stable, '正式版安卓版本': '15',
            '版本号': values, '当前服务器版本号': values.copy(), '抓取状态': {'stable': 'ok'}}


def fus_record(versions=(), stable=STABLE, fus=NEW_TEST, status="available"):
    item = record(versions, stable)
    item["最新固件下载"] = {"status": status, "version": fus,
                            "url": "https://cloud-neofussvr.samsungmobile.com/NF_SmartDownloadBinaryForMass.do?file=/test.zip.enc4"}
    return item


def events(before, after):
    with patch.dict(os.environ, {'FW_NOTIFY_INITIAL': 'false'}):
        return firmware_events({'SM-F9260': {'CHC': before}}, {'SM-F9260': {'CHC': after}}, [('SM-F9260', 'CHC')])


class VersionTests(unittest.TestCase):
    def test_field_positions_and_base36(self):
        build = parse_build(STABLE)
        self.assertEqual((build.device, build.market, build.update_type), ('F9260', 'TB', 'S'))
        self.assertEqual((build.bootloader, build.branch, build.year, build.month, build.revision), (17, 'K', 2026, 8, 2))
        beta = parse_build(OLD_BETA)
        self.assertEqual((beta.bootloader, beta.branch, beta.year, beta.month), (1, 'Z', 2022, 11))
        self.assertIsNone(parse_build('未知'))

    def test_old_beta_and_release_itself_are_not_current_tests(self):
        for value in (OLD_BETA, STABLE, STABLE.replace('HKZH2', 'GKZJ9'), STABLE.replace('HKZH2', 'HJZJ9')):
            self.assertFalse(newer_release(value, STABLE), value)
        self.assertTrue(newer_release(NEW_TEST, STABLE))
        self.assertTrue(newer_release(NEW_BETA, STABLE))
        self.assertFalse(newer_release(NEW_TEST, '暂无正式版'))
        self.assertFalse(newer_release(NEW_TEST.replace('F9260', 'F9360'), STABLE))

    def test_missing_cp_and_fus_fourth_field_are_not_upgrades(self):
        before = 'X920ZCSADZH1/X920CHNADZH1/'
        after = before + 'X920ZCSADZH1/X920ZCSADZH1'
        self.assertTrue(same_release(before, after))
        old = record(stable=before)
        new = deepcopy(old)
        new['最新固件下载'] = {'status': 'available', 'version': after}
        self.assertEqual(events(old, new), [])
        self.assertFalse(same_release(STABLE, STABLE.replace('F9260ZCSHKZH2', 'F9260ZCSHKZH3')))
        self.assertEqual(version_key(STABLE), version_key(STABLE.replace('TBS', 'ZCU')))

    def test_refresh_fields_excludes_historical_beta(self):
        item = record([OLD_BETA, STABLE, NEW_TEST])
        refresh_test_fields(item)
        self.assertEqual(item['大版本测试版'], '暂无大版本测试版')
        self.assertEqual(item['常规更新测试版'], NEW_TEST)
        self.assertEqual(item['测试版安卓版本'], '15')
        self.assertIn('推断', item['测试版安卓版本依据'])
        self.assertEqual(item['最新版本号说明'], '2026年9月第1个常规更新测试版')
        item = record([NEW_BETA])
        refresh_test_fields(item)
        self.assertEqual(item['大版本测试版'], NEW_BETA)
        self.assertEqual(item['测试版安卓版本'], '待确认')

    def test_metadata_only_recomputes_wrong_fields(self):
        old = record([OLD_BETA, STABLE])
        old['大版本测试版'] = OLD_BETA
        old['测试版安卓版本'] = '未知'
        updated = update_record('SM-F9260', 'CHC', 'Z Fold3', old, {'errors': {}}, None)
        self.assertEqual(updated['大版本测试版'], '暂无大版本测试版')
        self.assertEqual(updated['测试版安卓版本'], '暂无测试版')

    def test_one_event_for_all_versions_and_no_history_spam(self):
        old = record([STABLE])
        new = record([STABLE, OLD_BETA, NEW_TEST, NEW_BETA])
        result = events(old, new)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]['versions']), {NEW_TEST, NEW_BETA})
        self.assertNotIn(OLD_BETA, result[0]['text'])
        self.assertIn('2 个版本', result[0]['text'])
        self.assertEqual(events(new, deepcopy(new)), [])

    def test_initial_scan_is_silent_and_removed_versions_stay_silent(self):
        self.assertEqual(events({}, record([NEW_TEST])), [])
        new = record([NEW_TEST])
        new['当前服务器版本号'] = {}
        self.assertEqual(events(record(), new), [])

    def test_fota_update_and_rollback_are_silent(self):
        self.assertEqual(events(record(), record(stable=NEW_TEST)), [])
        self.assertEqual(events(record(stable=NEW_TEST), record()), [])

    def test_s948n_already_released_firmware_is_silent(self):
        old = 'S948NKSS4BZIG/S948NOKR4BZIG/S948NKSU4BZI9'
        latest = 'S948NKSU5BZJB/S948NOKR5BZJB/S948NKSU5BZJB'
        item = fus_record(stable=latest, fus=latest + '/' + latest.split('/')[0])
        result = firmware_events({'SM-S948N': {'KOO': record(stable=old)}},
                                 {'SM-S948N': {'KOO': item}}, [('SM-S948N', 'KOO')])
        self.assertEqual(result, [])
        self.assertEqual(item['最新服务器正式版'], latest)
        self.assertEqual(item['正式版来源'], 'FOTA')

    def test_only_fus_strictly_ahead_of_current_fota_notifies_once(self):
        item = fus_record()
        event = events(record(stable=OLD_BETA), item)[0]
        self.assertEqual(event['stable']['earlier'], STABLE)
        self.assertEqual(event['stable']['current'], NEW_TEST)
        self.assertEqual(event['stable']['source'], 'FUS')
        self.assertIn('FOTA 当前正式版：' + STABLE.split('/')[0], event['text'])
        self.assertIn('FUS 版本：' + NEW_TEST.split('/')[0], event['text'])
        self.assertIn('脚本发现时间：', event['text'])
        self.assertIn('下载地址', event['text'])
        self.assertEqual(events(item, deepcopy(item)), [])
        self.assertEqual(public_record(item)['已记录FUS领先版本'], NEW_TEST)

    def test_equal_older_or_unconfirmed_fus_does_not_notify(self):
        for version in (STABLE, OLD_BETA, STABLE.replace('HKZH2', 'GKZJ9'),
                        STABLE.replace('TBS', 'ZCU'), STABLE.replace('F9260', 'F9360'),
                        STABLE.replace('F9260ZCSHKZH2', 'F9260ZCSHKZH3')):
            with self.subTest(version=version):
                self.assertEqual(events(record(), fus_record(fus=version)), [])
        for status in ('disabled', 'unavailable', 'error', 'stale', 'not_checked'):
            with self.subTest(status=status):
                self.assertEqual(events(record(), fus_record(status=status)), [])
        self.assertEqual(events(record(), fus_record(stable='暂无正式版')), [])
        item = fus_record()
        item['抓取状态']['stable'] = 'connection_error'
        self.assertEqual(events(record(), item), [])
        recovered = deepcopy(item)
        recovered['抓取状态']['stable'] = 'ok'
        self.assertEqual(events(item, recovered)[0]['stable']['current'], NEW_TEST)

    def test_fus_initial_scan_respects_setting_and_comparison(self):
        item = fus_record()
        self.assertEqual(events({}, item), [])
        self.assertEqual(events(item, deepcopy(item)), [])
        with patch.dict(os.environ, {'FW_NOTIFY_INITIAL': 'true'}):
            result = firmware_events({}, {'SM-F9260': {'CHC': fus_record()}}, [('SM-F9260', 'CHC')])
            self.assertEqual(result[0]['stable']['current'], NEW_TEST)
            result = firmware_events({}, {'SM-F9260': {'CHC': fus_record(fus=STABLE)}}, [('SM-F9260', 'CHC')])
            self.assertEqual(result, [])

    def test_failure_and_fota_catchup_do_not_repeat_fus_alert(self):
        item = fus_record()
        events(record(), item)
        failed = deepcopy(item)
        failed['最新固件下载']['status'] = 'error'
        self.assertEqual(events(item, failed), [])
        recovered = deepcopy(failed)
        recovered['最新固件下载']['status'] = 'available'
        self.assertEqual(events(failed, recovered), [])
        caught_up = deepcopy(recovered)
        caught_up['最新正式版'] = NEW_TEST
        self.assertEqual(events(recovered, caught_up), [])
        rolled_back = deepcopy(caught_up)
        rolled_back['最新正式版'] = STABLE
        self.assertEqual(events(caught_up, rolled_back), [])
        next_version = deepcopy(caught_up)
        next_version['最新固件下载']['version'] = NEW_TEST.replace('ZI1', 'ZI2')
        self.assertEqual(events(caught_up, next_version)[0]['stable']['earlier'], NEW_TEST)

    def test_existing_fus_snapshot_migrates_without_repeat(self):
        for status in ('available', 'stale'):
            with self.subTest(status=status):
                old = fus_record(status=status)
                old.update(最新服务器正式版=NEW_TEST, 正式版来源='FUS')
                new = fus_record()
                self.assertEqual(events(old, new), [])
                self.assertEqual(new['已记录FUS领先版本'], NEW_TEST)

    def test_hash_algorithm_change_does_not_repeat_version(self):
        old = record([NEW_TEST])
        new = deepcopy(old)
        new['版本号']['a' * 64] = NEW_TEST
        self.assertEqual(events(old, new), [])

    def test_stable_and_test_share_one_digest(self):
        new = fus_record([NEW_TEST, NEW_BETA])
        result = events(record(), new)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['stable'])
        self.assertEqual(result[0]['versions'], [NEW_BETA])

    def test_new_fota_baseline_wins_over_old_server_snapshot(self):
        item = record([NEW_TEST], stable=NEW_TEST)
        item["最新服务器正式版"] = STABLE
        refresh_test_fields(item)
        self.assertEqual(item["常规更新测试版"], "暂无常规测试版")

    def test_major_branch_cannot_make_same_build_date_newer(self):
        self.assertFalse(newer_release(STABLE.replace("HKZH2", "HZZH2"), STABLE))
        self.assertTrue(newer_release(STABLE.replace("HKZH2", "HZZH3"), STABLE))
