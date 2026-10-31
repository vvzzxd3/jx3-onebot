"""Compatibility exports; implement weather features in modules/weather only."""
from .modules.weather.client import WeatherAuth, WeatherClient
from .modules.weather.rendering import format_forecast, quantity
from .modules.weather.service import WeatherService

__all__ = ["WeatherAuth", "WeatherClient", "WeatherService", "format_forecast", "quantity"]
