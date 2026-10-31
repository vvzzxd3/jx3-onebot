"""QQ-only transport; receive/control, durable acceptance, and heartbeat are separate."""
import asyncio
from inspect import signature
import json
import logging
import random
import time

from .errors import GatewayFatal, ProviderError, StorageError
from .integrations.qq.events import normalize_message
from .observability import DEFAULT_OBSERVER

log = logging.getLogger(__name__)
FATAL_CODES = {4001, 4002, 4010, 4011, 4012, 4013, 4014, 4914, 4915}
NEW_SESSION_CODES = {4006, 4007, *range(4900, 4914)}
BACKOFF_CODES = {4008, 4009}


class Reconnect(Exception):
    pass


class Gateway:
    def __init__(self, http, store, app_id, connector=None, clock=time.time, ack_intervals=2, observer=None, registry=None):
        if connector is None:
            from websockets.asyncio.client import connect
            options = {"ping_interval": None, "max_size": 1024*1024, "open_timeout": 15}
            # 15 auto-detects proxies; 14 is direct-only and has no proxy parameter.
            if "proxy" in signature(connect).parameters:
                options["proxy"] = None
            connector = lambda url: connect(url, **options)
        self.http, self.store, self.app_id = http, store, app_id
        self.connector, self.clock, self.ack_intervals = connector, clock, ack_intervals
        self.registry = registry
        self.stop = asyncio.Event()
        self.received_seq = None
        self._resume_failures = 0
        self.observer = observer or DEFAULT_OBSERVER
        self._connections = self._received = 0

    def _read_frame(self, raw):
        try:
            frame = json.loads(raw)
        except (ValueError, TypeError):
            frame = None
        self._received += 1
        trace = self.observer.ws(raw, frame, self.app_id, f"c{self._connections}", self._received, chat=False)
        if not isinstance(frame, dict) or "op" not in frame:
            raise GatewayFatal("Malformed QQ frame") from None
        return frame, trace

    async def _send(self, ws, op, data):
        await ws.send(json.dumps({"op": op, "d": data}, ensure_ascii=False))

    async def _persist(self, method, *args):
        # Finish an in-flight commit before reconnecting; cancelling to_thread alone
        # would allow a stale READY write to race the next socket's new session.
        task = asyncio.create_task(asyncio.to_thread(method, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def connection(self, ws):
        self._connections += 1
        connection = f"c{self._connections}"
        self.observer.event("qq.gateway.connected", connection=connection)
        state = await self._persist(self.store.meta, "gateway")
        self.received_seq = state["seq"] if state else None
        try:
            hello, _ = self._read_frame(await asyncio.wait_for(ws.recv(), timeout=15))
            milliseconds = hello["d"]["heartbeat_interval"]
            if isinstance(milliseconds, bool):
                raise ValueError
            interval = milliseconds / 1000
            if hello.get("op") != 10 or not 0 < interval <= 300:
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise GatewayFatal("Malformed QQ Hello") from None
        self.observer.event("qq.gateway.hello", connection=connection, heartbeat_ms=milliseconds)
        token = await self.http.tokens.get()
        self.observer.add_secret(token)
        if state and isinstance(state.get("session_id"), str) and state.get("seq") is not None:
            self.observer.event("qq.gateway.resume", connection=connection, seq=state["seq"])
            await self._send(ws, 6, {"token": "QQBot " + token, "session_id": state["session_id"], "seq": state["seq"]})
        else:
            self.observer.event("qq.gateway.identify", connection=connection, intents=1 << 25)
            await self._send(ws, 2, {"token": "QQBot " + token, "intents": 1 << 25, "shard": [0, 1]})
        session = state["session_id"] if state else None
        pending_ack_at = None
        dispatch = asyncio.Queue(maxsize=64)

        async def heartbeat():
            nonlocal pending_ack_at
            while True:
                await asyncio.sleep(interval)
                if pending_ack_at is not None and time.monotonic() - pending_ack_at >= interval * self.ack_intervals:
                    raise Reconnect("Heartbeat ACK overdue")
                if pending_ack_at is None:
                    pending_ack_at = time.monotonic()
                await self._send(ws, 1, self.received_seq)

        async def accept_dispatch():
            nonlocal session
            while True:
                frame = await dispatch.get()
                if frame is None:
                    return
                seq = frame["s"]
                if frame.get("t") == "READY":
                    session = frame.get("session_id")
                    if not isinstance(session, str) or not session:
                        raise GatewayFatal("READY has no session ID")
                    await self._persist(self.store.ready, session, seq)
                    self._resume_failures = 0
                    self.observer.event("qq.gateway.ready", connection=connection, seq=seq, intents=1 << 25)
                else:
                    if not session:
                        raise GatewayFatal("Dispatch before READY")
                    now = self.clock()
                    message = frame.get("message")
                    reason = frame.get("reason")
                    try:
                        outcome = await self._persist(self.store.accept, session, seq, message, now, True)
                    except Exception as error:
                        self.observer.event("qq.dispatch.result", logging.ERROR, connection=connection,
                                            trace=frame.get("trace"), seq=seq, state="accept_failed",
                                            error_type=type(error).__name__)
                        raise
                    self.observer.event("qq.dispatch.result", connection=connection, trace=frame.get("trace"),
                                        seq=seq, state=outcome, reason=reason if outcome == "ignored" else None,
                                        scene=message.scene if message is not None else None)
                    if frame.get("t") == "RESUMED":
                        self._resume_failures = 0
                        self.observer.event("qq.gateway.resumed", connection=connection, seq=seq)

        async def receive():
            nonlocal pending_ack_at
            async for raw in ws:
                frame, trace = self._read_frame(raw)
                op = frame["op"]
                if op == 11:
                    pending_ack_at = None
                elif op == 1:
                    if pending_ack_at is None:
                        pending_ack_at = time.monotonic()
                    await self._send(ws, 1, self.received_seq)
                elif op == 7:
                    raise Reconnect("Server requested reconnect")
                elif op == 9:
                    # Do not infer Discord resumability from QQ's op9 payload.
                    # The current connection must finish its commit before clear.
                    self._resume_failures += 1
                    if self._resume_failures > 2:
                        raise GatewayFatal("Repeated invalid QQ sessions; check configuration")
                    raise Reconnect("invalid-session")
                elif op == 10:
                    raise GatewayFatal("Duplicate Hello")
                elif op == 0:
                    seq = frame.get("s")
                    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
                        raise GatewayFatal("Dispatch has no valid sequence")
                    self.received_seq = seq
                    entry = {"s": seq, "t": frame.get("t"), "trace": trace}
                    if frame.get("t") == "READY":
                        data = frame.get("d")
                        entry["session_id"] = data.get("session_id") if isinstance(data, dict) else None
                    else:
                        decision = normalize_message(self.app_id, frame, self.clock())
                        message = decision.message
                        # Only a sanitized supported message enters the backlog;
                        # no registry.match, raw author, URLs or auth ext copied.
                        entry['message'] = self.store.clean_received(message) if message else None
                        entry['reason'] = decision.reason
                    try:
                        dispatch.put_nowait(entry)
                    except asyncio.QueueFull:
                        self.observer.event("qq.dispatch.result", logging.ERROR, connection=connection,
                                            trace=trace, seq=seq, state="queue_full")
                        raise StorageError("Gateway acceptance backlog full; checkpoint not skipped") from None
            raise Reconnect("Socket closed")

        tasks = [asyncio.create_task(heartbeat()), asyncio.create_task(accept_dispatch()),
                 asyncio.create_task(receive()), asyncio.create_task(self.stop.wait())]
        failure = None
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except BaseException as error:
            failure = error
            raise
        finally:
            invalid = isinstance(failure, Reconnect) and str(failure) == "invalid-session"
            close_code = getattr(getattr(failure, "rcvd", None), "code", None)
            reset = invalid or close_code in NEW_SESSION_CODES
            try:
                if reset:
                    # A fresh session cannot replay this queue. Commit every already
                    # received entry in order before clearing the resume checkpoint.
                    tasks[0].cancel()
                    tasks[2].cancel()
                    async def finish_receipts():
                        if tasks[1].done():
                            tasks[1].result()
                        else:
                            await dispatch.put(None)
                            await asyncio.shield(tasks[1])
                    drain = asyncio.create_task(finish_receipts())
                    try:
                        await asyncio.wait_for(asyncio.shield(drain), timeout=15)
                    except asyncio.CancelledError:
                        await asyncio.wait_for(drain, timeout=15)
                        raise
                    except Exception:
                        drain.cancel()
                        await asyncio.gather(drain, return_exceptions=True)
                        raise GatewayFatal("Cannot durably drain received events before session reset; stopped") from None
                    if invalid:
                        await self._persist(self.store.clear_session)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        from websockets.exceptions import ConnectionClosed
        failures = 0
        while not self.stop.is_set():
            started = self.clock()
            try:
                state = await self._persist(self.store.meta, "gateway")
                resuming = bool(state and state.get("session_id") and state.get("seq") is not None)
                url = await self.http.gateway(allow_resume=resuming)
                async with self.connector(url) as ws:
                    await self.connection(ws)
                if self.stop.is_set():
                    return
            except GatewayFatal:
                self.observer.event("qq.gateway.fatal", logging.ERROR, reason="protocol_or_configuration")
                raise
            except ConnectionClosed as error:
                code = getattr(getattr(error, "rcvd", None), "code", None)
                self.observer.event("qq.gateway.closed", logging.WARNING, code=code)
                if code in FATAL_CODES:
                    raise GatewayFatal(f"QQ gateway stopped for close code {code}") from None
                if code in NEW_SESSION_CODES:
                    await self._persist(self.store.clear_session)
                elif code not in {None, 1000, 1001, 1006, *BACKOFF_CODES}:
                    raise GatewayFatal(f"Unrecognized QQ close code {code}; manual verification required") from None
            except (Reconnect, ProviderError, OSError, asyncio.TimeoutError, StorageError) as error:
                reasons = {"Heartbeat ACK overdue": "heartbeat_ack_overdue", "Server requested reconnect": "server_reconnect",
                           "invalid-session": "invalid_session", "Socket closed": "socket_closed"}
                reason = reasons.get(str(error), "reconnect") if isinstance(error, Reconnect) else type(error).__name__
                self.observer.event("qq.gateway.interrupted", logging.WARNING, reason=reason,
                                    status=error.status if isinstance(error, ProviderError) else None,
                                    code=self.observer.code(error.code) if isinstance(error, ProviderError) else None)
            if self.clock() - started >= 60:
                failures = 0
            failures += 1
            if failures >= 10:
                raise GatewayFatal("QQ connection failed repeatedly; stopped instead of endless Identify")
            delay = min(60, max(5, 2**min(failures, 5))) + random.random()
            self.observer.event("qq.gateway.backoff", failures=failures, delay_seconds=round(delay, 2))
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
