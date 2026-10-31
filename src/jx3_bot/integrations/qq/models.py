"""Normalized official QQ group/C2C message data."""
from dataclasses import dataclass, asdict
import json

from ...core.models import Command


@dataclass(frozen=True)
class IncomingMessage:
    app_id: str
    group_id: str
    user_id: str
    message_id: str
    timestamp: float
    command: Command
    scene: str = "group"
    commands: tuple[Command, ...] = ()

    @property
    def conversation_id(self):
        return self.group_id  # Legacy field name; C2C uses author.user_openid as recipient.

    @property
    def key(self):
        scope = self.group_id if self.scene == "group" else json.dumps(["c2c", self.group_id], separators=(",", ":"))
        return (self.app_id, scope, self.message_id)

    @property
    def storage_key(self):
        return (self.app_id, self.scene, self.group_id, self.message_id)

    def to_dict(self):
        result = asdict(self)
        return result

    @classmethod
    def from_dict(cls, item):
        return cls(item["app_id"], item["group_id"], item["user_id"], item["message_id"],
                   item["timestamp"], Command(**item["command"]), item.get("scene", "group"),
                   tuple(Command(**c) for c in item.get("commands", ())))


@dataclass(frozen=True)
class MessageDecision:
    message: IncomingMessage | None
    reason: str


@dataclass(frozen=True)
class ReceivedMessage:
    """One bounded QQ projection, durably queued BEFORE business matching.

    user_id is the external OpenID here; chat repositories resolve their own
    INTEGER user_id foreign key. No raw author/event, attachment URLs or @ flag.
    """
    app_id: str
    scene: str
    conversation_id: str
    user_id: str
    message_id: str
    timestamp: float | None
    event_type: str
    content: str
    bot_author: bool = False
    self_author: bool = False
    truncated: bool = False
    sender_name: str | None = None
    is_bot: bool | None = None
    content_format: str = "text"
    received_at: float | None = None
    summary: str = ""

    @property
    def group_id(self):
        return self.conversation_id

    @property
    def key(self):
        scope = self.conversation_id if self.scene == "group" else json.dumps(
            ["c2c", self.conversation_id], separators=(",", ":"))
        return (self.app_id, scope, self.message_id)

    @property
    def storage_key(self):
        return (self.app_id, self.scene, self.conversation_id, self.message_id)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, item):
        return cls(**item)


@dataclass(frozen=True)
class ReceiveDecision:
    message: ReceivedMessage | None
    reason: str


@dataclass(frozen=True, slots=True)
class GroupMessageEvent:
    """Only the archived, bounded QQ group text visible to a business handler."""
    app_id: str
    message_id: str
    user_id: str
    content: str
    timestamp: float
    event_type: str
    sender_name: str | None
    group_id: str
    at_me: bool


@dataclass(frozen=True, slots=True)
class PrivateMessageEvent:
    """C2C has no at_me flag; never expose gateway/author raw payloads."""
    app_id: str
    message_id: str
    user_id: str
    content: str
    timestamp: float
    event_type: str
    sender_name: str | None
    user_openid: str


# Historical executable payloads remain readable during queue migration.
ObservedMessage = ReceivedMessage
GroupMessage = IncomingMessage
