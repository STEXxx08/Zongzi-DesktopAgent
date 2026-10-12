from pydantic import BaseModel, Field


class WeatherCoreConfig(BaseModel):
    provider: str = "open_meteo"
    geocoding_endpoint: str = "https://geocoding-api.open-meteo.com/v1/search"
    forecast_endpoint: str = "https://api.open-meteo.com/v1/forecast"
    default_location: str = ""
    language: str = "zh"
    forecast_days: int = Field(default=3, ge=1, le=16)
    timeout_seconds: float = Field(default=6.0, ge=0.5, le=30.0)
    cache_ttl_seconds: int = Field(default=600, ge=0, le=86400)
    max_cache_entries: int = Field(default=64, ge=0, le=1024)