"""Weather-domain city data and validation; forecast DTOs are a later step."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import unicodedata

from ...errors import BotError


@dataclass(frozen=True)
class City:
    id: str
    name: str
    name_en: str
    adm1: str
    adm2: str
    country: str
    latitude: str
    longitude: str
    timezone: str
    source: str = "official-list"

    @property
    def label(self):
        return "/".join(dict.fromkeys(x for x in (self.adm1, self.adm2, self.name) if x))

    def coordinates(self):
        return coordinate(self.latitude, True), coordinate(self.longitude, False)


def coordinate(value, latitude):
    try:
        number = Decimal(str(value))
        bound = Decimal(90 if latitude else 180)
        if not number.is_finite() or not -bound <= number <= bound:
            raise ValueError
        rounded = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if rounded == 0:
            rounded = abs(rounded)
        return format(rounded, ".2f")
    except (InvalidOperation, ValueError, TypeError):
        raise BotError("Invalid location coordinates") from None


def validate_city(city):
    if not city.id or not city.name or not city.timezone:
        raise BotError("Incomplete city record")
    city.coordinates()
    try:
        ZoneInfo(city.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise BotError("Invalid location timezone") from None
    return city


def normalized(value):
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def name_aliases(city):
    aliases = {city.id, normalized(city.name), normalized(city.name_en)}
    if city.name.endswith(("市", "区", "县")) and len(city.name) > 1:
        aliases.add(normalized(city.name[:-1]))
    return aliases - {""}
