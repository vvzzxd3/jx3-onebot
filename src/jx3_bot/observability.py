"""QQ console observations: summaries and opt-in original WS frame diagnostics.

Only payload mode may print original WS frames (including sensitive fields), with
single-line encoding and no size limit. No log values are automatically redacted.
HTTP bodies remain projected; persistence redaction is independent.
Transport diagnostics are best effort; production chat logs consume committed,
normalized records before matching or after a successful send archive commit.
"""
import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import logging
import queue
import re
import secrets
import sys
import threading
import time
from zoneinfo import ZoneInfo

from .errors import ConfigurationError

log = logging.getLogger(__name__)
CHAT_EVENTS = {"GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"}
# Free text may contain credentials even though its containing field is allowed.
JWT = re.compile(r"(?<![A-Za-z0-9_-])(?=([A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}(?![A-Za-z0-9_-])))")
AUTH = re.compile(r"(?i)\b(?:Bearer|QQBot|Basic)\s+[^\s,;\"'<>]+")
HEADERS = re.compile(r"(?im)[\"']?(?:authorization|cookie)[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\r\n]+)")
ASSIGNMENT = re.compile(
    r"(?i)(?:[\"']?(?:access_token|auth_token|client_?secret|authorization|cookie|private_key|token)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;<>]+)")
PEM = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)", re.DOTALL)
URL = re.compile(r"(?i)(?:https?|wss?)://[^\s<>\"']+")


def _redact_jwt(candidate):
    encoded = candidate.split(".", 1)[0]
    if len(encoded) > 16384:
        return "[TOKEN隐藏]"
    try:
        header = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, UnicodeError, binascii.Error):
        return candidate
    return "[JWT REDACTED]" if isinstance(header, dict) and isinstance(header.get("alg"), str) else candidate


def _hide_jwts(value):
    parts, end = [], 0
    for match in JWT.finditer(value):
        if match.start() < end:
            continue
        candidate = match.group(1)
        replacement = _redact_jwt(candidate)
        if replacement != candidate:
            parts.extend((value[end:match.start()], replacement))
            end = match.start() + len(candidate)
    return "".join(parts) + value[end:]


def _boolean(env, name, default):
    value = env.get(name, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise ConfigurationError(f"{name} must be true/false or 1/0")
    return value in {"true", "1"}


@dataclass(frozen=True)
class LogSettings:
    level: str = "INFO"
    receive: str = "summary"
    chat: bool = True
    message_text: bool = True

    @classmethod
    def from_env(cls, env):
        level = env.get("BOT_LOG_LEVEL", "INFO").strip().upper()
        receive = env.get("BOT_QQ_RECEIVE_LOG", "summary").strip().lower()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("BOT_LOG_LEVEL is invalid")
        if receive not in {"summary", "payload", "off"}:
            raise ConfigurationError("BOT_QQ_RECEIVE_LOG must be summary/payload/off")
        # Legacy BOT_LOG_MAX_PAYLOAD_CHARS is ignored: logs have no length cap.
        return cls(level, receive, _boolean(env, "BOT_QQ_CHAT_LOG", True),
                   _boolean(env, "BOT_QQ_LOG_MESSAGE_TEXT", True))


class Observer:
    def __init__(self, settings=None, known_secrets=()):
        self.settings = settings or LogSettings()
        self._key = secrets.token_bytes(32)
        # Retain observed credentials for independent pre-persistence redaction.
        # Logging does not consult this registry; never persist the registry.
        self._secrets = set()
        self._fixed_secrets = set()
        self._lock = threading.Lock()
        self.failures = 0
        for value in known_secrets:
            self.add_secret(value, permanent=True)

    def add_secret(self, value, permanent=False):
        if isinstance(value, str) and value:
            with self._lock:
                if permanent:
                    self._fixed_secrets.add(value)
                elif value not in self._secrets:
                    self._secrets.add(value)

    def redact(self, value):
        """Shared pre-persistence masking; preserve real line breaks in chat bodies."""
        if not isinstance(value, str):
            return ""
        with self._lock:
            known = tuple(self._fixed_secrets) + tuple(self._secrets)
        for secret in sorted(known, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = PEM.sub("[PRIVATE KEY REDACTED]", value)
        # URLs can carry opaque authorization in paths as well as query strings.
        value = URL.sub("[URL隐藏]", value)
        value = HEADERS.sub("[HEADER REDACTED]", value)
        value = AUTH.sub("[AUTH REDACTED]", value)
        value = ASSIGNMENT.sub("[AUTH REDACTED]", value)
        return _hide_jwts(value)

    def safe(self, value):
        """Escape a complete log value onto one line, without masking its contents."""
        if not isinstance(value, str):
            return "缺失"
        escaped = []
        for character in value:
            code = ord(character)
            if character == "\n":
                escaped.append("\\n")
            elif character == "\r":
                escaped.append("\\r")
            elif character == "\t":
                escaped.append("\\t")
            elif unicontrol(code):
                escaped.append(f"\\u{code:04x}")
            else:
                escaped.append(character)
        return "".join(escaped)

    def trace(self, app, group, message):
        data = json.dumps([app, group, message], ensure_ascii=True, separators=(",", ":"))
        return "m_" + hmac.new(self._key, data.encode(), hashlib.sha256).hexdigest()[:16]

    def frame_trace(self, app, frame):
        data = frame.get("d")
        if not isinstance(data, dict):
            return None
        author = data.get("author")
        author = author if isinstance(author, dict) else {}
        group = data.get("group_openid") if frame.get("t") != "C2C_MESSAGE_CREATE" else author.get("user_openid")
        message = data.get("id")
        if all(isinstance(x, str) and 0 < len(x) <= 256 for x in (group, message)):
            scope = json.dumps(["c2c", group], separators=(",", ":")) if frame.get("t") == "C2C_MESSAGE_CREATE" else group
            return self.trace(app, scope, message)
        return None

    def event(self, event, level=logging.INFO, **fields):
        """Only callers' fixed field names and projected values are accepted."""
        try:
            values = []
            for name, value in fields.items():
                if value is None:
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    rendered = str(value)
                elif isinstance(value, bool):
                    rendered = str(value).lower()
                else:
                    rendered = json.dumps(self.safe(str(value)), ensure_ascii=False)
                values.append(f"{name}={rendered}")
            log.log(level, "event=%s %s", event, " ".join(values))
        except Exception:
            self.failures += 1  # Never turn observation failures into business failure.

    @staticmethod
    def code(value):
        return str(value)

    def _content(self, data):
        if not self.settings.message_text:
            return "[正文隐藏]"
        content = data.get("content")
        parts = [content] if isinstance(content, str) and content else []
        # Iterative traversal avoids a recursion-depth cap. Cycles are impossible
        # in JSON, but guard synthetic cyclic lists without dropping shared lists.
        items = data.get("msg_elements")
        quoted = []
        stack = [(iter(items), id(items))] if isinstance(items, list) else []
        active = {id(items)} if stack else set()
        while stack:
            iterator, identity = stack[-1]
            try:
                item = next(iterator)
            except StopIteration:
                stack.pop()
                active.remove(identity)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("content")
            if isinstance(text, str):
                quoted.append(text)
            children = item.get("msg_elements")
            if isinstance(children, list) and id(children) not in active:
                active.add(id(children))
                stack.append((iter(children), id(children)))
        if quoted:
            parts.append("[引用/转发] " + " | ".join(quoted))
        if isinstance(data.get("ark_data"), dict):
            parts.append("[卡片]")
        if not parts:
            parts.append("[无文本]")
        attachments = data.get("attachments")
        if isinstance(attachments, list):
            counts = {}
            for item in attachments:
                kind = item.get("content_type", "") if isinstance(item, dict) else ""
                label = ("图片" if isinstance(kind, str) and kind.startswith("image/") else
                         "语音" if kind == "voice" else
                         "视频" if isinstance(kind, str) and kind.startswith("video/") else "文件")
                counts[label] = counts.get(label, 0) + 1
            parts.extend(f"[{kind}×{count}]" for kind, count in counts.items())
        return self.safe(" ".join(parts))

    def chat(self, frame, trace=None):
        if (not self.settings.chat or not isinstance(frame, dict) or frame.get("op") != 0
                or not isinstance(frame.get("t"), str) or frame.get("t") not in CHAT_EVENTS):
            return
        try:
            data = frame.get("d")
            if not isinstance(data, dict):
                return
            author = data.get("author")
            author = author if isinstance(author, dict) else {}
            nickname = author.get("username")
            nickname = self.safe(nickname) if isinstance(nickname, str) and nickname.strip() else "未知用户"
            private = frame["t"] == "C2C_MESSAGE_CREATE"
            user = author.get("user_openid" if private else "member_openid")
            source = ""
            if not isinstance(user, str) or not user:
                user = author.get("id")
                source = "[author.id]" if isinstance(user, str) and user else ""
            user = self.safe(user) if isinstance(user, str) and user else "缺失"
            group = data.get("group_openid")
            group = self.safe(group) if isinstance(group, str) and group else "缺失"
            prefix = "[单聊] - " if private else f"[群聊] 未知群（{group}） - "
            # No group-name field exists in documented QQ group message events.
            log.info("[receive] %s%s（%s）%s：%s", prefix, nickname, user, source, self._content(data))
            if trace:
                self.event("qq.chat.link", logging.DEBUG, trace=trace)
        except Exception:
            self.failures += 1

    def chat_message(self, message, trace=None):
        """Render only a normalized projection whose archive transaction committed."""
        if not self.settings.chat:
            return
        try:
            private = message.scene == "c2c"
            target = self.safe(message.conversation_id)
            user = self.safe(message.user_id)
            nickname = self.safe(message.sender_name) if message.sender_name else "未知用户"
            prefix = "[单聊] - " if private else f"[群聊] 未知群（{target}） - "
            body = message.content
            if message.content_format == "text" and message.summary:
                body = " ".join((body, message.summary)).strip()
            if message.truncated:
                body += " [正文截断]"
            body = self.safe(body) if self.settings.message_text else "[正文隐藏]"
            log.info("[receive] %s%s（%s）：%s", prefix, nickname, user, body)
            if trace:
                self.event("qq.chat.link", logging.DEBUG, trace=trace)
        except Exception:
            self.failures += 1

    def chat_sent(self, message, trace=None):
        """Successful platform send AND archive commit, never a planned/failed row."""
        if not self.settings.chat or message is None:
            return
        try:
            target = self.safe(message["conversation_id"])
            app = self.safe(message["app_id"])
            scene = "单聊" if message["scene"] == "c2c" else "群聊"
            body = self.safe(message["content"]) if self.settings.message_text else "[正文隐藏]"
            log.info("[send] [%s] 本应用（%s） → %s：%s", scene, app, target, body)
            if trace:
                self.event("qq.chat.sent_link", logging.DEBUG, trace=trace)
        except Exception:
            self.failures += 1

    def ws(self, raw, frame, app, connection, received, *, chat=True):
        try:
            size = len(raw.encode("utf-8", errors="replace")) if isinstance(raw, str) else len(raw)
            if self.settings.receive == "payload":
                # This is the received wire text, not the parsed/normalized d. JSON
                # string encoding keeps it on one physical console line; decoding
                # raw= recovers the complete original text, without a length cap.
                wire = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
                log.info("event=qq.ws.payload connection=%s received=%s raw=%s",
                         connection, received, json.dumps(wire, ensure_ascii=True))
            if not isinstance(frame, dict):
                if self.settings.receive != "off":
                    self.event("qq.ws.receive", connection=connection, received=received,
                               bytes=size, parse="invalid_json_or_object")
                return None
            trace = self.frame_trace(app, frame)
            if self.settings.receive != "off":
                data = frame.get("d")
                content = data.get("content") if isinstance(data, dict) else None
                self.event("qq.ws.receive", connection=connection, received=received,
                           op=self.code(frame.get("op")), event_type=self.event_type(frame.get("t")),
                           seq=self.code(frame.get("s")), bytes=size, trace=trace,
                           content_chars=len(content) if isinstance(content, str) else None)

            if chat:  # Legacy standalone observer; Gateway disables pre-commit chat.
                self.chat(frame, trace)
            return trace
        except Exception:
            self.failures += 1
            return None

    @staticmethod
    def event_type(value):
        return value if isinstance(value, str) else "unknown"

    def http_response(self, route, response, body, started, trace=None, parsed=True):
        if self.settings.receive == "off":
            return
        try:
            fields = {"route": route, "status": response.status_code,
                      "code": self.code(body.get("err_code", body.get("code", 0))) if isinstance(body, dict) else "unavailable",
                      "duration_ms": round((time.monotonic() - started) * 1000),
                      "bytes": len(response.content), "parse": "json" if parsed else "invalid_json", "trace": trace}
            if route == "token" and isinstance(body, dict):
                fields["expires_in"] = self.code(body.get("expires_in"))
            if self.settings.receive == "payload":
                fields["body_type"] = type(body).__name__
                fields["has_id"] = isinstance(body, dict) and isinstance(body.get("id"), str)
                fields["has_url"] = isinstance(body, dict) and isinstance(body.get("url"), str)
            self.event("qq.http.response", **fields)
        except Exception:
            self.failures += 1


def unicontrol(code):
    # Include line separators, bidi overrides and isolated UTF-16 surrogates.
    return code < 32 or 127 <= code <= 159 or code in {0x2028, 0x2029} or 0x202a <= code <= 0x202e or 0x2066 <= code <= 0x2069 or 0xd800 <= code <= 0xdfff


DEFAULT_OBSERVER = Observer()


class ConsoleFormatter(logging.Formatter):
    def __init__(self, observer):
        super().__init__("%(asctime)s %(levelname)s %(message)s")
        self.observer = observer

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

    def format(self, record):
        # Preserve the complete message, exception and stack information. Only
        # escape physical line breaks/control characters; do not redact or clip.
        copied = logging.makeLogRecord({**record.__dict__, "msg": record.getMessage(), "args": ()})
        return self.observer.safe(super().format(copied))


class ConsoleQueue(logging.Handler):
    """Nonblocking producers; one daemon writes already-safe single-line strings."""
    def __init__(self, observer, stream, capacity=1024):
        super().__init__()
        self.setFormatter(ConsoleFormatter(observer))
        self.queue = queue.Queue(maxsize=capacity)
        self.stream = stream
        self.stop = threading.Event()
        self._stats_lock = threading.Lock()
        self.dropped = 0
        self.output_errors = 0
        self.unflushed = 0
        self.reported = (0, 0)
        self.thread = threading.Thread(target=self._consume, name="qq-console-log", daemon=True)
        self.thread.start()

    def emit(self, record):
        try:
            self.queue.put_nowait(self.format(record))
        except Exception:
            with self._stats_lock:
                self.dropped += 1

    def _consume(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                line = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                with self._stats_lock:
                    stats = (self.dropped, self.output_errors)
                if stats != self.reported:
                    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
                    self.stream.write(f"{stamp} WARNING event=log.loss dropped={stats[0]} output_errors={stats[1]}\n")
                    self.stream.flush()
                    self.reported = stats
                self.stream.write(line + "\n")
                self.stream.flush()
            except Exception:
                with self._stats_lock:
                    self.dropped += 1
                    self.output_errors += 1
            finally:
                self.queue.task_done()
        # If drops happened after the last dequeued record, report at shutdown too.
        with self._stats_lock:
            stats = (self.dropped, self.output_errors)
        if stats != self.reported:
            try:
                self.stream.write(f"WARNING event=log.loss dropped={stats[0]} output_errors={stats[1]}\n")
                self.stream.flush()
                self.reported = stats
            except Exception:
                pass  # An unavailable device cannot be made reliable by recursive logging.

    def shutdown(self, timeout=2):
        self.stop.set()
        self.thread.join(timeout)
        flushed = not self.thread.is_alive()
        if not flushed:
            with self.queue.mutex:
                self.unflushed = self.queue.unfinished_tasks
        return flushed


class ConsoleRuntime:
    def __init__(self, observer, stream, emergency_stream=None):
        self.observer = observer
        self.emergency_stream = sys.stderr if emergency_stream is None else emergency_stream
        self.logger = logging.getLogger("jx3_bot")
        self.previous = (self.logger.level, self.logger.propagate)
        self.handler = ConsoleQueue(observer, stream)
        self.logger.addHandler(self.handler)
        self.logger.setLevel(observer.settings.level)
        self.logger.propagate = False
        for name in ("httpx", "httpcore", "websockets"):
            logging.getLogger(name).setLevel(logging.WARNING)

    def close(self, timeout=2):
        if self.observer.failures:
            self.observer.event("log.observation_failures", logging.WARNING, count=self.observer.failures)
        self.logger.removeHandler(self.handler)
        flushed = self.handler.shutdown(timeout)
        self.handler.close()
        self.logger.setLevel(self.previous[0])
        self.logger.propagate = self.previous[1]
        if not flushed:
            pending = self.handler.unflushed
            def report():
                try:
                    self.emergency_stream.write(f"WARNING event=log.flush_timeout pending={pending}; console output incomplete\n")
                    self.emergency_stream.flush()
                except Exception:
                    pass
            # Even a blocked stderr cannot indefinitely delay process shutdown.
            warning = threading.Thread(target=report, name="qq-console-warning", daemon=True)
            warning.start()
            warning.join(0.1)
        return flushed
