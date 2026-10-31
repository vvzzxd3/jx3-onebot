import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

import httpx

from jx3_bot.application import Application
from jx3_bot.errors import ProviderError, GatewayFatal
from jx3_bot.qq_http import QQTokens, QQHTTP
from jx3_bot.store import Store
from support import Clock, message


class QQHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_singleflight_refresh_and_body_failure(self):
        clock, calls = Clock(), []
        def handler(request):
            calls.append(request)
            return httpx.Response(200,json={'access_token':'synthetic-token','expires_in':'120'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            tokens = QQTokens(http,'app','synthetic-secret',clock)
            result = await asyncio.gather(*(tokens.get() for _ in range(20)))
            self.assertEqual(len(calls), 1)
            self.assertEqual(set(result), {'synthetic-token'})
            clock.now += 61
            await tokens.get()
            self.assertEqual(len(calls), 2)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,json={'code':400,'access_token':'bad','expires_in':7200}))) as http:
            with self.assertRaises(ProviderError): await QQTokens(http,'app','secret').get()

    async def test_gateway_dynamic_url_and_group_payload(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path.endswith('getAppAccessToken'):
                return httpx.Response(200,json={'access_token':'synthetic-token','expires_in':7200})
            if request.url.path == '/gateway/bot':
                return httpx.Response(200,json={'url':'wss://official-gateway.invalid/path','shards':1,'session_start_limit':{'remaining':10}})
            return httpx.Response(200,json={'id':'synthetic-reply'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            qq = QQHTTP(client,QQTokens(client,'app','secret'))
            self.assertEqual(await qq.gateway(), 'wss://official-gateway.invalid/path')
            await qq.reply('group','message-id','reply',1)
        import json
        self.assertEqual(requests[-1].headers['Authorization'], 'QQBot synthetic-token')
        self.assertEqual(json.loads(requests[-1].content), {'msg_type':0,'content':'reply','msg_id':'message-id','msg_seq':1})

    async def test_message_http_failure_is_not_retried(self):
        calls=[]
        def handler(request):
            calls.append(request)
            if request.url.path.endswith('getAppAccessToken'):
                return httpx.Response(200,json={'access_token':'synthetic-token','expires_in':7200})
            return httpx.Response(200,json={'err_code':400540,'message':'synthetic rejected duplicate'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            qq=QQHTTP(client,QQTokens(client,'app','secret'))
            with self.assertRaises(ProviderError): await qq.reply('g','m','content')
        self.assertEqual(len(calls),2)

    async def test_start_limit_is_honored(self):
        qq = QQHTTP(None, None)
        qq.request=AsyncMock(return_value={'url':'wss://gateway.invalid','session_start_limit':{'remaining':0}})
        with self.assertRaises(GatewayFatal): await qq.gateway()


class ApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'db')
        self.clock=Clock()
        self.weather=AsyncMock()
        self.weather.handle.return_value='synthetic weather answer'
        self.qq=AsyncMock()
        self.gateway=AsyncMock()
        self.gateway.stop=asyncio.Event()
        self.app=Application(self.store,self.weather,self.qq,self.gateway,clock=self.clock)
        self.store.ready('s',0)
    async def asyncTearDown(self):
        self.store.close(); self.temp.cleanup()

    async def test_double_delivery_one_business_and_reply(self):
        msg=message(self.clock)
        self.store.accept('s',1,msg,self.clock())
        self.store.accept('s',2,msg,self.clock())
        self.assertTrue(await self.app.process_one())
        self.assertFalse(await self.app.process_one())
        self.assertTrue(await self.app.send_one())
        self.assertFalse(await self.app.send_one())
        self.weather.handle.assert_awaited_once()
        self.qq.reply.assert_awaited_once_with('g','m1','synthetic weather answer',1, scene='group', expires=self.clock()+300, clock=self.clock)

    async def test_timeout_unknown_never_changes_sequence_or_resends(self):
        self.store.accept('s',1,message(self.clock),self.clock())
        await self.app.process_one()
        self.qq.reply.side_effect=ProviderError('qq-network')
        await self.app.send_one()
        self.store.recover()
        self.assertFalse(await self.app.send_one())
        self.qq.reply.assert_awaited_once()
        self.assertEqual(self.store._read('SELECT state,seq FROM outbox')[0]['state'],'uncertain')
        self.assertEqual(self.store._read('SELECT seq FROM outbox')[0][0],1)

    async def test_expired_message_never_calls_weather_or_sends(self):
        self.store.accept('s',1,message(self.clock),self.clock())
        self.clock.now += 300
        self.assertFalse(await self.app.process_one())
        self.assertFalse(await self.app.send_one())
        self.weather.handle.assert_not_called(); self.qq.reply.assert_not_called()

    async def test_crash_after_claim_retires_unplanned_business_without_send(self):
        self.store.accept('s',1,message(self.clock),self.clock())
        msg=self.store.claim_command(self.clock())
        self.assertIsNotNone(msg)
        self.assertEqual(self.store.recover()['retired_commands'], 1)
        self.assertFalse(await self.app.process_one())
        self.assertFalse(await self.app.send_one())
        self.weather.handle.assert_not_called()
        self.qq.reply.assert_not_called()
        self.assertEqual(self.store.meta('gateway'), {'session_id':'s','seq':1})
        self.assertEqual(self.store._read('SELECT state,payload FROM inbox')[0][:], ('expired',None))
