"""QQ-only, synchronous input matching and durable handler routing.

Inspired by NoneBot's public matcher concepts, not a compatibility layer.
Rules must be fast/pure; network calls belong to async handlers, never the receiver.
"""
from dataclasses import dataclass, replace
import re

from .errors import ConfigurationError
from .core.models import Command, MatchContext


def to_me(context):
    return context.to_me


@dataclass
class Matcher:
    name: str
    kind: str
    extract: object
    priority: int = 10
    block: bool = True
    scenes: frozenset = frozenset({"group", "c2c"})
    rule: object = None
    validator: object = None
    handler: object = None

    def handle(self, handler):
        """Decorator registering one async handler(message)."""
        if self.handler is not None:
            raise ConfigurationError("Matcher already has a handler")
        self.handler = handler
        return handler


class MatcherRegistry:
    def __init__(self):
        self._matchers = {}

    def _register(self, name, kind, extract, **options):
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", name) or name in self._matchers:
            raise ConfigurationError("Matcher name must be stable and unique")
        matcher = Matcher(name, kind, extract, **options)
        matcher.scenes = frozenset(matcher.scenes)
        if (isinstance(matcher.priority, bool) or not isinstance(matcher.priority, int)
                or matcher.priority < 1 or not matcher.scenes
                or not matcher.scenes <= {"group", "c2c"}):
            raise ConfigurationError("Invalid matcher priority/scenes")
        self._matchers[name] = matcher
        return matcher

    def on_command(self, name, command, *, aliases=(), kind=None, **options):
        words = (command, *aliases)
        if any(not isinstance(w, str) or not w or any(c.isspace() for c in w) for w in words):
            raise ConfigurationError("Command names must be nonempty words")
        pattern = re.compile(r"(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
                             + r")(?:\s+(.+))?", re.DOTALL)
        def extract(text):
            match = pattern.fullmatch(text)
            return (match[1] or "").strip() if match else None
        return self._register(name, kind or name, extract, **options)

    def on_fullmatch(self, name, texts, *, kind=None, **options):
        words = (texts,) if isinstance(texts, str) else tuple(texts)
        return self._register(name, kind or name, lambda text: "" if text in words else None, **options)

    def on_regex(self, name, pattern, *, flags=0, kind=None, **options):
        compiled = re.compile(pattern, flags)
        def extract(text):
            match = compiled.search(text)
            return match.group(0) if match else None
        return self._register(name, kind or name, extract, **options)

    def on_message(self, name, *, kind=None, **options):
        # Opt-in only: registering a catch-all intentionally changes chat retention.
        return self._register(name, kind or name, lambda text: text, **options)

    def match(self, content, context):
        if not isinstance(content, str) or len(content) > 4096:
            return ()
        text = content.strip()
        matches, blocked_priority = [], None
        for matcher in sorted(self._matchers.values(), key=lambda m: m.priority):
            if blocked_priority is not None and matcher.priority > blocked_priority:
                break
            if context.scene not in matcher.scenes or (matcher.rule and not matcher.rule(context)):
                continue
            argument = matcher.extract(text)
            if argument is None:
                continue
            valid = not matcher.validator or matcher.validator(argument)
            # Invalid command input must not be persisted, even for a custom rule.
            if len(argument) > 1024 or "\x00" in argument:
                valid = False
            matches.append(Command(matcher.kind if valid else "invalid", argument if valid else "", matcher.name))
            if matcher.block:
                blocked_priority = matcher.priority
            if len(matches) > 8:
                raise ConfigurationError("Too many matching handlers for one message")
        return tuple(matches)

    async def dispatch(self, message):
        results = []
        for command in message.commands or (message.command,):
            matcher = self._matchers.get(command.matcher)
            # Old weather inbox payloads did not carry matcher names.
            if matcher is None and not command.matcher:
                matcher = next((m for m in self._matchers.values() if m.kind == command.kind), None)
                if matcher is None and command.kind == "invalid":
                    matcher = self._matchers.get("weather.query")
            if matcher is None or matcher.handler is None or message.scene not in matcher.scenes:
                from .replies import render_result
                return [render_result("route_unavailable")]
            result = await matcher.handler(replace(message, command=command, commands=()))
            if result is not None:
                results.append(result)
        return results


def weather_registry(handler=None):
    """Legacy assembly helper; weather commands are defined in their module."""
    from .modules.weather.handlers import weather_registry as build_registry
    return build_registry(handler)


DEFAULT_MATCHERS = weather_registry()

def parse_weather_command(content):
    from .modules.weather.handlers import parse_command
    return parse_command(content)
