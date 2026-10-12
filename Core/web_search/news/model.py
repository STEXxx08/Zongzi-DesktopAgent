import html
import re
import time
import xml.etree.ElementTree as ET

import requests

from condata.pipeline.web_search import NewsItemData, NewsPrediction
from web_search.news.config import NewsCoreConfig


class SearXNGNewsModel:
    def __init__(self, config: NewsCoreConfig):
        self.config = config
        self._session = requests.Session()
        self._cache: dict[str, tuple[float, NewsPrediction]] = {}

    def get_news(self, query: str) -> NewsPrediction:
        query = query.strip() or self.config.default_query
        cached = self._cache.get(query.casefold())
        if cached and time.monotonic() - cached[0] <= self.config.cache_ttl_seconds:
            prediction = cached[1].model_copy(deep=True)
            prediction.cached = True
            return prediction

        groups: list[list[NewsItemData]] = []
        searxng_items = self._get_searxng_items(query)
        if searxng_items:
            groups.append(searxng_items)
        for source in self.config.rss_sources:
            if not source.enabled:
                continue
            try:
                source_items = self._get_feed_items(source)
            except Exception:
                # A broken optional feed must not disable the remaining sources.
                continue
            if source_items:
                groups.append(source_items)

        items = self._round_robin(groups, self.config.max_results)
        if not items:
            raise ValueError(f"所有新闻源均未返回结果：{query}")
        prediction = NewsPrediction(query=query, items=items)
        self._cache[query.casefold()] = (time.monotonic(), prediction.model_copy(deep=True))
        return prediction

    def _get_searxng_items(self, query: str) -> list[NewsItemData]:
        params = {
            "q": query,
            "format": "json",
            "categories": ",".join(self.config.categories),
            "language": self.config.language,
            "engines": ",".join(self.config.engines),
        }
        if self.config.time_range:
            params["time_range"] = self.config.time_range
        try:
            response = self._session.get(
                self.config.endpoint,
                params=params,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            values = response.json().get("results", [])
        except Exception:
            return []

        items = []
        for value in values:
            title = str(value.get("title") or "").strip()
            url = str(value.get("url") or "").strip()
            if not title or not url:
                continue
            items.append(NewsItemData(
                title=title,
                url=url,
                summary=str(value.get("content") or "").strip(),
                published_at=str(value.get("publishedDate") or ""),
                source=str(value.get("engine") or ""),
            ))
            if len(items) >= self.config.max_results:
                break
        return items

    def _get_feed_items(self, source) -> list[NewsItemData]:
        response = self._session.get(
            source.url,
            timeout=self.config.timeout_seconds,
            headers={"User-Agent": "Zongzi-NewsAggregator/1.0"},
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        entries = [
            element for element in root.iter()
            if self._local_name(element.tag) in {"item", "entry"}
        ]
        items = []
        for entry in entries:
            title = self._child_text(entry, {"title"})
            url = self._entry_link(entry)
            if not title or not url:
                continue
            summary = self._clean_markup(self._child_text(
                entry,
                {"description", "summary", "content"},
            ))
            published = self._child_text(
                entry,
                {"pubdate", "published", "updated", "date"},
            )
            items.append(NewsItemData(
                title=title,
                url=url,
                summary=summary,
                published_at=published,
                source=source.name,
            ))
            if len(items) >= source.max_results:
                break
        return items

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].casefold()

    @classmethod
    def _child_text(cls, element, names: set[str]) -> str:
        for child in element:
            if cls._local_name(child.tag) in names:
                return "".join(child.itertext()).strip()
        return ""

    @classmethod
    def _entry_link(cls, element) -> str:
        for child in element:
            if cls._local_name(child.tag) != "link":
                continue
            href = str(child.attrib.get("href") or "").strip()
            if href:
                return href
            text = "".join(child.itertext()).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _clean_markup(value: str) -> str:
        return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())

    @staticmethod
    def _round_robin(
        groups: list[list[NewsItemData]],
        limit: int,
    ) -> list[NewsItemData]:
        results = []
        seen_urls = set()
        index = 0
        while len(results) < limit and any(index < len(group) for group in groups):
            for group in groups:
                if index >= len(group):
                    continue
                item = group[index]
                key = item.url.casefold()
                if key not in seen_urls:
                    seen_urls.add(key)
                    results.append(item)
                    if len(results) >= limit:
                        break
            index += 1
        return results