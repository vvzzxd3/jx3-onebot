"""Synthetic-only logging regression coverage; never read operator credentials."""
import asyncio
import base64
import hashlib
import hmac
from dataclasses import replace
import io
import json
import logging
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from jx3_bot.application import Application
from jx3_bot.errors import ConfigurationError, GatewayFatal, ProviderError, StorageError
from jx3_bot.gateway import Gateway, Reconnect
from jx3_bot.integrations.qq.models import ReceivedMessage
from jx3_bot.models import inspect_group_message
from jx3_bot.observability import ConsoleFormatter, ConsoleQueue, ConsoleRuntime, LogSettings, Observer
from jx3_bot.qq_http import QQHTTP, QQTokens
from jx3_bot.store import Store
from support import Clock, event, message, enable_chat
from test_gateway import FakeSocket, hello, ready


class FormattingTests(unittest.TestCase):
    def setUp(self):
        self.observer = Observer()

    def output(self, frame, observer=None):
        with self.assertLogs('jx3_bot.observability', level='INFO') as captured:
            (observer or self.observer).chat(frame)
        return captured.records[0].getMessage()

    def test_log_defaults_and_validation(self):
        self.assertEqual(LogSettings.from_env({}), LogSettings())
        chosen = LogSettings.from_env({'BOT_LOG_LEVEL':'debug', 'BOT_QQ_RECEIVE_LOG':'payload',
                                      'BOT_QQ_CHAT_LOG':'0', 'BOT_QQ_LOG_MESSAGE_TEXT':'false',
                                      'BOT_LOG_MAX_PAYLOAD_CHARS':'128'})
        self.assertEqual(chosen, LogSettings('DEBUG', 'payload', False, False))
        self.assertFalse(hasattr(chosen, 'max_chars'))
        for key, value in [('BOT_LOG_LEVEL','TRACE'), ('BOT_QQ_RECEIVE_LOG','raw'),
                           ('BOT_QQ_CHAT_LOG','maybe'), ('BOT_QQ_LOG_MESSAGE_TEXT','yes')]:
            with self.subTest(key=key, value=value), self.assertRaises(ConfigurationError):
                LogSettings.from_env({key:value})

    def test_all_modes_ignore_all_legacy_limit_values(self):
        for mode in ('off', 'summary', 'payload'):
            for legacy in ('', 'x', '-1', '0', '127', '128', '16385', '32768', '999999999999999999999'):
                with self.subTest(mode=mode, legacy=legacy):
                    settings = LogSettings.from_env({'BOT_QQ_RECEIVE_LOG':mode,
                                                     'BOT_LOG_MAX_PAYLOAD_CHARS':legacy})
                    self.assertEqual(settings, LogSettings(receive=mode))

    def test_all_modes_preserve_large_events_and_third_party_console_records(self):
        value = '中'*60000 + '尾哨兵 FIXED_SYNTHETIC Bearer TOKEN_SYNTHETIC https://x.invalid/URL_SYNTHETIC'
        self.assertGreater(len(value), 32768)
        for mode in ('off', 'summary', 'payload'):
            with self.subTest(mode=mode):
                observer = Observer(LogSettings.from_env({'BOT_QQ_RECEIVE_LOG':mode,
                                                          'BOT_LOG_MAX_PAYLOAD_CHARS':'invalid'}),
                                    known_secrets=('FIXED_SYNTHETIC',))
                observer.add_secret('TOKEN_SYNTHETIC')
                stream = io.StringIO()
                runtime = ConsoleRuntime(observer, stream)
                try:
                    observer.event('synthetic.large', text=value)
                    # Third-party records can reach the same console handler.
                    record = logging.LogRecord('thirdparty.synthetic', logging.WARNING, '', 1,
                                               '第三方 %s\n尾行\t\x1b', (value,), None)
                    runtime.handler.handle(record)
                finally:
                    self.assertTrue(runtime.close())
                lines = stream.getvalue().splitlines()
                self.assertEqual(len(lines), 2)
                self.assertEqual(json.loads(lines[0].split(' text=', 1)[1]), value)
                self.assertTrue(lines[1].endswith('第三方 ' + value + '\\n尾行\\t\\u001b'))
                self.assertEqual(observer.failures, 0)

    def test_all_modes_preserve_long_chat_body_nickname_and_identities(self):
        body = '文'*60000 + '正文尾哨兵 auth_token=BODY_SECRET https://x.invalid/BODY_URL'
        nickname = '名'*60000 + '昵称尾哨兵 NICK_SECRET'
        user = '人'*60000 + '身份尾哨兵 USER_SECRET'
        group = '群'*60000 + '群尾哨兵 GROUP_SECRET'
        for mode in ('off', 'summary', 'payload'):
            for event_type in ('GROUP_MESSAGE_CREATE', 'C2C_MESSAGE_CREATE'):
                with self.subTest(mode=mode, event_type=event_type):
                    observer = Observer(LogSettings(receive=mode),
                                        known_secrets=('BODY_SECRET', 'NICK_SECRET', 'USER_SECRET', 'GROUP_SECRET'))
                    frame = event(Clock(), content=body, event_type=event_type)
                    frame['d']['group_openid'] = group
                    frame['d']['author'] = {'username':nickname, 'member_openid':user, 'user_openid':user}
                    shown = self.output(frame, observer)
                    expected = ('[receive] [单聊] - ' if event_type == 'C2C_MESSAGE_CREATE' else
                                '[receive] [群聊] 未知群（' + group + '） - ')
                    self.assertEqual(shown, expected + nickname + '（' + user + '）：' + body)
                    normalized = ReceivedMessage('app', 'c2c' if event_type == 'C2C_MESSAGE_CREATE' else 'group',
                                                 user if event_type == 'C2C_MESSAGE_CREATE' else group,
                                                 user, 'm', Clock()(), event_type, body, sender_name=nickname)
                    with self.assertLogs('jx3_bot.observability', level='INFO') as captured:
                        observer.chat_message(normalized)
                        observer.chat_sent({'scene':normalized.scene, 'app_id':user,
                                            'conversation_id':normalized.conversation_id, 'content':body})
                    self.assertEqual(captured.records[0].getMessage(), shown)
                    scene = '单聊' if normalized.scene == 'c2c' else '群聊'
                    self.assertEqual(captured.records[1].getMessage(),
                                     '[send] [' + scene + '] 本应用（' + user + '） → ' + normalized.conversation_id + '：' + body)
                    self.assertEqual(observer.failures, 0)

    def test_unknown_event_types_and_codes_preserve_original_strings(self):
        event_type = 'UNKNOWN.auth_token=EVENT_SECRET https://x.invalid/EVENT_URL ' + '事'*60000 + '尾哨兵'
        code = 'auth_token=CODE_SECRET https://x.invalid/CODE_URL ' + '码'*60000 + '尾哨兵'
        self.assertEqual(self.observer.code(code), code)
        for value in (None, 0, True, ['synthetic']):
            self.assertEqual(self.observer.code(value), str(value))
        self.assertEqual(self.observer.event_type(event_type), event_type)
        self.assertEqual(self.observer.event_type(None), 'unknown')
        for mode in ('summary', 'payload'):
            with self.subTest(mode=mode):
                observer = Observer(LogSettings(receive=mode))
                frame = {'op':code, 't':event_type, 's':code, 'd':{'unprojected':'DO_NOT_ADD_TO_SUMMARY'}}
                with self.assertLogs('jx3_bot.observability', level='INFO') as captured:
                    observer.ws(json.dumps(frame), frame, 'app', 'c1', 1)
                summary = next(record.getMessage() for record in captured.records if record.getMessage().startswith('event=qq.ws.receive '))
                self.assertIn('event_type=' + json.dumps(event_type, ensure_ascii=False), summary)
                self.assertIn('op=' + json.dumps(code, ensure_ascii=False), summary)
                self.assertIn('seq=' + json.dumps(code, ensure_ascii=False), summary)
                self.assertNotIn('DO_NOT_ADD_TO_SUMMARY', summary)

    def test_http_selected_fields_are_complete_without_adding_response_body(self):
        code = '码'*60000 + '尾哨兵 Bearer CODE_TOKEN https://x.invalid/CODE_URL'
        body = {'code':code, 'expires_in':code, 'access_token':'UNPROJECTED_TOKEN',
                'message':'UNPROJECTED_MESSAGE', 'url':'https://x.invalid/UNPROJECTED_URL'}
        response = httpx.Response(200, json=body)
        for mode in ('summary', 'payload'):
            with self.subTest(mode=mode):
                observer = Observer(LogSettings(receive=mode), known_secrets=('CODE_TOKEN',))
                with self.assertLogs('jx3_bot.observability', level='INFO') as captured:
                    observer.http_response('token', response, body, time.monotonic())
                shown = captured.records[0].getMessage()
                self.assertIn('code=' + json.dumps(code, ensure_ascii=False), shown)
                self.assertIn('expires_in=' + json.dumps(code, ensure_ascii=False), shown)
                for value in ('UNPROJECTED_TOKEN', 'UNPROJECTED_MESSAGE', 'UNPROJECTED_URL'):
                    self.assertNotIn(value, shown)
                self.assertEqual(observer.failures, 0)
        with patch('jx3_bot.observability.log.log') as emit:
            Observer(LogSettings(receive='off')).http_response('token', response, body, time.monotonic())
        emit.assert_not_called()

    def test_group_format_and_missing_group_name(self):
        frame = event(Clock())
        frame['d']['author']['username'] = '小明'
        frame['d']['group_name'] = 'UNVERIFIED_DO_NOT_INFER'
        self.assertEqual(self.output(frame), '[receive] [群聊] 未知群（g） - 小明（u）：天气 北京')

    def test_single_chat_does_not_repeat_peer(self):
        frame = {'op':0, 't':'C2C_MESSAGE_CREATE', 'd':{'author':{'user_openid':'peer', 'username':'小红'}, 'content':'你好'}}
        text = self.output(frame)
        self.assertEqual(text, '[receive] [单聊] - 小红（peer）：你好')
        self.assertEqual(text.count('peer'), 1)

    def test_missing_nickname_and_fallback_identity_source(self):
        frame = event(Clock())
        frame['d']['author'] = {'id':'fallback-id', 'username':''}
        self.assertIn('未知用户（fallback-id）[author.id]', self.output(frame))
        frame['d']['author'] = None
        self.assertIn('未知用户（缺失）', self.output(frame))

    def test_whitespace_controls_and_multiline_remain_one_line(self):
        frame = event(Clock(), content='  第一行\n第二行\r\x1b[31m\b\u2028\u202e  ')
        frame['d']['author']['username'] = '昵称\nINFO 假日志'
        text = self.output(frame)
        self.assertIn('：  第一行\\n第二行\\r\\u001b', text)
        self.assertIn('昵称\\nINFO 假日志', text)
        self.assertTrue(text.endswith('  '))
        self.assertEqual(len(text.splitlines()), 1)
        self.assertNotIn('\x1b', text)

    def test_persistence_redact_remains_independent_of_complete_log_values(self):
        observer = Observer(known_secrets=('FIXED_SECRET_ABC',))
        observer.add_secret('CURRENT_TOKEN_ABC')
        raw = ('中'*60000 + 'FIXED_SECRET_ABC CURRENT_TOKEN_ABC auth_token=OPAQUE_VALUE '
               'Bearer BEARER_VALUE eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ4In0.SIGNATURE '
               'https://example.invalid/path/URL_SECRET?auth=QUERY_SECRET\n尾哨兵')
        self.assertEqual(observer.safe(raw), raw.replace('\n', '\\n'))
        persisted = observer.redact(raw)
        for secret in ('FIXED_SECRET', 'CURRENT_TOKEN', 'OPAQUE_VALUE', 'BEARER_VALUE', 'eyJ', 'URL_SECRET', 'QUERY_SECRET'):
            self.assertNotIn(secret, persisted)
        self.assertTrue(persisted.endswith('\n尾哨兵'))
        self.assertIn('中'*60000, persisted)
        for i in range(40): observer.add_secret(f'rotated-{i}')
        self.assertEqual(observer.redact('FIXED_SECRET_ABC'), '[REDACTED]')
        self.assertEqual(observer.safe('FIXED_SECRET_ABC'), 'FIXED_SECRET_ABC')

    def test_private_key_and_quoted_auth_redact_only_for_persistence(self):
        raw = ('"Authorization": "Bearer TOKEN_VALUE" auth_token="WITH SPACE" '
               '-----BEGIN PRIVATE KEY-----\nKEY_MATERIAL\n-----END PRIVATE KEY-----')
        self.assertEqual(self.observer.safe(raw), raw.replace('\n', '\\n'))
        text = self.observer.redact(raw)
        for value in ('TOKEN_VALUE', 'WITH SPACE', 'KEY_MATERIAL'): self.assertNotIn(value, text)

    def test_text_disabled_does_not_hide_identity(self):
        observer = Observer(LogSettings(message_text=False))
        text = self.output(event(Clock(), content='DO_NOT_SHOW'), observer)
        self.assertIn('（g）', text)
        self.assertIn('（u）', text)
        self.assertIn('[正文隐藏]', text)
        self.assertNotIn('DO_NOT_SHOW', text)

    def test_chat_disabled_leaves_protocol_summary(self):
        observer = Observer(LogSettings(chat=False))
        frame = event(Clock())
        with self.assertLogs('jx3_bot.observability', level='INFO') as captured:
            observer.ws(json.dumps(frame), frame, 'app', 'c1', 1)
        text = '\n'.join(captured.output)
        self.assertIn('qq.ws.receive', text)
        self.assertNotIn('[receive]', text)

    def test_summary_off_still_prints_chat(self):
        observer = Observer(LogSettings(receive='off'))
        frame = event(Clock())
        with self.assertLogs('jx3_bot.observability', level='INFO') as captured:
            observer.ws(json.dumps(frame), frame, 'app', 'c1', 1)
        self.assertIn('[receive]', captured.output[0])
        self.assertNotIn('qq.ws.receive', '\n'.join(captured.output))

    def test_payload_preserves_received_wire_frame_before_projection(self):
        observer = Observer(LogSettings(receive='payload'))
        raw = '{ "id":"envelope-id", "op":0,"s":3,"t":"GROUP_MESSAGE_CREATE","d":' \
              '{"id":"message-id","content":"first\\nsecond","message_scene":{"ext":["auth_token=EXAMPLE"]}}}'
        frame = json.loads(raw)
        stream = io.StringIO()
        runtime = ConsoleRuntime(observer, stream)
        try:
            observer.ws(raw, frame, 'app', 'c1', 1, chat=False)
        finally:
            self.assertTrue(runtime.close())
        line = next(line for line in stream.getvalue().splitlines() if 'event=qq.ws.payload ' in line)
        self.assertEqual(json.loads(line.split(' raw=', 1)[1]), raw)
        self.assertIn('event=qq.ws.receive', stream.getvalue())
        self.assertIn('auth_token=EXAMPLE', line)  # Explicit opt-in raw includes sensitive fields.
        self.assertEqual(len(line.splitlines()), 1)

    def test_large_payload_round_trips_and_other_modes_keep_projection(self):
        raw = '{"op":0,"d":{"content":"' + '中' * 60000 + '尾哨兵 auth_token=PAYLOAD_SECRET https://x.invalid/URL_SECRET"}}'
        self.assertGreater(len(raw), 32768)
        frame = json.loads(raw)
        observer = Observer(LogSettings(receive='payload', chat=False))
        stream = io.StringIO()
        runtime = ConsoleRuntime(observer, stream)
        try:
            observer.ws(raw, frame, 'app', 'c1', 1)
        finally:
            self.assertTrue(runtime.close())
        payload = next(line for line in stream.getvalue().splitlines() if 'event=qq.ws.payload ' in line)
        self.assertNotIn('truncated=true', payload)
        self.assertEqual(json.loads(payload.split(' raw=', 1)[1]), raw)
        self.assertEqual(len(stream.getvalue().splitlines()), 2)
        summary = next(line for line in stream.getvalue().splitlines() if 'event=qq.ws.receive ' in line)
        self.assertIn('content_chars=', summary)
        self.assertNotIn('PAYLOAD_SECRET', summary)  # Summary is a projection, not a raw body.
        for mode in ('summary', 'off'):
            with self.subTest(mode=mode):
                observer = Observer(LogSettings(receive=mode, chat=False))
                with self.assertLogs('jx3_bot.observability', level='INFO') if mode == 'summary' else patch('jx3_bot.observability.log.info') as captured:
                    observer.ws(raw, frame, 'app', 'c1', 1)
                if mode == 'summary':
                    self.assertNotIn('qq.ws.payload', '\n'.join(captured.output))
                else:
                    captured.assert_not_called()

    def test_attachments_are_summaries_without_urls(self):
        frame = event(Clock(), content='')
        frame['d']['attachments'] = [{'content_type':'image/jpeg', 'url':'https://x.invalid/rkey=SECRET'},
                                     {'content_type':'voice', 'voice_wav_url':'https://x.invalid/SECRET'}]
        frame['d']['message_scene'] = {'ext':['auth_token=EXT_SECRET']}
        text = self.output(frame)
        self.assertIn('[图片×1]', text); self.assertIn('[语音×1]', text)
        self.assertNotIn('SECRET', text)

    def test_quoted_elements_labeled_and_not_expanded_as_raw_payload(self):
        frame = event(Clock(), content='')
        frame['d']['msg_elements'] = [{'content':'被引用正文', 'author':{'username':'NOT_CURRENT_AUTHOR'},
                                      'unknown':'SECRET'}]
        text = self.output(frame)
        self.assertIn('[引用/转发] 被引用正文', text)
        self.assertNotIn('NOT_CURRENT_AUTHOR', text); self.assertNotIn('SECRET', text)

    def test_non_eyj_jwt_with_dotted_prefix_and_event_names(self):
        encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=')
        header = encode(b'{ "alg":"HS256", "typ":"JWT" }')
        payload = encode(b'{"sub":"synthetic"}')
        unsigned = header + b'.' + payload
        signature = encode(hmac.new(b'synthetic-test-key-32-bytes-long!!', unsigned, hashlib.sha256).digest())
        token = (unsigned + b'.' + signature).decode()
        import jwt
        self.assertEqual(jwt.decode(token, b'synthetic-test-key-32-bytes-long!!', algorithms=['HS256'])['sub'], 'synthetic')
        self.assertFalse(token.startswith('eyJ'))
        for value in (token, 'prefix.' + token, 'other.prefix.' + token):
            self.assertEqual(self.observer.safe(value), value)
            self.assertNotIn(token, self.observer.redact(value))
            self.assertIn('[JWT REDACTED]', self.observer.redact(value))
        record = logging.LogRecord('jx3_bot', logging.INFO, '', 1,
                                   'event=qq.http.response event=qq.ws.receive', (), None)
        self.assertIn('event=qq.http.response event=qq.ws.receive', ConsoleFormatter(self.observer).format(record))

    def test_persistence_rotated_opaque_token_protection_is_not_evicted(self):
        for i in range(40): self.observer.add_secret(f'opaque-{i:02}-synthetic')
        self.assertEqual(self.observer.redact('opaque-00-synthetic'), '[REDACTED]')
        self.assertEqual(self.observer.safe('opaque-00-synthetic'), 'opaque-00-synthetic')

    def test_basic_custom_authorization_and_cookie_complete_values(self):
        for text in ('Authorization: Basic BASE64_SECRET', 'Authorization: CustomScheme CUSTOM_SECRET',
                     'Cookie: session=COOKIE_SECRET; secondary=COOKIE_SECOND_SECRET',
                     '"aUtHoRiZaTiOn": "Basic BASE64_SECRET"'):
            with self.subTest(text=text):
                self.assertEqual(self.observer.safe(text), text)
                result = self.observer.redact(text)
                self.assertNotIn('SECRET', result)

    def test_quote_count_and_depth_are_unlimited_and_content_coexists(self):
        frame = event(Clock(), content='当前正文')
        frame['d']['msg_elements'] = [{'content':f'引用{i}'} for i in range(1200)]
        result = self.output(frame)
        expected = '[引用/转发] ' + ' | '.join(f'引用{i}' for i in range(1200))
        self.assertIn('当前正文 ' + expected, result)
        self.assertNotIn('[引用摘要截断]', result)
        nested = [{'content':'深层 auth_token=DEEP_SECRET https://x.invalid/DEEP_URL 尾哨兵'}]
        for _ in range(1200):
            nested = [{'msg_elements':nested}]
        frame['d']['msg_elements'] = nested
        self.assertIn('[引用/转发] 深层 auth_token=DEEP_SECRET https://x.invalid/DEEP_URL 尾哨兵', self.output(frame))
        self.assertEqual(self.observer.failures, 0)

    def test_attachment_count_is_not_limited_but_urls_remain_unprojected(self):
        frame = event(Clock(), content='正文')
        frame['d']['attachments'] = [{'content_type':'image/jpeg', 'url':'https://x.invalid/UNPROJECTED'} for _ in range(1200)]
        frame['d']['attachments'].append({'content_type':'voice'})
        shown = self.output(frame)
        self.assertIn('[图片×1200] [语音×1]', shown)
        self.assertNotIn('UNPROJECTED', shown)

    def test_unusual_unicode_and_non_string_event_do_not_break_observer(self):
        self.assertTrue(self.observer.trace('app', '\ud800', 'm').startswith('m_'))
        frame = {'op':0, 's':1, 't':[], 'd':{}}
        with self.assertLogs('jx3_bot.observability', level='INFO') as captured:
            self.observer.ws(json.dumps(frame), frame, 'app', 'c1', 1)
        self.assertIn('qq.ws.receive', captured.output[0])
        self.assertEqual(self.observer.failures, 0)

    def test_flush_timeout_is_visible_on_independent_stderr(self):
        entered, release = threading.Event(), threading.Event()
        class Blocked(io.StringIO):
            def write(self, text):
                entered.set()
                if not release.wait(2): raise RuntimeError('synthetic output timeout')
                return super().write(text)
        emergency = io.StringIO()
        runtime = ConsoleRuntime(self.observer, Blocked(), emergency_stream=emergency)
        try:
            self.observer.event('test.first')
            self.assertTrue(entered.wait(1))
            self.observer.event('test.second')
            self.assertFalse(runtime.close(timeout=0.01))
            self.assertGreaterEqual(runtime.handler.unflushed, 2)
            self.assertIn('event=log.flush_timeout', emergency.getvalue())
        finally:
            release.set()
            self.assertTrue(runtime.handler.shutdown())

    def test_trace_shared_across_at_variant_but_separate_runs(self):
        frame = event(Clock())
        other = event(Clock(), event_type='GROUP_AT_MESSAGE_CREATE')
        expected = self.observer.trace('app', 'g', 'm1')
        self.assertEqual(self.observer.frame_trace('app', frame), expected)
        self.assertEqual(self.observer.frame_trace('app', other), expected)
        self.assertNotEqual(Observer().trace('app', 'g', 'm1'), expected)

    def test_formatter_timestamp_preserves_exception_and_stack_on_one_line(self):
        record = logging.LogRecord('jx3_bot', logging.INFO, '', 1, '[receive] 测试', (), None)
        record.created = 1791694801  # A synthetic fixed Unix timestamp.
        exception_text = 'SECRET_EXCEPTION\n' + '错'*60000 + '异常尾哨兵 Bearer EXCEPTION_TOKEN https://x.invalid/EXCEPTION_URL'
        stack_text = 'Stack (synthetic):\n' + '栈'*60000 + 'STACK_SECRET\t栈尾哨兵'
        try:
            raise ValueError(exception_text)
        except ValueError:
            import sys
            record.exc_info = sys.exc_info()
        record.stack_info = stack_text
        shown = ConsoleFormatter(self.observer).format(record)
        self.assertRegex(shown, r'^2026-10-11 \d\d:\d\d:\d\d INFO \[receive\] 测试\\nTraceback')
        self.assertIn('ValueError: ' + exception_text.replace('\n', '\\n'), shown)
        self.assertTrue(shown.endswith(stack_text.replace('\n', '\\n').replace('\t', '\\t')))
        self.assertEqual(len(shown.splitlines()), 1)

    def test_output_queue_flush_and_concurrent_lines(self):
        stream = io.StringIO()
        runtime = ConsoleRuntime(self.observer, stream)
        try:
            threads = [threading.Thread(target=lambda n=n: self.observer.event('test.line', number=n)) for n in range(20)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        finally:
            self.assertTrue(runtime.close())
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 20)
        self.assertTrue(all('event=test.line number=' in line for line in lines))

    def test_slow_output_queue_is_bounded_and_loss_visible(self):
        entered, release = threading.Event(), threading.Event()
        class Slow(io.StringIO):
            def write(self, text):
                entered.set()
                if not release.wait(2): raise RuntimeError('test release timeout')
                return super().write(text)
        stream = Slow()
        handler = ConsoleQueue(self.observer, stream, capacity=1)
        record = logging.LogRecord('jx3_bot', logging.INFO, '', 1, 'safe', (), None)
        try:
            handler.handle(record)
            self.assertTrue(entered.wait(1))
            handler.handle(record)
            for _ in range(10): handler.handle(record)
            self.assertEqual(handler.queue.qsize(), 1)
            self.assertGreater(handler.dropped, 0)
        finally:
            release.set()
            self.assertTrue(handler.shutdown())
            handler.close()
        self.assertIn('event=log.loss', stream.getvalue())

    def test_output_failure_recovers_without_raw_fallback(self):
        failed = threading.Event()
        class Flaky(io.StringIO):
            def __init__(self): super().__init__(); self.once=True
            def write(self, text):
                if self.once:
                    self.once=False; failed.set(); raise OSError('UNSAFE_EXCEPTION_BODY')
                return super().write(text)
        stream = Flaky(); handler = ConsoleQueue(self.observer, stream)
        record = logging.LogRecord('jx3_bot', logging.INFO, '', 1, 'safe', (), None)
        handler.handle(record)
        self.assertTrue(failed.wait(1))
        handler.handle(record)
        self.assertTrue(handler.shutdown()); handler.close()
        self.assertIn('event=log.loss', stream.getvalue())
        self.assertNotIn('UNSAFE_EXCEPTION_BODY', stream.getvalue())


class IntegrationLogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.observer = Observer(LogSettings(receive='payload'), known_secrets=('FIXED_SECRET',))
        self.store = Store(Path(self.temp.name)/'db', observer=self.observer)
        self.clock = Clock()

    async def asyncTearDown(self):
        self.store.close(); self.temp.cleanup()

    async def test_all_protocol_frames_but_chat_only_after_committed_acceptance(self):
        enable_chat(self.store)
        http = Mock(); http.tokens.get = AsyncMock(return_value='SYNTHETIC_WS_TOKEN')
        group = event(self.clock, seq=2, content='普通聊天')
        group['d']['author']['username']='小明'
        private = {'op':0, 's':3, 't':'C2C_MESSAGE_CREATE', 'd':{'id':'private-m',
                   'author':{'user_openid':'private-u'}, 'timestamp':self.clock(), 'content':'单聊正文'}}
        frames = [hello(1000), ready(), group, private, {'op':11,'d':None}, {'op':222,'d':{'token':'HIDDEN'}}, {'op':9,'d':False}]
        gateway = Gateway(http, self.store, 'app', clock=self.clock, observer=self.observer)
        weather = AsyncMock()
        app = Application(self.store, weather, AsyncMock(), gateway, clock=self.clock, observer=self.observer)
        with self.assertLogs('jx3_bot.observability', level='INFO') as captured:
            with self.assertRaises(Reconnect): await gateway.connection(FakeSocket(frames))
            self.assertFalse(any('[receive]' in record.getMessage() for record in captured.records))
            self.assertEqual(self.store._read('SELECT COUNT(*) FROM inbox')[0][0], 2)
            self.assertTrue(await app.process_one()); self.assertTrue(await app.process_one())
            self.assertFalse(await app.process_one())
        lines = [record.getMessage() for record in captured.records]
        self.assertEqual(sum(line.startswith('event=qq.ws.receive ') for line in lines), len(frames))
        self.assertTrue(any('[receive] [群聊]' in line and '普通聊天' in line for line in lines))
        self.assertTrue(any('[receive] [单聊]' in line and '单聊正文' in line for line in lines))
        self.assertEqual(sum('reason="command_not_matched"' in line for line in lines), 2)
        self.assertNotIn('SYNTHETIC_WS_TOKEN', '\n'.join(lines))
        self.assertIn('HIDDEN', '\n'.join(lines))  # Opt-in wire payload is intentionally unredacted.
        weather.handle.assert_not_called()
        dump = '\n'.join(self.store.db.iterdump())
        self.assertIn('普通聊天', dump); self.assertIn('单聊正文', dump); self.assertIn('小明', dump)
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM chat_group_messages')[0][0], 1)
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM chat_user_messages')[0][0], 1)

    async def test_store_redacts_before_committed_chat_but_wire_payload_remains_original(self):
        enable_chat(self.store)
        self.store.ready('s', 0)
        body = '合成正文\nFIXED_SECRET Bearer STORE_TOKEN https://x.invalid/STORE_URL 尾哨兵'
        frame = event(self.clock, content=body)
        frame['d']['author']['username'] = '昵称 FIXED_SECRET'
        normalized = ReceivedMessage('test-app', 'group', 'g', 'u', 'm1', self.clock(),
                                     'GROUP_MESSAGE_CREATE', body, sender_name='昵称 FIXED_SECRET')
        self.assertTrue(self.store.accept('s', 1, normalized, self.clock()))
        row = self.store._read('SELECT content,sender_name FROM chat_group_messages')[0]
        self.assertEqual(row['sender_name'], '昵称 [REDACTED]')
        self.assertIn('[REDACTED]', row['content'])
        self.assertIn('[AUTH REDACTED]', row['content'])
        self.assertIn('[URL隐藏]', row['content'])
        self.assertIn('\n', row['content'])
        with self.assertLogs('jx3_bot.observability', level='INFO') as captured:
            self.observer.ws(json.dumps(frame, ensure_ascii=False), frame, 'test-app', 'c1', 1, chat=False)
            self.observer.chat_message(replace(normalized, content=row['content'], sender_name=row['sender_name']))
        payload = next(record.getMessage() for record in captured.records if record.getMessage().startswith('event=qq.ws.payload '))
        self.assertEqual(json.loads(payload.split(' raw=', 1)[1]), json.dumps(frame, ensure_ascii=False))
        chat = next(record.getMessage() for record in captured.records if record.getMessage().startswith('[receive] '))
        for value in ('FIXED_SECRET', 'STORE_TOKEN', 'STORE_URL'):
            self.assertNotIn(value, chat)  # Logging cannot restore values removed upstream.
        self.assertIn('昵称 [REDACTED]', chat)
        self.assertIn('合成正文\\n', chat)
        self.assertIn('尾哨兵', chat)

    async def test_blocked_console_does_not_block_gateway_heartbeat_ack(self):
        entered, release = threading.Event(), threading.Event()
        class Blocked(io.StringIO):
            def write(self, text):
                entered.set()
                if not release.wait(2): raise RuntimeError('synthetic output timeout')
                return super().write(text)
        runtime = ConsoleRuntime(self.observer, Blocked())
        http = Mock(); http.tokens.get = AsyncMock(return_value='SYNTHETIC_TOKEN')
        gateway = Gateway(http, self.store, 'app', clock=self.clock, observer=self.observer)
        ws = FakeSocket([hello(5), ready()], auto_ack=True)
        task = asyncio.create_task(gateway.connection(ws))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            await asyncio.sleep(0.03)
            self.assertFalse(task.done())
            self.assertGreaterEqual(sum(frame['op']==1 for frame in ws.sent), 3)
            gateway.stop.set()
            await asyncio.wait_for(task, 1)
        finally:
            release.set()
            task.cancel(); await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(runtime.close())

    async def test_malformed_json_and_non_object_observed_before_fatal(self):
        for raw in ('{INVALID SECRET', '[]', 'null', '"SECRET"', '{畸形' + '中'*60000 + 'auth_token=MALFORMED_SECRET 尾哨兵'):
            gateway = Gateway(Mock(), self.store, 'app', observer=self.observer)
            ws = FakeSocket([]); ws.frames.put_nowait(raw)
            with self.assertLogs('jx3_bot.observability', level='INFO') as captured:
                with self.assertRaises(GatewayFatal): await gateway.connection(ws)
            shown = '\n'.join(captured.output)
            self.assertIn('qq.ws.receive', shown)
            self.assertIn('qq.ws.payload', shown)  # Even malformed input shows received wire text.
            payload = next(record.getMessage() for record in captured.records if record.getMessage().startswith('event=qq.ws.payload '))
            self.assertEqual(json.loads(payload.split(' raw=', 1)[1]), raw)

    async def test_accept_duplicate_stale_and_expiry_reason(self):
        self.store.ready('s', 0)
        msg = message(self.clock)
        self.assertEqual(self.store.accept('s',1,msg,self.clock(),True), 'accepted')
        self.assertEqual(self.store.accept('s',2,msg,self.clock(),True), 'duplicate_message')
        self.assertEqual(self.store.accept('s',1,msg,self.clock(),True), 'stale_sequence')
        self.assertEqual(self.store.accept('s',3,None,self.clock(),True), 'ignored')
        self.assertFalse(self.store.accept('s',4,msg,self.clock()))
        expired = event(self.clock)
        expired['d']['timestamp'] = self.clock()-300
        self.assertEqual(inspect_group_message('app',expired,self.clock()).reason,'expired')

    async def test_token_non_json_response_is_logged_without_body(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503,text='HTML SECRET_BODY'))) as client:
            tokens=QQTokens(client,'app','FIXED_SECRET',observer=self.observer)
            with self.assertLogs('jx3_bot.observability',level='INFO') as captured:
                with self.assertRaises(ProviderError): await tokens.get()
        shown='\n'.join(captured.output)
        self.assertIn('status=503',shown); self.assertIn('parse="invalid_json"',shown)
        self.assertNotIn('SECRET_BODY',shown)

    async def test_every_401_get_attempt_logged_and_token_hidden(self):
        calls=[]
        def handler(request):
            calls.append(request.url.path)
            if request.url.path.endswith('getAppAccessToken'):
                return httpx.Response(200,json={'access_token':'OPAQUE_TOKEN_SECRET','expires_in':7200,'unknown':'SECRET_BODY'})
            gateways=sum(path=='/gateway/bot' for path in calls)
            return httpx.Response(401 if gateways==1 else 200,json={'code':401 if gateways==1 else 0,'url':'wss://x.invalid/SECRET_URL'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            qq=QQHTTP(client,QQTokens(client,'app','FIXED_SECRET',observer=self.observer),self.observer)
            with self.assertLogs('jx3_bot.observability',level='INFO') as captured:
                await qq.gateway()
        shown='\n'.join(captured.output)
        self.assertEqual(sum('event=qq.http.response' in x for x in captured.output),4)
        self.assertIn('status=401',shown); self.assertIn('status=200',shown)
        for value in ('OPAQUE_TOKEN_SECRET','SECRET_BODY','SECRET_URL','FIXED_SECRET'): self.assertNotIn(value,shown)

    async def test_business_error_code_logged_without_message_or_path(self):
        def handler(request):
            if request.url.path.endswith('getAppAccessToken'):
                return httpx.Response(200,json={'access_token':'TOKEN_SECRET','expires_in':7200})
            return httpx.Response(200,json={'err_code':400540,'message':'DO_NOT_ECHO_SECRET','id':'SECRET_ID'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            qq=QQHTTP(client,QQTokens(client,'app','FIXED_SECRET',observer=self.observer),self.observer)
            with self.assertLogs('jx3_bot.observability',level='INFO') as captured:
                with self.assertRaises(ProviderError): await qq.reply('HIDDEN_GROUP','HIDDEN_MSG','HIDDEN_CONTENT')
        shown='\n'.join(captured.output)
        self.assertIn('code="400540"',shown)
        for value in ('DO_NOT_ECHO_SECRET','SECRET_ID','HIDDEN_GROUP','HIDDEN_MSG','HIDDEN_CONTENT','TOKEN_SECRET'): self.assertNotIn(value,shown)

    async def test_network_exception_text_not_logged(self):
        def handler(request): raise httpx.ConnectError('SECRET_URL https://x.invalid/secret',request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tokens=QQTokens(client,'app','FIXED_SECRET',observer=self.observer)
            with self.assertLogs('jx3_bot.observability',level='INFO') as captured:
                with self.assertRaises(ProviderError): await tokens.get()
        shown='\n'.join(captured.output)
        self.assertIn('error_type="ConnectError"',shown)
        self.assertNotIn('SECRET_URL',shown)
        self.assertNotIn('qq.http.response',shown)

    async def test_gateway_worker_http_trace_and_uncertain_not_resent(self):
        def handler(request):
            if request.url.path.endswith('getAppAccessToken'):
                return httpx.Response(200,json={'access_token':'TOKEN_SECRET','expires_in':7200})
            return httpx.Response(200,json={})  # Submitted, but cannot confirm a reply ID.
        self.store.ready('s',0)
        self.store.accept('s',1,message(self.clock),self.clock())
        weather=AsyncMock(); weather.handle.return_value='ANSWER_NOT_LOGGED'
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            qq=QQHTTP(client,QQTokens(client,'test-app','FIXED_SECRET',observer=self.observer),self.observer)
            app=Application(self.store,weather,qq,Mock(),clock=self.clock,observer=self.observer)
            with self.assertLogs('jx3_bot.observability',level='INFO') as captured:
                await app.process_one(); await app.send_one()
                self.assertFalse(await app.send_one())
        shown='\n'.join(captured.output)
        traces=re.findall(r'trace="(m_[a-f0-9]+)"',shown)
        self.assertEqual(len(set(traces)),1)
        for name in ('command.start','command.done','reply.planned','reply.submit','qq.http.response','reply.result'):
            self.assertIn('event='+name,shown)
        self.assertIn('state="uncertain"',shown)
        self.assertNotIn('ANSWER_NOT_LOGGED',shown)
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0],'uncertain')

    async def test_failed_accept_never_claimed_accepted(self):
        self.store.ready('s',0)
        self.store.accept=Mock(side_effect=StorageError('UNSAFE_SECRET_EXCEPTION'))
        http=Mock(); http.tokens.get=AsyncMock(return_value='TOKEN_SECRET')
        gateway=Gateway(http,self.store,'app',clock=self.clock,observer=self.observer)
        with self.assertLogs('jx3_bot.observability',level='INFO') as captured:
            with self.assertRaises(GatewayFatal):
                await gateway.connection(FakeSocket([hello(1000),event(self.clock),{'op':9,'d':False}]))
        shown='\n'.join(captured.output)
        self.assertIn('state="accept_failed"',shown)
        self.assertNotIn('state="accepted"',shown)
        self.assertNotIn('UNSAFE_SECRET_EXCEPTION',shown)
        self.assertEqual(self.store.meta('gateway')['seq'],0)

    async def test_cli_usage_keeps_json_stdout_without_chat_logger(self):
        from jx3_bot.__main__ import main
        out=io.StringIO()
        with patch('jx3_bot.__main__.load_dotenv'), patch.dict('os.environ',{},clear=True), patch('sys.stdout',out):
            self.assertEqual(main(['--env-file','unused-synthetic','--database',str(self.store.path),'usage']),0)
        self.assertIn('attempts',json.loads(out.getvalue()))
        self.assertNotIn('[receive]',out.getvalue())
