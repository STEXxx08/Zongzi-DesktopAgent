from enum import Enum
import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class WebSearchMode(str, Enum):
    DISABLE = "disable"
    LOCAL_ONLY = "local_only"
    ENABLE = "enable"

    @classmethod
    def _missing_(cls, value):
        # Backward compatibility for configs created before the mode names were finalized.
        aliases = {
            "disabled": cls.DISABLE,
            "local": cls.LOCAL_ONLY,
            "local_model": cls.ENABLE,
        }
        return aliases.get(str(value).lower())


class WebSearchKeywordRoute(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    tool: str = Field(
        default="search",
        pattern="^(search|weather|news|steam|github|exchange_rate)$",
    )
    keywords: list[str] = Field(min_length=1)
    query_template: str = Field(
        default="{query}",
        description="Supports {query}, {text}, and {keyword} placeholders.",
    )
    default_query: str = ""
    acknowledgement: str | None = None


class WeatherClientConfig(BaseModel):
    endpoint: str = "http://127.0.0.1:11012/web-search/weather"
    fallback_to_search: bool = True


class WebToolClientConfig(BaseModel):
    endpoint: str
    fallback_to_search: bool = True


class WebSearchServiceConfig(BaseModel):
    mode: WebSearchMode = Field(
        default=WebSearchMode.DISABLE,
        description="Web search mode: disable, local_only, or enable.",
    )
    endpoint: str = Field(
        default="http://127.0.0.1:11012/web-search/search",
        description="Core web-search gateway endpoint.",
    )
    timeout_seconds: float = Field(default=10.0, ge=0.5, le=60.0)
    max_context_chars: int = Field(default=6000, ge=500, le=20000)
    acknowledgement: str = Field(default="稍等，{bot_name}联网看看。")
    keywords: list[str] = Field(
        default_factory=lambda: [
            "联网搜索",
            "网页搜索",
            "搜索一下",
            "搜一下",
            "查一下",
            "帮我查",
            "上网查",
        ]
    )
    keyword_routes: list[WebSearchKeywordRoute] = Field(default_factory=list)
    weather: WeatherClientConfig = Field(default_factory=WeatherClientConfig)
    news: WebToolClientConfig = Field(default_factory=lambda: WebToolClientConfig(
        endpoint="http://127.0.0.1:11012/web-search/news"
    ))
    steam: WebToolClientConfig = Field(default_factory=lambda: WebToolClientConfig(
        endpoint="http://127.0.0.1:11012/web-search/steam"
    ))
    github: WebToolClientConfig = Field(default_factory=lambda: WebToolClientConfig(
        endpoint="http://127.0.0.1:11012/web-search/github"
    ))
    exchange_rate: WebToolClientConfig = Field(default_factory=lambda: WebToolClientConfig(
        endpoint="http://127.0.0.1:11012/web-search/exchange-rate"
    ))
    model_marker: str = Field(default="web_tool")


def _as_plain_dict(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return None


def _load_yaml_fallback() -> dict | None:
    bot_config = Path(__file__).resolve().parents[2] / "config.yaml"
    for path in (bot_config,):
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as file:
                root = yaml.safe_load(file) or {}
            value = root.get("service", {}).get("web_search")
            if isinstance(value, dict):
                return value
        except Exception as exc:
            logger.warning(f"Failed to load web search config from {path}: {exc}")
    return None


def load_web_search_config(root_config: Any = None) -> WebSearchServiceConfig:
    service_config = getattr(root_config, "service", None) if root_config is not None else None
    raw_config = _as_plain_dict(getattr(service_config, "web_search", None))
    if raw_config is None:
        raw_config = _load_yaml_fallback()
    try:
        return WebSearchServiceConfig.model_validate(raw_config or {})
    except Exception as exc:
        logger.error(f"Invalid web search config; service disabled: {exc}")
        return WebSearchServiceConfig()