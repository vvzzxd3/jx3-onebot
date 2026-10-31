"""Offline regressions for business ownership, legacy imports and shared persistence."""
import ast
from contextlib import redirect_stdout
from dataclasses import asdict, FrozenInstanceError, replace
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import httpx

from jx3_bot.core.models import Command, MatchContext, Reply
from jx3_bot.errors import BudgetExhausted, StorageError
from jx3_bot.integrations.qq.models import IncomingMessage
from jx3_bot.modules.weather import cities, client, handlers, models, rendering, service
from jx3_bot.modules.weather.repository import WeatherRepository, weather_repository
from jx3_bot.replies import templates
from jx3_bot.store import Store
from support import Clock, city, csv_bytes, daily_body, message, settings, weather_auth

ROOT = Path(__file__).resolve().parents[1]


class WeatherModuleTests(unittest.TestCase):
    def test_legacy_exports_are_the_same_objects(self):
        legacy_weather = importlib.import_module('jx3_bot.weather')
        legacy_cities = importlib.import_module('jx3_bot.cities')
        legacy_models = importlib.import_module('jx3_bot.models')
        legacy_matching = importlib.import_module('jx3_bot.matching')
        legacy_replies = importlib.import_module('jx3_bot.replies')
        for name, value in (
                ('WeatherAuth', client.WeatherAuth), ('WeatherClient', client.WeatherClient),
                ('WeatherService', service.WeatherService), ('format_forecast', rendering.format_forecast),
                ('quantity', rendering.quantity)):
            self.assertIs(getattr(legacy_weather, name), value)
        for name in legacy_cities.__all__:
            self.assertIs(getattr(legacy_cities, name), getattr(cities, name))
        self.assertIs(legacy_models.City, models.City)
        self.assertIs(legacy_models.Command, Command)
        self.assertIs(legacy_models.GroupMessage, IncomingMessage)
        self.assertIs(legacy_models.parse_command, handlers.parse_command)
        self.assertIs(legacy_matching.MatchContext, MatchContext)
        self.assertIs(legacy_replies.Reply, Reply)

    def test_model_definitions_belong_to_their_modules(self):
        self.assertEqual(models.City.__module__, 'jx3_bot.modules.weather.models')
        self.assertEqual(IncomingMessage.__module__, 'jx3_bot.integrations.qq.models')
        for value in (Command, MatchContext, Reply):
            self.assertEqual(value.__module__, 'jx3_bot.core.models')
        for filename in ('weather.py', 'cities.py', 'models.py'):
            tree = ast.parse((ROOT / 'src/jx3_bot' / filename).read_text())
            self.assertFalse(any(isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                                 for node in ast.walk(tree)), filename)

    def test_fresh_import_orders_without_provider_calls(self):
        orders = (
            ('modules.weather.handlers', 'matching', 'models', '__main__'),
            ('store', 'modules.weather.service', 'integrations.qq.events', 'weather'),
            ('core.models', 'modules.weather.client', 'replies', 'cities'),
        )
        for order in orders:
            with self.subTest(order=order):
                script = (
                    'import importlib\nfrom unittest.mock import patch\n'
                    'with patch("httpx.AsyncClient") as http:\n'
                    f'    for name in {order!r}: importlib.import_module("jx3_bot." + name)\n'
                    '    http.assert_not_called()\n'
                )
                result = subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT,
                    env={**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'},
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_message_and_city_serialization_unchanged(self):
        clock = Clock()
        for scene in ('group', 'c2c'):
            with self.subTest(scene=scene):
                value = replace(message(clock), scene=scene, commands=(Command('weather', '北京', 'weather.query'),))
                self.assertEqual(IncomingMessage.from_dict(value.to_dict()), value)
                self.assertEqual(value.storage_key, ('test-app', scene, 'g', 'm1'))
                self.assertEqual(IncomingMessage.from_dict(value.to_dict()).key, value.key)
        legacy = message(clock).to_dict()
        legacy.pop('scene')
        legacy.pop('commands')
        self.assertEqual(IncomingMessage.from_dict(legacy), message(clock))
        value = city()
        self.assertEqual(models.City(**asdict(value)), value)
        self.assertEqual(value.coordinates(), ('39.90', '116.41'))
        with self.assertRaises(FrozenInstanceError):
            value.id = 'changed'

    def test_repository_and_legacy_store_share_state_across_restart(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.sqlite'
            storage = Store(path)
            try:
                repository = storage.weather
                self.assertIs(repository.storage, storage)
                self.assertIs(weather_repository(storage), repository)
                self.assertIs(weather_repository(repository), repository)
                repository.bind_timezone('Asia/Shanghai')
                repository.reserve(2, 'Asia/Shanghai', clock())
                storage.reserve(2, 'Asia/Shanghai', clock())
                with self.assertRaises(BudgetExhausted):
                    repository.reserve(2, 'Asia/Shanghai', clock())
                repository.set_preference('a', 'g', 'u', '北京')
                storage.set_preference('a', 'g', 'u', '上海', 'c2c')
                self.assertEqual(storage.preference('a', 'g', 'u'), '北京')
                self.assertEqual(repository.preference('a', 'g', 'u', 'c2c'), '上海')
                cities.import_csv(repository, csv_bytes())
                self.assertIsInstance(storage.find_cities('北京')[0], models.City)
                repository.cache_put('synthetic', {'value': 1}, clock() + 10)
                self.assertEqual(storage.cache_get('synthetic', clock()), {'value': 1})
            finally:
                storage.close()
            restored = Store(path)
            try:
                self.assertEqual(restored.weather.usage('Asia/Shanghai', clock())[1], 2)
                self.assertEqual(restored.weather.preference('a', 'g', 'u'), '北京')
                self.assertEqual(restored.weather.preference('a', 'g', 'u', 'c2c'), '上海')
                with self.assertRaises(BudgetExhausted):
                    restored.reserve(2, 'Asia/Shanghai', clock())
            finally:
                restored.close()

    def test_repository_uses_existing_storage_failure_boundary(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as temp:
            storage = Store(Path(temp) / 'synthetic.sqlite')
            repository = storage.weather
            repository.bind_timezone('Asia/Shanghai')
            storage.close()
            with self.assertRaises(StorageError):
                repository.reserve(1, 'Asia/Shanghai', clock())
            with self.assertRaises(StorageError):
                repository.find_cities('北京')

    def test_templates_are_loaded_from_weather_module(self):
        folder = Path(service.__file__).parent
        expected = folder / 'templates/weather.toml'
        self.assertTrue(expected.is_file())
        self.assertFalse((ROOT / 'src/jx3_bot/templates/weather.toml').exists())
        with expected.open('rb') as handle:
            self.assertEqual(templates('weather'), tomllib.load(handle))
        self.assertEqual(len(templates('weather')), 19)
        self.assertEqual(rendering.render_result('city_missing').text,
                         '📍 尚未设置默认城市\n未设置默认城市。\n────────\n用法：天气城市 北京')

    def test_offline_cli_local_import_and_usage_use_module(self):
        from jx3_bot.__main__ import main
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.sqlite'
            csv = Path(temp) / 'synthetic.csv'
            csv.write_bytes(csv_bytes())
            with patch('jx3_bot.__main__.load_dotenv'), patch.dict('os.environ', {}, clear=True), \
                    patch('httpx.AsyncClient') as http:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(['--database', str(path), 'import-cities', '--csv', str(csv)]), 0)
                self.assertEqual(json.loads(output.getvalue())['records'], 1)
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(['--database', str(path), 'usage']), 0)
                self.assertEqual(json.loads(output.getvalue())['attempts'], 0)
                http.assert_not_called()
            storage = Store(path)
            try:
                self.assertEqual(storage.weather.find_cities('北京')[0].id, '101010100')
            finally:
                storage.close()

    def test_weather_sql_is_not_duplicated_in_legacy_methods(self):
        for name in ('bind_timezone', 'reserve', 'usage', 'replace_cities', 'find_cities',
                     'preference', 'set_preference', 'cache_get', 'cache_put'):
            method = getattr(Store, name)
            self.assertEqual(method.__code__.co_names, ('weather', name))
            self.assertTrue(callable(getattr(WeatherRepository, name)))


class WeatherModuleAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_module_repository_client_service_and_handlers_chain(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as temp:
            storage = Store(Path(temp) / 'synthetic.sqlite')
            try:
                calls = []
                def handle(request):
                    calls.append(request)
                    return httpx.Response(200, json=daily_body())
                configuration = settings(storage.path)
                repository = storage.weather
                cities.import_csv(repository, csv_bytes())
                async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                    provider = client.WeatherClient(http, repository, configuration,
                        auth=weather_auth(configuration, clock), clock=clock)
                    weather = service.WeatherService(repository, provider, configuration, clock)
                    registry = handlers.weather_registry(weather.handle_result)
                    for scene in ('group', 'c2c'):
                        command = registry.match('天气 北京', MatchContext(scene))[0]
                        result = await registry.dispatch(replace(message(clock), scene=scene, command=command))
                        self.assertIsInstance(result[0], Reply)
                        self.assertIn('12.5°C～25°C', result[0].text)
                    self.assertIs(provider.repository, repository)
                    self.assertIs(weather.repository, repository)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].url.path, '/weather/v1/daily/39.90/116.41')
                self.assertEqual(storage.usage('Asia/Shanghai', clock())[1], 1)
            finally:
                storage.close()
