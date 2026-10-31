import asyncio
import logging
import time
from urllib.parse import quote, urlsplit

import httpx

from .errors import ProviderError, GatewayFatal, ReplyNotSent, ReplyExpired
from .observability import DEFAULT_OBSERVER

TOKEN_URL = "https://api.bot.qq.com/app/getAppAccessToken"
API_ORIGIN = "https://api.sgroup.qq.com"


def _body(response):
    try:
        return response.json(), True
    except ValueError:
        return None, False


class QQTokens:
    def __init__(self, client, app_id, secret, clock=time.monotonic, observer=None):
        self.client, self.app_id, self.secret, self.clock = client, app_id, secret, clock
        self.observer = observer or DEFAULT_OBSERVER
        self.observer.add_secret(secret, permanent=True)
        self.lock = asyncio.Lock()
        self._token, self._refresh_at = "", 0.0

    async def get(self):
        if self._token and self.clock() < self._refresh_at:
            return self._token
        async with self.lock:
            if self._token and self.clock() < self._refresh_at:
                return self._token
            started = self.clock()
            observed_at = time.monotonic()
            try:
                response = await self.client.post(TOKEN_URL, json={"appId": self.app_id, "clientSecret": self.secret}, follow_redirects=False)
            except httpx.HTTPError as error:
                self.observer.event("qq.http.error", logging.WARNING, route="token", error_type=type(error).__name__)
                raise ProviderError("qq-token") from None
            body, parsed = _body(response)
            if isinstance(body, dict):
                self.observer.add_secret(body.get("access_token"))
            self.observer.http_response("token", response, body, observed_at, parsed=parsed)
            try:
                if not isinstance(body, dict):
                    raise ValueError
                lifetime = int(body.get("expires_in", 0))
                token = body.get("access_token")
                if (response.status_code != 200 or str(body.get("code", 0)) not in {"0", "None"}
                        or not isinstance(token, str) or not token or lifetime <= 0):
                    raise ProviderError("qq-token", response.status_code, body.get("code", 0))
                self._token = token
                self._refresh_at = started + max(1, lifetime - min(60, lifetime / 2))
                self.observer.event("qq.token.refreshed", expires_in=lifetime)
                return token
            except (ValueError, TypeError, AttributeError, OverflowError):
                raise ProviderError("qq-token", response.status_code) from None

    def invalidate(self, token):
        if self._token == token:
            self._refresh_at = 0
            self.observer.event("qq.token.invalidated")


class QQHTTP:
    def __init__(self, client, tokens, observer=None):
        self.client, self.tokens = client, tokens
        self.observer = observer or (tokens.observer if isinstance(tokens, QQTokens) else DEFAULT_OBSERVER)

    async def request(self, method, path, payload=None, retry_auth=True, trace=None, expires=None, clock=time.time):
        route = "gateway" if path == "/gateway/bot" else ("c2c.reply" if path.startswith("/v2/users/") else "group.reply") if method == "POST" else "qq.api"
        try:
            token = await self.tokens.get()
        except ProviderError as error:
            self.observer.event("qq.http.preflight_failed", logging.WARNING, route=route, trace=trace,
                                status=error.status, code=self.observer.code(error.code))
            if method == "POST":
                raise ReplyNotSent("QQ token preflight failed before message submission") from None
            raise
        self.observer.add_secret(token)
        if method == "POST" and expires is not None and clock() >= expires:
            raise ReplyExpired("Passive reply expired before message submission")
        started = time.monotonic()
        try:
            response = await self.client.request(method, API_ORIGIN + path, json=payload,
                headers={"Authorization": "QQBot " + token}, follow_redirects=False)
        except httpx.HTTPError as error:
            self.observer.event("qq.http.error", logging.WARNING, route=route, trace=trace,
                                error_type=type(error).__name__, duration_ms=round((time.monotonic()-started)*1000))
            raise ProviderError("qq-network") from None
        body, parsed = _body(response)
        self.observer.http_response(route, response, body, started, trace, parsed)
        # Invalidate for future messages, but never replay this POST automatically.
        if response.status_code == 401:
            self.tokens.invalidate(token)
        if method == "GET" and response.status_code == 401 and retry_auth:
            return await self.request(method, path, retry_auth=False, trace=trace)
        if not isinstance(body, dict):
            raise ProviderError("qq", response.status_code)
        code = body.get("err_code", body.get("code", 0))
        if response.status_code >= 300 or str(code) not in {"0", "None"}:
            raise ProviderError("qq", response.status_code, code)
        return body

    async def gateway(self, allow_resume=False):
        body = await self.request("GET", "/gateway/bot")
        url = body.get("url", "")
        parsed = urlsplit(url)
        if parsed.scheme != "wss" or not parsed.hostname or parsed.username or parsed.password:
            raise GatewayFatal("Official gateway did not return a valid WSS URL")
        if int(body.get("shards", 1)) != 1:
            raise GatewayFatal("This deployment supports one shard; configure a sharded deployment separately")
        limit = body.get("session_start_limit") or {}
        if limit and int(limit.get("remaining", 0)) <= 0 and not allow_resume:
            raise GatewayFatal("QQ gateway session-start budget exhausted; wait before restarting")
        return url

    async def reply(self, group, message_id, content, seq=1, scene="group", expires=None, clock=time.time):
        import json
        from .replies import validate_payload
        if scene not in {"group", "c2c"} or not 1 <= seq <= (5 if scene == "group" else 4):
            raise ValueError("Invalid passive reply scene/sequence")
        payload = validate_payload(content) if isinstance(content, dict) else validate_payload({"msg_type": 0, "content": content})
        scope = group if scene == "group" else json.dumps(["c2c", group], separators=(",", ":"))
        trace = self.observer.trace(self.tokens.app_id, scope, message_id)
        endpoint = "groups" if scene == "group" else "users"
        result = await self.request("POST", f"/v2/{endpoint}/{quote(group, safe='')}/messages",
            {**payload, "msg_id": message_id, "msg_seq": seq}, trace=trace, expires=expires, clock=clock)
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise ProviderError("qq-reply-unconfirmed", 200)
        return result
