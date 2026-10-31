class BotError(Exception):
    """Safe, non-secret application error."""


class ConfigurationError(BotError):
    pass


class ReplyTooLong(ConfigurationError):
    """Rendered output cannot fit one conservative passive message."""


class StorageError(BotError):
    pass


class BudgetExhausted(BotError):
    pass


class ProviderError(BotError):
    def __init__(self, provider: str, status: int = 0, code: str = ""):
        self.provider, self.status, self.code = provider, status, str(code)
        super().__init__(f"{provider} request failed (status={status})")


class ReplyNotSent(BotError):
    """Failure before a message HTTP request began; safe to retry within deadline."""


class ReplyExpired(BotError):
    """Passive deadline elapsed before the message POST began; never retry."""


class NoWeather(BotError):
    pass


class GatewayFatal(BotError):
    pass
