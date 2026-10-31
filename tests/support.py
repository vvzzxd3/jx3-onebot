from dataclasses import replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import csv
import io

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jx3_bot.config import Settings
from jx3_bot.models import City, Command, GroupMessage
from jx3_bot.weather import WeatherAuth
from jx3_bot.modules.chat_history import ChatSettings


def enable_chat(store):
    """Configure a synthetic fixture; supported chats persist by default."""
    store.chat.configure(ChatSettings())


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc).timestamp()
    def __call__(self):
        return self.now


def settings(database, **kwargs):
    result = Settings("test-app", "synthetic-secret", "https://test.qweatherapi.com",
                      developer_id="synthetic-developer", project_id="synthetic-project",
                      credential_id="synthetic-kid", private_key_file="synthetic-unused.pem",
                      database=Path(database), retries=0)
    return replace(result, **kwargs)


@lru_cache(maxsize=1)
def _test_signing_key():
    return Ed25519PrivateKey.generate()


def weather_auth(settings, clock):
    # Sign real JWTs using a generated in-memory test key, never an operator key file.
    def signer(payload, unused_key, **kwargs):
        return jwt.encode(payload, _test_signing_key(), **kwargs)
    return WeatherAuth(settings, clock, signer=signer)


def city(id="101010100", name="北京", adm1="北京市", adm2="北京"):
    return City(id, name, "Beijing" if name == "北京" else "", adm1, adm2, "CN", "39.90498", "116.40528", "Asia/Shanghai")


def csv_bytes(cities=None):
    out = io.StringIO()
    out.write("China-City-List test-version\n")
    writer = csv.writer(out)
    writer.writerow(["Location_ID", "Location_Name_EN", "Location_Name_ZH", "Adm1_Name_ZH", "Adm2_Name_ZH", "ISO_3166_1", "Latitude", "Longitude", "Timezone"])
    for c in cities or [city()]:
        writer.writerow([c.id, c.name_en, c.name, c.adm1, c.adm2, c.country, c.latitude, c.longitude, c.timezone])
    return out.getvalue().encode()


def message(clock, content="北京", id="m1", kind="weather", group="g", user="u", app="test-app"):
    return GroupMessage(app, group, user, id, clock(), Command(kind, content))


def event(clock, seq=1, id="m1", event_type="GROUP_MESSAGE_CREATE", content="天气 北京"):
    return {"op": 0, "s": seq, "id": "outer-not-message-id", "t": event_type,
            "d": {"id": id, "group_openid": "g", "author": {"member_openid": "u"},
                  "timestamp": datetime.fromtimestamp(clock(), timezone.utc).isoformat(), "content": content}}


def daily_body():
    return {"metadata": {"attributions": ["https://developer.qweather.com/attribution.html"]},
            "days": [{"forecastStartTime": "2026-10-10T06:00:00+08:00",
                      "temperatureMax": {"value": 25, "unit": "°C"}, "temperatureMin": {"value": 12.5, "unit": "°C"},
                      "daytime": {"condition": {"text": "晴"}}, "nighttime": {"condition": {"text": "多云"}}}]}
