import threading
import time
from collections import OrderedDict

import requests

from condata.pipeline.web_search import (
    WebSearchPrediction,
    WebSearchResultData,
)
from web_search.config import WebSearchCoreConfig


class SearXNGSearchModel:
    def __init__(self, config: WebSearchCoreConfig):
        self.config = config
        self._session = requests.Session()
        self._semaphore = threading.BoundedSemaphore(config.max_concurrency)
        self._cache_lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[float, list[WebSearchResultData]]] = OrderedDict()

    def _cache_key(self, query: str) -> str:
        return "|".join([
            query.strip().casefold(),
            self.config.language,
            ",".join(self.config.categories),
            ",".join(self.config.engines),
            str(self.config.safe_search),
            self.config.time_range or "",
            str(self.config.page_no),
        ])

    def _get_cached(self, key: str) -> list[WebSearchResultData] | None:
        if self.config.cache_ttl_seconds <= 0 or self.config.max_cache_entries <= 0:
            return None
        now = time.monotonic()
        with self._cache_lock:
            item = self._cache.get(key)
            if item is None:
                return None
            created_at, results = item
            if now - created_at > self.config.cache_ttl_seconds:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return [result.model_copy(deep=True) for result in results]

    def _put_cached(self, key: str, results: list[WebSearchResultData]):
        if self.config.cache_ttl_seconds <= 0 or self.config.max_cache_entries <= 0:
            return
        with self._cache_lock:
            self._cache[key] = (
                time.monotonic(),
                [result.model_copy(deep=True) for result in results],
            )
            self._cache.move_to_end(key)
            while len(self._cache) > self.config.max_cache_entries:
                self._cache.popitem(last=False)

    def search(self, query: str) -> WebSearchPrediction:
        query = query.strip()
        key = self._cache_key(query)
        cached = self._get_cached(key)
        if cached is not None:
            return WebSearchPrediction(query=query, results=cached, cached=True)

        params = {
            "q": query,
            "format": "json",
            "language": self.config.language,
            "categories": ",".join(self.config.categories),
            "safesearch": self.config.safe_search,
            "pageno": self.config.page_no,
        }
        if self.config.engines:
            params["engines"] = ",".join(self.config.engines)
        if self.config.time_range:
            params["time_range"] = self.config.time_range

        with self._semaphore:
            response = self._session.get(
                self.config.endpoint,
                params=params,
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_ssl,
            )
            response.raise_for_status()
            payload = response.json()

        results: list[WebSearchResultData] = []
        seen_urls: set[str] = set()
        for item in payload.get("results", []):
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            score = item.get("score")
            results.append(WebSearchResultData(
                title=title,
                url=url,
                snippet=str(item.get("content") or "").strip(),
                engine=str(item.get("engine") or "").strip() or None,
                score=float(score) if isinstance(score, (int, float)) else None,
            ))
            if len(results) >= self.config.max_results:
                break

        self._put_cached(key, results)
        return WebSearchPrediction(query=query, results=results)