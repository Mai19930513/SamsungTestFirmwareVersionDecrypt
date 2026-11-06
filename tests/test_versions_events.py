import hashlib
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from samsung_firmware.events import firmware_events
from samsung_firmware.records import refresh_test_fields, update_record
from samsung_firmware.versions import newer_test, parse_build, same_release, version_key

STABLE = 'F9260TBSHKZH2/F9260ZACHKZH2/F9260ZCSHKZH2'
OLD_BETA = 'F9260ZCU1ZVK3/F9260CHC1ZVK3/F9260ZCU1DVK3'
NEW_TEST = 'F9260ZCSHKZI1/F9260CHCHKZI1/F9260ZCSHKZI1'
NEW_BETA = 'F9260ZCUHZZJ1/F9260CHCHZZJ1/F9260ZCUHKZJ1'


def record(versions=(), stable=STABLE):
    values = {hashlib.md5(v.encode()).hexdigest(): v for v in versions}
    return {'机型': 'Z Fold3', '最新正式版': stable, '正式版安卓版本': '15',
            '版本号': values, '当前服务器版本号': values.copy(), '抓取状态': {'stable': 'ok'}}


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
            self.assertFalse(newer_test(value, STABLE), value)
        self.assertTrue(newer_test(NEW_TEST, STABLE))
        self.assertTrue(newer_test(NEW_BETA, STABLE))
        self.assertFalse(newer_test(NEW_TEST, '暂无正式版'))
        self.assertFalse(newer_test(NEW_TEST.replace('F9260', 'F9360'), STABLE))

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

    def test_real_update_and_rollback(self):
        old, new = record(), record(stable=NEW_TEST)
        event = events(old, new)[0]
        self.assertFalse(event['stable']['rollback'])
        self.assertTrue(events(record(stable=NEW_TEST), record())[0]['stable']['rollback'])

    def test_hash_algorithm_change_does_not_repeat_version(self):
        old = record([NEW_TEST])
        new = deepcopy(old)
        new['版本号']['a' * 64] = NEW_TEST
        self.assertEqual(events(old, new), [])

    def test_stable_and_test_share_one_digest(self):
        new = record([NEW_TEST, NEW_BETA], stable=NEW_TEST)
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
        self.assertFalse(newer_test(STABLE.replace("HKZH2", "HZZH2"), STABLE))
        self.assertTrue(newer_test(STABLE.replace("HKZH2", "HZZH3"), STABLE))
