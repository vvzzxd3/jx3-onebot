"""Approved receive/send boundaries: temporary SQLite, synthetic QQ, no network."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from jx3_bot.application import Application
from jx3_bot.errors import ProviderError, ReplyExpired, ReplyNotSent, StorageError
from jx3_bot.gateway import Gateway, Reconnect
from jx3_bot.integrations.qq.events import normalize_message
from jx3_bot.integrations.qq.models import ReceivedMessage
from jx3_bot.matching import DEFAULT_MATCHERS, MatcherRegistry, to_me
from jx3_bot.modules.chat_history import ChatSettings
from jx3_bot.observability import Observer, LogSettings
from jx3_bot.replies import Reply
from jx3_bot.store import Store
from support import Clock, enable_chat, event, message
from test_gateway import FakeSocket, hello, ready


class OrderedObserver(Observer):
    def __init__(self):
        super().__init__(LogSettings(receive='off'))
        self.steps = []
        self.store = None

    def chat_message(self, item, trace=None):
        assert not self.store.db.in_transaction
        with self.store.lock:
            archived = self.store.chat.existing_message(self.store.db, item)
        assert archived is not None and archived['direction'] == 'in'
        assert archived['content'] == item.content
        self.steps.append(('receive_log', item.message_id))
        super().chat_message(item, trace)

    def chat_sent(self, item, trace=None):
        if item is not None:
            assert not self.store.db.in_transaction
            table = 'chat_group_messages' if item['scene'] == 'group' else 'chat_user_messages'
            assert self.store._read(f'SELECT direction FROM {table} WHERE id=?', (item['id'],))[0][0] == 'out'
            assert self.store._read('SELECT state FROM outbox WHERE state=\'sent\'')
            self.steps.append(('send_log', item['message_id']))
        super().chat_sent(item, trace)


class ChatPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'synthetic.sqlite'
        self.clock = Clock()
        self.observer = OrderedObserver()
        self.store = Store(self.path, self.observer)
        self.observer.store = self.store
        self.store.bind_app('test-app')
        enable_chat(self.store)
        self.store.ready('s', 0)
        self.sequence = 0
        self.weather = AsyncMock()
        self.weather.handle.return_value = '合成回复正文\n第二行'
        async def acknowledge(target, trigger, content, seq, **kwargs):
            assert not self.store.db.in_transaction
            row = self.store._read('SELECT state FROM outbox WHERE scene=? AND grp=? AND msg=?',
                                   (kwargs['scene'], target, trigger))[0]
            assert row['state'] == 'sending'
            qq_id = f"out-{kwargs['scene']}-{trigger}"
            assert not any(r['qq_message_id'] == qq_id for r in self.history())
            self.observer.steps.append(('http_ack', qq_id))
            return {'id': qq_id, 'timestamp': self.clock(), 'ext_info': {'secret': 'NEVER_COPY'}}
        self.qq = SimpleNamespace(reply=AsyncMock(side_effect=acknowledge))
        self.gateway = SimpleNamespace(stop=asyncio.Event())
        self.app = Application(self.store, self.weather, self.qq, self.gateway,
                               clock=self.clock, observer=self.observer)

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    def parsed(self, content='天气 北京', mid='m1', scene='group', user='u', **values):
        frame = event(self.clock, id=mid, content=content,
                      event_type='C2C_MESSAGE_CREATE' if scene == 'c2c' else 'GROUP_MESSAGE_CREATE')
        frame['d']['author']['member_openid'] = user
        if scene == 'c2c':
            frame['d'].pop('group_openid')
            frame['d']['author'] = {'user_openid': user}
        item = normalize_message('test-app', frame, self.clock()).message
        self.assertIsInstance(item, ReceivedMessage)
        return replace(item, **values)

    def ingest(self, item, *, seq=None):
        self.sequence = self.sequence + 1 if seq is None else seq
        return self.store.accept('s', self.sequence, item, self.clock(), True)

    def history(self):
        return [dict(r) for table in ('chat_user_messages', 'chat_group_messages')
                for r in self.store._read(f'SELECT * FROM {table}')]

    def outgoing(self):
        return [r for r in self.history() if r['direction'] == 'out']

    def assert_no_new_receipt(self):
        for table in ('chat_users', 'chat_groups', 'chat_user_messages', 'chat_group_messages', 'inbox'):
            self.assertEqual(self.store._read(f'SELECT COUNT(*) FROM {table}')[0][0], 0)
        self.assertEqual(self.store.meta('gateway'), {'session_id': 's', 'seq': 0})
        self.assertEqual(self.observer.steps, [])

    async def test_parse_commit_receive_log_match_in_that_order(self):
        original = DEFAULT_MATCHERS.match
        def match_after_log(content, context):
            self.assertEqual(self.store.meta('gateway')['seq'], 1)
            self.assertEqual(self.observer.steps[-1], ('receive_log', 'm1'))
            self.assertFalse(self.store.db.in_transaction)
            self.observer.steps.append(('match', 'm1'))
            return original(content, context)
        with patch.object(DEFAULT_MATCHERS, 'match', side_effect=match_after_log) as matcher:
            item = self.parsed()
            matcher.assert_not_called()
            self.assertEqual(self.ingest(item), 'accepted')
            matcher.assert_not_called()
            self.assertEqual(self.observer.steps, [])
            self.assertEqual(len(self.history()), 1)
            self.assertTrue(await self.app.process_one())
        self.assertEqual(self.observer.steps[:2], [('receive_log', 'm1'), ('match', 'm1')])
        self.weather.handle.assert_awaited_once()
        self.assertEqual(self.outgoing(), [])

    async def test_ordinary_authorized_messages_share_user_but_have_two_collections(self):
        self.ingest(self.parsed('普通群聊'))
        self.ingest(self.parsed('普通单聊', scene='c2c'))
        self.assertTrue(await self.app.process_one()); self.assertTrue(await self.app.process_one())
        self.assertFalse(await self.app.process_one()); self.assertFalse(await self.app.send_one())
        self.weather.handle.assert_not_called(); self.qq.reply.assert_not_called()
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM chat_users')[0][0], 1)
        self.assertEqual(len(self.history()), 2)
        self.assertEqual([step[0] for step in self.observer.steps], ['receive_log', 'receive_log'])

    async def test_unlisted_group_body_is_archived_and_logged(self):
        item = replace(self.parsed('UNLISTED_BODY'), conversation_id='unlisted-group')
        self.assertEqual(self.ingest(item), 'accepted')
        self.assertTrue(await self.app.process_one())
        self.assertEqual(self.history()[0]['content'], 'UNLISTED_BODY')
        self.assertEqual([step[0] for step in self.observer.steps], ['receive_log'])
        self.weather.handle.assert_not_called()
        self.assertEqual(self.store.meta('gateway')['seq'], 1)

    async def test_message_job_failure_rolls_back_entities_history_and_watermark(self):
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fail_receipt_job BEFORE INSERT ON inbox BEGIN SELECT RAISE(ABORT,'synthetic job failure'); END")
        with self.assertRaises(StorageError): self.ingest(self.parsed())
        self.assert_no_new_receipt()

    async def test_checkpoint_failure_rolls_back_message_and_job(self):
        with patch.object(self.store, '_meta', side_effect=sqlite3.OperationalError('synthetic checkpoint failure')):
            with self.assertRaises(StorageError): self.ingest(self.parsed())
        self.assert_no_new_receipt()

    async def test_queue_full_rolls_back_new_history_without_losing_existing_jobs(self):
        with self.store.transaction() as db:
            for index in range(256):
                item = message(self.clock, id=f'legacy-{index}')
                db.execute("INSERT INTO inbox(app,scene,grp,msg,payload,created,expires,state) VALUES(?,?,?,?,?,?,?,?)",
                           (*item.storage_key, json.dumps(item.to_dict()), self.clock(), self.clock()+300, 'pending'))
        with self.assertRaises(StorageError): self.ingest(self.parsed('新消息不能跳洞'))
        self.assertEqual(self.history(), [])
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM chat_users')[0][0], 0)
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM inbox')[0][0], 256)
        self.assertEqual(self.store.meta('gateway')['seq'], 0)

    async def test_gateway_parses_once_and_storage_failure_never_logs_or_matches(self):
        http = SimpleNamespace(tokens=SimpleNamespace(get=AsyncMock(return_value='synthetic-token')))
        registry = Mock(); registry.match.side_effect = AssertionError('matcher ran before durable acceptance')
        gateway = Gateway(http, self.store, 'test-app', clock=self.clock, observer=self.observer, registry=registry)
        frames = [hello(1000), ready(0), event(self.clock)]
        with patch('jx3_bot.gateway.normalize_message', wraps=normalize_message) as parser, \
                patch.object(self.store.chat, 'record_received', side_effect=sqlite3.OperationalError('synthetic archive failure')):
            with self.assertRaises(StorageError): await gateway.connection(FakeSocket(frames))
        self.assertEqual(parser.call_count, 1)
        registry.match.assert_not_called()
        self.assertEqual(self.observer.steps, [])
        self.assertEqual(self.history(), [])
        self.assertEqual(self.store.meta('gateway'), {'session_id': 'session', 'seq': 0})

    async def test_log_output_failure_does_not_undo_receipt_or_stop_business(self):
        self.ingest(self.parsed())
        with patch('jx3_bot.observability.log.info', side_effect=OSError('synthetic output failure')):
            self.assertTrue(await self.app.process_one())
        self.weather.handle.assert_awaited_once()
        self.assertEqual(len(self.history()), 1)
        self.assertGreater(self.observer.failures, 0)

    async def test_expired_reply_ttl_archives_and_logs_without_business_or_post(self):
        self.ingest(self.parsed(timestamp=self.clock()-300))
        self.assertTrue(await self.app.process_one())
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.observer.steps, [('receive_log', 'm1')])
        self.weather.handle.assert_not_called(); self.qq.reply.assert_not_called()
        self.assertEqual(self.store._read('SELECT state FROM inbox')[0][0], 'expired')

    async def test_missing_platform_time_is_null_and_never_guesses_reply_ttl(self):
        frame = event(self.clock); frame['d'].pop('timestamp')
        item = normalize_message('test-app', frame, self.clock()).message
        self.assertIsNone(item.timestamp)
        self.ingest(item)
        self.assertTrue(await self.app.process_one())
        self.assertIsNone(self.history()[0]['message_at'])
        self.weather.handle.assert_not_called(); self.qq.reply.assert_not_called()

    async def test_restart_retires_unexecuted_jobs_but_preserves_archive_checkpoint_and_new_fifo(self):
        self.ingest(self.parsed(mid='pending'))
        self.ingest(self.parsed(mid='working', user='another'))
        self.assertEqual(self.store.claim_command(self.clock()).message_id, 'pending')
        self.ingest(self.parsed('普通旧聊天', mid='unmatched', user='third'))
        # This unmatched generic event would otherwise upgrade into a command
        # if the QQ AT alias arrived after restarting.
        working = self.store.claim_command(self.clock())
        self.assertEqual(working.message_id, 'working')
        self.store.finish_command(working)
        unmatched = self.store.claim_command(self.clock())
        self.store.finish_command(unmatched, 'unmatched')
        before = self.history()
        checkpoint = self.store.meta('gateway')
        self.store.close(); self.store = Store(self.path, self.observer)
        self.observer.store = self.store; self.app.store = self.store
        enable_chat(self.store)
        result = self.store.recover()
        self.assertEqual(result, {'recovered_commands': 0, 'retired_commands': 2, 'uncertain_replies': 0})
        self.assertEqual(self.history(), before)
        self.assertEqual(self.store.meta('gateway'), checkpoint)
        self.assertEqual([(r['state'], r['payload']) for r in self.store._read('SELECT state,payload FROM inbox ORDER BY rowid')],
                         [('expired', None), ('done', None), ('expired', None)])
        self.assertFalse(await self.app.process_one())
        self.assertEqual(self.ingest(replace(self.parsed('普通旧聊天', mid='unmatched', user='third'),
                                             event_type='GROUP_AT_MESSAGE_CREATE')), 'duplicate_message')
        self.assertFalse(await self.app.process_one())
        self.assertEqual(self.history(), before)
        self.assertEqual(self.observer.steps, [])
        self.weather.handle.assert_not_called()
        self.assertEqual(self.ingest(self.parsed(mid='fresh')), 'accepted')
        self.assertTrue(await self.app.process_one())
        self.weather.handle.assert_awaited_once()
        self.assertEqual(self.observer.steps, [('receive_log', 'fresh')])

    async def test_restart_keeps_planned_outbox_and_uncertain_send_without_replaying_business(self):
        self.ingest(self.parsed(mid='sending'))
        self.ingest(self.parsed(mid='planned'))
        await self.app.process_one(); await self.app.process_one()
        self.ingest(self.parsed(mid='unplanned'))
        before = self.history()
        checkpoint = self.store.meta('gateway')
        sending = self.store.claim_reply(self.clock())
        self.assertEqual(sending['msg'], 'sending')
        self.store.close(); self.store = Store(self.path, self.observer)
        self.observer.store = self.store; self.app.store = self.store
        enable_chat(self.store)
        self.assertEqual(self.store.recover(),
                         {'recovered_commands': 0, 'retired_commands': 1, 'uncertain_replies': 1})
        self.assertEqual(self.history(), before)
        self.assertEqual(self.store.meta('gateway'), checkpoint)
        self.assertEqual([(r['msg'], r['state'], r['seq'], r['attempts'])
                          for r in self.store._read('SELECT msg,state,seq,attempts FROM outbox ORDER BY rowid')],
                         [('sending', 'uncertain', 1, 1), ('planned', 'pending', 1, 0)])
        self.assertFalse(await self.app.process_one())
        self.assertTrue(await self.app.send_one())
        self.assertFalse(await self.app.send_one())
        self.assertEqual([r['msg'] for r in self.store._read("SELECT msg FROM outbox WHERE state='sent'")],
                         ['planned'])
        self.assertEqual([r['msg'] for r in self.store._read("SELECT msg FROM outbox WHERE state='uncertain'")],
                         ['sending'])
        self.assertEqual(len(self.outgoing()), 1)
        self.assertEqual(self.weather.handle.await_count, 2)
        self.qq.reply.assert_awaited_once()
        self.assertEqual(self.store._read('PRAGMA foreign_key_check'), [])

    async def test_normalized_fifo_keeps_other_user_and_scene_concurrent(self):
        for item in (self.parsed(mid='first'), self.parsed(mid='second'),
                     self.parsed(mid='other', user='other'), self.parsed(mid='private', scene='c2c')):
            self.ingest(item)
        first = self.store.claim_command(self.clock())
        self.assertEqual(first.message_id, 'first')
        self.assertEqual(self.store.claim_command(self.clock()).message_id, 'other')
        self.assertEqual(self.store.claim_command(self.clock()).message_id, 'private')
        self.assertIsNone(self.store.claim_command(self.clock()))
        self.store.finish_command(first)
        self.assertEqual(self.store.claim_command(self.clock()).message_id, 'second')

    async def test_full_then_at_before_processing_upgrades_context_not_history_or_business(self):
        registry = MatcherRegistry()
        handler = AsyncMock(return_value=Reply('test', '合成定向回复'))
        registry.on_fullmatch('test.directed', '定向', rule=to_me).handle(handler)
        self.app.registry = registry
        item = self.parsed('定向')
        self.ingest(item)
        self.ingest(replace(item, event_type='GROUP_AT_MESSAGE_CREATE'))
        self.assertTrue(await self.app.process_one()); self.assertFalse(await self.app.process_one())
        self.ingest(item); self.ingest(replace(item, event_type='GROUP_AT_MESSAGE_CREATE'))
        self.assertFalse(await self.app.process_one())
        handler.assert_awaited_once()
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.history()[0]['event_type'], 'GROUP_MESSAGE_CREATE')

    async def test_later_at_can_match_previously_unmatched_once_without_at_column(self):
        registry = MatcherRegistry()
        handler = AsyncMock(return_value=Reply('test', '合成定向回复'))
        registry.on_fullmatch('test.directed', '定向', rule=to_me).handle(handler)
        self.app.registry = registry
        item = self.parsed('定向')
        self.ingest(item); self.assertTrue(await self.app.process_one())
        handler.assert_not_called()
        self.assertEqual(self.ingest(replace(item, event_type='GROUP_AT_MESSAGE_CREATE')), 'accepted')
        self.assertTrue(await self.app.process_one())
        self.assertEqual(self.ingest(item), 'duplicate_message')
        self.assertFalse(await self.app.process_one())
        handler.assert_awaited_once()
        self.assertEqual(len(self.history()), 1)

    async def test_at_alias_arrives_while_generic_claim_is_working(self):
        registry = MatcherRegistry()
        handler = AsyncMock(return_value=Reply('test', '定向响应'))
        registry.on_fullmatch('test.directed', '定向', rule=to_me).handle(handler)
        self.app.registry = registry
        item = self.parsed('定向')
        self.ingest(item)
        working = self.store.claim_command(self.clock())
        self.assertEqual(self.ingest(replace(item, event_type='GROUP_AT_MESSAGE_CREATE')), 'accepted')
        from jx3_bot.integrations.qq.events import match_message
        self.assertEqual(match_message(working, self.clock(), registry).reason, 'command_not_matched')
        self.store.finish_command(working, 'unmatched')
        self.assertEqual(self.store._read('SELECT state FROM inbox')[0][0], 'pending')
        self.assertTrue(await self.app.process_one())
        self.assertFalse(await self.app.process_one())
        handler.assert_awaited_once()
        self.assertEqual(len(self.history()), 1)

    async def test_duplicate_after_short_inbox_cleanup_never_requeues_business(self):
        item = self.parsed('普通聊天')
        self.ingest(item); await self.app.process_one()
        self.clock.now += 86401; self.store.cleanup(self.clock())
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM inbox')[0][0], 0)
        self.assertEqual(self.ingest(item), 'duplicate_message')
        self.assertFalse(await self.app.process_one())
        self.assertEqual(len(self.history()), 1)

    async def test_old_late_body_is_archived_and_logged(self):
        item = self.parsed('OLD_BODY', timestamp=self.clock()-1000*86400, sender_name='OLD_NAME')
        self.ingest(item)
        self.assertEqual(self.history()[0]['content'], 'OLD_BODY')
        self.assertTrue(await self.app.process_one())
        self.assertEqual([step[0] for step in self.observer.steps], ['receive_log'])
        self.store.cleanup(self.clock()+1000*86400)
        self.assertEqual(self.history()[0]['content'], 'OLD_BODY')

    async def test_forget_immediately_clears_unmatched_projection_not_only_on_claim(self):
        self.ingest(self.parsed('SYNTHETIC_BODY', sender_name='SYNTHETIC_NAME'))
        self.assertTrue(await self.app.process_one())
        self.assertEqual(self.store._read('SELECT state FROM inbox')[0][0], 'unmatched')
        self.store.chat.forget('group', 'g', self.clock())
        self.assertNotIn('SYNTHETIC_BODY', '\\n'.join(self.store.db.iterdump()))
        self.assertNotIn('SYNTHETIC_NAME', '\\n'.join(self.store.db.iterdump()))
        self.assertFalse(await self.app.process_one())

    async def test_cleanup_keeps_archived_projection(self):
        from jx3_bot.modules.chat_history import ChatSettings
        self.store.chat.configure(ChatSettings())
        self.ingest(self.parsed('NEAR_EXPIRY_BODY', timestamp=self.clock()-86400+10))
        self.clock.now += 11
        self.store.cleanup(self.clock())
        self.assertIn('NEAR_EXPIRY_BODY', '\\n'.join(self.store.db.iterdump()))
        self.assertEqual(self.history()[0]['content'], 'NEAR_EXPIRY_BODY')

    async def test_forget_immediately_drops_promoted_command_copy(self):
        self.ingest(self.parsed('天气 北京'))
        received = self.store.claim_command(self.clock())
        from jx3_bot.integrations.qq.events import match_message
        self.store.promote_command(match_message(received, self.clock()).message)
        self.store.chat.forget('group', 'g', self.clock())
        self.assertNotIn('天气 北京', '\\n'.join(self.store.db.iterdump()))
        self.assertIsNone(self.store._read('SELECT payload FROM inbox')[0][0])

    async def test_forget_pending_projection_prevents_post_restart_body_and_business_revival(self):
        self.ingest(self.parsed('天气 北京'))
        self.store.chat.forget('group', 'g', self.clock())
        self.store.recover()
        self.assertFalse(await self.app.process_one())
        self.assertEqual(self.observer.steps, [])
        self.weather.handle.assert_not_called()
        self.assertIsNone(self.store._read('SELECT payload FROM inbox')[0][0])

    async def test_forget_promoted_working_command_does_not_restore_its_handler(self):
        self.ingest(self.parsed())
        received = self.store.claim_command(self.clock())
        from jx3_bot.integrations.qq.events import match_message
        matched = match_message(received, self.clock()).message
        self.store.promote_command(matched)
        self.store.chat.forget('group', 'g', self.clock())
        # A worker already holding the promoted task must not create a reply.
        with self.assertRaises(StorageError):
            self.store.plan_reply(matched, '不应发送')
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM outbox')[0][0], 0)
        self.store.recover()
        self.assertFalse(await self.app.process_one())
        self.weather.handle.assert_not_called()

    async def test_forget_retires_planned_unsent_copy_before_any_post(self):
        self.ingest(self.parsed())
        await self.app.process_one()
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0], 'pending')
        self.store.chat.forget('group', 'g', self.clock())
        row = self.store._read('SELECT state,content,payload FROM outbox')[0]
        self.assertEqual((row['state'], row['content'], row['payload']), ('expired', '', None))
        self.assertFalse(await self.app.send_one())
        self.assertEqual(self.outgoing(), [])

    async def test_forget_scrubs_already_sent_short_transport_copy(self):
        self.ingest(self.parsed())
        await self.app.process_one()
        await self.app.send_one()
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0], 'sent')
        self.store.chat.forget('group', 'g', self.clock())
        row = self.store._read('SELECT state,content,payload FROM outbox')[0]
        self.assertEqual((row['state'], row['content'], row['payload']), ('sent', '', None))
        self.assertEqual(self.outgoing()[0]['content'], '')

    async def test_forget_during_inflight_ack_never_marks_sent_without_history(self):
        self.ingest(self.parsed())
        await self.app.process_one()
        claimed = self.store.claim_reply(self.clock())
        self.store.chat.forget('group', 'g', self.clock())
        row = self.store._read('SELECT state,content,payload FROM outbox')[0]
        self.assertEqual((row['state'], row['content'], row['payload']), ('sending', '', None))
        with self.assertRaises(StorageError):
            self.store.confirm_reply(claimed, {'id': 'ACK-FORGOTTEN'}, self.clock())
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0], 'sending')
        self.assertEqual(self.outgoing(), [])
        self.store.recover()
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0], 'uncertain')

    async def test_post_success_then_history_and_sent_commit_then_send_log(self):
        self.ingest(self.parsed()); await self.app.process_one()
        self.assertEqual(self.outgoing(), [])
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0], 'pending')
        self.assertTrue(await self.app.send_one())
        self.assertEqual(self.observer.steps[-2:], [('http_ack', 'out-group-m1'), ('send_log', 'out-group-m1')])
        row = self.outgoing()[0]
        self.assertEqual(row['qq_message_id'], 'out-group-m1')
        self.assertEqual(row['content'], self.weather.handle.return_value)
        self.assertEqual(row['reply_to_id'], self.history()[0]['id'])
        self.assertIsNone(row['sender_user_id'])
        self.assertNotIn('NEVER_COPY', '\n'.join(self.store.db.iterdump()))
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0], 'sent')

    async def test_c2c_success_uses_internal_user_id_for_both_directions(self):
        self.ingest(self.parsed(scene='c2c')); await self.app.process_one(); await self.app.send_one()
        rows = self.history()
        self.assertEqual([r['direction'] for r in rows], ['in', 'out'])
        self.assertEqual(rows[0]['user_id'], rows[1]['user_id'])
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM chat_users')[0][0], 1)
        self.assertEqual(self.store._read('SELECT user_openid FROM chat_users')[0][0], 'u')

    async def test_rejected_ambiguous_or_preflight_expired_send_never_creates_history(self):
        for index, error in enumerate((ProviderError('synthetic', 403), ProviderError('synthetic', 503), ReplyExpired()), 1):
            with self.subTest(error=type(error).__name__):
                self.ingest(self.parsed(mid=f'error-{index}')); await self.app.process_one()
                self.qq.reply.side_effect = error
                self.assertTrue(await self.app.send_one())
        self.assertEqual(self.outgoing(), [])
        self.assertFalse(any(step[0] == 'send_log' for step in self.observer.steps))
        self.assertEqual([r[0] for r in self.store._read('SELECT state FROM outbox ORDER BY rowid')], ['failed', 'uncertain', 'expired'])

    async def test_missing_success_id_is_uncertain_and_never_auto_resent(self):
        self.ingest(self.parsed()); await self.app.process_one()
        self.qq.reply.side_effect = None; self.qq.reply.return_value = {}
        await self.app.send_one(); self.store.recover()
        self.assertFalse(await self.app.send_one())
        self.qq.reply.assert_awaited_once()
        self.assertEqual(self.outgoing(), [])
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0], 'uncertain')

    async def test_ack_then_local_commit_failure_rolls_back_and_never_resends(self):
        self.ingest(self.parsed()); await self.app.process_one()
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fail_sent BEFORE UPDATE OF state ON outbox WHEN NEW.state='sent' BEGIN SELECT RAISE(ABORT,'synthetic sent failure'); END")
        with self.assertRaises(StorageError): await self.app.send_one()
        self.assertEqual(self.outgoing(), [])
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0], 'sending')
        self.assertFalse(any(step[0] == 'send_log' for step in self.observer.steps))
        self.assertFalse(await self.app.send_one())
        self.assertEqual(self.store.recover()['uncertain_replies'], 1)
        self.assertFalse(await self.app.send_one())
        self.qq.reply.assert_awaited_once()

    async def test_only_local_archive_retry_with_retained_ack_is_idempotent(self):
        self.ingest(self.parsed()); await self.app.process_one()
        task = self.store.claim_reply(self.clock())
        ack = {'id': 'local-confirm-id', 'timestamp': self.clock()}
        original = self.store.chat.record_sent
        def insert_then_fail(db, reply, response, now):
            original(db, reply, response, now)
            raise sqlite3.OperationalError('synthetic archive write failure')
        with patch.object(self.store.chat, 'record_sent', side_effect=insert_then_fail):
            with self.assertRaises(StorageError): self.store.confirm_reply(task, ack, self.clock())
        self.assertEqual(self.outgoing(), [])
        self.assertIsNotNone(self.store.confirm_reply(task, ack, self.clock()))
        self.assertIsNone(self.store.confirm_reply(task, ack, self.clock()))
        self.assertEqual(len(self.outgoing()), 1)
        self.qq.reply.assert_not_called()

    async def test_future_ack_timestamp_keeps_permanent_archive(self):
        from jx3_bot.modules.chat_history import ChatSettings
        self.store.chat.configure(ChatSettings())
        self.ingest(self.parsed()); await self.app.process_one()
        future = self.clock() + 3650*86400
        self.qq.reply.side_effect = None
        self.qq.reply.return_value = {'id': 'synthetic-future', 'timestamp': future}
        await self.app.send_one()
        self.assertEqual(self.outgoing()[0]['message_at'], future)
        self.clock.now += 86401
        self.store.cleanup(self.clock())
        content = self.outgoing()[0]['content']
        self.assertTrue(content)
        self.clock.now += 1000*86400
        self.store.cleanup(self.clock())
        self.assertEqual(self.outgoing()[0]['content'], content)

    async def test_two_different_triggers_cannot_share_a_successful_qq_id(self):
        self.ingest(self.parsed(mid='first')); self.ingest(self.parsed(mid='second'))
        await self.app.process_one(); await self.app.process_one()
        self.qq.reply.side_effect = None
        self.qq.reply.return_value = {'id': 'synthetic-shared', 'timestamp': self.clock()}
        await self.app.send_one()
        with self.assertRaises(StorageError): await self.app.send_one()
        self.assertEqual([r[0] for r in self.store._read('SELECT state FROM outbox ORDER BY rowid')],
                         ['sent', 'sending'])
        self.assertEqual(len(self.outgoing()), 1)
        self.assertEqual(self.store.recover()['uncertain_replies'], 1)
        self.assertFalse(await self.app.send_one())
        self.assertEqual(self.qq.reply.await_count, 2)

    async def test_known_self_echo_before_ack_and_after_ack_never_fabricates_inbound_user(self):
        self.ingest(self.parsed()); await self.app.process_one()
        async def echo_before_ack(*args, **kwargs):
            echo = self.parsed('自己回投', mid='out-group-m1', user='synthetic-bot',
                               bot_author=True, self_author=True, is_bot=True)
            self.assertEqual(self.ingest(echo), 'ignored')
            self.assertEqual(self.outgoing(), [])
            return {'id': 'out-group-m1', 'timestamp': self.clock()}
        self.qq.reply.side_effect = echo_before_ack
        await self.app.send_one()
        echo = self.parsed('自己回投', mid='out-group-m1', user='synthetic-bot',
                           bot_author=True, self_author=True, is_bot=True)
        self.assertEqual(self.ingest(echo), 'duplicate_message')
        self.assertFalse(await self.app.process_one())
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM chat_users')[0][0], 1)
        self.weather.handle.assert_awaited_once()

    async def test_external_bot_and_non_text_archive_log_but_do_not_match(self):
        self.ingest(self.parsed(bot_author=True, is_bot=True))
        self.ingest(self.parsed('[图片×1]', mid='photo', content_format='non_text'))
        await self.app.process_one(); await self.app.process_one()
        self.weather.handle.assert_not_called()
        self.assertEqual(len(self.history()), 2)
        self.assertEqual([step[0] for step in self.observer.steps], ['receive_log', 'receive_log'])

    async def test_sensitive_projection_masks_before_persistence_without_console_newline_escapes(self):
        self.observer.add_secret('SYNTHETIC_KNOWN_SECRET')
        frame = event(self.clock, content='第一行\nSYNTHETIC_KNOWN_SECRET token=HIDDEN_VALUE https://media.invalid/credential')
        frame['d']['message_scene'] = {'ext': ['NEVER_RAW_EXT']}
        frame['d']['attachments'] = [{'content_type': 'image/jpeg', 'url': 'NEVER_ATTACHMENT_URL'}]
        frame['d']['author']['username'] = '昵称 SYNTHETIC_KNOWN_SECRET'
        self.ingest(normalize_message('test-app', frame, self.clock()).message)
        dump = '\n'.join(self.store.db.iterdump())
        for value in ('SYNTHETIC_KNOWN_SECRET', 'HIDDEN_VALUE', 'media.invalid', 'NEVER_RAW_EXT', 'NEVER_ATTACHMENT_URL'):
            self.assertNotIn(value, dump)
        self.assertIn('第一行\n', self.history()[0]['content'])
        self.assertNotIn('\\n', self.history()[0]['content'])
        await self.app.process_one()

    async def test_chat_history_survives_one_day_transport_cleanup(self):
        self.ingest(self.parsed()); await self.app.process_one(); await self.app.send_one()
        self.clock.now += 86401; self.store.cleanup(self.clock())
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM inbox')[0][0], 0)
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM outbox')[0][0], 0)
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(self.store._read('PRAGMA foreign_key_check'), [])
