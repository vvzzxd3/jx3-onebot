import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from jx3_bot.errors import GatewayFatal, StorageError
from jx3_bot.gateway import Gateway, Reconnect, NEW_SESSION_CODES, FATAL_CODES
from jx3_bot.store import Store
from support import Clock, event, enable_chat


class FakeSocket:
    def __init__(self, frames, auto_ack=False):
        self.frames=asyncio.Queue()
        for item in frames: self.frames.put_nowait(json.dumps(item))
        self.sent=[]
        self.auto_ack=auto_ack
    async def recv(self):
        await asyncio.sleep(0)
        return await self.frames.get()
    def __aiter__(self): return self
    async def __anext__(self): return await self.recv()
    async def send(self, raw):
        frame=json.loads(raw)
        self.sent.append(frame)
        if self.auto_ack and frame['op']==1:
            self.frames.put_nowait(json.dumps({'op':11,'d':None}))


def hello(interval=10): return {'op':10,'d':{'heartbeat_interval':interval}}
def ready(seq=1): return {'op':0,'s':seq,'t':'READY','d':{'session_id':'session'}}


class GatewayConnectorTests(unittest.IsolatedAsyncioTestCase):
    def test_default_connector_disables_proxy_when_supported(self):
        calls, result = [], object()
        def connect15(url, *, proxy=True, **options):
            calls.append((url, proxy, options))
            return result
        with patch('websockets.asyncio.client.connect', new=connect15):
            gateway = Gateway(None, None, 'synthetic-app')
            self.assertIs(gateway.connector('wss://offline-gateway.invalid/'), result)
        self.assertEqual(calls, [('wss://offline-gateway.invalid/', None,
                                 {'ping_interval': None, 'max_size': 1024*1024, 'open_timeout': 15})])

    def test_default_connector_preserves_websockets14_signature(self):
        calls, result = [], object()
        def connect14(url, **options):
            calls.append((url, options))
            return result
        with patch('websockets.asyncio.client.connect', new=connect14):
            gateway = Gateway(None, None, 'synthetic-app')
            self.assertIs(gateway.connector('wss://offline-gateway.invalid/'), result)
        self.assertEqual(calls, [('wss://offline-gateway.invalid/',
                                 {'ping_interval': None, 'max_size': 1024*1024, 'open_timeout': 15})])

    def test_injected_connector_is_unchanged(self):
        injected = Mock(return_value=object())
        with patch('jx3_bot.gateway.signature', side_effect=AssertionError('Unexpected default connector')):
            gateway = Gateway(None, None, 'synthetic-app', connector=injected)
        self.assertIs(gateway.connector, injected)
        self.assertIs(gateway.connector('wss://offline-gateway.invalid/'), injected.return_value)
        injected.assert_called_once_with('wss://offline-gateway.invalid/')

    async def test_real_connector_bypasses_environment_and_system_proxies(self):
        loop = asyncio.get_running_loop()
        proxy_env = {'ALL_PROXY': 'socks5h://proxy.invalid:1080',
                     'HTTPS_PROXY': 'http://proxy.invalid:8080',
                     'WSS_PROXY': 'socks5h://proxy.invalid:1080'}
        with patch.dict('os.environ', proxy_env), \
                patch('websockets.asyncio.client.get_proxy', create=True,
                      side_effect=AssertionError('Unexpected proxy discovery')) as discover, \
                patch('websockets.asyncio.client.connect_socks_proxy', create=True,
                      side_effect=AssertionError('Unexpected SOCKS connection')) as socks, \
                patch.object(loop, 'create_connection', new_callable=AsyncMock,
                             side_effect=OSError('synthetic direct transport blocked')) as direct:
            gateway = Gateway(None, None, 'synthetic-app')
            connection = gateway.connector('wss://offline-gateway.invalid/')
            with self.assertRaisesRegex(OSError, 'synthetic direct transport blocked'):
                await connection.create_connection()
            discover.assert_not_called()
            socks.assert_not_called()
            direct.assert_awaited_once()
            options = direct.await_args.kwargs
            self.assertEqual(options['host'], 'offline-gateway.invalid')
            self.assertEqual(options['port'], 443)
            self.assertEqual(options['server_hostname'], 'offline-gateway.invalid')
            self.assertIs(options['ssl'], True)
            self.assertNotIn('sock', options)


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'test.sqlite')
        enable_chat(self.store)
        self.clock=Clock()
        self.http=Mock()
        self.http.tokens=Mock()
        self.http.tokens.get=AsyncMock(return_value='synthetic-token')
        self.http.gateway=AsyncMock(return_value='wss://synthetic.invalid')
        self.gateway=Gateway(self.http,self.store,'test-app',clock=self.clock)
    async def asyncTearDown(self):
        self.store.close(); self.temp.cleanup()

    async def test_identify_and_zero_sequence_resume(self):
        ws=FakeSocket([hello(1000),ready(0)])
        task=asyncio.create_task(self.gateway.connection(ws))
        await asyncio.sleep(0.02)
        self.gateway.stop.set(); await asyncio.wait_for(task,1)
        self.assertEqual(ws.sent[0],{'op':2,'d':{'token':'QQBot synthetic-token','intents':1<<25,'shard':[0,1]}})
        self.assertEqual(self.store.meta('gateway')['seq'],0)
        other=Gateway(self.http,self.store,'test-app',clock=self.clock)
        ws=FakeSocket([hello(1000),{'op':7,'d':None}])
        with self.assertRaises(Reconnect): await other.connection(ws)
        self.assertEqual(ws.sent[0]['op'],6)
        self.assertEqual(ws.sent[0]['d']['seq'],0)
        self.assertEqual(ws.sent[0]['d']['session_id'],'session')

    async def test_duplicate_invalid_hello_and_missing_ready(self):
        for frames in ([{'op':10,'d':{'heartbeat_interval':True}}],
                       [hello(),hello()], [hello(),{'op':0,'s':1,'t':'READY','d':{}}]):
            with self.subTest(frames=frames), self.assertRaises(GatewayFatal):
                await asyncio.wait_for(self.gateway.connection(FakeSocket(frames)),1)

    async def test_ack_deadline_causes_reconnect(self):
        ws=FakeSocket([hello(5),ready()])
        with self.assertRaises(Reconnect): await asyncio.wait_for(self.gateway.connection(ws),1)
        self.assertGreaterEqual(len([f for f in ws.sent if f['op']==1]),2)

    async def test_platform_heartbeat_and_unknown_event_privacy(self):
        self.store.ready('session',1)
        ws=FakeSocket([hello(1000),{'op':0,'s':2,'t':'UNKNOWN_EVENT','d':{'content':'NEVER_STORE_CHAT','auth_token':'NEVER_STORE_AUTH'}}, {'op':1,'d':None}],auto_ack=True)
        task=asyncio.create_task(self.gateway.connection(ws))
        await asyncio.sleep(0.02)
        self.gateway.stop.set(); await asyncio.wait_for(task,1)
        self.assertEqual([f for f in ws.sent if f['op']==1][0]['d'],2)
        self.assertEqual(self.store.meta('gateway')['seq'],2)
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM inbox')[0][0],0)
        dump='\n'.join(self.store.db.iterdump())
        self.assertNotIn('NEVER_STORE',dump)
        self.assertFalse(any(f['op']==11 for f in ws.sent))

    async def test_slow_storage_does_not_block_acks(self):
        started,release,finished=threading.Event(),threading.Event(),threading.Event()
        original=self.store.accept
        def slow(*args):
            started.set()
            if not release.wait(1): raise RuntimeError('synthetic test timeout')
            result=original(*args)
            finished.set()
            return result
        self.store.accept=slow
        ws=FakeSocket([hello(5),ready(),event(self.clock,seq=2)],auto_ack=True)
        task=asyncio.create_task(self.gateway.connection(ws))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait,1))
            await asyncio.sleep(0.03)
            self.assertFalse(task.done())
            self.assertEqual(self.gateway.received_seq,2)
            self.assertEqual(self.store.meta('gateway')['seq'],1)
            self.assertGreater(len([f for f in ws.sent if f['op']==1]),2)
            release.set()
            self.assertTrue(await asyncio.to_thread(finished.wait,1))
            self.gateway.stop.set(); await asyncio.wait_for(task,1)
            self.assertEqual(self.store.meta('gateway')['seq'],2)
        finally:
            release.set()
            task.cancel(); await asyncio.gather(task,return_exceptions=True)

    async def test_storage_failure_does_not_skip_checkpoint(self):
        self.store.accept=Mock(side_effect=StorageError('synthetic'))
        ws=FakeSocket([hello(1000),ready(),event(self.clock,seq=2)])
        with self.assertRaises(StorageError): await asyncio.wait_for(self.gateway.connection(ws),1)
        self.assertEqual(self.store.meta('gateway')['seq'],1)
        self.assertEqual(self.gateway.received_seq,2)

    async def test_op9_clears_session_without_discord_boolean_assumption(self):
        for payload in (True,False,None,{'unsupported':'value'}):
            self.store.ready('session',0)
            gateway=Gateway(self.http,self.store,'test-app',clock=self.clock)
            with self.assertRaises(Reconnect):
                await gateway.connection(FakeSocket([hello(1000),{'op':9,'d':payload}]))
            self.assertIsNone(self.store.meta('gateway'))

    async def test_close_code_policy_is_bounded_and_explicit(self):
        for code in (4006,4007,4900,4001,4014,4914,4999,4008):
            with self.subTest(code=code):
                self.store.ready('session',0)
                gateway=Gateway(self.http,self.store,'test-app',clock=self.clock)
                class Connection:
                    async def __aenter__(self):
                        gateway.stop.set()
                        raise ConnectionClosedError(Close(code,'synthetic'),Close(code,'synthetic'),True)
                    async def __aexit__(self,*args): pass
                gateway.connector=lambda _: Connection()
                if code in FATAL_CODES or code==4999:
                    with self.assertRaises(GatewayFatal): await gateway.run()
                else:
                    await gateway.run()
                if code in NEW_SESSION_CODES:
                    self.assertIsNone(self.store.meta('gateway'))
