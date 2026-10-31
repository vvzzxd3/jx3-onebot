"""Legacy model exports; definitions belong to core, QQ integration or weather."""
from .core.models import Command
from .integrations.qq.models import GroupMessage, IncomingMessage, MessageDecision
from .integrations.qq.events import group_message, inspect_group_message, inspect_message
from .modules.weather.models import City, coordinate, name_aliases, normalized, validate_city
from .modules.weather.handlers import parse_command

__all__ = [
    "City", "coordinate", "name_aliases", "normalized", "validate_city",
    "Command", "parse_command", "GroupMessage", "IncomingMessage", "MessageDecision",
    "group_message", "inspect_group_message", "inspect_message",
]
