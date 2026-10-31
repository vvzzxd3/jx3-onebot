from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os

from .errors import ConfigurationError
from .observability import LogSettings
from .modules.chat_history.config import ChatSettings


def _integer(env, key, default, minimum, maximum):
    try:
        value = int(env.get(key, str(default)))
    except (TypeError, ValueError):
        raise ConfigurationError(f"{key} must be an integer") from None
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{key} is outside the supported range")
    return value


@dataclass(frozen=True)
class Settings:
    app_id: str
    client_secret: str = field(repr=False)
    qweather_host: str
    developer_id: str = ""
    project_id: str = ""
    credential_id: str = ""
    private_key_file: str = field(default="", repr=False)
    database: Path = Path("data/bot.sqlite3")
    monthly_limit: int = 50000
    usage_timezone: str = "Asia/Shanghai"
    forecast_days: int = 3
    cache_seconds: int = 3600
    retries: int = 1
    workers: int = 2
    ack_intervals: int = 2
    logs: LogSettings = field(default_factory=LogSettings)
    reply_format: str = "text"
    chat: ChatSettings = field(default_factory=ChatSettings, repr=False)
    command_prefix: str = ""

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        app_id = env.get("QQ_APP_ID", "").strip()
        secret = env.get("QQ_CLIENT_SECRET", "").strip()
        host = env.get("QWEATHER_API_HOST", "").strip().rstrip("/")
        if not app_id or not secret:
            raise ConfigurationError("QQ_APP_ID and QQ_CLIENT_SECRET are required")
        try:
            parsed = urlsplit(host)
            invalid = (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                       parsed.password or parsed.path or parsed.query or parsed.fragment or parsed.port)
        except ValueError:
            raise ConfigurationError("QWEATHER_API_HOST is malformed") from None
        if invalid:
            raise ConfigurationError("QWEATHER_API_HOST must be an HTTPS origin without path or credentials")
        if parsed.hostname in {"api.qweather.com", "devapi.qweather.com", "geoapi.qweather.com"}:
            raise ConfigurationError("Use the account-specific QWeather API Host")
        zone = env.get("QWEATHER_USAGE_TIMEZONE", "Asia/Shanghai")
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigurationError("QWEATHER_USAGE_TIMEZONE is invalid") from None
        jwt_fields = [env.get(x, "").strip() for x in (
            "QWEATHER_DEVELOPER_ID", "QWEATHER_PROJECT_ID", "QWEATHER_CREDENTIAL_ID", "QWEATHER_PRIVATE_KEY_FILE")]
        if not all(jwt_fields):
            raise ConfigurationError("Configure all four QWeather JWT settings")
        reply_format = env.get("QQ_REPLY_FORMAT", "text").strip().lower()
        if reply_format not in {"text", "markdown"}:
            raise ConfigurationError("QQ_REPLY_FORMAT must be text or markdown")
        prefix = env.get("BOT_COMMAND_PREFIX", "")
        if (not isinstance(prefix, str) or len(prefix) > 16
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in prefix)):
            raise ConfigurationError("BOT_COMMAND_PREFIX must be at most 16 non-whitespace printable characters")
        return cls(app_id, secret, host, *jwt_fields,
                   Path(env.get("BOT_DATABASE", "data/bot.sqlite3")),
                   _integer(env, "QWEATHER_MONTHLY_REQUEST_LIMIT", 50000, 0, 2**63-1), zone,
                   _integer(env, "QWEATHER_FORECAST_DAYS", 3, 1, 10),
                   _integer(env, "QWEATHER_CACHE_SECONDS", 3600, 0, 21600),
                   _integer(env, "QWEATHER_MAX_RETRIES", 1, 0, 2),
                   _integer(env, "BOT_WORKERS", 2, 1, 8),
                   _integer(env, "QQ_HEARTBEAT_ACK_INTERVALS", 2, 1, 5),
                   LogSettings.from_env(env), reply_format, ChatSettings.from_env(env), prefix)
