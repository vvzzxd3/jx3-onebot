from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jx3_bot.config import Settings
from jx3_bot.errors import ConfigurationError
from jx3_bot.models import parse_command, group_message
from jx3_bot.weather import WeatherAuth
from jx3_bot.__main__ import instance_lock, main, stop_signal_handlers
from support import Clock, event, settings


class ConfigModelTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name)/'synthetic.pem'
        key = Ed25519PrivateKey.generate()
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.env = {'QQ_APP_ID':'test-app','QQ_CLIENT_SECRET':'synthetic-secret',
                    'QWEATHER_API_HOST':'https://test.qweatherapi.com',
                    'QWEATHER_DEVELOPER_ID':'synthetic-developer','QWEATHER_PROJECT_ID':'synthetic-project',
                    'QWEATHER_CREDENTIAL_ID':'synthetic-kid','QWEATHER_PRIVATE_KEY_FILE':str(path)}

    def test_valid_config_and_secret_repr(self):
        value = Settings.from_env(self.env)
        self.assertEqual(value.monthly_limit, 50000)
        self.assertEqual(value.usage_timezone, 'Asia/Shanghai')
        self.assertEqual(value.developer_id, 'synthetic-developer')
        self.assertEqual(value.project_id, 'synthetic-project')
        self.assertEqual(value.credential_id, 'synthetic-kid')
        self.assertNotIn('synthetic-secret', repr(value))
        self.assertNotIn(self.env['QWEATHER_PRIVATE_KEY_FILE'], repr(value))
        self.assertNotIn('api_key', Settings.__dataclass_fields__)
        self.assertEqual(Settings.from_env({**self.env,'QWEATHER_MONTHLY_REQUEST_LIMIT':'0'}).monthly_limit, 0)

    def test_legacy_key_cannot_replace_required_jwt(self):
        env = {key:value for key,value in self.env.items() if key in ('QQ_APP_ID','QQ_CLIENT_SECRET','QWEATHER_API_HOST')}
        env['QWEATHER_API_KEY'] = 'synthetic-unused-legacy-value'
        with self.assertRaisesRegex(ConfigurationError, 'JWT settings'):
            Settings.from_env(env)
        # Unknown legacy variables do not become an alternative credential or conflict with JWT.
        value = Settings.from_env({**self.env,'QWEATHER_API_KEY':'synthetic-unused-legacy-value'})
        self.assertEqual(value.project_id, 'synthetic-project')
        self.assertNotIn('synthetic-unused-legacy-value', repr(value))

    def test_invalid_settings_fail_closed(self):
        for changes in ({'QQ_APP_ID':''}, {'QWEATHER_API_HOST':'http://x.invalid'},
                {'QWEATHER_API_HOST':'https://key@x.invalid'}, {'QWEATHER_API_HOST':'https://x.invalid/path'},
                {'QWEATHER_API_HOST':'https://x.invalid:not-a-port'}, {'QWEATHER_API_HOST':'https://[invalid'},
                {'QWEATHER_MONTHLY_REQUEST_LIMIT':'-1'}, {'QWEATHER_USAGE_TIMEZONE':'No/Zone'},
                {'QWEATHER_FORECAST_DAYS':'11'}, {'QWEATHER_DEVELOPER_ID':''},
                {'QWEATHER_PROJECT_ID':''}, {'QWEATHER_CREDENTIAL_ID':''}, {'QWEATHER_PRIVATE_KEY_FILE':''}):
            with self.subTest(changes=changes), self.assertRaises(ConfigurationError):
                Settings.from_env({**self.env,**changes})

    def test_exact_command_matching(self):
        self.assertEqual(parse_command(' 天气 北京 ').argument, '北京')
        self.assertEqual(parse_command('天气').kind, 'weather')
        self.assertEqual(parse_command('天气城市 删除').kind, 'city')
        self.assertEqual(parse_command('天气额度').kind, 'usage')
        for text in ('今天天气很好','天气预报','ai 天气 北京','天气北京','/天气 北京',''):
            self.assertIsNone(parse_command(text), text)
        for text in ('天气 ' + 'a'*81, '天气 <@123>', '天气 北京\n朝阳'):
            self.assertEqual(parse_command(text).kind, 'invalid')

    def test_event_identity_and_expiry(self):
        frame = event(self.clock)
        value = group_message('test-app', frame, self.clock())
        self.assertEqual(value.message_id, 'm1')
        self.assertNotEqual(value.message_id, frame['id'])
        self.assertIsNone(group_message('test-app', event(self.clock,content='普通聊天'), self.clock()))
        frame['d']['author']['bot'] = True
        self.assertIsNone(group_message('test-app', frame, self.clock()))
        frame = event(self.clock)
        self.assertIsNone(group_message('test-app', frame, self.clock()+300))
        del frame['d']['author']['member_openid']
        self.assertIsNone(group_message('test-app', frame, self.clock()))

    def test_real_ed25519_sign_verify_with_ephemeral_key(self):
        with tempfile.TemporaryDirectory() as temp:
            key = Ed25519PrivateKey.generate()
            path = Path(temp)/'synthetic.pem'
            path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            s = settings(Path(temp)/'unused.sqlite', developer_id='synthetic-developer',
                         project_id='synthetic-project', credential_id='synthetic-kid', private_key_file=str(path))
            token = WeatherAuth(s,self.clock).headers()['Authorization'].removeprefix('Bearer ')
            payload = jwt.decode(token,key.public_key(),algorithms=['EdDSA'],issuer='synthetic-developer',
                                 options={'verify_exp':False,'verify_iat':False})
            self.assertEqual(payload['sub'], 'synthetic-project')
            self.assertEqual(jwt.get_unverified_header(token)['kid'], 'synthetic-kid')
            self.assertEqual(payload['exp']-int(self.clock()), 900)

    def test_missing_key_is_safe_configuration_error(self):
        s = settings(Path('unused'),private_key_file='/nonexistent/synthetic-key')
        with self.assertRaises(ConfigurationError) as error:
            WeatherAuth(s).headers()
        self.assertNotIn('/nonexistent', str(error.exception))

    def test_instance_lock_prevents_second_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'test.sqlite'
            with instance_lock(path):
                with self.assertRaises(ConfigurationError):
                    with instance_lock(path): pass

    def test_windows_instance_lock_uses_msvcrt_and_releases(self):
        calls = []
        backend = SimpleNamespace(
            LK_NBLCK=1,
            LK_UNLCK=2,
            locking=lambda _fd, mode, size: calls.append((mode, size)),
        )
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(sys, "platform", "win32"), \
                patch.dict(sys.modules, {"msvcrt": backend}):
            path = Path(temp) / "test.sqlite"
            with instance_lock(path):
                self.assertEqual(path.with_suffix(".lock").read_bytes(), b"\0")
        self.assertEqual(calls, [(backend.LK_NBLCK, 1), (backend.LK_UNLCK, 1)])

    def test_signal_handlers_fall_back_and_restore_on_windows_loop(self):
        loop = MagicMock()
        loop.add_signal_handler.side_effect = NotImplementedError
        stop = MagicMock()
        installed = {}

        def replace_handler(signum, handler):
            previous = installed.get(signum, f"previous-{signum}")
            installed[signum] = handler
            return previous

        with patch("jx3_bot.__main__.signal.signal", side_effect=replace_handler):
            with stop_signal_handlers(loop, stop):
                current = list(installed.values())
                current[0](None, None)
                loop.call_soon_threadsafe.assert_called_once_with(stop.set)
            self.assertTrue(all(str(handler).startswith("previous-") for handler in installed.values()))
        loop.remove_signal_handler.assert_not_called()

    def test_cli_help_and_check_config_never_connect(self):
        with patch('jx3_bot.__main__.load_dotenv'), patch.dict('os.environ', self.env, clear=True), patch('httpx.AsyncClient') as http:
            self.assertEqual(main(['check-config']), 0)
            http.assert_not_called()
