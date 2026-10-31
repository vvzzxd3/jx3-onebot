"""Weather command registration and compatibility parsing."""
from ...core.models import Command, MatchContext
from ...integrations.qq.models import GroupMessageEvent, IncomingMessage, PrivateMessageEvent
from ...routing import Inject, on_group, on_private
from .rendering import render_result
from .service import WeatherService


def _city_input(argument):
    return len(argument) <= 80 and not any(char in argument for char in "<>@:/\\\x00\r\n")


def _weather_message(event, kind, args):
    scene = "group" if isinstance(event, GroupMessageEvent) else "c2c"
    group_id = event.group_id if scene == "group" else event.user_openid
    return IncomingMessage(event.app_id, group_id, event.user_id, event.message_id,
                           event.timestamp, Command(kind, args), scene)


@on_group(match="天气城市")
@on_group(match="天气城市", at_me=True)
@on_private(match="天气城市")
async def city(event: GroupMessageEvent | PrivateMessageEvent, args: str,
               weather: WeatherService = Inject("weather")):
    if not _city_input(args):
        return render_result("invalid")
    return await weather.handle_result(_weather_message(event, "city", args))


@on_group(match="天气额度")
@on_group(match="天气额度", at_me=True)
@on_private(match="天气额度")
async def usage(event: GroupMessageEvent | PrivateMessageEvent, args: str,
                weather: WeatherService = Inject("weather")):
    if args:
        return render_result("invalid")
    return await weather.handle_result(_weather_message(event, "usage", args))


@on_group(match="天气")
@on_group(match="天气", at_me=True)
@on_private(match="天气")
async def query(event: GroupMessageEvent | PrivateMessageEvent, args: str,
                weather: WeatherService = Inject("weather")):
    if not _city_input(args):
        return render_result("invalid")
    return await weather.handle_result(_weather_message(event, "weather", args))


def weather_registry(handler=None):
    from ...matching import MatcherRegistry
    registry = MatcherRegistry()
    def city_input(argument):
        return len(argument) <= 80 and not any(c in argument for c in "<>@:/\\\x00\r\n")
    for name, word, kind, validator in (
            ("weather.city", "天气城市", "city", city_input),
            ("weather.usage", "天气额度", "usage", lambda arg: not arg),
            ("weather.query", "天气", "weather", city_input)):
        matcher = registry.on_command(name, word, kind=kind, validator=validator)
        if handler is not None:
            matcher.handle(handler)
    return registry


def parse_command(content):
    from ...matching import DEFAULT_MATCHERS
    matches = DEFAULT_MATCHERS.match(content, MatchContext("group"))
    return matches[0] if matches else None
