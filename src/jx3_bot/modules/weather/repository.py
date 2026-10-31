"""Weather SQL using the application's existing connection, lock and transactions."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo

from ...errors import BudgetExhausted, StorageError
from .models import City, normalized, name_aliases


class WeatherRepository:
    def __init__(self, storage):
        self.storage = storage

    def bind_timezone(self, zone):
        ZoneInfo(zone)
        with self.storage.transaction() as db:
            row = db.execute("SELECT value FROM meta WHERE key='usage_timezone'").fetchone()
            if row and json.loads(row[0]) != zone:
                raise StorageError("Budget timezone differs from stored timezone; explicit migration required")
            self.storage._meta(db, "usage_timezone", zone)

    def reserve(self, limit, zone, now):
        if limit < 0:
            raise ValueError("Negative budget")
        month = datetime.fromtimestamp(now, timezone.utc).astimezone(ZoneInfo(zone)).strftime("%Y-%m")
        with self.storage.transaction() as db:
            bound = db.execute("SELECT value FROM meta WHERE key='usage_timezone'").fetchone()
            if not bound or json.loads(bound[0]) != zone:
                raise StorageError("Budget timezone is not bound")
            last = db.execute("SELECT value FROM meta WHERE key='last_usage_month'").fetchone()
            if last and month < json.loads(last[0]):
                raise StorageError("Clock moved to an earlier budget month")
            row = db.execute("SELECT attempts FROM usage WHERE month=?", (month,)).fetchone()
            count = row[0] if row else 0
            if count >= limit:
                raise BudgetExhausted("Weather monthly request limit reached")
            db.execute("INSERT INTO usage VALUES(?,1) ON CONFLICT(month) DO UPDATE SET attempts=attempts+1", (month,))
            self.storage._meta(db, "last_usage_month", month)
            return count + 1

    def usage(self, zone, now):
        month = datetime.fromtimestamp(now, timezone.utc).astimezone(ZoneInfo(zone)).strftime("%Y-%m")
        rows = self.storage._read("SELECT attempts FROM usage WHERE month=?", (month,))
        return month, rows[0][0] if rows else 0

    def replace_cities(self, cities, provenance):
        if not cities:
            raise ValueError("City list is empty")
        with self.storage.transaction() as db:
            db.execute("DELETE FROM cities")
            for city in cities:
                db.execute("INSERT INTO cities VALUES(?,?)", (city.id, json.dumps(asdict(city), ensure_ascii=False)))
                db.executemany("INSERT INTO aliases VALUES(?,?)", ((a, city.id) for a in name_aliases(city)))
            self.storage._meta(db, "city_source", provenance)
            db.execute("DELETE FROM cache")

    def find_cities(self, query):
        def lookup(value):
            variants = [normalized(value)]
            if value.endswith(('市', '区', '县')) and len(value) > 1:
                variants.append(normalized(value[:-1]))
            for alias in variants:
                matches = [City(**json.loads(r[0])) for r in self.storage._read(
                    "SELECT cities.data FROM aliases JOIN cities ON cities.id=aliases.city_id WHERE aliases.name=? ORDER BY cities.id LIMIT 20", (alias,))]
                if matches:
                    return matches
            return []
        direct = lookup(query)
        if direct:
            return direct
        parts = query.split()
        if len(parts) < 2:
            return []
        matches = lookup(parts[-1])
        return [c for c in matches if all(normalized(p) in normalized(c.label + c.country) for p in parts[:-1])]

    def preference(self, app, group, user, scene="group"):
        rows = self.storage._read("SELECT input FROM preferences WHERE app=? AND scene=? AND grp=? AND usr=?", (app, scene, group, user))
        return rows[0][0] if rows else None

    def set_preference(self, app, group, user, value, scene="group"):
        with self.storage.transaction() as db:
            if value is None:
                db.execute("DELETE FROM preferences WHERE app=? AND scene=? AND grp=? AND usr=?", (app, scene, group, user))
            else:
                db.execute("INSERT INTO preferences(app,grp,usr,input,scene) VALUES(?,?,?,?,?) ON CONFLICT(app,scene,grp,usr) DO UPDATE SET input=excluded.input", (app, group, user, value, scene))

    def cache_get(self, key, now):
        rows = self.storage._read("SELECT data FROM cache WHERE key=? AND expires>?", (key, now))
        return json.loads(rows[0][0]) if rows else None

    def cache_put(self, key, data, expires):
        with self.storage.transaction() as db:
            db.execute("INSERT INTO cache VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data,expires=excluded.expires", (key, json.dumps(data, ensure_ascii=False), expires))
            db.execute("DELETE FROM cache WHERE key NOT IN (SELECT key FROM cache ORDER BY expires DESC LIMIT 256)")


def weather_repository(storage):
    """Accept the module repository or the historical shared Store constructor arg."""
    return storage if isinstance(storage, WeatherRepository) else storage.weather
