"""Weather forecast validation and conversion to shared replies."""
from datetime import datetime, timezone
import math
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ...errors import NoWeather
from ...replies import render_result as render_template

# The weather template contract belongs to the business, not the generic loader.
TEMPLATE_ARGUMENTS = dict.fromkeys((
    "forecast", "usage", "city_saved", "city_deleted", "city_current", "city_missing",
    "query_missing", "invalid", "ambiguous_local", "ambiguous_geo", "not_found", "budget_exhausted",
    "no_weather", "auth_error", "rate_limited", "unavailable", "expired", "route_unavailable", "output_too_long",
), frozenset())
TEMPLATE_ARGUMENTS.update({
    "forecast": {"label", "forecast", "updated", "sources"},
    "usage": {"month", "count", "limit", "remaining", "zone"},
    "city_saved": {"scope", "query"}, "city_current": {"scope", "query"},
    "city_deleted": {"scope"}, "ambiguous_local": {"choices"},
})


def render_result(name, **values):
    return render_template(name, module="weather", **values)


def quantity(item):
    if not isinstance(item, dict):
        return "暂无"
    value, unit = item.get("value"), item.get("unit", "")
    try:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            return "暂无"
        return f"{value:g}{_safe_text(unit, 10)}"
    except (OverflowError, ValueError):
        return "暂无"


def _safe_text(value, maximum=80):
    return str(value).replace("@", "").replace("<", "").replace(">", "").replace("\n", " ")[:maximum]


def format_forecast(data, label, zone, days, now, cached=False, show_timezone=True, as_reply=False):
    tz = ZoneInfo(zone)
    today = datetime.fromtimestamp(now, timezone.utc).astimezone(tz).date()
    lines = []
    for day in data["days"]:
        try:
            stamp = datetime.fromisoformat(day["forecastStartTime"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError
            date = stamp.astimezone(tz).date()
            if date < today:
                continue
            daytime, nighttime = day.get("daytime") or {}, day.get("nighttime") or {}
            high, low = day.get("temperatureMax"), day.get("temperatureMin")
            if high is None:
                high = daytime.get("temperatureMax")
            if low is None:
                low = nighttime.get("temperatureMin")
            text_day = _safe_text((daytime.get("condition") or {}).get("text", "暂无"))
            text_night = _safe_text((nighttime.get("condition") or {}).get("text", "暂无"))
            lines.append(f"📅 {date.isoformat()}\n天气：{text_day}／{text_night}\n气温：{quantity(low)}～{quantity(high)}")
            if len(lines) >= days:
                break
        except (KeyError, TypeError, ValueError, AttributeError):
            raise NoWeather("Daily forecast format is invalid") from None
    if not lines:
        raise NoWeather("Forecast is out of date")
    attributions = data.get("attributions", [])
    if not attributions:
        raise NoWeather("Forecast attribution is missing")
    for attribution in attributions:
        try:
            if not isinstance(attribution, str) or any(c in attribution for c in ('\r', '\n', ' ', '<', '>', '@')):
                raise ValueError
            parsed = urlsplit(attribution)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query:
                raise ValueError
        except (ValueError, TypeError):
            raise NoWeather("Forecast attribution is invalid") from None
    fetched = datetime.fromtimestamp(data["fetched_at"], timezone.utc).astimezone(tz).strftime("%m-%d %H:%M")
    zone_label = zone if show_timezone else "查询地点当地时间"
    reply = render_result("forecast", label=_safe_text(label), forecast="\n\n".join(lines),
                          updated=("缓存，" if cached else "") + f"获取于 {fetched}（{zone_label}）",
                          sources=" ".join(attributions))
    if len(reply.text) > 1800:
        raise NoWeather("Forecast reply is too long")
    return reply if as_reply else reply.text
