"""Weather commands, preferences, city resolution and forecast/cache orchestration."""
import asyncio
from datetime import datetime, timezone, timedelta, time as day_time
import json
import time
from zoneinfo import ZoneInfo
import weakref

from ...errors import BudgetExhausted, NoWeather, ProviderError
from ...observability import DEFAULT_OBSERVER
from .rendering import format_forecast, render_result
from .repository import weather_repository


class WeatherService:
    def __init__(self, store, client, settings, clock=time.time, observer=None):
        self.store, self.client, self.settings, self.clock = store, client, settings, clock
        self.repository = weather_repository(store)
        self.observer = observer or DEFAULT_OBSERVER
        self._locks = weakref.WeakValueDictionary()

    async def handle(self, message):
        """Legacy text interface; production routing uses the structured result."""
        return (await self.handle_result(message)).text

    async def handle_result(self, message):
        cmd, s = message.command, self.settings
        scope = "本群" if message.scene == "group" else "本次单聊"
        if cmd.kind == "invalid":
            return render_result("invalid")
        if cmd.kind == "usage":
            month, count = await asyncio.to_thread(self.repository.usage, s.usage_timezone, self.clock())
            return render_result("usage", month=month, count=count, limit=s.monthly_limit,
                                 remaining=max(0, s.monthly_limit-count), zone=s.usage_timezone)
        if cmd.kind == "city":
            if cmd.argument == "删除":
                await asyncio.to_thread(self.repository.set_preference, message.app_id, message.group_id, message.user_id, None, message.scene)
                return render_result("city_deleted", scope=scope)
            if cmd.argument:
                await asyncio.to_thread(self.repository.set_preference, message.app_id, message.group_id, message.user_id, cmd.argument, message.scene)
                return render_result("city_saved", scope=scope, query=cmd.argument)
            value = await asyncio.to_thread(self.repository.preference, message.app_id, message.group_id, message.user_id, message.scene)
            return render_result("city_current", scope=scope, query=value) if value else render_result("city_missing")
        query = cmd.argument or await asyncio.to_thread(self.repository.preference, message.app_id, message.group_id, message.user_id, message.scene)
        if not query:
            return render_result("query_missing")
        try:
            cities = await asyncio.to_thread(self.repository.find_cities, query)
            if not cities:
                cities = await self.client.geo(query)
                if len(cities) > 1:
                    # Do not persist API candidates/names in reply outbox.
                    return render_result("ambiguous_geo")
            if not cities:
                return render_result("not_found")
            if len(cities) > 1:
                labels = [f"{index}. {c.label}（天气 {c.id}）" for index, c in enumerate(cities[:8], 1)]
                return render_result("ambiguous_local", choices="\n".join(labels))
            city = cities[0]
            if city.source == "geo":
                data = await self.client.daily(city)
                return format_forecast(data, query, city.timezone, s.forecast_days, self.clock(), show_timezone=False, as_reply=True)
            lat, lon = city.coordinates()
            key = json.dumps(["v1", city.id, lat, lon, s.forecast_days, "zh", True, city.timezone])
            lock = self._locks.setdefault(key, asyncio.Lock())
            async with lock:
                now = self.clock()
                data = await asyncio.to_thread(self.repository.cache_get, key, now)
                cached = data is not None
                if data is None:
                    data = await self.client.daily(city)
                    # Validate response before saving, including mandatory attribution.
                    format_forecast(data, city.label, city.timezone, s.forecast_days, now)
                    if s.cache_seconds > 0:
                        tz = ZoneInfo(city.timezone)
                        date = datetime.fromtimestamp(now, timezone.utc).astimezone(tz).date()
                        midnight = datetime.combine(date + timedelta(days=1), day_time.min, tz).timestamp()
                        await asyncio.to_thread(self.repository.cache_put, key, data, min(now+s.cache_seconds, midnight))
                return format_forecast(data, city.label, city.timezone, s.forecast_days, self.clock(), cached, as_reply=True)
        except BudgetExhausted:
            self.observer.event("weather.result", trace=self.observer.trace(*message.key), result="budget_exhausted")
            return render_result("budget_exhausted")
        except NoWeather:
            self.observer.event("weather.result", trace=self.observer.trace(*message.key), result="no_weather")
            return render_result("no_weather")
        except ProviderError as error:
            self.observer.event("weather.result", trace=self.observer.trace(*message.key), result="provider_error",
                                status=error.status, code=self.observer.code(error.code))
            if error.status in {401, 403}:
                return render_result("auth_error")
            if error.status == 429:
                return render_result("rate_limited")
            return render_result("unavailable")
