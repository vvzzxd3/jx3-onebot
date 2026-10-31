"""Offline server-state contract tests; all HTTP uses MockTransport."""
from datetime import datetime, timedelta, timezone
import unittest

import httpx

from jx3_bot.modules.api.service import SERVER_STATE_URL, ServerStateService


NAME = "青梅煮酒"
URL = "https://spider2.jx3box.com/api/spider/server/server_state"
UNAVAILABLE = "暂时无法查询开服状态，请稍后再试"


def row(name=NAME, state=True, timestamp=1744241100):
    return {"server_name": name, "connect_state": state, "maintain_time": timestamp}


class ServerStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.response = httpx.Response(200, json=[row()])

        def handle(request):
            self.requests.append(request)
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.service = ServerStateService(self.http)

    async def asyncTearDown(self):
        await self.http.aclose()

    def test_timestamp_is_independently_verified_in_beijing(self):
        beijing = timezone(timedelta(hours=8))
        # 1744238700 is 06:45, not 07:25; the latter is 1744241100.
        self.assertEqual(datetime.fromtimestamp(1744238700, beijing).strftime("%Y-%m-%d %H:%M:%S"),
                         "2025-04-10 06:45:00")
        self.assertEqual(datetime(2025, 4, 10, 7, 25, tzinfo=beijing).timestamp(), 1744241100)

    async def test_open_server_uses_fixed_get_and_beijing_maintenance_time(self):
        self.assertEqual(SERVER_STATE_URL, URL)
        self.assertEqual(await self.service.query(NAME),
                         f"{NAME}已开服\n开服时间：2025-04-10 07:25:00")
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url), URL)
        self.assertEqual(request.content, b"")

    async def test_given_timestamp_is_converted_without_forty_minute_offset(self):
        self.response = httpx.Response(200, json=[row(timestamp=1744238700)])
        self.assertEqual(await self.service.query(NAME),
                         f"{NAME}已开服\n开服时间：2025-04-10 06:45:00")

    async def test_closed_server_without_maintenance_time(self):
        self.response = httpx.Response(200, json=[{"server_name": NAME, "connect_state": False}])
        self.assertEqual(await self.service.query(NAME), f"{NAME}维护中\n维护时间：不可用")
        self.assertEqual(len(self.requests), 1)

    async def test_invalid_maintenance_time_preserves_closed_status(self):
        for timestamp in (None, "invalid", True, 10 ** 30):
            with self.subTest(timestamp=timestamp):
                self.response = httpx.Response(200, json=[row(state=False, timestamp=timestamp)])
                self.assertEqual(await self.service.query(NAME),
                                 f"{NAME}维护中\n维护时间：不可用")

    async def test_exact_local_name_match_not_substring_or_wrong_row(self):
        self.response = httpx.Response(200, json=[row(NAME + "二区", False),
                                                row("其他服务器", False), row()])
        self.assertEqual(await self.service.query(NAME),
                         f"{NAME}已开服\n开服时间：2025-04-10 07:25:00")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(str(self.requests[0].url), URL)
        for payload in ([], [row(NAME + "二区")], [row("其他服务器")]):
            with self.subTest(payload=payload):
                self.response = httpx.Response(200, json=payload)
                self.assertIn("未找到", await self.service.query(NAME))

    async def test_empty_arguments_use_default_server(self):
        self.response = httpx.Response(200, json=[row("绝代天骄")])
        for argument in ("", " ", "\t\n"):
            with self.subTest(argument=repr(argument)):
                self.assertEqual(await self.service.query(argument),
                                 "绝代天骄已开服\n开服时间：2025-04-10 07:25:00")
        self.assertEqual(len(self.requests), 3)

    async def test_closed_server_includes_maintenance_time(self):
        self.response = httpx.Response(200, json=[row(state=False)])
        self.assertEqual(await self.service.query(NAME),
                         f"{NAME}维护中\n维护时间：2025-04-10 07:25:00")

    async def test_invalid_arguments_fail_before_http(self):
        invalid = ["服" * 81, "青梅 煮酒", "青梅\u3000煮酒"]
        invalid += ["青梅" + char + "煮酒" for char in "<>@:/\\\x00\x01\x1f\x7f\t\r\n"]
        for argument in invalid:
            with self.subTest(argument=repr(argument)):
                result = await self.service.query(argument)
                self.assertIn("服务器名称无效", result)
        self.assertEqual(self.requests, [])

    async def test_name_length_boundary_accepts_eighty_characters(self):
        name = "服" * 80
        self.response = httpx.Response(200, json=[row(name, False)])
        self.assertEqual(await self.service.query(name), f"{name}维护中\n维护时间：2025-04-10 07:25:00")
        self.assertEqual(len(self.requests), 1)

    async def test_network_http_json_and_non_array_fail_safely(self):
        cases = [httpx.ConnectError("synthetic connection failure"),
                 httpx.ReadTimeout("synthetic timeout"),
                 httpx.Response(503, json=[]),
                 httpx.Response(404, json=[]),
                 httpx.Response(200, content=b"not json"),
                 httpx.Response(200, json={"data": [row()]}),
                 httpx.Response(200, content=b"null"),
                 httpx.Response(200, json="not an array"),
                 httpx.Response(200, json=[None]),
                 httpx.Response(200, json=[row(), "malformed row"])]
        for response in cases:
            with self.subTest(response=repr(response)):
                self.response = response
                self.assertIn(UNAVAILABLE, await self.service.query(NAME))

    async def test_matching_connect_state_requires_strict_bool(self):
        for state in (0, 1, "true", "false", None, [], {}):
            with self.subTest(state=repr(state)):
                self.response = httpx.Response(200, json=[row(state=state)])
                self.assertIn(UNAVAILABLE, await self.service.query(NAME))
        self.response = httpx.Response(200, json=[{"server_name": NAME}])
        self.assertIn(UNAVAILABLE, await self.service.query(NAME))

    async def test_bad_or_missing_time_preserves_open_status(self):
        cases = [row(timestamp=value) for value in (None, "invalid", True, 10 ** 30, [], {})]
        cases.append({"server_name": NAME, "connect_state": True})
        for payload in cases:
            with self.subTest(payload=payload):
                self.response = httpx.Response(200, json=[payload])
                result = await self.service.query(NAME)
                self.assertIn(f"{NAME}已开服", result)
                self.assertIn("开服时间：不可用", result)
                self.assertNotIn(UNAVAILABLE, result)


if __name__ == "__main__":
    unittest.main()
