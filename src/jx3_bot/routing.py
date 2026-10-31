"""QQ-only business decorators and deterministic per-application route compilation.

Importing a handler records metadata only. The application owns discovery, services,
matching after durable receipt, and exactly one passive reply plan.
"""
from dataclasses import dataclass
import importlib
import inspect
import pkgutil
import re
from typing import get_args, get_origin, get_type_hints

from .core.models import Command, Reply
from .errors import ConfigurationError
from .integrations.qq.events import business_event
from .integrations.qq.models import GroupMessageEvent, PrivateMessageEvent, ReceivedMessage


@dataclass(frozen=True)
class Inject:
    name: str

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", self.name):
            raise ConfigurationError("Invalid service injection name")


@dataclass(frozen=True)
class _Rule:
    scene: str
    at_me: bool | None
    match: str | None
    priority: int
    block: bool


def _decorate(scene, at_me, match, priority, block):
    if (match is not None and (not isinstance(match, str) or not match
                               or any(c.isspace() for c in match))):
        raise ConfigurationError("match must be one nonempty keyword")
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
        raise ConfigurationError("Invalid route priority")
    if not isinstance(block, bool):
        raise ConfigurationError("block must be a bool")
    rule = _Rule(scene, at_me, match, priority, block)

    def register(function):
        if not inspect.iscoroutinefunction(function):
            raise ConfigurationError("Route handler must be async")
        # Attribute assignment does not enroll the handler in a process-global registry.
        function.__qq_routes__ = (*getattr(function, "__qq_routes__", ()), rule)
        return function
    return register


def on_group(*, match: str | None = None, at_me: bool = False,
             priority: int = 10, block: bool = True):
    if not isinstance(at_me, bool):
        raise ConfigurationError("at_me must be a bool")
    return _decorate("group", at_me, match, priority, block)


def on_private(*, match: str | None = None, priority: int = 10,
               block: bool = True):
    return _decorate("c2c", None, match, priority, block)


def _accepts_event(annotation, expected):
    if annotation is expected:
        return True
    arguments = get_args(annotation)
    return bool(arguments) and expected in arguments and all(
        item in (GroupMessageEvent, PrivateMessageEvent) for item in arguments)


def _bind(function, rules, services):
    if not inspect.iscoroutinefunction(function):
        raise ConfigurationError("Route handler must be async")
    try:
        hints = get_type_hints(function)
        params = inspect.signature(function).parameters
    except (TypeError, ValueError, NameError) as error:
        raise ConfigurationError("Invalid handler signature or annotation") from error
    allowed_events = {"group": GroupMessageEvent, "c2c": PrivateMessageEvent}
    arguments = {}
    for name, param in params.items():
        if param.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            raise ConfigurationError("Route parameters must be named keyword-compatible parameters")
        if name == "event":
            if param.default is not inspect.Parameter.empty or not all(
                    _accepts_event(hints.get(name), allowed_events[rule.scene]) for rule in rules):
                raise ConfigurationError("Incompatible event annotation")
        elif name == "args":
            if (param.default is not inspect.Parameter.empty or hints.get(name) is not str
                    or any(rule.match is None for rule in rules)):
                raise ConfigurationError("args requires a match keyword and str annotation")
        elif isinstance(param.default, Inject):
            key = param.default.name
            if key not in services or key in ("store", "qq", "config", "gateway", "raw_payload"):
                raise ConfigurationError("Unregistered or unsafe service injection")
            annotation = hints.get(name)
            if not isinstance(annotation, type) or not isinstance(services[key], annotation):
                raise ConfigurationError("Incompatible service annotation")
            value = services[key]
            # Never inject application infrastructure even if supplied under an alias.
            if (type(value).__name__.lower() in ("store", "gateway", "application", "qqhttpclient", "config")
                    or type(value).__module__.startswith(("jx3_bot.qq_http", "jx3_bot.gateway", "jx3_bot.store", "jx3_bot.config"))):
                raise ConfigurationError("Unsafe service injection")
            arguments[name] = value
        else:
            raise ConfigurationError("Unsupported route parameter")
    return arguments


@dataclass(frozen=True)
class _Compiled:
    key: str
    function: object
    rule: _Rule
    injections: dict
    order: int


class RouteRegistry:
    """Routes belong to one application instance, never to module import state."""

    def __init__(self, routes, command_prefix=""):
        if not isinstance(command_prefix, str) or len(command_prefix) > 16 or any(
                c.isspace() or ord(c) < 32 or ord(c) == 127 for c in command_prefix):
            raise ConfigurationError("Invalid command prefix")
        self.command_prefix = command_prefix
        self._routes = tuple(sorted(routes, key=lambda route: (route.rule.priority, route.order)))
        self._by_key = {route.key: route for route in routes}
        if len(self._by_key) != len(routes):
            raise ConfigurationError("Duplicate route identity")

    def match_received(self, received: ReceivedMessage):
        try:
            event = business_event(received)
        except ValueError:
            return ()
        text = event.content.strip()
        matches, invoked, blocked = [], set(), None
        for route in self._routes:
            rule = route.rule
            if blocked is not None and rule.priority > blocked:
                break
            if (route.function in invoked or
                    rule.scene != received.scene or
                    (rule.at_me is not None and rule.at_me != event.at_me)):
                continue
            if rule.match is None:
                argument = ""
            else:
                keyword = self.command_prefix + rule.match
                if text == keyword:
                    argument = ""
                elif text.startswith(keyword) and len(text) > len(keyword) and text[len(keyword)].isspace():
                    argument = text[len(keyword):].strip()
                    if not argument:
                        argument = ""  # trailing whitespace is the no-argument form
                else:
                    continue
            if len(argument) > 1024 or "\x00" in argument:
                continue
            matches.append(Command(route.key, argument, route.key))
            invoked.add(route.function)
            if rule.block:
                blocked = rule.priority
            if len(matches) > 8:
                raise ConfigurationError("Too many matching handlers for one message")
        return tuple(matches)

    async def dispatch(self, message, *, received: ReceivedMessage | None = None):
        if received is None or not isinstance(received, ReceivedMessage):
            raise ConfigurationError("A cleaned received event is required for routing")
        if (message.storage_key != received.storage_key or message.timestamp != received.timestamp
                or received.bot_author or received.self_author):
            raise ConfigurationError("Mismatched or unsafe event projection")
        event = business_event(received)
        permitted = self.match_received(received)
        requested = message.commands or (message.command,)
        if any(command not in permitted for command in requested):
            raise ConfigurationError("Dispatch command does not match this event")
        results, invoked = [], set()
        for command in requested:
            route = self._by_key.get(command.matcher)
            if route is None or route.function in invoked or command.kind != route.key:
                raise ConfigurationError("Unknown or duplicate route")
            kwargs = dict(route.injections)
            params = inspect.signature(route.function).parameters
            if "event" in params:
                kwargs["event"] = event
            if "args" in params:
                kwargs["args"] = command.argument
            result = await route.function(**kwargs)
            if result is not None and not isinstance(result, (Reply, str)):
                raise ConfigurationError("Route must return Reply, str or None")
            if result is not None:
                results.append(result)
            invoked.add(route.function)
        return results


def build_registry(handlers, *, services=None, command_prefix=""):
    """Compile trusted handler functions into an isolated deterministic registry."""
    services = dict(services or {})
    seen, routes = set(), []
    for function in sorted(handlers, key=lambda item: (item.__module__, item.__qualname__)):
        if function in seen:
            raise ConfigurationError("Duplicate route handler")
        seen.add(function)
        rules = getattr(function, "__qq_routes__", ())
        if not rules:
            raise ConfigurationError("Handler has no QQ route decorator")
        injections = _bind(function, rules, services)
        for index, rule in enumerate(reversed(rules)):
            key = f"{function.__module__}.{function.__qualname__}:{index}"
            routes.append(_Compiled(key, function, rule, injections, len(routes)))
    return RouteRegistry(routes, command_prefix)


def discover_routes(*, services=None, command_prefix=""):
    """Import only sorted first-party jx3_bot.modules.<business>.handlers modules."""
    from . import modules
    handlers = []
    for info in sorted(pkgutil.iter_modules(modules.__path__), key=lambda item: item.name):
        if not info.ispkg or info.name.startswith("_") or not info.name.isidentifier():
            continue
        module_name = f"{modules.__name__}.{info.name}.handlers"
        try:
            spec = importlib.util.find_spec(module_name)
        except ModuleNotFoundError:
            continue  # Existing business modules need not define handlers.
        if spec is None:
            continue
        module = importlib.import_module(module_name)
        for name, function in sorted(vars(module).items()):
            if (inspect.iscoroutinefunction(function) and function.__module__ == module.__name__
                    and getattr(function, "__qq_routes__", ())):
                handlers.append(function)
    return build_registry(handlers, services=services, command_prefix=command_prefix)
