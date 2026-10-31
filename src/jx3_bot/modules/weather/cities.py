"""Official public city CSV download, validation and atomic import."""
import base64
import csv
import hashlib
import io
from datetime import datetime, timezone

from ...errors import BotError, ProviderError
from .models import City, validate_city
from .repository import weather_repository

OFFICIAL_API = "https://api.github.com/repos/qwd/LocationList/contents/China-City-List-latest.csv"
OFFICIAL_PAGE = "https://dev.qweather.com/docs/resource/location-list/"
MAX_BYTES = 2_000_000


def parse_csv(raw):
    if len(raw) > MAX_BYTES:
        raise BotError("City CSV exceeds the import size limit")
    try:
        text = raw.decode("utf-8-sig")
        first, remaining = text.split("\n", 1)
        if first.startswith("Location_ID,"):
            version, remaining = "unversioned", text
        elif first.startswith("China-City-List "):
            version = first.split(",", 1)[0].strip()
        else:
            raise ValueError
        reader = csv.DictReader(io.StringIO(remaining))
        required = {"Location_ID", "Location_Name_EN", "Location_Name_ZH", "Adm1_Name_ZH", "Adm2_Name_ZH", "ISO_3166_1", "Latitude", "Longitude", "Timezone"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError
        result, seen = [], set()
        for row in reader:
            if None in row or any(row.get(k) is None for k in required):
                raise ValueError
            if not row["Location_ID"].strip() or row["Location_ID"] in seen:
                raise ValueError
            seen.add(row["Location_ID"])
            city = validate_city(City(row["Location_ID"].strip(), row["Location_Name_ZH"].strip(),
                row["Location_Name_EN"].strip(), row["Adm1_Name_ZH"].strip(), row["Adm2_Name_ZH"].strip(),
                row["ISO_3166_1"].strip(), row["Latitude"].strip(), row["Longitude"].strip(), row["Timezone"].strip()))
            result.append(city)
        if not result:
            raise ValueError
        return result, version
    except (UnicodeError, ValueError, KeyError, csv.Error, AttributeError):
        raise BotError("Invalid official city CSV; existing city data is unchanged") from None


def import_csv(store, raw, source=OFFICIAL_PAGE, revision=None):
    cities, version = parse_csv(raw)
    provenance = {"source": source, "version": version, "revision": revision,
                  "sha256": hashlib.sha256(raw).hexdigest(), "records": len(cities),
                  "imported_at": datetime.now(timezone.utc).isoformat()}
    weather_repository(store).replace_cities(cities, provenance)
    return provenance


async def download_official(client):
    # Public GitHub download, not a paid QWeather data request. No bot secrets.
    try:
        async with client.stream("GET", OFFICIAL_API, headers={"Accept": "application/vnd.github+json", "User-Agent": "jx3-official-city-import"}) as response:
            if response.status_code != 200:
                raise ProviderError("city-download", response.status_code)
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES * 2:
                    raise BotError("City download metadata exceeds size limit")
                chunks.append(chunk)
        import json
        metadata = json.loads(b"".join(chunks))
        if metadata.get("encoding") != "base64" or metadata.get("size", MAX_BYTES+1) > MAX_BYTES:
            raise ValueError
        raw = base64.b64decode(metadata["content"], validate=False)
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if actual != metadata.get("sha"):
            raise ValueError
        return raw, metadata["sha"]
    except (KeyError, ValueError, TypeError):
        raise BotError("Invalid city download metadata") from None
