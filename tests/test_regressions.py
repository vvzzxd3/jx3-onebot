import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from jx3_bot.__main__ import instance_lock
from jx3_bot.application import Application
from jx3_bot.cities import import_csv
from jx3_bot.errors import ConfigurationError, GatewayFatal, NoWeather, ProviderError, ReplyNotSent, StorageError
from jx3_bot.gateway import Gateway, Reconnect
from jx3_bot.models import group_message
from jx3_bot.qq_http import QQHTTP, QQTokens
from jx3_bot.store import Store
from jx3_bot.weather import WeatherClient, WeatherService, format_forecast, quantity
from support import Clock, city, csv_bytes, daily_body, event, message, settings, weather_auth, enable_chat
from test_gateway import FakeSocket, hello


class RegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'db.sqlite'
        self.store=Store(self.path)
        enable_chat(self.store)
        self.clock=Clock()
        self.settings=settings(self.path)
    async def asyncTearDown(self):
        self.store.close(); self.temp.cleanup()

    async def test_symlink_and_hardlink_cannot_bypass_instance_lock(self):
        alias=Path(self.temp.name)/'alias.sqlite'
        try:
            alias.symlink_to(self.path)
        except OSError:
            alias = None
        if alias is not None:
            with instance_lock(self.path):
                with self.assertRaises(ConfigurationError):
                    with instance_lock(alias): pass
        hard=Path(self.temp.name)/'hard.sqlite'
        try:
            os.link(self.path,hard)
        except OSError:
            hard = None
        if hard is not None:
            with self.assertRaises(ConfigurationError):
                with instance_lock(hard): pass
        if alias is None and hard is None:
            self.skipTest("filesystem does not permit symlink or hardlink capability checks")

    async def test_malformed_attribution_is_friendly_and_not_cached(self):
        body=daily_body(); body['metadata']['attributions']=[123]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=body))) as http:
            client=WeatherClient(http,self.store,self.settings,auth=weather_auth(self.settings,self.clock),clock=self.clock)
            import_csv(self.store,csv_bytes())
            service=WeatherService(self.store,client,self.settings,self.clock)
            self.assertIn('暂时没有可用', await service.handle(message(self.clock)))
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM cache')[0][0],0)
        self.assertEqual(quantity({'value':10**1000,'unit':'°C'}),'暂无')

    async def test_token_preflight_retry_is_bounded_and_same_payload(self):
        self.store.ready('s',0)
        self.store.accept('s',1,message(self.clock),self.clock())
        weather=AsyncMock(); weather.handle.return_value='synthetic stable reply'
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(503,json={'code':503})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            qq=QQHTTP(http,QQTokens(http,'test-app','synthetic-secret'))
            app=Application(self.store,weather,qq,Mock(),clock=self.clock)
            await app.process_one()
            with patch('jx3_bot.application.asyncio.sleep',new=AsyncMock()):
                for _ in range(3): self.assertTrue(await app.send_one())
            self.assertFalse(await app.send_one())
        self.assertEqual(len(calls),3)
        self.assertTrue(all(r.url.path.endswith('getAppAccessToken') for r in calls))
        row=self.store._read('SELECT state,seq,content,attempts FROM outbox')[0]
        self.assertEqual(row['state'],'failed')
        self.assertEqual(row['seq'],1)
        self.assertEqual(row['attempts'],3)
        self.assertEqual(row['content'],'synthetic stable reply')

    async def test_post_401_invalidates_for_next_message_only(self):
        calls=[]; token_count=0
        def handler(request):
            nonlocal token_count
            calls.append(request)
            if request.url.path.endswith('getAppAccessToken'):
                token_count+=1
                return httpx.Response(200,json={'access_token':f'token-{token_count}','expires_in':7200})
            return httpx.Response(401,json={'err_code':401})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            qq=QQHTTP(http,QQTokens(http,'app','synthetic-secret'))
            for id in ('m1','m2'):
                with self.assertRaises(ProviderError): await qq.reply('g',id,'reply')
        posts=[r for r in calls if '/v2/groups/' in r.url.path]
        self.assertEqual(len(posts),2)
        self.assertEqual(token_count,2)
        self.assertNotEqual(posts[0].headers['Authorization'],posts[1].headers['Authorization'])

    async def test_200_without_reply_id_is_not_claimed_sent(self):
        self.store.ready('s',0)
        self.store.accept('s',1,message(self.clock),self.clock())
        weather=AsyncMock(); weather.handle.return_value='synthetic'
        calls=[]
        def handler(request):
            calls.append(request)
            if request.url.path.endswith('getAppAccessToken'):
                return httpx.Response(200,json={'access_token':'synthetic','expires_in':7200})
            return httpx.Response(200,json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            qq=QQHTTP(http,QQTokens(http,'app','synthetic'))
            app=Application(self.store,weather,qq,Mock(),clock=self.clock)
            await app.process_one(); await app.send_one()
            self.assertFalse(await app.send_one())
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0],'uncertain')
        self.assertEqual(len([r for r in calls if '/v2/groups/' in r.url.path]),1)

    async def test_resume_allowed_when_identify_budget_exhausted(self):
        qq=QQHTTP(None,None)
        qq.request=AsyncMock(return_value={'url':'wss://gateway.invalid','session_start_limit':{'remaining':0}})
        self.assertEqual(await qq.gateway(allow_resume=True),'wss://gateway.invalid')
        with self.assertRaises(GatewayFatal): await qq.gateway()

    async def test_user_fifo_claim_and_other_user_concurrency(self):
        self.store.ready('s',0)
        first=message(self.clock,'北京',id='m1',kind='city')
        second=message(self.clock,'上海',id='m2',kind='city')
        third=message(self.clock,'删除',id='m3',kind='city')
        for i,msg in enumerate((first,second,third),1): self.store.accept('s',i,msg,self.clock())
        other=message(self.clock,'北京',id='m4',kind='city',user='other')
        self.store.accept('s',4,other,self.clock())
        self.assertEqual(self.store.claim_command(self.clock()).message_id,'m1')
        self.assertEqual(self.store.claim_command(self.clock()).message_id,'m4')
        self.assertIsNone(self.store.claim_command(self.clock()))
        self.store.plan_reply(first,'set')
        self.assertEqual(self.store.claim_command(self.clock()).message_id,'m2')
        self.store.plan_reply(second,'set')
        self.assertEqual(self.store.claim_command(self.clock()).message_id,'m3')

    async def test_op9_drains_all_buffered_receipts_before_clear(self):
        self.store.ready('session',1)
        qq=Mock(); qq.tokens=Mock(); qq.tokens.get=AsyncMock(return_value='synthetic')
        gateway=Gateway(qq,self.store,'test-app',clock=self.clock)
        class BufferedSocket(FakeSocket):
            async def recv(self): return await self.frames.get()
        ws=BufferedSocket([hello(1000),event(self.clock,seq=2,id='m2'),event(self.clock,seq=3,id='m3'),{'op':9,'d':False}])
        with self.assertRaises(Reconnect): await gateway.connection(ws)
        self.assertIsNone(self.store.meta('gateway'))
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM inbox')[0][0],2)
        self.assertEqual([r[0] for r in self.store._read('SELECT msg FROM inbox ORDER BY rowid')],['m2','m3'])

    async def test_new_session_close_drains_buffered_receipts(self):
        from websockets.exceptions import ConnectionClosedError
        from websockets.frames import Close
        self.store.ready('session',1)
        qq=Mock(); qq.tokens=Mock(); qq.tokens.get=AsyncMock(return_value='synthetic')
        qq.gateway=AsyncMock(return_value='wss://synthetic.invalid')
        gateway=Gateway(qq,self.store,'test-app',clock=self.clock)
        class ClosedBuffered(FakeSocket):
            async def recv(self):
                if self.frames.empty():
                    raise ConnectionClosedError(Close(4007,'synthetic'),Close(4007,'synthetic'),True)
                return await self.frames.get()
        ws=ClosedBuffered([hello(1000),event(self.clock,seq=2,id='m2'),event(self.clock,seq=3,id='m3')])
        class Context:
            async def __aenter__(self): return ws
            async def __aexit__(self,*args): gateway.stop.set()
        gateway.connector=lambda _:Context()
        await gateway.run()
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM inbox')[0][0],2)
        self.assertIsNone(self.store.meta('gateway'))

    async def test_drain_storage_failure_stops_without_clearing_checkpoint(self):
        self.store.ready('session',1)
        qq=Mock(); qq.tokens=Mock(); qq.tokens.get=AsyncMock(return_value='synthetic')
        gateway=Gateway(qq,self.store,'test-app',clock=self.clock)
        self.store.accept=Mock(side_effect=StorageError('synthetic'))
        class Buffered(FakeSocket):
            async def recv(self): return await self.frames.get()
        ws=Buffered([hello(1000),event(self.clock,seq=2),{'op':9,'d':False}])
        with self.assertRaises(GatewayFatal): await gateway.connection(ws)
        self.assertEqual(self.store.meta('gateway')['seq'],1)

    async def test_geo_fields_do_not_leak_into_outbox(self):
        self.store.ready('session',0)
        msg=message(self.clock,'用户自有输入')
        self.store.accept('session',1,msg,self.clock())
        geo={'code':'200','location':[{'id':'GEO_ID_NOT_STORED','name':'GEO_NAME_NOT_STORED','lat':'40.12','lon':'118.12','tz':'Asia/Tokyo'}]}
        def handler(request):
            return httpx.Response(200,json=geo if '/geo/' in request.url.path else daily_body())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client=WeatherClient(http,self.store,self.settings,auth=weather_auth(self.settings,self.clock),clock=self.clock)
            service=WeatherService(self.store,client,self.settings,self.clock)
            app=Application(self.store,service,AsyncMock(),Mock(),clock=self.clock)
            self.assertTrue(await app.process_one())
        dump='\n'.join(self.store.db.iterdump())
        for marker in ('GEO_ID_NOT_STORED','GEO_NAME_NOT_STORED','Asia/Tokyo','118.12'):
            self.assertNotIn(marker,dump)
        self.assertIn('用户自有输入',dump)
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM cache')[0][0],0)

    async def test_bind_app_rejects_cross_app_database(self):
        self.store.bind_app('app-a')
        with self.assertRaises(StorageError): self.store.bind_app('app-b')

    async def test_nonfinite_timestamp_is_discarded(self):
        frame=event(self.clock)
        frame['d']['timestamp']=float('nan')
        self.assertIsNone(group_message('test-app',frame,self.clock()))

    async def test_geo_adm_query_and_local_suffix(self):
        import_csv(self.store,csv_bytes([city(name='北京',adm1='北京')]))
        self.assertEqual(len(self.store.find_cities('北京市')),1)
        received=[]
        def handler(request):
            received.append(request)
            return httpx.Response(200,json={'code':'200','location':[{'id':'geo','name':'朝阳','adm1':'北京','adm2':'北京','country':'CN','lat':'40','lon':'116','tz':'Asia/Shanghai'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client=WeatherClient(http,self.store,self.settings,auth=weather_auth(self.settings,self.clock),clock=self.clock)
            result=await client.geo('北京 朝阳')
        self.assertEqual(len(result),1)
        self.assertEqual(received[0].url.params['location'],'朝阳')
        self.assertEqual(received[0].url.params['adm'],'北京')

    async def test_full_chain_two_event_types_one_forecast_one_reply(self):
        self.store.ready('session',0)
        import_csv(self.store,csv_bytes())
        requests=[]
        def handler(request):
            requests.append(request)
            if request.url.path.endswith('getAppAccessToken'):
                return httpx.Response(200,json={'access_token':'synthetic','expires_in':7200})
            if '/weather/v1/' in request.url.path:
                return httpx.Response(200,json=daily_body())
            return httpx.Response(200,json={'id':'synthetic-reply'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            qq=QQHTTP(http,QQTokens(http,'test-app','synthetic-secret'))
            gateway=Gateway(qq,self.store,'test-app',clock=self.clock)
            service=WeatherService(self.store,WeatherClient(http,self.store,self.settings,auth=weather_auth(self.settings,self.clock),clock=self.clock),self.settings,self.clock)
            app=Application(self.store,service,qq,gateway,clock=self.clock)
            ws=FakeSocket([hello(1000),event(self.clock,seq=1),event(self.clock,seq=2,event_type='GROUP_AT_MESSAGE_CREATE')])
            task=asyncio.create_task(gateway.connection(ws))
            await asyncio.sleep(0.03)
            gateway.stop.set(); await asyncio.wait_for(task,1)
            self.assertTrue(await app.process_one()); self.assertFalse(await app.process_one())
            self.assertTrue(await app.send_one()); self.assertFalse(await app.send_one())
        self.assertEqual(len([r for r in requests if '/weather/v1/' in r.url.path]),1)
        replies=[r for r in requests if '/v2/groups/' in r.url.path]
        self.assertEqual(len(replies),1)
        self.assertEqual(json.loads(replies[0].content)['msg_id'],'m1')
        self.assertEqual(self.store.usage('Asia/Shanghai',self.clock())[1],1)
