"""Four-table, authorized QQ chat history using the shared Store transaction."""
from .config import ChatSettings
from .repository import ChatRepository, SCHEMA

__all__ = ("ChatSettings", "ChatRepository", "SCHEMA")
