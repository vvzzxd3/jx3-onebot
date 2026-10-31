"""QWeather JWT and budget-gated Geo/daily HTTP requests."""
import asyncio
from pathlib import Path
import time

import httpx

from ...errors import BotError, ConfigurationError, NoWeather, ProviderError
from .models import City, validate_city, normalized
from .repository import weather_repository


class WeatherAuth:
    def __init__(self, settings, clock=time.time, signer=None):
        self.settings, self.clock, self.signer = settings, clock, signer
        self._token, self._expires = "", 0
        self._key = None

    def headers(self):
        s = self.settings
        now = int(self.clock())
        if now >= self._expires - 60:
            if self.signer is None:
                try:
                    import jwt
                    from cryptography.hazmat.primitives import serialization
                    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                    if self._key is None:
                        key = serialization.load_pem_private_key(Path(s.private_key_file).read_bytes(), password=None)
                        if not isinstance(key, Ed25519PrivateKey):
                            raise ValueError
                        self._key = key
                    signer = jwt.encode
                except (ImportError, OSError, ValueError, TypeError):
                    raise ConfigurationError("Cannot load new Ed25519 key; install official dependencies and configure a safe key path") from None
            else:
                signer = self.signer
            self._expires = now + 900
            try:
                self._token = signer({"iss": s.developer_id, "sub": s.project_id, "iat": now-30, "exp": self._expires},
                    self._key, algorithm="EdDSA", headers={"kid": s.credential_id, "typ": "JWT"})
            except Exception:
                self._expires = 0
                raise ConfigurationError("QWeather JWT signing failed") from None
        return {"Authorization": "Bearer " + self._token}


class WeatherClient:
    def __init__(self, client, store, settings, auth=None, clock=time.time, sleep=asyncio.sleep):
        self.client, self.store, self.settings = client, store, settings
        self.clock, self.sleep = clock, sleep
        self.auth = auth or WeatherAuth(settings, clock)
        self.repository = weather_repository(store)
        self.repository.bind_timezone(settings.usage_timezone)

    async def _request(self, path, params):
        s = self.settings
        for attempt in range(s.retries + 1):
            headers = self.auth.headers()
            await asyncio.to_thread(self.repository.reserve, s.monthly_limit, s.usage_timezone, self.clock())
            try:
                response = await self.client.get(s.qweather_host + path, params=params, headers=headers, follow_redirects=False)
            except httpx.HTTPError:
                if attempt < s.retries:
                    await self.sleep(0.2 * 2**attempt)
                    continue
                raise ProviderError("weather-network") from None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < s.retries:
                    await self.sleep(0.2 * 2**attempt)
                    continue
            if response.status_code != 200:
                raise ProviderError("weather", response.status_code)
            try:
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                raise ProviderError("weather-invalid-json", response.status_code) from None
            return body
        raise ProviderError("weather")

    async def geo(self, query):
        params = {"location": query, "number": 10, "lang": "zh"}
        parts = query.split()
        administrative = len(parts) > 1 and any('\u4e00' <= c <= '\u9fff' for c in query)
        if administrative:
            params["location"], params["adm"] = parts[-1], parts[0]
        body = await self._request("/geo/v2/city/lookup", params)
        if str(body.get("code")) != "200":
            if str(body.get("code")) == "404":
                return []
            raise ProviderError("geo", 200, body.get("code", ""))
        locations = body.get("location")
        if not isinstance(locations, list):
            raise ProviderError("geo-format")
        result = []
        for item in locations[:10]:
            try:
                result.append(validate_city(City(str(item["id"]), str(item["name"]), "", str(item.get("adm1", "")),
                    str(item.get("adm2", "")), str(item.get("country", "")), str(item["lat"]), str(item["lon"]), str(item["tz"]), "geo")))
            except (KeyError, TypeError, BotError):
                raise ProviderError("geo-format") from None
        if administrative:
            result = [c for c in result if all(normalized(p) in normalized(c.label + c.country) for p in parts[:-1])]
        return result

    async def daily(self, city):
        latitude, longitude = city.coordinates()
        body = await self._request(f"/weather/v1/daily/{latitude}/{longitude}",
            {"days": self.settings.forecast_days, "localTime": "true", "lang": "zh"})
        # Deliberately keep only forecast fields and attribution, not location metadata.
        metadata = body.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("zeroResult") or not isinstance(body.get("days"), list) or not body["days"]:
            raise NoWeather("No daily forecast available")
        if not isinstance(metadata.get("attributions"), list) or not metadata["attributions"]:
            raise NoWeather("Forecast attribution is missing")
        return {"days": body["days"], "attributions": metadata["attributions"], "fetched_at": self.clock()}
