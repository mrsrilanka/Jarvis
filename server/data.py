"""Live data sources: weather (Open-Meteo), commute (Google Routes API), news (RSS).

Every fetch is cached for display.refresh_minutes so the dashboard and the AI
share the same numbers and you don't burn API quota.
"""
from __future__ import annotations

import os
import time
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import feedparser
import httpx

log = logging.getLogger("jarvis.data")

WEATHER_CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy",
    45: "foggy", 48: "foggy", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle", 61: "light rain", 63: "rain",
    65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers",
    81: "rain showers", 82: "heavy rain showers", 85: "snow showers",
    86: "heavy snow showers", 95: "thunderstorms", 96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}


def icon_for(code: int) -> str:
    if code in (0, 1):
        return "sun"
    if code in (2, 3, 45, 48):
        return "cloud"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "storm"
    return "rain"


class DataHub:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ttl = int(cfg.get("display", {}).get("refresh_minutes", 10)) * 60
        self.tz = ZoneInfo(cfg["home"].get("timezone", "America/Chicago"))
        self._cache: dict[str, tuple[float, object]] = {}
        self.maps_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

    # ---------- cache helper ----------
    def _cached(self, key: str, fn, force: bool = False):
        now = time.time()
        hit = self._cache.get(key)
        if hit and not force and now - hit[0] < self.ttl:
            return hit[1]
        try:
            val = fn()
            self._cache[key] = (now, val)
            return val
        except Exception as e:  # keep showing old data if a refresh fails
            log.warning("fetch %s failed: %s", key, e)
            if hit:
                return hit[1]
            return {"error": f"{key} unavailable right now"}

    # ---------- weather ----------
    def weather(self, force: bool = False) -> dict:
        return self._cached("weather", self._fetch_weather, force)

    def _fetch_weather(self) -> dict:
        h = self.cfg["home"]
        params = {
            "latitude": h["latitude"], "longitude": h["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "hourly": "temperature_2m,weather_code,precipitation_probability",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
            "timezone": h.get("timezone", "auto"), "forecast_days": 2,
        }
        r = httpx.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        cur = d["current"]
        now = datetime.now(self.tz).replace(minute=0, second=0, microsecond=0)
        hourly = []
        for t, temp, code, pop in zip(d["hourly"]["time"], d["hourly"]["temperature_2m"],
                                      d["hourly"]["weather_code"],
                                      d["hourly"]["precipitation_probability"]):
            ts = datetime.fromisoformat(t).replace(tzinfo=self.tz)
            if ts >= now and len(hourly) < 12:
                hourly.append({"time": ts.strftime("%-I %p").lower(), "temp": round(temp),
                               "icon": icon_for(code), "rain_chance": pop})
        hourly = hourly[::2][:6]  # every 2 hours, 6 slots
        daily = d["daily"]
        return {
            "temp": round(cur["temperature_2m"]),
            "feels_like": round(cur["apparent_temperature"]),
            "condition": WEATHER_CODES.get(cur["weather_code"], "mixed"),
            "icon": icon_for(cur["weather_code"]),
            "wind_mph": round(cur["wind_speed_10m"]),
            "high": round(daily["temperature_2m_max"][0]),
            "low": round(daily["temperature_2m_min"][0]),
            "rain_chance_today": daily["precipitation_probability_max"][0],
            "tomorrow": {
                "high": round(daily["temperature_2m_max"][1]),
                "low": round(daily["temperature_2m_min"][1]),
                "condition": WEATHER_CODES.get(daily["weather_code"][1], "mixed"),
                "rain_chance": daily["precipitation_probability_max"][1],
            },
            "hourly": hourly,
            "units": "Fahrenheit, mph",
        }

    # ---------- commute ----------
    def places(self) -> list[dict]:
        return self.cfg.get("places", [])

    def commute(self, place_id: str, force: bool = False) -> dict:
        place = next((p for p in self.places() if p["id"] == place_id), None)
        if not place:
            return {"error": f"unknown place '{place_id}'",
                    "known_places": [p["id"] for p in self.places()]}
        return self._cached(f"commute:{place_id}", lambda: self._fetch_route(place), force)

    def all_commutes(self) -> list[dict]:
        return [self.commute(p["id"]) | {"id": p["id"], "label": p["label"]} for p in self.places()]

    def _fetch_route(self, place: dict) -> dict:
        base = {"label": place["label"]}
        if not self.maps_key:
            return base | {"error": "traffic unavailable (no GOOGLE_MAPS_API_KEY set)"}
        r = httpx.post(
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            headers={
                "X-Goog-Api-Key": self.maps_key,
                "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.distanceMeters,routes.description",
            },
            json={
                "origin": {"address": self.cfg["home"]["address"]},
                "destination": {"address": place["address"]},
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_AWARE",
            },
            timeout=10,
        )
        r.raise_for_status()
        routes = r.json().get("routes", [])
        if not routes:
            return base | {"error": "no route found — check the address in config.yaml"}
        rt = routes[0]
        mins = round(int(rt["duration"].rstrip("s")) / 60)
        usual = round(int(rt.get("staticDuration", rt["duration"]).rstrip("s")) / 60)
        out = base | {
            "minutes": mins,
            "usual_minutes": usual,
            "delay_minutes": max(0, mins - usual),
            "miles": round(rt.get("distanceMeters", 0) / 1609.34, 1),
            "via": rt.get("description", ""),
        }
        if place.get("arrive_by"):
            now = datetime.now(self.tz)
            hh, mm = map(int, place["arrive_by"].split(":"))
            arrive = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            leave = arrive - timedelta(minutes=mins)
            out["arrive_by"] = arrive.strftime("%-I:%M %p")
            out["leave_by"] = leave.strftime("%-I:%M %p")
            out["minutes_until_leave"] = round((leave - now).total_seconds() / 60)
        return out

    # ---------- news ----------
    def news(self, force: bool = False) -> list[dict]:
        return self._cached("news", self._fetch_news, force)

    def _fetch_news(self) -> list[dict]:
        ncfg = self.cfg.get("news", {})
        limit = int(ncfg.get("max_items", 6))
        feeds = ncfg.get("feeds", [])
        items: list[dict] = []
        per_feed = max(1, -(-limit // max(1, len(feeds))))  # ceil
        for f in feeds:
            r = httpx.get(f["url"], timeout=10, follow_redirects=True,
                          headers={"User-Agent": "Jarvis-Home/1.0"})
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            for e in parsed.entries[:per_feed]:
                items.append({"source": f.get("name", ""), "title": e.get("title", "").strip(),
                              "summary": (e.get("summary", "") or "")[:300]})
        return items[:limit]

    # ---------- everything for the dashboard ----------
    def dashboard(self) -> dict:
        d = self.cfg.get("display", {})
        return {
            "name": self.cfg["home"].get("name", ""),
            "weather": self.weather(),
            "commutes": self.all_commutes(),
            "news": self.news(),
            "night_start": d.get("night_start", "22:00"),
            "night_end": d.get("night_end", "05:30"),
            "provider": self.cfg.get("llm", {}).get("provider", "gemini"),
            "updated": datetime.now(self.tz).strftime("%-I:%M %p"),
        }
