import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from jx3_bot.application import Application
from jx3_bot.cities import import_csv
from jx3_bot.config import Settings
from jx3_bot.errors import ConfigurationError, ProviderError, StorageError
from jx3_bot.gateway import Gateway, Reconnect
from jx3_bot.matching import MatchContext, MatcherRegistry, to_me, weather_registry
from jx3_bot.models import Command, GroupMessage, inspect_message
from jx3_bot.observability import Observer
from jx3_bot.qq_http import QQHTTP, QQTokens
from jx3_bot.replies import Reply, message_payload, render_result, templates, validate_payload, validate_templates
from jx3_bot.store import Store
from jx3_bot.weather import WeatherClient, WeatherService
from support import Clock, csv_bytes, daily_body, event, message, settings, weather_auth, enable_chat
from test_gateway import FakeSocket, hello


def private_event(clock, content='天气 北京', seq=1, id='m1', user='g'):
    frame = event(clock, seq=seq, id=id, event_type='C2C_MESSAGE_CREATE', content=content)
    frame['d'].pop('group_openid')
    frame['d']['author'] = {'user_openid': user}
    return frame


class MatcherTests(unittest.IsolatedAsyncioTestCase):
    def test_command_alias_whitespace_and_no_false_positive(self):
        registry = MatcherRegistry()
        registry.on_command('game.query', '副本', aliases=('raid',))
        ctx = MatchContext('group')
        for text, arg in (('副本 达摩洞', '达摩洞'), ('raid test', 'test'), ('副本', ''), (' 副本\t达摩洞 ', '达摩洞')):
            result = registry.match(text, ctx)[0]
            self.assertEqual(result.matcher, 'game.query')
            self.assertEqual(result.argument, arg)
        for text in ('今天打副本', '副本攻略', '/副本 达摩洞', None, ''):
            self.assertEqual(registry.match(text, ctx), ())

    def test_priority_block_stops_next_tier_not_same_tier(self):
        registry = MatcherRegistry()
        registry.on_fullmatch('same.one', 'hello', priority=10, block=True)
        registry.on_fullmatch('same.two', 'hello', priority=10, block=False)
        registry.on_fullmatch('later', 'hello', priority=20)
        registry.on_fullmatch('first', 'hello', priority=5, block=False)
        self.assertEqual([c.matcher for c in registry.match('hello', MatchContext('group'))], ['first', 'same.one', 'same.two'])
        self.assertEqual(registry.match('not hello', MatchContext('group')), ())

    def test_scene_rule_to_me_and_regex_search(self):
        registry = MatcherRegistry()
        registry.on_regex('group.query', r'副本\s+\w+', scenes={'group'}, rule=to_me)
        registry.on_fullmatch('private.help', ('帮助', 'help'), scenes={'c2c'})
        self.assertEqual(registry.match('前缀 副本 达摩洞 后缀', MatchContext('group')), ())
        self.assertEqual(registry.match('前缀 副本 达摩洞 后缀', MatchContext('group', to_me=True))[0].argument, '副本 达摩洞')
        self.assertEqual(registry.match('帮助', MatchContext('group')), ())
        self.assertEqual(registry.match('帮助', MatchContext('c2c'))[0].matcher, 'private.help')

    def test_invalid_argument_is_not_retained(self):
        registry = weather_registry()
        for text in ('天气 <@123>', '天气 ' + 'x'*81, '天气 北京\n朝阳', '天气额度 北京'):
            result = registry.match(text, MatchContext('c2c'))[0]
            self.assertEqual(result.kind, 'invalid')
            self.assertEqual(result.argument, '')
        self.assertEqual(registry.match('天气 ' + 'x'*5000, MatchContext('group')), ())

    def test_invalid_registry_configuration_and_match_bound(self):
        registry = MatcherRegistry()
        registry.on_fullmatch('one', 'hello')
        with self.assertRaises(ConfigurationError): registry.on_fullmatch('one', 'hello')
        for options in ({'scenes':{'guild'}}, {'priority':0}, {'priority':True}):
            with self.assertRaises(ConfigurationError): MatcherRegistry().on_fullmatch('x', 'hello', **options)
        for i in range(9):
            if i: registry.on_fullmatch(f'x{i}', 'hello', block=False)
        with self.assertRaises(ConfigurationError): registry.match('hello', MatchContext('group'))

    async def test_decorator_dispatch_multiple_results_and_legacy(self):
        registry = MatcherRegistry()
        matcher = registry.on_command('game.query', '副本', block=False)
        @matcher.handle
        async def handler(msg): return Reply('副本结果', msg.command.argument)
        with self.assertRaises(ConfigurationError): matcher.handle(handler)
        msg = replace(message(Clock()), command=Command('game.query', '达摩洞', 'game.query'))
        self.assertEqual((await registry.dispatch(msg))[0].body, '达摩洞')
        weather = AsyncMock(return_value=render_result('city_missing'))
        legacy = await weather_registry(weather).dispatch(message(Clock(), kind='city'))
        self.assertIn('默认城市', legacy[0].title)
        invalid = await weather_registry(weather).dispatch(message(Clock(), kind='invalid'))
        self.assertEqual(len(invalid), 1)

    async def test_removed_persisted_matcher_is_friendly(self):
        msg = replace(message(Clock()), command=Command('old', '', 'removed'))
        self.assertIn('功能配置已变更', (await weather_registry().dispatch(msg))[0].text)

    def test_opt_in_on_message(self):
        registry = MatcherRegistry()
        self.assertEqual(registry.match('你好', MatchContext('group')), ())
        registry.on_message('echo', scenes={'c2c'}, priority=100)
        self.assertEqual(registry.match('你好', MatchContext('group')), ())
        self.assertEqual(registry.match('你好', MatchContext('c2c'))[0].argument, '你好')


class ReplyTests(unittest.TestCase):
    def test_every_result_template_renders_text_and_markdown(self):
        values = dict(label='北京', forecast='2026-10-10 晴', updated='获取于 10-10 20:00', sources='https://example.invalid/source',
                      month='2026-10', count=10, limit=50000, remaining=49990, zone='Asia/Shanghai', scope='本次单聊', query='北京', choices='1. 北京')
        self.assertEqual(len(templates()), 19)
        for name in templates():
            with self.subTest(name=name):
                reply = render_result(name, **values)
                self.assertTrue(reply.title)
                self.assertNotIn('{query}', reply.text)
                self.assertEqual(message_payload(reply)['msg_type'], 0)
                markdown = message_payload(reply, 'markdown')
                self.assertEqual(markdown['msg_type'], 2)
                self.assertNotIn('content', markdown)
                self.assertTrue(markdown['markdown']['content'].startswith('## '))
                validate_payload(markdown)

    def test_dynamic_markdown_escaped_and_mentions_removed(self):
        reply = render_result('city_current', scope='本群', query='[标题](https://evil.invalid)\n# 注入 @all <script>')
        payload = message_payload(reply, 'markdown')['markdown']['content']
        self.assertIn(r'\[标题\]\(https://evil\.invalid\)', payload)
        self.assertIn(r'\# 注入', payload)
        self.assertNotIn('@all', payload)
        self.assertNotIn('<script>', payload)
        for mode in ('text','markdown'):
            direct=message_payload(Reply('标题','<@all> [x](https://evil.invalid)'),mode)
            self.assertNotIn('@all',json.dumps(direct))
            self.assertNotIn('<@',json.dumps(direct))
        self.assertFalse(message_payload('普通结果','markdown')['markdown']['content'].startswith('## '))

    def test_preflight_size_fallback_and_no_silent_truncation(self):
        reply = Reply('测试', '.'*1000)
        self.assertEqual(message_payload(reply, 'markdown')['msg_type'], 0)
        for reply in (Reply('', ''), Reply('标题', 'x'*1800)):
            with self.assertRaises(ConfigurationError): message_payload(reply)
        with self.assertRaises(ConfigurationError): message_payload(Reply('title','body'), 'ark')

    def test_payload_whitelist(self):
        for payload in ({'msg_type':3,'ark':{}}, {'msg_type':0,'content':'text','msg_id':'injected'},
                        {'msg_type':2,'markdown':{'custom_template_id':'deprecated'}},
                        {'msg_type':2,'markdown':{'content':'ok','url':'not allowed'}},
                        {'msg_type':False,'content':'text'}, {'msg_type':0,'content':None}):
            with self.subTest(payload=payload), self.assertRaises(ConfigurationError): validate_payload(payload)

    def test_template_validation_rejects_missing_and_wrong_placeholders(self):
        validate_templates()
        for name, field in (('city_missing','query'),('forecast','scope'),('city_current','query.__class__')):
            malformed={k:dict(v) for k,v in templates().items()}
            malformed[name]['body']='{' + field + '}'
            with patch('jx3_bot.replies.templates',return_value=malformed):
                with self.assertRaises(ConfigurationError): validate_templates()
        with patch('jx3_bot.replies.templates',return_value={}):
            with self.assertRaises(ConfigurationError): validate_templates()

    def test_independent_feature_template_module_and_safe_paths(self):
        specs={'raid':{'title':'副本查询','body':'结果：{boss}','footer':'来源：{source}'}}
        with patch('jx3_bot.replies.templates',return_value=specs) as loader:
            validate_templates('game')
            reply=render_result('raid',module='game',boss='达摩洞',source='测试数据')
            self.assertIn('达摩洞',reply.body)
            loader.assert_called_with('game')
        for module in ('../weather','/tmp/secrets','.env',None):
            with self.assertRaises(ConfigurationError): templates(module)

    def test_default_and_markdown_config(self):
        env = {'QQ_APP_ID':'app','QQ_CLIENT_SECRET':'synthetic','QWEATHER_API_HOST':'https://synthetic.qweatherapi.com',
               'QWEATHER_DEVELOPER_ID':'d','QWEATHER_PROJECT_ID':'p','QWEATHER_CREDENTIAL_ID':'c','QWEATHER_PRIVATE_KEY_FILE':'synthetic.pem'}
        self.assertEqual(Settings.from_env(env).reply_format, 'text')
        self.assertEqual(Settings.from_env({**env,'QQ_REPLY_FORMAT':'markdown'}).reply_format, 'markdown')
        with self.assertRaises(ConfigurationError): Settings.from_env({**env,'QQ_REPLY_FORMAT':'ark'})


class SceneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'db.sqlite'
        self.store = Store(self.path)
        enable_chat(self.store)
        self.clock = Clock()
        self.s = settings(self.path)
        self.store.ready('s', 0)

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_private_identity_expiry_bot_and_timestamp(self):
        frame = private_event(self.clock)
        msg = inspect_message('test-app', frame, self.clock()).message
        self.assertEqual((msg.scene,msg.conversation_id,msg.user_id), ('c2c','g','g'))
        self.assertNotEqual(msg.key, message(self.clock).key)
        self.assertEqual(GroupMessage.from_dict(msg.to_dict()), msg)
        for change, reason in (({'timestamp':float('nan')},'invalid_timestamp'), ({'timestamp':self.clock()+31},'future_timestamp'),
                               ({'timestamp':self.clock()-300},'expired')):
            altered = private_event(self.clock); altered['d'].update(change)
            self.assertEqual(inspect_message('test-app', altered,self.clock()).reason,reason)
        frame['d']['author']['bot'] = True
        self.assertEqual(inspect_message('test-app', frame,self.clock()).reason,'bot_author')
        frame = private_event(self.clock); frame['d']['author']={'member_openid':'wrong-scene'}
        self.assertEqual(inspect_message('test-app', frame,self.clock()).reason,'missing_identity')

    def test_group_and_c2c_same_ids_dedup_isolation_and_fifo(self):
        group = message(self.clock, group='g',user='g')
        private = inspect_message('test-app',private_event(self.clock),self.clock()).message
        self.assertTrue(self.store.accept('s',1,group,self.clock()))
        self.assertTrue(self.store.accept('s',2,private,self.clock()))
        self.assertFalse(self.store.accept('s',3,private,self.clock()))
        later = replace(private,message_id='m2')
        self.assertTrue(self.store.accept('s',4,later,self.clock()))
        self.assertEqual(self.store.claim_command(self.clock()).scene,'group')
        self.assertEqual(self.store.claim_command(self.clock()).scene,'c2c')
        self.assertIsNone(self.store.claim_command(self.clock()))
        self.store.finish_command(private)
        self.assertEqual(self.store.claim_command(self.clock()).message_id,'m2')

    async def test_preferences_isolated_set_show_delete_and_no_network(self):
        client = AsyncMock()
        service = WeatherService(self.store,client,self.s,self.clock)
        group = message(self.clock,'北京',kind='city',group='g',user='g')
        private = replace(group,scene='c2c',command=Command('city','上海'))
        self.assertIn('本次单聊', await service.handle(private))
        await service.handle(group)
        self.assertIn('北京', await service.handle(replace(group,command=Command('city'))))
        self.assertIn('上海', await service.handle(replace(private,command=Command('city'))))
        await service.handle(replace(private,command=Command('city','删除')))
        self.assertEqual(self.store.preference('test-app','g','g'),'北京')
        self.assertIsNone(self.store.preference('test-app','g','g','c2c'))
        client.geo.assert_not_called(); client.daily.assert_not_called()

    async def test_c2c_full_gateway_to_weather_to_markdown_reply(self):
        import_csv(self.store,csv_bytes())
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path.endswith('getAppAccessToken'):
                return httpx.Response(200,json={'access_token':'synthetic-token','expires_in':7200})
            if '/weather/v1/' in request.url.path:
                return httpx.Response(200,json=daily_body())
            return httpx.Response(200,json={'id':'synthetic-reply'})
        observer = Observer()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            qq = QQHTTP(http,QQTokens(http,'test-app','synthetic',observer=observer))
            service = WeatherService(self.store,WeatherClient(http,self.store,self.s,auth=weather_auth(self.s,self.clock),clock=self.clock),self.s,self.clock)
            registry = weather_registry(service.handle_result)
            gateway = Gateway(qq,self.store,'test-app',clock=self.clock,registry=registry,observer=observer)
            frames = [hello(1000),private_event(self.clock,seq=1),private_event(self.clock,seq=2),
                      private_event(self.clock,content='普通聊天',seq=3,id='ordinary'),{'op':9,'d':False}]
            with self.assertRaises(Reconnect): await gateway.connection(FakeSocket(frames))
            app = Application(self.store,service,qq,gateway,clock=self.clock,registry=registry,reply_format='markdown',observer=observer)
            await app.process_one(); await app.send_one()
            self.assertTrue(await app.process_one())  # Ordinary message logs after archive, without a reply.
            self.assertFalse(await app.process_one()); self.assertFalse(await app.send_one())
        posts = [r for r in requests if '/v2/users/' in r.url.path]
        self.assertEqual(len(posts),1)
        self.assertEqual(posts[0].url.path,'/v2/users/g/messages')
        payload = json.loads(posts[0].content)
        self.assertEqual(payload['msg_type'],2)
        self.assertEqual((payload['msg_id'],payload['msg_seq']),('m1',1))
        self.assertIn('每日天气',payload['markdown']['content'])
        self.assertNotIn('content',payload)
        self.assertEqual(self.store.usage('Asia/Shanghai',self.clock())[1],1)
        self.assertIn('普通聊天','\n'.join(self.store.db.iterdump()))
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM chat_user_messages')[0][0], 3)
        msg = inspect_message('test-app',private_event(self.clock),self.clock()).message
        self.assertEqual(observer.frame_trace('test-app',private_event(self.clock)),observer.trace(*msg.key))

    async def test_multiple_handlers_one_outbox_and_no_reply_handler(self):
        registry = MatcherRegistry()
        first = registry.on_fullmatch('first','帮助',block=False)
        second = registry.on_fullmatch('second','帮助',block=True)
        first.handle(AsyncMock(return_value=Reply('一','第一结果')))
        second.handle(AsyncMock(return_value=Reply('二','第二结果')))
        msg = inspect_message('test-app',private_event(self.clock,'帮助'),self.clock(),registry).message
        self.store.accept('s',1,msg,self.clock())
        qq = AsyncMock()
        app = Application(self.store,Mock(),qq,Mock(),clock=self.clock,registry=registry)
        await app.process_one(); await app.send_one()
        payload = qq.reply.call_args.args[2]
        self.assertIn('第一结果',payload['content']); self.assertIn('第二结果',payload['content'])
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM outbox')[0][0],1)
        registry = MatcherRegistry(); registry.on_fullmatch('no.reply','静默').handle(AsyncMock(return_value=None))
        msg = inspect_message('test-app',private_event(self.clock,'静默',id='m2'),self.clock(),registry).message
        self.store.accept('s',2,msg,self.clock()); app.registry=registry
        await app.process_one()
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM outbox')[0][0],1)
        self.assertFalse(await app.send_one())

    async def test_restart_preserves_fixed_markdown_and_uncertain_state(self):
        msg = inspect_message('test-app',private_event(self.clock),self.clock()).message
        self.store.accept('s',1,msg,self.clock())
        payload = message_payload(render_result('city_saved',scope='本次单聊',query='北京'),'markdown')
        self.store.plan_reply(msg,payload)
        self.store.close(); self.store=Store(self.path)
        qq = AsyncMock(); qq.reply.side_effect=ProviderError('synthetic-network')
        app = Application(self.store,Mock(),qq,Mock(),clock=self.clock,reply_format='text')
        await app.send_one()
        self.assertEqual(qq.reply.call_args.args[2],payload)
        self.store.recover()
        self.assertFalse(await app.send_one())
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0],'uncertain')
        self.clock.now += 301; self.store.cleanup(self.clock())
        row = self.store._read('SELECT content,payload FROM outbox')[0]
        self.assertEqual(row['content'],''); self.assertIsNone(row['payload'])

    async def test_rejected_markdown_never_fallback_posts(self):
        msg = inspect_message('test-app',private_event(self.clock),self.clock()).message
        self.store.accept('s',1,msg,self.clock())
        self.store.plan_reply(msg,message_payload(render_result('city_missing'),'markdown'))
        requests=[]
        def handler(request):
            requests.append(request)
            if request.url.path.endswith('getAppAccessToken'):
                return httpx.Response(200,json={'access_token':'synthetic','expires_in':7200})
            return httpx.Response(403,json={'code':304036})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            app = Application(self.store,Mock(),QQHTTP(http,QQTokens(http,'test-app','synthetic')),Mock(),clock=self.clock)
            await app.send_one(); self.assertFalse(await app.send_one())
        self.assertEqual(len([r for r in requests if '/v2/users/' in r.url.path]),1)
        self.assertEqual(self.store._read('SELECT state FROM outbox')[0][0],'failed')

    async def test_token_preflight_crossing_deadline_never_posts(self):
        for index, scene in enumerate(('group','c2c'),1):
            msg = replace(message(self.clock,id=f'expired-{index}'),scene=scene)
            self.store.accept('s',index,msg,self.clock())
            self.store.plan_reply(msg,message_payload(render_result('city_missing'),'markdown'))
            self.clock.now += 299
            requests=[]
            def handler(request):
                requests.append(request)
                self.clock.now += 2
                return httpx.Response(200,json={'access_token':'synthetic','expires_in':7200})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                app = Application(self.store,Mock(),QQHTTP(http,QQTokens(http,'test-app','synthetic')),Mock(),clock=self.clock)
                await app.send_one()
                self.assertFalse(await app.send_one())
            self.assertEqual(len(requests),1)
            self.assertTrue(requests[0].url.path.endswith('getAppAccessToken'))
            row=self.store._read('SELECT state,payload,content FROM outbox WHERE msg=?',(msg.message_id,))[0]
            self.assertEqual(row['state'],'expired'); self.assertIsNone(row['payload']); self.assertEqual(row['content'],'')

    async def test_multiple_large_results_do_not_stop_application(self):
        registry=MatcherRegistry()
        for name in ('one','two'):
            registry.on_fullmatch(name,'帮助',block=False).handle(AsyncMock(return_value=Reply(name,'x'*1000)))
        msg=inspect_message('test-app',private_event(self.clock,'帮助'),self.clock(),registry).message
        self.store.accept('s',1,msg,self.clock())
        qq=AsyncMock()
        app=Application(self.store,Mock(),qq,Mock(),clock=self.clock,registry=registry,reply_format='markdown')
        self.assertTrue(await app.process_one()); self.assertTrue(await app.send_one())
        self.assertIn('结果内容过长',qq.reply.call_args.args[2]['markdown']['content'])
        self.assertEqual(self.store._read('SELECT state FROM inbox')[0][0],'done')
        self.assertFalse(await app.process_one())

    async def test_geo_fields_not_in_markdown_outbox(self):
        import_csv(self.store,csv_bytes())
        msg = inspect_message('test-app',private_event(self.clock,'天气 用户自有输入'),self.clock()).message
        self.store.accept('s',1,msg,self.clock())
        geo = {'code':'200','location':[{'id':'GEO_ID_SECRET','name':'GEO_NAME_SECRET','lat':'40.12','lon':'118.12','tz':'Asia/Tokyo'}]}
        def handler(request): return httpx.Response(200,json=geo if '/geo/' in request.url.path else daily_body())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            service = WeatherService(self.store,WeatherClient(http,self.store,self.s,auth=weather_auth(self.s,self.clock),clock=self.clock),self.s,self.clock)
            app = Application(self.store,service,AsyncMock(),Mock(),clock=self.clock,registry=weather_registry(service.handle_result),reply_format='markdown')
            await app.process_one()
        dump = '\n'.join(self.store.db.iterdump())
        for marker in ('GEO_ID_SECRET','GEO_NAME_SECRET','118.12','Asia/Tokyo'):
            self.assertNotIn(marker,dump)
        self.assertIn('用户自有输入',dump)
        self.assertEqual(self.store._read('SELECT COUNT(*) FROM cache')[0][0],0)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'legacy.sqlite'
        self.clock = Clock()
        db = sqlite3.connect(self.path)
        db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE usage(month TEXT PRIMARY KEY,attempts INTEGER NOT NULL);
            CREATE TABLE preferences(app TEXT,grp TEXT,usr TEXT,input TEXT NOT NULL,PRIMARY KEY(app,grp,usr));
            CREATE TABLE inbox(app TEXT,grp TEXT,msg TEXT,payload TEXT,created REAL NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL,PRIMARY KEY(app,grp,msg));
            CREATE TABLE outbox(app TEXT,grp TEXT,msg TEXT,content TEXT NOT NULL,seq INTEGER NOT NULL DEFAULT 1,expires REAL NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(app,grp,msg),FOREIGN KEY(app,grp,msg) REFERENCES inbox(app,grp,msg) ON DELETE CASCADE);
        ''')
        db.executemany('INSERT INTO meta VALUES(?,?)', [('app_id',json.dumps('test-app')),('usage_timezone',json.dumps('Asia/Shanghai')),('gateway',json.dumps({'session_id':'s','seq':5})),('city_source',json.dumps({'version':'synthetic'}))])
        db.execute('INSERT INTO usage VALUES(?,?)',('2026-10',49999))
        db.execute('INSERT INTO preferences VALUES(?,?,?,?)',('test-app','g','u','北京'))
        for index,state in enumerate(('pending','done','done','done'),1):
            msg = message(self.clock,id=f'm{index}')
            payload = msg.to_dict(); payload.pop('scene'); payload.pop('commands'); payload['command'].pop('matcher')
            db.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?)',(*msg.key,json.dumps(payload) if state=='pending' else None,self.clock(),self.clock()+300,state))
        for index,state in enumerate(('pending','sending','uncertain'),2):
            db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?,?)',('test-app','g',f'm{index}',f'legacy-{state}',1,self.clock()+300,state,index-1))
        db.commit(); db.close()

    def test_migration_preserves_all_existing_state_and_is_idempotent(self):
        with_store = Store(self.path)
        try:
            self.assertEqual(with_store.usage('Asia/Shanghai',self.clock())[1],49999)
            self.assertEqual(with_store.preference('test-app','g','u'),'北京')
            self.assertIsNone(with_store.preference('test-app','g','u','c2c'))
            self.assertEqual(with_store.meta('gateway'),{'session_id':'s','seq':5})
            self.assertEqual(with_store.meta('city_source'),{'version':'synthetic'})
            self.assertEqual(with_store._read('PRAGMA foreign_key_check'),[])
            self.assertEqual(with_store.claim_command(self.clock()).scene,'group')
            rows = with_store._read('SELECT state,attempts,scene,content FROM outbox ORDER BY msg')
            self.assertEqual([r['state'] for r in rows],['pending','sending','uncertain'])
            self.assertEqual([r['attempts'] for r in rows],[1,2,3])
            self.assertTrue(all(r['scene']=='group' for r in rows))
            self.assertEqual(with_store.recover()['uncertain_replies'],1)
        finally: with_store.close()
        again=Store(self.path)
        try:
            self.assertEqual(again.usage('Asia/Shanghai',self.clock())[1],49999)
            self.assertEqual(again._read('SELECT COUNT(*) FROM outbox')[0][0],3)
            reply=again.claim_reply(self.clock())
            self.assertEqual((reply['scene'],reply['content'],reply['seq']),('group','legacy-pending',1))
        finally: again.close()

    def test_older_schema_without_attempts_is_migrated_atomically(self):
        db=sqlite3.connect(self.path); db.execute('ALTER TABLE outbox DROP COLUMN attempts'); db.commit(); db.close()
        store=Store(self.path)
        try:
            self.assertEqual([r[0] for r in store._read('SELECT attempts FROM outbox')],[0,0,0])
            self.assertEqual(store.usage('Asia/Shanghai',self.clock())[1],49999)
            self.assertEqual(store._read('PRAGMA foreign_key_check'),[])
        finally: store.close()

    def test_failed_migration_rolls_back_schema_and_data(self):
        db=sqlite3.connect(self.path); db.execute('CREATE TABLE preferences_v2(conflict TEXT)'); db.commit(); db.close()
        with self.assertRaises(StorageError): Store(self.path)
        db=sqlite3.connect(self.path)
        try:
            self.assertNotIn('scene',[r[1] for r in db.execute('PRAGMA table_info(inbox)')])
            self.assertEqual(db.execute('SELECT attempts FROM usage').fetchone()[0],49999)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],3)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='inbox_v2'").fetchone())
        finally: db.close()
