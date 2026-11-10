import os
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from samsung_firmware.config import Settings
from samsung_firmware.databases import Databases
from samsung_firmware.files import atomic_json, load_json
from samsung_firmware.events import firmware_events, render_message
from samsung_firmware.messages import digest_counts, digest_pages, render_digest_page
from samsung_firmware.notify import enqueue, notify, queue_path, recipient_key, split_message, split_text
from samsung_firmware.storage import Store
from test_versions_events import NEW_TEST, NEW_BETA, STABLE, events, fus_record, record


class NotifyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {'TG_BOT_TOKEN': 'offline-test', 'TG_USER_ID': '12345',
                                     'TG_CHAT_ID': '-10012345', 'FW_NOTIFY_GROUPS': 'false'}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.settings = Settings.from_env(Path(temp.name))
        clock = patch('samsung_firmware.notify.time.time', return_value=10000)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        sleep = patch('samsung_firmware.notify.time.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)
        session_patch = patch('samsung_firmware.notify.requests.Session')
        self.session = session_patch.start().return_value.__enter__.return_value
        self.addCleanup(session_patch.stop)
        self.response = self.session.post.return_value.__enter__.return_value
        self.response.status_code = 200
        self.response.json.return_value = {'ok': True}

    def enqueue_versions(self, *versions):
        result = events(record(), record(versions))
        enqueue(self.settings, result, '-10012345')
        return result

    def test_batch_window_merges_then_cooldown_defers_next_digest(self):
        first = self.enqueue_versions(NEW_TEST)
        self.clock.return_value = 10100
        self.enqueue_versions(NEW_BETA)
        enqueue(self.settings, first, '-10012345')
        queue = load_json(queue_path(self.settings))
        self.assertEqual(len(queue), 1)
        notify(self.settings, '-10012345')
        self.session.post.assert_not_called()
        self.clock.return_value = 10301
        notify(self.settings, '-10012345')
        self.assertEqual(self.session.post.call_count, 1)
        text = self.session.post.call_args.kwargs['json']['text']
        self.assertIn(NEW_TEST.split('/')[0], text)
        self.assertIn(NEW_BETA.split('/')[0], text)
        later = NEW_BETA.replace('ZJ1', 'ZJ2')
        self.enqueue_versions(later)
        self.clock.return_value = 11000
        notify(self.settings, '-10012345')
        self.assertEqual(self.session.post.call_count, 1)
        self.clock.return_value = 12200
        notify(self.settings, '-10012345')
        self.assertEqual(self.session.post.call_count, 2)
        notify(self.settings, '-10012345')
        self.assertEqual(self.session.post.call_count, 2)

    def test_group_pause_blocks_restored_targets_and_legacy_queue(self):
        with patch.dict(os.environ, {'FW_NOTIFY_GROUPS': 'true'}):
            self.enqueue_versions(NEW_TEST)
        path = queue_path(self.settings)
        queue = load_json(path)
        queue['legacy'] = {'text': '三星新增测试固件\nold', 'targets': {recipient_key('offline-test', '-10012345'): 0}}
        atomic_json(path, queue)
        self.clock.return_value = 10400
        notify(self.settings, '-10012345')
        self.assertEqual(self.session.post.call_count, 1)
        self.assertEqual(self.session.post.call_args.kwargs['json']['chat_id'], '12345')
        self.assertTrue(load_json(path)['legacy']['suppressed_at'])

    def test_latest_state_discards_promoted_test_before_delivery(self):
        self.enqueue_versions(NEW_TEST)
        self.clock.return_value = 10400
        notify(self.settings, data={'SM-F9260': {'CHC': record([NEW_TEST], stable=NEW_TEST)}})
        self.session.post.assert_not_called()

    def test_group_digest_includes_stable_section(self):
        with patch.dict(os.environ, {'FW_NOTIFY_GROUPS': 'true', 'FW_NOTIFY_BATCH_SECONDS': '0'}):
            item = fus_record([NEW_BETA])
            enqueue(self.settings, events(record(), item), '-10012345')
            notify(self.settings, '-10012345', data={'SM-F9260': {'CHC': item}})
        sent = {call.kwargs['json']['chat_id']: call.kwargs['json']['text'] for call in self.session.post.call_args_list}
        self.assertIn('FUS 新版本上线', sent['12345'])
        self.assertEqual(sent['-10012345'], sent['12345'])
        self.assertIn(NEW_BETA.split('/')[0], sent['-10012345'])
        self.assertIn(NEW_BETA, sent['-10012345'])
        self.assertEqual(self.session.post.call_count, 2)
        self.assertNotIn('仅列出高于', sent['-10012345'])

    def test_fus_only_notifications_reach_both_recipients_by_default(self):
        item = fus_record()
        updates = events(record(), item)
        data = {'SM-F9260': {'CHC': item}}
        with patch.dict(os.environ):
            os.environ.pop('FW_NOTIFY_GROUPS')
            enqueue(self.settings, updates, '-10012345')
            notify(self.settings, '-10012345', data)
            self.session.post.assert_not_called()
            self.clock.return_value = 10400
            notify(self.settings, '-10012345')
            self.session.post.assert_not_called()
            notify(self.settings, '-10012345', data)
            enqueue(self.settings, updates, '-10012345')
            notify(self.settings, '-10012345', data)
        sent = {call.kwargs['json']['chat_id']: call.kwargs['json'] for call in self.session.post.call_args_list}
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(set(sent), {'12345', '-10012345'})
        self.assertIn('FUS 新版本上线', sent['-10012345']['text'])
        self.assertNotIn('内测固件', sent['-10012345']['text'])
        self.assertEqual(sent['-10012345']['text'], sent['12345']['text'])
        self.assertEqual(sent['-10012345']['entities'], sent['12345']['entities'])

    def test_group_only_fus_notifications_retry_without_personal_id(self):
        item = fus_record()
        data = {'SM-F9260': {'CHC': item}}
        with patch.dict(os.environ, {'FW_NOTIFY_GROUPS': 'true', 'TG_USER_ID': ''}):
            enqueue(self.settings, events(record(), item), '-10012345')
            self.clock.return_value = 10400
            self.response.json.return_value = {'ok': False}
            notify(self.settings, '-10012345', data)
            self.session.post.assert_called_once()
            first = self.session.post.call_args.kwargs['json']
            self.assertEqual(first['chat_id'], '-10012345')
            self.assertIn('FUS 新版本上线', first['text'])
            self.clock.return_value = 10459
            notify(self.settings, '-10012345', data)
            self.session.post.assert_called_once()
            self.response.json.return_value = {'ok': True}
            self.clock.return_value = 10461
            notify(self.settings, '-10012345', data)
            notify(self.settings, '-10012345', data)
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(self.session.post.call_args.kwargs['json'], first)

    def test_disabling_groups_blocks_pending_fus_digest(self):
        item = fus_record()
        data = {'SM-F9260': {'CHC': item}}
        with patch.dict(os.environ, {'FW_NOTIFY_GROUPS': 'true'}):
            enqueue(self.settings, events(record(), item), '-10012345')
            self.clock.return_value = 10400
            self.response.json.return_value = {'ok': False}
            notify(self.settings, '-10012345', data)
        self.session.post.assert_called_once()
        self.assertEqual(self.session.post.call_args.kwargs['json']['chat_id'], '-10012345')
        self.response.json.return_value = {'ok': True}
        self.clock.return_value = 10461
        notify(self.settings, '-10012345', data)
        notify(self.settings, '-10012345', data)
        self.assertEqual([call.kwargs['json']['chat_id'] for call in self.session.post.call_args_list],
                         ['-10012345', '12345'])

    def test_enabling_groups_does_not_backfill_existing_personal_queue(self):
        item = fus_record()
        updates = events(record(), item)
        data = {'SM-F9260': {'CHC': item}}
        enqueue(self.settings, updates, '-10012345')
        self.clock.return_value = 10400
        with patch.dict(os.environ, {'FW_NOTIFY_GROUPS': 'true'}):
            notify(self.settings, '-10012345', data)
            enqueue(self.settings, updates, '-10012345')
            notify(self.settings, '-10012345', data)
        self.session.post.assert_called_once()
        self.assertEqual(self.session.post.call_args.kwargs['json']['chat_id'], '12345')

    def test_group_partial_fus_retry_preserves_pages_and_waits_for_evidence(self):
        item = fus_record(stable=STABLE.replace('ZH2', 'ZH1'))
        event = events(record(), item)[0]
        event['name'] = 'A' * 4000
        data = {'SM-F9260': {'CHC': item}}
        with patch.dict(os.environ, {'FW_NOTIFY_GROUPS': 'true', 'TG_USER_ID': ''}):
            enqueue(self.settings, [event], '-10012345')
            self.clock.return_value = 10400
            self.response.json.side_effect = [{'ok': True}, {'ok': False}]
            notify(self.settings, '-10012345', data)
            self.assertEqual(self.session.post.call_count, 2)
            first, failed = [call.kwargs['json'] for call in self.session.post.call_args_list]
            self.response.json.side_effect = None
            self.clock.return_value = 10461
            notify(self.settings, '-10012345')
            self.assertEqual(self.session.post.call_count, 2)
            item['最新正式版'] = STABLE
            notify(self.settings, '-10012345', data)
            notify(self.settings, '-10012345', data)
        self.assertEqual(self.session.post.call_args_list[2].kwargs['json'], failed)
        self.assertEqual(sum(call.kwargs['json'] == first for call in self.session.post.call_args_list), 1)
        pending = load_json(queue_path(self.settings))[event['id']]
        self.assertTrue(pending['delivered_at'])
        self.assertEqual(pending['entries'][0]['stable']['earlier'], STABLE.replace('ZH2', 'ZH1'))

    def test_old_digest_keeps_test_only_group_layout_on_revalidation(self):
        item = fus_record([NEW_BETA], stable=STABLE.replace('ZH2', 'ZH1'))
        event = events(record(), item)[0]
        data = {'SM-F9260': {'CHC': item}}
        with patch.dict(os.environ, {'FW_NOTIFY_GROUPS': 'true'}):
            enqueue(self.settings, [event], '-10012345')
            self.clock.return_value = 10400
            self.response.json.return_value = {'ok': False}
            notify(self.settings, '-10012345', data)
            queue = load_json(queue_path(self.settings))
            pending = queue[event['id']]
            pending.pop('group_stable', None)
            group = pending['group_target']
            pending['pages'][group] = digest_pages(pending['entries'], group, group, group_stable=False)
            atomic_json(queue_path(self.settings), queue)
            self.session.post.reset_mock()
            self.response.json.return_value = {'ok': True}
            item['最新正式版'] = STABLE
            self.clock.return_value = 10461
            notify(self.settings, '-10012345', data)
        sent = {call.kwargs['json']['chat_id']: call.kwargs['json']['text'] for call in self.session.post.call_args_list}
        self.assertEqual(self.session.post.call_count, 2)
        self.assertIn(NEW_BETA, sent['-10012345'])
        self.assertNotIn('FUS', sent['-10012345'])
        self.assertIn('FOTA 当前正式版：' + STABLE.split('/')[0], sent['12345'])

    def test_started_legacy_group_message_keeps_test_only_chunk_offsets(self):
        item = fus_record([NEW_BETA])
        event = events(record(), item)[0]
        with patch.dict(os.environ, {'FW_NOTIFY_GROUPS': 'true', 'TG_USER_ID': ''}):
            enqueue(self.settings, [event], '-10012345')
            queue = load_json(queue_path(self.settings))
            pending = queue[event['id']]
            pending['name'] = 'A' * 4000
            pending['started_at'] = 10000
            pending['targets'][recipient_key('offline-test', '-10012345')] = 1
            chunks = split_message(**render_message({**pending, 'stable': None}))
            atomic_json(queue_path(self.settings), queue)
            self.clock.return_value = 10400
            notify(self.settings, '-10012345', {'SM-F9260': {'CHC': item}})
        self.assertEqual([call.kwargs['json']['text'] for call in self.session.post.call_args_list],
                         [chunk['text'] for chunk in chunks[1:]])
        self.assertTrue(all('FUS' not in call.kwargs['json']['text'] for call in self.session.post.call_args_list))

    def test_fota_catchup_removes_queued_fus_but_keeps_tests(self):
        item = fus_record([NEW_BETA])
        enqueue(self.settings, events(record(), item))
        item['最新正式版'] = NEW_TEST
        self.clock.return_value = 10400
        notify(self.settings, data={'SM-F9260': {'CHC': item}})
        self.session.post.assert_called_once()
        text = self.session.post.call_args.kwargs['json']['text']
        self.assertIn(NEW_BETA.split('/')[0], text)
        self.assertNotIn('FUS 新版本上线', text)

    def test_legacy_stable_backlog_and_failed_attempt_are_suppressed(self):
        for started in (False, True):
            with self.subTest(started=started):
                item = fus_record()
                event = events(record(), item)[0]
                event['stable'].pop('rule')
                event['id'] = str(started)
                enqueue(self.settings, [event])
                queue = load_json(queue_path(self.settings))
                if started:
                    queue[event['id']]['started_at'] = 10001
                    atomic_json(queue_path(self.settings), queue)
                item['最新正式版'] = NEW_TEST
                self.clock.return_value += 400
                notify(self.settings, data={'SM-F9260': {'CHC': item}})
                self.session.post.assert_not_called()
                self.assertTrue(load_json(queue_path(self.settings))[event['id']]['suppressed_at'])

    def test_fus_alert_waits_for_valid_state_then_delivers(self):
        item = fus_record()
        enqueue(self.settings, events(record(), item))
        self.clock.return_value = 10400
        notify(self.settings)
        self.session.post.assert_not_called()
        item['抓取状态']['stable'] = 'http_503'
        notify(self.settings, data={'SM-F9260': {'CHC': item}})
        self.session.post.assert_not_called()
        item['抓取状态']['stable'] = 'ok'
        item['最新固件下载']['status'] = 'stale'
        notify(self.settings, data={'SM-F9260': {'CHC': item}})
        self.session.post.assert_not_called()
        item['最新固件下载']['status'] = 'available'
        notify(self.settings, data={'SM-F9260': {'CHC': item}})
        self.session.post.assert_called_once()

    def test_queued_fus_updates_fota_baseline_before_delivery(self):
        item = fus_record(stable=STABLE.replace('ZH2', 'ZH1'))
        enqueue(self.settings, events(record(), item))
        item['最新正式版'] = STABLE
        self.clock.return_value = 10400
        notify(self.settings, data={'SM-F9260': {'CHC': item}})
        text = self.session.post.call_args.kwargs['json']['text']
        self.assertIn('FOTA 当前正式版：' + STABLE.split('/')[0], text)

    def test_obsolete_fus_stops_partially_delivered_message(self):
        item = fus_record()
        event = events(record(), item)[0]
        event['name'] = 'A' * 4000
        enqueue(self.settings, [event])
        self.clock.return_value = 10400
        self.response.json.side_effect = [{'ok': True}, {'ok': False}]
        notify(self.settings, data={'SM-F9260': {'CHC': item}})
        self.assertEqual(self.session.post.call_count, 2)
        item['最新正式版'] = NEW_TEST
        self.clock.return_value = 10461
        notify(self.settings, data={'SM-F9260': {'CHC': item}})
        self.assertEqual(self.session.post.call_count, 2)
        self.assertTrue(load_json(queue_path(self.settings))[event['id']]['suppressed_at'])

    def test_merged_fus_updates_use_latest_fota_baseline(self):
        first = fus_record()
        enqueue(self.settings, events(record(), first))
        self.clock.return_value = 10100
        latest = fus_record(stable=NEW_TEST, fus=NEW_TEST.replace('ZI1', 'ZI2'))
        enqueue(self.settings, events(first, latest))
        queue = load_json(queue_path(self.settings))
        self.assertEqual(len(queue), 1)
        pending = next(iter(queue.values()))
        self.assertEqual(pending['stable']['earlier'], NEW_TEST)
        self.assertEqual(pending['stable']['current'], latest['最新固件下载']['version'])
        self.clock.return_value = 10400
        notify(self.settings, data={'SM-F9260': {'CHC': latest}})
        self.session.post.assert_called_once()

    def test_fus_seen_marker_survives_database_restart(self):
        item = fus_record()
        events(record(), item)
        store = Store(self.settings)
        try:
            with store.transaction():
                store.save_device('SM-F9260', 'CHC', item)
        finally:
            store.close()
        reopened = Store(self.settings)
        self.addCleanup(reopened.close)
        previous = reopened.snapshots()['SM-F9260']['CHC']
        self.assertEqual(previous['已记录FUS领先版本'], NEW_TEST)
        self.assertEqual(events(previous, fus_record()), [])

    def test_superseded_fus_alert_is_discarded(self):
        item = fus_record()
        event = events(record(), item)[0]
        enqueue(self.settings, [event])
        item['最新固件下载']['version'] = NEW_TEST.replace('ZI1', 'ZI2')
        self.clock.return_value = 10400
        notify(self.settings, data={'SM-F9260': {'CHC': item}})
        self.session.post.assert_not_called()
        self.assertTrue(load_json(queue_path(self.settings))[event['id']]['suppressed_at'])

    def test_failed_send_is_retried_without_mutating_started_message(self):
        self.enqueue_versions(NEW_TEST)
        self.clock.return_value = 10400
        self.response.status_code = 500
        notify(self.settings)
        text = self.session.post.call_args.kwargs['json']['text']
        self.enqueue_versions(NEW_BETA)
        self.assertEqual(len(load_json(queue_path(self.settings))), 2)
        self.response.status_code = 200
        self.clock.return_value = 10461
        notify(self.settings)
        self.assertEqual(self.session.post.call_args.kwargs['json']['text'], text)
        self.assertEqual(self.session.post.call_count, 2)

    def test_split_preserves_lines_and_telegram_utf16_limit(self):
        text = '\n'.join(f'{i}. {NEW_TEST} 😀' for i in range(180))
        chunks = split_text(text)
        self.assertGreater(len(chunks), 1)
        self.assertEqual('\n'.join(chunks), text)
        self.assertTrue(all(len(value.encode('utf-16-le')) // 2 <= 3900 for value in chunks))

    def test_text_links_survive_long_messages_and_emoji(self):
        item = fus_record()
        event = events(record(), item)[0]
        event['name'] = '设备 😀 <Ultra> & ' * 350
        enqueue(self.settings, [event])
        self.clock.return_value = 10400
        notify(self.settings, data={'SM-F9260': {'CHC': item}})
        labels = {}
        for call in self.session.post.call_args_list:
            payload = call.kwargs['json']
            encoded = payload['text'].encode('utf-16-le')
            self.assertLessEqual(len(encoded) // 2, 3900)
            self.assertNotIn('https://', payload['text'])
            for link in payload['entities']:
                label = encoded[link['offset'] * 2:(link['offset'] + link['length']) * 2].decode('utf-16-le')
                labels[label] = link['url']
        self.assertGreater(self.session.post.call_count, 1)
        self.assertEqual(labels, {'下载地址': item['最新固件下载']['url'],
                                  '更新说明': 'https://doc.samsungmobile.com/SM-F9260/CHC/doc.html'})

    def test_link_crossing_chunk_boundary_keeps_clickable_label(self):
        chunks = split_message('😀下载地址', [{'type': 'text_link', 'offset': 2,
                                             'length': 4, 'url': 'https://example.com'}], limit=4)
        labels = []
        for chunk in chunks:
            encoded = chunk['text'].encode('utf-16-le')
            for link in chunk['entities']:
                labels.append(encoded[link['offset'] * 2:(link['offset'] + link['length']) * 2].decode('utf-16-le'))
        self.assertEqual(''.join(labels), '下载地址')

    def test_group_id_saved_when_notifications_disabled_and_survives_restart(self):
        db = Databases(self.settings)
        self.assertFalse(self.settings.notifications)
        self.assertEqual(db.telegram_group_id(), '-10012345')
        self.assertEqual(db.progress.get_setting('telegram_group_id'), '-10012345')
        self.assertEqual(db.snapshots(), {})
        db.close()
        with patch.dict(os.environ, {'TG_CHAT_ID': '-10098765'}):
            db = Databases(self.settings)
            self.addCleanup(db.close)
            self.assertEqual(db.telegram_group_id(), '-10012345')

    def test_cloud_setting_has_precedence_and_seeds_local(self):
        db = Databases(self.settings)
        self.addCleanup(db.close)
        cloud = MagicMock()
        cloud.get_setting.return_value = '-10055555'
        db.cloud = cloud
        self.assertEqual(db.telegram_group_id(), '-10055555')
        self.assertEqual(db.progress.get_setting('telegram_group_id'), '-10055555')
        cloud.set_setting.assert_not_called()

    def test_invalid_group_id_not_persisted(self):
        with patch.dict(os.environ, {'TG_CHAT_ID': '12345'}):
            db = Databases(self.settings)
            self.addCleanup(db.close)
            self.assertIsNone(db.telegram_group_id())
            self.assertIsNone(db.progress.get_setting('telegram_group_id'))

    def test_telegram_retry_after_pauses_all_pending_sends(self):
        self.enqueue_versions(NEW_TEST)
        other = events(record(), record([NEW_BETA]))[0]
        other.update(id="different-region", region="TGY")
        enqueue(self.settings, [other])
        self.clock.return_value = 10400
        self.response.status_code = 429
        self.response.json.return_value = {"ok": False, "parameters": {"retry_after": 100}}
        notify(self.settings)
        self.assertEqual(self.session.post.call_count, 1)
        self.clock.return_value = 10499
        notify(self.settings)
        self.assertEqual(self.session.post.call_count, 1)
        self.response.status_code = 200
        self.response.json.return_value = {"ok": True}
        self.clock.return_value = 10501
        notify(self.settings)
        self.assertEqual(self.session.post.call_count, 2)
        text = self.session.post.call_args.kwargs["json"]["text"]
        self.assertIn("共<2>台机型", text)
        self.assertIn(NEW_TEST, text)
        self.assertIn(NEW_BETA, text)

    def test_sync_config_mode_never_fetches_or_sends(self):
        from argparse import Namespace
        from dataclasses import replace
        from samsung_firmware.pipeline import execute_run
        with patch("samsung_firmware.pipeline.HttpClient") as client, patch("samsung_firmware.notify.notify") as send:
            result = execute_run(replace(self.settings, notifications=True), Namespace(sync_notification_config=True))
        self.assertEqual(result, 0)
        client.return_value.fetch_device.assert_not_called()
        send.assert_not_called()
        store = Store(self.settings)
        self.addCleanup(store.close)
        self.assertEqual(store.get_setting("telegram_group_id"), "-10012345")

    def test_failed_cloud_read_cannot_overwrite_unknown_cloud_setting(self):
        db = Databases(self.settings)
        self.addCleanup(db.close)
        cloud = MagicMock()
        cloud.get_setting.side_effect = OSError("offline")
        db.cloud = cloud
        self.assertEqual(db.telegram_group_id(), "-10012345")
        cloud.set_setting.assert_not_called()
        self.assertTrue(db.cloud_error)

    def test_partial_message_retries_only_unsent_chunks(self):
        event = events(record(), record([NEW_TEST]))[0]
        event["versions"] = [NEW_TEST.replace("ZI1", f"ZI{x}") for x in "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
        event["name"] = "A" * 4000
        enqueue(self.settings, [event])
        self.clock.return_value = 10400
        self.response.status_code = 200
        self.response.json.side_effect = [{"ok": True}, {"ok": False}, {"ok": True}, {"ok": True}, {"ok": True}]
        notify(self.settings)
        first_text = self.session.post.call_args_list[0].kwargs["json"]["text"]
        failed_text = self.session.post.call_args_list[1].kwargs["json"]["text"]
        self.clock.return_value = 10461
        notify(self.settings)
        self.assertEqual(self.session.post.call_args_list[2].kwargs["json"]["text"], failed_text)
        self.assertEqual(sum(call.kwargs["json"]["text"] == first_text for call in self.session.post.call_args_list), 1)


    def device_event(self, model, name, region="CHC", versions=(NEW_BETA,), fus=False):
        code = model.removeprefix("SM-")
        versions = [v.replace("F9260", code) for v in versions]
        baseline = STABLE.replace("F9260", code)
        before = record(stable=baseline)
        after = (fus_record(versions, stable=baseline, fus=NEW_TEST.replace("F9260", code))
                 if fus else record(versions, stable=baseline))
        after["机型"] = name
        event = firmware_events({model: {region: before}}, {model: {region: after}}, [(model, region)])[0]
        return event, after

    def test_restores_original_digest_for_multiple_models_and_deduplicates(self):
        first, _ = self.device_event("SM-X800", "Tab S8+", "TGY")
        second, _ = self.device_event("SM-X900", "Tab S8 Ultra", "TGY")
        first["versions"] = ["X800XXSBEZK1/X800OXMBEZK1/"]
        second["versions"] = ["X900XXSBEZK1/X900OXMBEZK1/"]
        enqueue(self.settings, [second, first])
        self.clock.return_value = 10400
        notify(self.settings)
        self.session.post.assert_called_once()
        self.assertEqual(self.session.post.call_args.kwargs["json"]["text"],
                         "三星新增内测固件—共<2>台机型(1/1)\n"
                         "1. #X800 - Tab S8+(香港版)\nX800XXSBEZK1/X800OXMBEZK1/\n\n"
                         "2. #X900 - Tab S8 Ultra(香港版)\nX900XXSBEZK1/X900OXMBEZK1/")
        enqueue(self.settings, [first, second])
        notify(self.settings)
        self.session.post.assert_called_once()
        self.assertEqual(len(load_json(queue_path(self.settings))), 1)

    def test_digest_keeps_distinct_csc_cp_versions_for_same_ap(self):
        event, _ = self.device_event("SM-F9260", "Z Fold3", "ZZZ")
        other_cp = NEW_BETA.rsplit("/", 1)[0] + "/F9260ZCUHKZJ2"
        event["versions"] = [NEW_BETA, other_cp, NEW_BETA]
        enqueue(self.settings, [event])
        self.clock.return_value = 10400
        notify(self.settings)
        text = self.session.post.call_args.kwargs["json"]["text"]
        self.assertIn("Z Fold3(ZZZ版)", text)
        self.assertEqual(text.count(NEW_BETA), 1)
        self.assertEqual(text.count(other_cp), 1)

    def test_all_fus_and_test_updates_share_one_message_per_recipient(self):
        first, one = self.device_event("SM-F9260", "Z Fold3 😀", fus=True)
        second, two = self.device_event("SM-F9360", "Z Fold4", fus=True)
        data = {first["model"]: {"CHC": one}, second["model"]: {"CHC": two}}
        with patch.dict(os.environ, {"FW_NOTIFY_GROUPS": "true", "FW_NOTIFY_BATCH_SECONDS": "0"}):
            enqueue(self.settings, [second, first], "-10012345")
            notify(self.settings, "-10012345", data)
        sent = {call.kwargs["json"]["chat_id"]: call.kwargs["json"] for call in self.session.post.call_args_list}
        self.assertEqual(len(self.session.post.call_args_list), 2)
        personal = sent["12345"]
        self.assertIn("三星新增内测固件—共<2>台机型(1/1)", personal["text"])
        self.assertIn("三星 FUS 新版本上线—共<2>台机型(1/1)", personal["text"])
        self.assertEqual(sent["-10012345"]["text"], personal["text"])
        self.assertEqual(sent["-10012345"]["entities"], personal["entities"])
        labels = []
        encoded = personal["text"].encode("utf-16-le")
        for link in personal["entities"]:
            labels.append(encoded[2 * link["offset"]:2 * (link["offset"] + link["length"])].decode("utf-16-le"))
        self.assertEqual(labels, ["下载地址", "更新说明"] * 2)
        self.assertEqual([link["url"] for link in personal["entities"] if "doc.samsungmobile" in link["url"]],
                         ["https://doc.samsungmobile.com/SM-F9260/CHC/doc.html",
                          "https://doc.samsungmobile.com/SM-F9360/CHC/doc.html"])

    def test_fus_only_updates_for_multiple_models_share_one_message(self):
        first, one = self.device_event("SM-F9260", "Z Fold3", versions=(), fus=True)
        second, two = self.device_event("SM-F9360", "Z Fold4", versions=(), fus=True)
        enqueue(self.settings, [first, second])
        self.clock.return_value = 10400
        notify(self.settings, data={first["model"]: {"CHC": one}, second["model"]: {"CHC": two}})
        self.session.post.assert_called_once()
        text = self.session.post.call_args.kwargs["json"]["text"]
        self.assertTrue(text.startswith("三星 FUS 新版本上线—共<2>台机型(1/1)"))
        self.assertNotIn("内测固件", text)
        self.assertEqual(text.count("FOTA 当前正式版："), 2)
        self.assertEqual(text.count("FUS 版本："), 2)

    def test_expired_fus_on_retry_does_not_drop_other_devices_or_repeat_tests(self):
        test, _ = self.device_event("SM-X800", "A" * 3800)
        first, one = self.device_event("SM-F9260", "Z Fold3", versions=(), fus=True)
        second, two = self.device_event("SM-F9360", "Z Fold4", versions=(), fus=True)
        data = {first["model"]: {"CHC": one}, second["model"]: {"CHC": two}}
        enqueue(self.settings, [test, first, second])
        self.clock.return_value = 10400
        self.response.json.side_effect = [{"ok": True}, {"ok": False}]
        notify(self.settings, data=data)
        self.assertEqual(self.session.post.call_count, 2)
        first_page = self.session.post.call_args_list[0].kwargs["json"]["text"]
        one["最新正式版"] = one["最新固件下载"]["version"]
        self.response.json.side_effect = None
        self.clock.return_value = 10461
        notify(self.settings, data=data)
        self.assertEqual(self.session.post.call_count, 3)
        final = self.session.post.call_args.kwargs["json"]["text"]
        self.assertNotIn("#F9260", final)
        self.assertIn("#F9360", final)
        self.assertEqual(sum(call.kwargs["json"]["text"] == first_page for call in self.session.post.call_args_list), 1)
        self.assertTrue(next(iter(load_json(queue_path(self.settings)).values()))["delivered_at"])

    def test_retry_waits_for_fus_evidence_without_resending_test_page(self):
        test, _ = self.device_event("SM-X800", "A" * 3800)
        fus, state = self.device_event("SM-F9260", "Z Fold3", versions=(), fus=True)
        data = {fus["model"]: {"CHC": state}}
        enqueue(self.settings, [test, fus])
        self.clock.return_value = 10400
        self.response.json.side_effect = [{"ok": True}, {"ok": False}]
        notify(self.settings, data=data)
        self.response.json.side_effect = None
        self.clock.return_value = 10461
        notify(self.settings)
        self.assertEqual(self.session.post.call_count, 2)
        notify(self.settings, data=data)
        self.assertEqual(self.session.post.call_count, 3)
        self.assertNotIn("#X800", self.session.post.call_args.kwargs["json"]["text"])

    def test_group_success_and_personal_retry_keep_separate_progress(self):
        event, state = self.device_event("SM-F9260", "Z Fold3", fus=True)
        data = {event["model"]: {"CHC": state}}
        with patch.dict(os.environ, {"FW_NOTIFY_GROUPS": "true"}):
            enqueue(self.settings, [event], "-10012345")
            self.clock.return_value = 10400
            self.response.json.side_effect = [{"ok": True}, {"ok": False}]
            notify(self.settings, "-10012345", data)
            self.assertIn("FUS 新版本上线", self.session.post.call_args_list[0].kwargs["json"]["text"])
            state["最新正式版"] = state["最新固件下载"]["version"]
            self.response.json.side_effect = None
            self.clock.return_value = 10461
            notify(self.settings, "-10012345", data)
            notify(self.settings, "-10012345", data)
        self.assertEqual([call.kwargs["json"]["chat_id"] for call in self.session.post.call_args_list],
                         ["-10012345", "12345", "12345"])
        final = self.session.post.call_args.kwargs["json"]["text"]
        self.assertIn(NEW_BETA, final)
        self.assertNotIn("FUS", final)

    def test_multi_device_retry_freezes_pages_and_defers_later_same_device(self):
        first, _ = self.device_event("SM-X800", "A" * 3800)
        second, _ = self.device_event("SM-X900", "Tab S8 Ultra")
        with patch.dict(os.environ, {"FW_NOTIFY_BATCH_SECONDS": "0"}):
            enqueue(self.settings, [first, second])
            self.response.json.side_effect = [{"ok": True}, {"ok": False}]
            notify(self.settings)
            failed = self.session.post.call_args.kwargs["json"]["text"]
            later, _ = self.device_event("SM-X900", "Tab S8 Ultra", versions=(NEW_BETA.replace("ZJ1", "ZJ2"),))
            enqueue(self.settings, [later])
            self.response.json.side_effect = None
            self.clock.return_value = 10061
            notify(self.settings)
            self.assertEqual(self.session.post.call_count, 3)
            self.assertEqual(self.session.post.call_args.kwargs["json"]["text"], failed)
            notify(self.settings)
            self.assertEqual(self.session.post.call_count, 3)
            self.clock.return_value = 11862
            notify(self.settings)
        self.assertEqual(self.session.post.call_count, 4)
        self.assertIn(later["versions"][0], self.session.post.call_args.kwargs["json"]["text"])

    def test_promoted_device_is_removed_from_digest_count(self):
        first, state = self.device_event("SM-X800", "Tab S8+")
        second, _ = self.device_event("SM-X900", "Tab S8 Ultra")
        enqueue(self.settings, [first, second])
        state["最新正式版"] = first["versions"][0]
        self.clock.return_value = 10400
        notify(self.settings, data={first["model"]: {"CHC": state}})
        text = self.session.post.call_args.kwargs["json"]["text"]
        self.assertIn("共<1>台机型", text)
        self.assertNotIn("#X800", text)
        self.assertIn("1. #X900", text)

    def test_digest_pagination_keeps_device_blocks_and_multidigit_page_numbers(self):
        entries = []
        for index in range(40):
            event, _ = self.device_event(f"SM-X{index:03}", f"Tab {index} 😀")
            event["targets"] = {"personal": 0}
            entries.append(event)
        pages = digest_pages(entries, "personal", limit=300)
        counts = digest_counts([block for page in pages for block in page])
        self.assertGreaterEqual(len(pages), 10)
        rendered = [render_digest_page(page, counts, index, len(pages)) for index, page in enumerate(pages, 1)]
        for index, message in enumerate(rendered, 1):
            self.assertLessEqual(len(message["text"].encode("utf-16-le")) // 2, 300)
            self.assertTrue(message["text"].startswith(f"三星新增内测固件—共<40>台机型({index}/{len(pages)})"))
        for index, event in enumerate(entries, 1):
            block = f"{index}. #{event['model'][3:]} - {event['name']}(国行版)\n{event['versions'][0]}"
            self.assertEqual(sum(block in message["text"] for message in rendered), 1)

    def test_mixed_digest_pagination_preserves_links_after_section_headers(self):
        entries = []
        for index in range(15):
            event, _ = self.device_event(f"SM-F{index:03}", f"Fold 😀 {index}", fus=True)
            event["targets"] = {"personal": 0}
            entries.append(event)
        pages = digest_pages(entries, "personal", limit=400)
        counts = digest_counts([block for page in pages for block in page])
        labels = []
        for index, page in enumerate(pages, 1):
            message = render_digest_page(page, counts, index, len(pages))
            encoded = message["text"].encode("utf-16-le")
            self.assertLessEqual(len(encoded) // 2, 400)
            for link in message["entities"]:
                label = encoded[2 * link["offset"]:2 * (link["offset"] + link["length"])].decode("utf-16-le")
                labels.append(label)
        self.assertEqual(labels, ["下载地址", "更新说明"] * len(entries))

    def test_started_legacy_message_keeps_original_chunk_offsets(self):
        event = self.enqueue_versions(NEW_TEST)[0]
        queue = load_json(queue_path(self.settings))
        pending = queue[event["id"]]
        pending["name"] = "A" * 4000
        pending["started_at"] = 10000
        pending["targets"][recipient_key("offline-test", "12345")] = 1
        chunks = split_message(**render_message(pending))
        atomic_json(queue_path(self.settings), queue)
        self.clock.return_value = 10400
        notify(self.settings)
        self.assertEqual([call.kwargs["json"]["text"] for call in self.session.post.call_args_list],
                         [chunk["text"] for chunk in chunks[1:]])
