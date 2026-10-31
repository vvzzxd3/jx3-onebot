"""Chat archival is always on for supported QQ scenes; no scope or TTL opt-in."""
from dataclasses import dataclass

from ...errors import ConfigurationError


def valid_identity(value):
    return (isinstance(value, str) and 0 < len(value) <= 256
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value))


@dataclass(frozen=True)
class ChatSettings:
    max_text_chars: int = 16384

    def __post_init__(self):
        if type(self.max_text_chars) is not int or not 128 <= self.max_text_chars <= 65536:
            raise ConfigurationError("BOT_CHAT_MAX_TEXT_CHARS is outside its permitted integer range")

    @classmethod
    def from_env(cls, env):
        # Retired BOT_CHAT_ENABLED/GROUPS/USERS/TEXT_DAYS/DEDUP_DAYS are ignored:
        # old environment files must not turn off, scope, or expire chat history.
        value = env.get("BOT_CHAT_MAX_TEXT_CHARS", "16384")
        if not isinstance(value, str) or not value.strip().isdigit() or len(value.strip()) > 10:
            raise ConfigurationError("BOT_CHAT_MAX_TEXT_CHARS must be an integer")
        return cls(max_text_chars=int(value.strip()))

    def allows(self, scene, conversation):
        return scene in ("group", "c2c") and valid_identity(conversation)
