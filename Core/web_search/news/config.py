from pydantic import BaseModel, Field


class NewsRSSSource(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=1000)
    enabled: bool = True
    max_results: int = Field(default=5, ge=1, le=50)


class NewsCoreConfig(BaseModel):
    endpoint: str = "http://127.0.0.1:8080/search"
    default_query: str = "今日新闻"
    language: str = "zh-CN"
    engines: list[str] = Field(default_factory=lambda: ["bing"])
    categories: list[str] = Field(default_factory=lambda: ["general"])
    # Leave unset unless every selected SearXNG engine supports time ranges.
    # Unsupported search conditions cause an engine to be skipped entirely.
    time_range: str | None = None
    max_results: int = Field(default=5, ge=1, le=20)
    timeout_seconds: float = Field(default=6.0, ge=0.5, le=30.0)
    cache_ttl_seconds: int = Field(default=300, ge=0, le=86400)
    rss_sources: list[NewsRSSSource] = Field(default_factory=list)