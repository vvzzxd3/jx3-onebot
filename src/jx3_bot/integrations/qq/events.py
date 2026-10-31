"""Parse QQ once, then match the persisted projection in the worker."""
from datetime import datetime
import math

from ...core.models import MatchContext
from .models import (GroupMessageEvent, IncomingMessage, MessageDecision,
                     PrivateMessageEvent, ReceivedMessage, ReceiveDecision)

MESSAGE_EVENTS = {"GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"}


def timestamp(value):
    """Strict platform time; never accept naive local dates or booleans."""
    if isinstance(value, str):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Timezone required")
        value = stamp.timestamp()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        value = float(value)
    else:
        raise ValueError("Invalid time")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid time")
    return value


def _summary(data):
    """Bounded media/type labels only; no attachment URLs, auth ext or raw objects."""
    parts = []
    attachments = data.get("attachments")
    if isinstance(attachments, list):
        counts = {}
        for item in attachments[:100]:
            kind = item.get("content_type") if isinstance(item, dict) else None
            label = ("图片" if isinstance(kind, str) and kind.startswith("image/") else
                     "语音" if kind == "voice" else
                     "视频" if isinstance(kind, str) and kind.startswith("video/") else "文件")
            counts[label] = counts.get(label, 0) + 1
        parts.extend(f"[{kind}×{count}]" for kind, count in counts.items())
        if len(attachments) > 100:
            parts.append("[附件摘要截断]")
    if isinstance(data.get("ark_data"), dict):
        parts.append("[卡片]")
    if isinstance(data.get("msg_elements"), list) and data["msg_elements"]:
        parts.append("[引用/转发]")
    return " ".join(parts)


def normalize_message(app_id, payload, now):
    """No matcher, database, HTTP call or reply-TTL filtering in this stage."""
    if (not isinstance(payload, dict) or not isinstance(payload.get("t"), str)
            or payload["t"] not in MESSAGE_EVENTS):
        return ReceiveDecision(None, "unsupported_event")
    data = payload.get("d")
    if not isinstance(data, dict) or not isinstance(data.get("author"), dict):
        return ReceiveDecision(None, "malformed_payload")
    author = data["author"]
    scene = "c2c" if payload["t"] == "C2C_MESSAGE_CREATE" else "group"
    user = author.get("user_openid" if scene == "c2c" else "member_openid")
    target = user if scene == "c2c" else data.get("group_openid")
    mid = data.get("id")
    if not all(isinstance(v, str) and 0 < len(v) <= 256 and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in v)
               for v in (app_id, user, target, mid)):
        return ReceiveDecision(None, "missing_identity")
    try:
        occurred = timestamp(data['timestamp']) if data.get('timestamp') is not None else None
    except (ValueError, OverflowError, OSError):
        return ReceiveDecision(None, "invalid_timestamp")
    if occurred is not None and occurred > now + 30:
        return ReceiveDecision(None, "future_timestamp")
    raw = data.get("content")
    raw = raw if isinstance(raw, str) else ""
    oversized = len(raw) > 65536
    summary = _summary(data)
    content = "" if oversized else raw
    form = "text" if raw else "non_text"
    if not raw:
        content = summary
    nickname = author.get("username")
    nickname = nickname if isinstance(nickname, str) and nickname.strip() else None
    # Excessive names are omitted before masking rather than leaking a token prefix.
    if nickname is not None and len(nickname) > 65536:
        nickname = None
    is_bot = author.get("bot") if isinstance(author.get("bot"), bool) else None
    return ReceiveDecision(ReceivedMessage(app_id, scene, target, user, mid, occurred,
        payload["t"], content, is_bot is True, author.get("id") == app_id, oversized,
        nickname, is_bot, form, now, summary), "received")


def business_event(message: ReceivedMessage) -> GroupMessageEvent | PrivateMessageEvent:
    """Build a restricted DTO from the Store's cleaned projection, never QQ d."""
    if (not isinstance(message, ReceivedMessage) or message.truncated
            or message.content_format != "text" or not isinstance(message.content, str)
            or len(message.content) > 4096 or message.timestamp is None):
        raise ValueError("Unsupported business text event")
    common = dict(app_id=message.app_id, message_id=message.message_id,
                  user_id=message.user_id, content=message.content,
                  timestamp=message.timestamp, event_type=message.event_type,
                  sender_name=message.sender_name)
    if message.scene == "group" and message.event_type in (
            "GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"):
        return GroupMessageEvent(**common, group_id=message.conversation_id,
                                 at_me=message.event_type == "GROUP_AT_MESSAGE_CREATE")
    if message.scene == "c2c" and message.event_type == "C2C_MESSAGE_CREATE":
        return PrivateMessageEvent(**common, user_openid=message.conversation_id)
    raise ValueError("Unsupported QQ message event type")


def match_message(message, now, registry=None, excluded_group_authors=()):
    """Called only AFTER archive commit and receive log in the runtime pipeline."""
    from ...matching import DEFAULT_MATCHERS
    if message.bot_author or message.self_author or (
            message.scene == "group" and message.user_id in excluded_group_authors):
        return MessageDecision(None, "bot_author")
    if message.timestamp is None:
        return MessageDecision(None, "invalid_timestamp")
    if message.timestamp + 300 <= now:
        return MessageDecision(None, "expired")
    if message.content_format != "text" or message.truncated:
        return MessageDecision(None, "command_not_matched")
    context = MatchContext(message.scene, message.app_id, message.conversation_id,
                           message.user_id, message.scene == "c2c" or message.event_type == "GROUP_AT_MESSAGE_CREATE")
    active = registry or DEFAULT_MATCHERS
    commands = (active.match_received(message) if hasattr(active, "match_received")
                else active.match(message.content, context))
    if not commands:
        return MessageDecision(None, "command_not_matched")
    return MessageDecision(IncomingMessage(message.app_id, message.conversation_id,
        message.user_id, message.message_id, message.timestamp, commands[0], message.scene, commands), "matched")


def inspect_message(app_id, payload, now, registry=None, excluded_group_authors=()):
    """Compatibility convenience; the Gateway never uses this pre-match path."""
    result = normalize_message(app_id, payload, now)
    if result.message is None:
        return MessageDecision(None, result.reason)
    return match_message(result.message, now, registry, excluded_group_authors)


def observe_message(app_id, payload, now):
    return normalize_message(app_id, payload, now).message


inspect_group_message = inspect_message


def group_message(app_id, payload, now):
    return inspect_message(app_id, payload, now).message
