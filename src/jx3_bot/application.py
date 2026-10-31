import asyncio
import logging
import time
import json

from .replies import render_result, message_payload

from .errors import ProviderError, ReplyNotSent, ReplyExpired, ReplyTooLong
from .integrations.qq.models import ReceivedMessage
from .integrations.qq.events import match_message
from .observability import DEFAULT_OBSERVER


class Application:
    def __init__(self, store, weather, qq, gateway, workers=2, clock=time.time, observer=None, registry=None, reply_format="text"):
        self.store, self.weather, self.qq, self.gateway = store, weather, qq, gateway
        self.workers, self.clock = workers, clock
        self.registry, self.reply_format = registry, reply_format
        self.observer = observer or DEFAULT_OBSERVER
        self.stop = asyncio.Event()

    async def process_one(self):
        message = await asyncio.to_thread(self.store.claim_command, self.clock())
        if message is None:
            return False
        trace = self.observer.trace(*message.key)
        received = message if isinstance(message, ReceivedMessage) else None
        if received is not None:
            # claim_command can only see committed jobs. No matcher is called in
            # Gateway or Store: even ordinary/expired/bot messages log first.
            self.observer.chat_message(message, trace)
            decision = match_message(message, self.clock(), self.registry)
            self.observer.event('message.matched', trace=trace, reason=decision.reason)
            if decision.message is None:
                state = ('unmatched' if decision.reason == 'command_not_matched'
                         and message.scene == 'group' and message.timestamp + 300 > self.clock()
                         else 'expired' if decision.reason == 'expired' else 'done')
                await asyncio.to_thread(self.store.finish_command, message, state)
                return True
            message = decision.message
            await asyncio.to_thread(self.store.promote_command, message)
        started = time.monotonic()
        self.observer.event("command.start", trace=trace, command=message.command.kind)
        try:
            if message.timestamp + 300 <= self.clock():
                await asyncio.to_thread(self.store.plan_reply, message, message_payload(render_result("expired"), self.reply_format))
                self.observer.event("command.done", trace=trace, result="expired")
                self.observer.event("reply.planned", trace=trace, msg_seq=1)
                return True
            if self.registry is None:
                # Compatibility path for existing embedders; CLI always injects a registry.
                content = await self.weather.handle(message)
            else:
                if hasattr(self.registry, "match_received"):
                    results = await self.registry.dispatch(message, received=received)
                else:
                    results = await self.registry.dispatch(message)
                if not results:
                    await asyncio.to_thread(self.store.finish_command, message)
                    self.observer.event("command.done", trace=trace, result="no_reply")
                    return True
                try:
                    content = message_payload(results, self.reply_format)
                except ReplyTooLong:
                    content = message_payload(render_result("output_too_long"), self.reply_format)
                    self.observer.event("command.output_limited", trace=trace, result="output_too_long")
            await asyncio.to_thread(self.store.plan_reply, message, content)
        except Exception as error:
            self.observer.event("command.error", logging.ERROR, trace=trace, error_type=type(error).__name__)
            raise
        self.observer.event("command.done", trace=trace, duration_ms=round((time.monotonic()-started)*1000))
        self.observer.event("reply.planned", trace=trace, msg_seq=1)
        return True

    async def send_one(self):
        reply = await asyncio.to_thread(self.store.claim_reply, self.clock())
        if reply is None:
            return False
        key = reply["app"], reply["scene"], reply["grp"], reply["msg"]
        scope = reply["grp"] if reply["scene"] == "group" else json.dumps(["c2c", reply["grp"]], separators=(",", ":"))
        trace = self.observer.trace(reply["app"], scope, reply["msg"])
        if reply["expires"] <= self.clock():
            await asyncio.to_thread(self.store.reply_state, key, "expired")
            self.observer.event("reply.result", trace=trace, state="expired")
            return True
        self.observer.event("reply.submit", trace=trace, msg_seq=reply["seq"], attempt=reply["attempts"])
        try:
            content = json.loads(reply["payload"]) if reply["payload"] else reply["content"]
            response = await self.qq.reply(reply["grp"], reply["msg"], content, reply["seq"],
                                           scene=reply["scene"], expires=reply["expires"], clock=self.clock)
            if not isinstance(response, dict) or not isinstance(response.get('id'), str) or not response['id']:
                raise ProviderError('qq-reply-unconfirmed', 200)
        except ReplyExpired:
            await asyncio.to_thread(self.store.reply_state, key, "expired")
            self.observer.event("reply.result", trace=trace, state="expired", reason="preflight_expired")
        except ReplyNotSent:
            state = 'expired' if reply['expires'] <= self.clock() else 'pending' if reply['attempts'] < 3 else 'failed'
            await asyncio.to_thread(self.store.reply_state, key, state)
            self.observer.event("reply.result", logging.WARNING, trace=trace, state=state,
                                reason="preflight_expired" if state == "expired" else "preflight_retry" if state == "pending" else "preflight_exhausted",
                                attempt=reply["attempts"], msg_seq=reply["seq"])
            await asyncio.sleep(0.5 * reply['attempts'])
        except ProviderError as error:
            # No automatic retransmission after an ambiguous send, even on restart.
            state = "failed" if 400 <= error.status < 500 else "uncertain"
            await asyncio.to_thread(self.store.reply_state, key, state)
            self.observer.event("reply.result", logging.WARNING, trace=trace, state=state,
                                status=error.status, code=self.observer.code(error.code), msg_seq=reply["seq"])
        except Exception as error:
            # Preserve existing recovery semantics: an unhandled sending remains uncertain on restart.
            self.observer.event("reply.error", logging.ERROR, trace=trace, error_type=type(error).__name__)
            raise
        else:
            try:
                archived = await asyncio.to_thread(self.store.confirm_reply, reply, response, self.clock())
            except Exception as error:
                # QQ already accepted this POST. Keep 'sending' for uncertain
                # recovery and never repair a local write failure by sending again.
                self.observer.event('reply.archive_failed', logging.ERROR, trace=trace,
                                    error_type=type(error).__name__, msg_seq=reply['seq'])
                raise
            self.observer.chat_sent(archived, trace)
            self.observer.event("reply.result", trace=trace, state="sent", msg_seq=reply["seq"])
        return True

    async def worker(self):
        while not self.stop.is_set():
            processed = await self.process_one()
            sent = await self.send_one()
            if not processed and not sent:
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass

    async def housekeeping(self):
        while not self.stop.is_set():
            await asyncio.to_thread(self.store.cleanup, self.clock())
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def run(self):
        recovered = self.store.recover()
        self.observer.event("application.start", workers=self.workers, **recovered)
        tasks = [asyncio.create_task(self.gateway.run()), asyncio.create_task(self.housekeeping()),
                 *(asyncio.create_task(self.worker()) for _ in range(self.workers)),
                 asyncio.create_task(self.stop.wait())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except Exception as error:
            self.observer.event("application.error", logging.ERROR, error_type=type(error).__name__)
            raise
        finally:
            self.stop.set()
            self.gateway.stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.observer.event("application.stop")
