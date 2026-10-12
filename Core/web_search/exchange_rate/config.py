from pydantic import BaseModel, Field


class ExchangeRateCoreConfig(BaseModel):
    endpoint: str = "https://api.frankfurter.dev/v2/rate"
    default_base: str = "USD"
    default_quote: str = "CNY"
    timeout_seconds: float = Field(default=6.0, ge=0.5, le=30.0)
    cache_ttl_seconds: int = Field(default=3600, ge=0, le=86400)