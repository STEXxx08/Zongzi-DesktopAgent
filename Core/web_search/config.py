from pydantic import BaseModel, Field


class WebSearchCoreConfig(BaseModel):
    endpoint: str = "http://127.0.0.1:8080/search"
    timeout_seconds: float = Field(default=6.0, ge=0.5, le=30.0)
    max_results: int = Field(default=5, ge=1, le=20)
    language: str = "zh-CN"
    categories: list[str] = Field(default_factory=lambda: ["general"])
    engines: list[str] = Field(default_factory=lambda: ["bing", "baidu"])
    safe_search: int = Field(default=1, ge=0, le=2)
    time_range: str | None = None
    page_no: int = Field(default=1, ge=1, le=10)
    verify_ssl: bool = True
    max_concurrency: int = Field(default=1, ge=1, le=16)
    cache_ttl_seconds: int = Field(default=300, ge=0, le=86400)
    max_cache_entries: int = Field(default=128, ge=0, le=4096)