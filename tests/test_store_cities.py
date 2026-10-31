from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from jx3_bot.cities import import_csv, parse_csv
from jx3_bot.errors import BotError, BudgetExhausted, StorageError
from jx3_bot.models import coordinate
from jx3_bot.store import Store
from support import Clock, city, csv_bytes, message


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.sqlite'
        self.store = Store(self.path)
        self.store.bind_timezone('Asia/Shanghai')
        self.clock = Clock()
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_csv_version_bom_and_coordinates(self):
        values, version = parse_csv(b'\xef\xbb\xbf' + csv_bytes())
        self.assertEqual(len(values), 1)
        self.assertEqual(version, 'China-City-List test-version')
        self.assertEqual(values[0].coordinates(), ('39.90', '116.41'))
        self.assertEqual(coordinate('-0.001', True), '0.00')
        self.assertEqual(coordinate('1.005', False), '1.01')
        for value in ('nan', 'inf', '91', 'junk'):
            with self.assertRaises(BotError): coordinate(value, True)

    def test_import_atomic_and_duplicate_ids(self):
        import_csv(self.store, csv_bytes(), source='synthetic')
        for raw in (b'bad', csv_bytes([city(), city()]), csv_bytes([replace(city(), latitude='nan')]),
                    csv_bytes([replace(city(), timezone='NoSuch/Zone')])):
            with self.assertRaises(BotError): import_csv(self.store, raw)
            self.assertEqual(self.store.find_cities('北京')[0].id, '101010100')
        self.assertEqual(self.store.meta('city_source')['source'], 'synthetic')

    def test_ambiguous_and_admin_lookup(self):
        values = [city('b', '朝阳', '北京', '北京'), city('l', '朝阳', '辽宁', '朝阳'), city('j', '朝阳', '吉林', '长春')]
        import_csv(self.store, csv_bytes(values))
        self.assertEqual(len(self.store.find_cities('朝阳')), 3)
        self.assertEqual(self.store.find_cities('北京 朝阳')[0].id, 'b')
        self.assertEqual(self.store.find_cities('j')[0].id, 'j')
        self.assertEqual(self.store.find_cities("' OR 1=1 --"), [])

    def test_last_budget_slot_is_atomic_across_connections(self):
        other = Store(self.path)
        try:
            def reserve(i):
                try:
                    (self.store if i % 2 else other).reserve(1, 'Asia/Shanghai', self.clock())
                    return True
                except BudgetExhausted:
                    return False
            with ThreadPoolExecutor(max_workers=8) as pool:
                result = list(pool.map(reserve, range(32)))
            self.assertEqual(sum(result), 1)
            self.assertEqual(self.store.usage('Asia/Shanghai', self.clock())[1], 1)
        finally: other.close()

    def test_zero_lowered_limit_restart_and_month_boundary(self):
        with self.assertRaises(BudgetExhausted): self.store.reserve(0, 'Asia/Shanghai', self.clock())
        self.store.reserve(5, 'Asia/Shanghai', self.clock())
        self.store.close()
        self.store = Store(self.path)
        self.store.bind_timezone('Asia/Shanghai')
        with self.assertRaises(BudgetExhausted): self.store.reserve(1, 'Asia/Shanghai', self.clock())
        self.clock.now = datetime(2026, 10, 31, 15, 59, 59, tzinfo=timezone.utc).timestamp()
        with self.assertRaises(BudgetExhausted): self.store.reserve(1, 'Asia/Shanghai', self.clock())
        self.clock.now += 1
        self.assertEqual(self.store.reserve(1, 'Asia/Shanghai', self.clock()), 1)
        self.assertEqual(self.store.usage('Asia/Shanghai', self.clock())[0], '2026-11')
        self.clock.now -= 1
        with self.assertRaises(StorageError): self.store.reserve(5, 'Asia/Shanghai', self.clock())

    def test_timezone_change_cannot_reset_budget(self):
        with self.assertRaises(StorageError): self.store.bind_timezone('UTC')
        with self.assertRaises(StorageError): self.store.reserve(2, 'UTC', self.clock())

    def test_inbox_checkpoint_double_event_and_restart(self):
        msg = message(self.clock)
        self.store.ready('session', 0)
        self.assertTrue(self.store.accept('session', 1, msg, self.clock()))
        self.assertFalse(self.store.accept('session', 3, msg, self.clock()))
        self.assertEqual(self.store.meta('gateway')['seq'], 3)
        self.assertEqual(self.store.claim_command(self.clock()).message_id, 'm1')
        self.store.close()
        self.store = Store(self.path)
        recovered = self.store.recover()
        self.assertEqual(recovered['retired_commands'], 1)
        self.assertIsNone(self.store.claim_command(self.clock()))
        self.assertEqual(self.store.meta('gateway')['seq'], 3)
        self.assertTrue(self.store.accept('session', 4, replace(msg, message_id='m2'), self.clock()))
        current = self.store.claim_command(self.clock())
        self.store.plan_reply(current, 'synthetic answer')
        out = self.store.claim_reply(self.clock())
        self.assertEqual(out['seq'], 1)
        self.assertEqual(out['msg'], 'm2')
        self.store.recover()
        self.assertIsNone(self.store.claim_reply(self.clock()))
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0], 'uncertain')

    def test_checkpoint_not_advanced_when_acceptance_fails(self):
        self.store.ready('session', 0)
        with self.assertRaises(StorageError): self.store.accept('wrong', 9, message(self.clock), self.clock())
        self.assertEqual(self.store.meta('gateway')['seq'], 0)
        self.store.accept('session', 2, None, self.clock())
        self.assertEqual(self.store.meta('gateway')['seq'], 2)
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM inbox')[0][0], 0)

    def test_preference_scope_and_deletion(self):
        self.store.set_preference('a','g','u','北京')
        self.assertEqual(self.store.preference('a','g','u'), '北京')
        self.assertIsNone(self.store.preference('a','g2','u'))
        self.assertIsNone(self.store.preference('b','g','u'))
        self.store.set_preference('a','g','u',None)
        self.assertIsNone(self.store.preference('a','g','u'))

    def test_replies_expire_at_original_message_time(self):
        self.store.ready('s', 0)
        msg = message(self.clock)
        self.store.accept('s', 1, msg, self.clock())
        msg = self.store.claim_command(self.clock())
        self.store.plan_reply(msg, 'synthetic')
        self.clock.now += 300
        self.assertIsNone(self.store.claim_reply(self.clock()))
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0], 'expired')
