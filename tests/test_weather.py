import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import httpx

from jx3_bot.cities import import_csv
from jx3_bot.errors import BudgetExhausted, ProviderError, StorageError
from jx3_bot.store import Store
from jx3_bot.weather import WeatherAuth, WeatherClient, WeatherService, format_forecast
from support import Clock, city, csv_bytes, daily_body, message, settings, weather_auth


class WeatherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'test.sqlite')
        self.clock = Clock()
        self.settings = settings(self.store.path)
        self.calls = []
        self.responses = []
        def handler(request):
            self.calls.append(request)
            if self.responses:
                result = self.responses.pop(0)
                if isinstance(result, Exception): raise result
                return result
            return httpx.Response(200, json=daily_body())
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.client = WeatherClient(self.http, self.store, self.settings, auth=weather_auth(self.settings, self.clock), clock=self.clock)
        self.service = WeatherService(self.store, self.client, self.settings, self.clock)
        import_csv(self.store, csv_bytes())
    async def asyncTearDown(self):
        await self.http.aclose()
        self.store.close()
        self.temp.cleanup()

    async def test_v1_coordinates_auth_cache_and_singleflight(self):
        result = await asyncio.gather(*(self.service.handle(message(self.clock, id=f'm{i}')) for i in range(12)))
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(request.url.path, '/weather/v1/daily/39.90/116.41')
        self.assertEqual(request.url.params['localTime'], 'true')
        self.assertEqual(request.url.params['days'], '3')
        self.assertTrue(request.headers['Authorization'].startswith('Bearer '))
        self.assertNotIn('X-QW-Api-Key', request.headers)
        self.assertNotIn('key', request.url.params)
        self.assertIn('12.5°C～25°C', result[0])
        self.assertIn('https://developer.qweather.com/attribution.html', result[0])
        self.assertTrue(any('缓存' in value for value in result))
        self.assertEqual(self.store.usage('Asia/Shanghai', self.clock())[1], 1)

    async def test_exhaustion_and_cached_weather_still_works(self):
        s = replace(self.settings, monthly_limit=1)
        client = WeatherClient(self.http, self.store, s, auth=weather_auth(s, self.clock), clock=self.clock)
        service = WeatherService(self.store, client, s, self.clock)
        await service.handle(message(self.clock))
        self.assertIn('缓存', await service.handle(message(self.clock)))
        self.clock.now += 3601
        self.assertIn('达到配置上限', await service.handle(message(self.clock)))
        self.assertEqual(len(self.calls), 1)

    async def test_failed_retried_attempts_are_counted(self):
        s = replace(self.settings, retries=1)
        self.responses = [httpx.Response(503, json={}), httpx.Response(200, json=daily_body())]
        client = WeatherClient(self.http, self.store, s, auth=weather_auth(s, self.clock), clock=self.clock, sleep=lambda _: asyncio.sleep(0))
        await client.daily(city())
        self.assertEqual(self.store.usage('Asia/Shanghai', self.clock())[1], 2)
        self.responses = [httpx.ReadTimeout('synthetic timeout'), httpx.ReadTimeout('synthetic timeout')]
        with self.assertRaises(ProviderError): await client.daily(city())
        self.assertEqual(self.store.usage('Asia/Shanghai', self.clock())[1], 4)

    async def test_last_slot_does_not_allow_retry(self):
        s = replace(self.settings, retries=1, monthly_limit=1)
        self.responses = [httpx.Response(503, json={})]
        client = WeatherClient(self.http, self.store, s, auth=weather_auth(s, self.clock), clock=self.clock, sleep=lambda _: asyncio.sleep(0))
        with self.assertRaises(BudgetExhausted): await client.daily(city())
        self.assertEqual(len(self.calls), 1)

    async def test_geo_not_cached_or_persisted(self):
        geo = {'code':'200', 'location':[{'id':'opaque-geo-id','name':'API_CITY_MUST_NOT_BE_STORED','adm1':'API_ADMIN','adm2':'API_ADMIN2','country':'China','lat':'40.12','lon':'118.12','tz':'Asia/Shanghai'}]}
        for _ in range(2):
            self.responses += [httpx.Response(200, json=geo), httpx.Response(200, json=daily_body())]
            response = await self.service.handle(message(self.clock, '用户自有输入'))
            self.assertIn('用户自有输入', response)
            self.assertNotIn('API_CITY_MUST_NOT_BE_STORED', response)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM cache')[0][0], 0)
        self.assertNotIn('opaque-geo-id', '\n'.join(self.store.db.iterdump()))
        self.assertNotIn('API_CITY_MUST_NOT_BE_STORED', '\n'.join(self.store.db.iterdump()))

    async def test_ambiguous_local_no_network_and_geo_safe_reply(self):
        import_csv(self.store, csv_bytes([city('1','朝阳','北京','北京'), city('2','朝阳','辽宁','朝阳')]))
        answer = await self.service.handle(message(self.clock, '朝阳'))
        self.assertIn('天气 1', answer)
        self.assertEqual(self.calls, [])
        geo = {'code':'200','location':[{'id':str(i),'name':'API_PRIVATE_NAME','lat':'40','lon':'118','tz':'Asia/Shanghai'} for i in (1,2)]}
        self.responses.append(httpx.Response(200, json=geo))
        answer = await self.service.handle(message(self.clock, '输入城市'))
        self.assertIn('多个城市', answer)
        self.assertNotIn('API_PRIVATE_NAME', answer)
        self.assertEqual(len(self.calls), 1)

    async def test_user_preferences_and_usage_do_not_call_api(self):
        await self.service.handle(message(self.clock, '北京', kind='city'))
        self.assertEqual(self.store.preference('test-app','g','u'), '北京')
        await self.service.handle(message(self.clock, '', kind='city'))
        await self.service.handle(message(self.clock, '', kind='usage'))
        self.assertEqual(self.calls, [])
        await self.service.handle(message(self.clock, '删除', kind='city'))
        self.assertIsNone(self.store.preference('test-app','g','u'))

    async def test_redirect_and_business_error_not_followed(self):
        self.responses = [httpx.Response(302, headers={'Location':'https://other.invalid/'})]
        with self.assertRaises(ProviderError): await self.client.daily(city())
        self.assertEqual(len(self.calls), 1)
        self.responses = [httpx.Response(200, json={'code':'401','location':[]})]
        with self.assertRaises(ProviderError): await self.client.geo('unknown')
        self.assertEqual(len(self.calls), 2)

    async def test_cache_expires_at_local_midnight(self):
        self.clock.now = 1791647700  # 2026-10-10 23:55 Asia/Shanghai
        await self.service.handle(message(self.clock))
        expiry = self.store._read('SELECT expires FROM cache')[0][0]
        self.assertLessEqual(expiry-self.clock(), 3600)
        self.assertEqual((expiry + 8*3600) % 86400, 0)

    async def test_database_failure_blocks_before_network(self):
        with unittest.mock.patch.object(self.client.repository, 'reserve', side_effect=StorageError('synthetic')):
            with self.assertRaises(StorageError): await self.client.daily(city())
        self.assertEqual(self.calls, [])

    def test_jwt_header_payload_and_refresh(self):
        signer = Mock(return_value='synthetic.jwt.signature')
        s = replace(self.settings, developer_id='developer', project_id='project', credential_id='credential')
        auth = WeatherAuth(s, self.clock, signer)
        auth.headers(); auth.headers()
        self.assertEqual(signer.call_count, 1)
        payload = signer.call_args.args[0]
        self.assertEqual(payload['iss'], 'developer')
        self.assertEqual(payload['sub'], 'project')
        self.assertEqual(payload['iat'], int(self.clock())-30)
        self.assertEqual(signer.call_args.kwargs['headers']['kid'], 'credential')
        self.clock.now += 841
        auth.headers()
        self.assertEqual(signer.call_count, 2)
