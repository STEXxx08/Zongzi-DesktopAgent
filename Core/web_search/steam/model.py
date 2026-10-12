import re
import time
from datetime import datetime, timezone

import requests

from condata.pipeline.web_search import SteamNewsItemData, SteamNewsPrediction
from web_search.steam.config import SteamCoreConfig


class SteamNewsModel:
    def __init__(self, config: SteamCoreConfig):
        self.config = config
        self._session = requests.Session()
        self._cache: dict[str, tuple[float, SteamNewsPrediction]] = {}

    def _resolve_app(self, query: str) -> tuple[int, str]:
        if query.isdigit():
            return int(query), query
        alias = self.config.app_aliases.get(query.casefold())
        if alias is not None:
            return alias, query
        response = self._session.get(
            self.config.store_search_endpoint,
            params={"term": query, "l": self.config.language, "cc": self.config.country},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        if not items:
            raise ValueError(f"未找到 Steam 游戏：{query}")
        return int(items[0]["id"]), str(items[0].get("name") or query)

    def get_news(self, game: str) -> SteamNewsPrediction:
        game = re.sub(
            r"(?:最近|最新|更新了什么|了什么|有什么更新|有更新|新补丁|新版本)+$",
            "",
            game.strip(),
            flags=re.IGNORECASE,
        ).strip()
        if not game:
            raise ValueError("未提供 Steam 游戏名或 AppID")
        cached = self._cache.get(game.casefold())
        if cached and time.monotonic() - cached[0] <= self.config.cache_ttl_seconds:
            prediction = cached[1].model_copy(deep=True)
            prediction.cached = True
            return prediction
        appid, game_name = self._resolve_app(game)
        response = self._session.get(
            self.config.news_endpoint,
            params={
                "appid": appid,
                "count": self.config.count,
                "maxlength": self.config.max_content_length,
                "format": "json",
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        appnews = response.json().get("appnews") or {}
        items = []
        news_items = appnews.get("newsitems") or []
        if not news_items:
            raise ValueError(f"Steam 未返回游戏新闻：{game_name} ({appid})")
        for value in news_items:
            timestamp = value.get("date")
            published = (
                datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
                if isinstance(timestamp, (int, float)) else ""
            )
            summary = re.sub(r"<[^>]+>", " ", str(value.get("contents") or ""))
            items.append(SteamNewsItemData(
                title=str(value.get("title") or "").strip(),
                url=str(value.get("url") or "").strip(),
                summary=" ".join(summary.split()),
                published_at=published,
                author=str(value.get("author") or ""),
            ))
        prediction = SteamNewsPrediction(
            query=game,
            appid=appid,
            game_name=str(appnews.get("appid") and game_name or game_name),
            items=items,
        )
        self._cache[game.casefold()] = (time.monotonic(), prediction.model_copy(deep=True))
        return prediction