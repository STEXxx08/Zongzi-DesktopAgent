import threading
import time
from collections import OrderedDict

import requests

from condata.pipeline.web_search import (
    WeatherCurrentData,
    WeatherDailyData,
    WeatherPrediction,
)
from web_search.weather.config import WeatherCoreConfig


class OpenMeteoWeatherModel:
    def __init__(self, config: WeatherCoreConfig):
        if config.provider != "open_meteo":
            raise ValueError(f"Unsupported weather provider: {config.provider}")
        self.config = config
        self._session = requests.Session()
        self._cache_lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[float, WeatherPrediction]] = OrderedDict()

    def _get_cached(self, key: str) -> WeatherPrediction | None:
        if self.config.cache_ttl_seconds <= 0 or self.config.max_cache_entries <= 0:
            return None
        with self._cache_lock:
            item = self._cache.get(key)
            if item is None:
                return None
            created_at, prediction = item
            if time.monotonic() - created_at > self.config.cache_ttl_seconds:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            cached = prediction.model_copy(deep=True)
            cached.cached = True
            return cached

    def _put_cached(self, key: str, prediction: WeatherPrediction):
        if self.config.cache_ttl_seconds <= 0 or self.config.max_cache_entries <= 0:
            return
        with self._cache_lock:
            self._cache[key] = (time.monotonic(), prediction.model_copy(deep=True))
            self._cache.move_to_end(key)
            while len(self._cache) > self.config.max_cache_entries:
                self._cache.popitem(last=False)

    def _resolve_location(self, location: str) -> dict:
        response = self._session.get(
            self.config.geocoding_endpoint,
            params={
                "name": location,
                "count": 1,
                "language": self.config.language,
                "format": "json",
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            raise ValueError(f"无法识别天气查询地点：{location}")
        return results[0]

    def get_weather(self, location: str) -> WeatherPrediction:
        requested_location = location.strip() or self.config.default_location.strip()
        if not requested_location:
            raise ValueError("未提供天气查询地点，也没有配置 default_location")

        cache_key = requested_location.casefold()
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        place = self._resolve_location(requested_location)
        latitude = float(place["latitude"])
        longitude = float(place["longitude"])
        response = self._session.get(
            self.config.forecast_endpoint,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": ",".join([
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                ]),
                "daily": ",".join([
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_probability_max",
                ]),
                "timezone": "auto",
                "forecast_days": self.config.forecast_days,
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        current = payload.get("current") or {}
        daily = payload.get("daily") or {}
        dates = daily.get("time") or []

        prediction = WeatherPrediction(
            query=requested_location,
            location=" ".join(filter(None, [
                str(place.get("name") or "").strip(),
                str(place.get("admin1") or "").strip(),
                str(place.get("country") or "").strip(),
            ])),
            latitude=latitude,
            longitude=longitude,
            timezone=str(payload.get("timezone") or place.get("timezone") or ""),
            current=WeatherCurrentData(
                time=str(current.get("time") or ""),
                temperature=current.get("temperature_2m"),
                apparent_temperature=current.get("apparent_temperature"),
                relative_humidity=current.get("relative_humidity_2m"),
                precipitation=current.get("precipitation"),
                weather_code=current.get("weather_code"),
                wind_speed=current.get("wind_speed_10m"),
            ),
            daily=[
                WeatherDailyData(
                    date=str(date),
                    weather_code=self._at(daily, "weather_code", index),
                    temperature_max=self._at(daily, "temperature_2m_max", index),
                    temperature_min=self._at(daily, "temperature_2m_min", index),
                    precipitation_probability_max=self._at(
                        daily, "precipitation_probability_max", index
                    ),
                )
                for index, date in enumerate(dates)
            ],
            source="Open-Meteo",
        )
        self._put_cached(cache_key, prediction)
        return prediction

    @staticmethod
    def _at(data: dict, key: str, index: int):
        values = data.get(key) or []
        return values[index] if index < len(values) else None