from pydantic import BaseModel, Field


class SteamCoreConfig(BaseModel):
    news_endpoint: str = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
    store_search_endpoint: str = "https://store.steampowered.com/api/storesearch/"
    language: str = "schinese"
    country: str = "CN"
    count: int = Field(default=5, ge=1, le=20)
    max_content_length: int = Field(default=800, ge=100, le=5000)
    timeout_seconds: float = Field(default=8.0, ge=0.5, le=30.0)
    cache_ttl_seconds: int = Field(default=600, ge=0, le=86400)
    app_aliases: dict[str, int] = Field(default_factory=dict)