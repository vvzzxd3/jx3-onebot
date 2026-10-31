"""Offline-only QQ route contract tests; no network, database or real QQ client."""
import asyncio
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch

import httpx

from jx3_bot.core.models import Reply
from jx3_bot.modules.api.service import ServerStateService
from jx3_bot.errors import ConfigurationError
from jx3_bot.integrations.qq.events import business_event, match_message
from jx3_bot.integrations.qq.models import (GroupMessageEvent, IncomingMessage,
                                             PrivateMessageEvent, ReceivedMessage)
from jx3_bot.routing import Inject, RouteRegistry, build_registry, discover_routes, on_group, on_private


def received(event_type="GROUP_MESSAGE_CREATE", content="天气 北京", **overrides):
    group = event_type != "C2C_MESSAGE_CREATE"
    attrs = dict(app_id="test", scene="group" if group else "c2c",
                 conversation_id="g" if group else "u", user_id="u", message_id="mid",
                 timestamp=100.0, event_type=event_type, content=content)
    attrs.update(overrides)
    return ReceivedMessage(**attrs)


def selected(registry, item):
    decision = match_message(item, 101.0, registry)
    assert decision.message is not None, decision.reason
    return decision.message


class RoutingTests(unittest.TestCase):
    def test_keyword_prefix_and_arguments(self):
        seen = []

        @on_group(match="天气")
        async def weather(event: GroupMessageEvent, args: str):
            seen.append((event, args))
            return Reply("天气", args)

        plain = build_registry([weather])
        for text, expected in (("天气", ""), ("天气   ", ""), ("天气 北京  ", "北京")):
            item = received(content=text)
            self.assertEqual(asyncio.run(plain.dispatch(selected(plain, item), received=item))[0].body, expected)
        self.assertEqual([args for _, args in seen], ["", "", "北京"])
        self.assertIsInstance(seen[0][0], GroupMessageEvent)
        for text in ("天气北京", "天气预报", "/天气", "X天气"):
            self.assertEqual(plain.match_received(received(content=text)), ())
        prefixed = build_registry([weather], command_prefix="/")
        self.assertEqual(prefixed.match_received(received(content="天气 北京")), ())
        self.assertEqual(prefixed.match_received(received(content="/天气 北京"))[0].argument, "北京")
        self.assertIsInstance(build_registry([weather], command_prefix="x" * 16), RouteRegistry)
        with self.assertRaises(ConfigurationError):
            build_registry([weather], command_prefix="x" * 17)

    def test_event_types_and_safe_immutable_projection(self):
        @on_group(match="天气", at_me=True)
        @on_private(match="天气")
        async def weather(event: GroupMessageEvent | PrivateMessageEvent, args: str):
            return str(event.user_id) + args

        registry = build_registry([weather])
        self.assertEqual(registry.match_received(received()), ())
        group = received("GROUP_AT_MESSAGE_CREATE")
        self.assertTrue(business_event(group).at_me)
        self.assertEqual(registry.match_received(group)[0].argument, "北京")
        private = received("C2C_MESSAGE_CREATE")
        obj = business_event(private)
        self.assertIsInstance(obj, PrivateMessageEvent)
        self.assertFalse(hasattr(obj, "at_me"))
        self.assertFalse(hasattr(obj, "raw_payload"))
        self.assertEqual(obj.user_openid, "u")
        with self.assertRaises(FrozenInstanceError):
            obj.user_id = "other"
        self.assertEqual(asyncio.run(registry.dispatch(selected(registry, private), received=private)), ["u北京"])
        self.assertEqual(registry.match_received(received("OTHER_EVENT")), ())
        self.assertEqual(registry.match_received(received(content="天气 北京", truncated=True)), ())
        self.assertEqual(registry.match_received(received(content="天气 北京", content_format="non_text")), ())

    def test_stacked_handler_called_once_and_same_priority_not_blocked(self):
        calls = []

        @on_group(match="天气", priority=3)
        @on_group(priority=3)
        async def first(event: GroupMessageEvent):
            calls.append("first")
            return "one"

        @on_group(priority=3)
        async def second():
            calls.append("second")
            return "two"

        @on_group(priority=4)
        async def third():
            calls.append("third")
            return "three"

        registry = build_registry([third, second, first])
        item = received()
        msg = selected(registry, item)
        self.assertEqual(len(msg.commands), 2)
        self.assertEqual(asyncio.run(registry.dispatch(msg, received=item)), ["one", "two"])
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual([c.matcher for c in registry.match_received(item)], [c.matcher for c in msg.commands])

    def test_unconditional_text_without_args_and_dispatch_fails_closed(self):
        @on_private()
        async def catch(event: PrivateMessageEvent):
            return event.content

        registry = build_registry([catch])
        item = received("C2C_MESSAGE_CREATE", "hello")
        msg = selected(registry, item)
        with self.assertRaises(ConfigurationError):
            asyncio.run(registry.dispatch(msg))
        with self.assertRaises(ConfigurationError):
            asyncio.run(registry.dispatch(msg, received=received("C2C_MESSAGE_CREATE", "hello", message_id="other")))
        self.assertEqual(asyncio.run(registry.dispatch(msg, received=item)), ["hello"])
        self.assertEqual(registry.match_received(received("C2C_MESSAGE_CREATE", "hello", truncated=True)), ())

    def test_service_injection_and_bad_return(self):
        class Service:
            value = "available"
        service = Service()

        @on_group(match="天气")
        async def query(args: str, service: Service = Inject("weather")):
            return service.value + args

        with self.assertRaises(ConfigurationError):
            build_registry([query])
        registry = build_registry([query], services={"weather": service})
        item = received()
        self.assertEqual(asyncio.run(registry.dispatch(selected(registry, item), received=item)), ["available北京"])
        @on_group()
        async def invalid():
            return object()
        invalid_routes = build_registry([invalid])
        with self.assertRaises(ConfigurationError):
            asyncio.run(invalid_routes.dispatch(selected(invalid_routes, item), received=item))

    def test_invalid_registration(self):
        with self.assertRaises(ConfigurationError):
            on_group(at_me=None)
        with self.assertRaises(ConfigurationError):
            on_group(match="天气 北京")
        with self.assertRaises(ConfigurationError):
            on_group(priority=True)
        with self.assertRaises(TypeError):
            on_private(at_me=True)
        with self.assertRaises(ConfigurationError):
            @on_group()
            def sync():
                pass

        @on_group(match="天气")
        async def no_annotation(event):
            pass
        with self.assertRaises(ConfigurationError):
            build_registry([no_annotation])
        @on_group()
        async def invalid_args(args: str):
            pass
        with self.assertRaises(ConfigurationError):
            build_registry([invalid_args])
        @on_group()
        async def duplicate():
            pass
        with self.assertRaises(ConfigurationError):
            build_registry([duplicate, duplicate])
        @on_group()
        @on_private()
        async def incompatible(event: GroupMessageEvent):
            pass
        with self.assertRaises(ConfigurationError):
            build_registry([incompatible])

    def test_text_limits_exclusion_and_too_many_handlers(self):
        @on_group(match="天气")
        async def query(args: str):
            return args

        registry = build_registry([query])
        for attrs in ({"bot_author": True}, {"self_author": True}, {"timestamp": None},
                      {"content_format": "non_text"}, {"truncated": True}):
            item = received(**attrs)
            self.assertIsNone(match_message(item, 101, registry).message)
        self.assertEqual(registry.match_received(received(content="天气 " + "a" * 1025)), ())
        self.assertEqual(registry.match_received(received(content="天气 " + "a" * 4097)), ())
        self.assertEqual(registry.match_received(received(content="天气 \x00")), ())
        handlers = []
        for index in range(9):
            async def handler():
                return None
            handler.__name__ = f"handler{index}"
            handler.__qualname__ = f"handler{index}"
            handlers.append(on_group(block=False)(handler))
        crowded = build_registry(handlers)
        with self.assertRaises(ConfigurationError):
            crowded.match_received(received(content="hi"))

    def test_discovered_weather_group_at_and_private(self):
        from jx3_bot.modules.weather.service import WeatherService
        from jx3_bot.modules.weather.rendering import render_result

        class OfflineWeather(WeatherService):
            def __init__(self):
                self.calls = []

            async def handle_result(self, message):
                self.calls.append((message.scene, message.command.kind, message.command.argument))
                return Reply("test", message.command.argument)

        service = OfflineWeather()
        registry = discover_routes(services={"weather": service, "server_state": ServerStateService(None)})
        cases = (("GROUP_MESSAGE_CREATE", "天气", "weather", ""),
                 ("GROUP_AT_MESSAGE_CREATE", "天气 北京", "weather", "北京"),
                 ("C2C_MESSAGE_CREATE", "天气城市 北京", "city", "北京"),
                 ("GROUP_MESSAGE_CREATE", "天气额度", "usage", ""))
        for event_type, content, kind, argument in cases:
            item = received(event_type, content)
            results = asyncio.run(registry.dispatch(selected(registry, item), received=item))
            self.assertEqual(results[0].body, argument)
            self.assertEqual(service.calls[-1], ("c2c" if event_type.startswith("C2C") else "group", kind, argument))
        item = received(content="天气额度 多余参数")
        invalid = asyncio.run(registry.dispatch(selected(registry, item), received=item))[0]
        self.assertEqual(invalid, render_result("invalid"))
        self.assertEqual(len(service.calls), len(cases))
        self.assertEqual(registry.match_received(received(content="天气预报")), ())
        prefixed = discover_routes(services={"weather": service, "server_state": ServerStateService(None)}, command_prefix="/")
        self.assertEqual(prefixed.match_received(received(content="天气")), ())
        self.assertEqual(prefixed.match_received(received(content="/天气"))[0].argument, "")

    def test_discovery_existing_modules(self):
        # Empty business directory is valid independently of weather migration.
        with patch("jx3_bot.routing.pkgutil.iter_modules", return_value=[]):
            registry = discover_routes(services={})
        self.assertIsInstance(registry, RouteRegistry)
        self.assertEqual(registry.match_received(received()), ())


class ServerStateRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        def handle(request):
            self.requests.append(request)
            return httpx.Response(200, json=[{
                "server_name": "青梅煮酒", "connect_state": False,
            }])

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.service = ServerStateService(self.http)

    async def asyncTearDown(self):
        await self.http.aclose()

    def registry(self, *, discovered=False, prefix=""):
        from jx3_bot.modules.api.handlers import query
        from jx3_bot.modules.weather.service import WeatherService

        services = {"server_state": self.service}
        if discovered:
            # Weather is discovered but never invoked by these server-state cases.
            services["weather"] = WeatherService.__new__(WeatherService)
            return discover_routes(services=services, command_prefix=prefix)
        return build_registry([query], services=services, command_prefix=prefix)

    async def test_build_and_discovery_dispatch_all_three_supported_events(self):
        for discovered in (False, True):
            registry = self.registry(discovered=discovered)
            for event_type in ("GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"):
                with self.subTest(discovered=discovered, event_type=event_type):
                    item = received(event_type, "开服 青梅煮酒")
                    before = len(self.requests)
                    commands = registry.match_received(item)
                    self.assertEqual(len(commands), 1)
                    self.assertEqual(commands[0].argument, "青梅煮酒")
                    results = await registry.dispatch(selected(registry, item), received=item)
                    self.assertEqual(results, ["青梅煮酒维护中\n维护时间：不可用"])
                    self.assertEqual(len(self.requests), before + 1)
        self.assertTrue(all(request.method == "GET" for request in self.requests))
        self.assertTrue(all(str(request.url) ==
                            "https://spider2.jx3box.com/api/spider/server/server_state"
                            for request in self.requests))

    async def test_build_and_discovery_keyword_boundaries_and_command_prefix(self):
        for discovered in (False, True):
            for prefix in ("", "/"):
                with self.subTest(discovered=discovered, prefix=prefix):
                    registry = self.registry(discovered=discovered, prefix=prefix)
                    for text in ("开服青梅煮酒", "开服状态", "X开服", "我想开服", "普通聊天"):
                        self.assertEqual(registry.match_received(received(content=prefix + text)), ())
                    wrong_prefix = "开服 青梅煮酒" if prefix else "/开服 青梅煮酒"
                    self.assertEqual(registry.match_received(received(content=wrong_prefix)), ())
                    item = received(content=prefix + "开服")
                    self.assertEqual(registry.match_received(item)[0].argument, "")
                    results = await registry.dispatch(selected(registry, item), received=item)
                    self.assertEqual(results, ["未找到服务器“绝代天骄”，请检查服务器名称"])
                    item = received(content=prefix + "开服 青梅煮酒")
                    self.assertEqual(await registry.dispatch(selected(registry, item), received=item),
                                     ["青梅煮酒维护中\n维护时间：不可用"])
        self.assertEqual(len(self.requests), 8)

    def test_build_and_discovery_reject_missing_or_wrong_server_state_injection(self):
        from jx3_bot.modules.api.handlers import query
        from jx3_bot.modules.weather.service import WeatherService

        weather = WeatherService.__new__(WeatherService)
        for supplied in ({}, {"server_state": object()}):
            with self.subTest(supplied=supplied):
                with self.assertRaises(ConfigurationError):
                    build_registry([query], services=supplied)
                with self.assertRaises(ConfigurationError):
                    discover_routes(services={"weather": weather, **supplied})
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
