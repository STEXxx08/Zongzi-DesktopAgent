import os
import re
import time
from urllib.parse import quote

import requests

from condata.pipeline.web_search import (
    GitHubReleaseItemData,
    GitHubReleasePrediction,
)
from web_search.github.config import GitHubCoreConfig


class GitHubReleaseModel:
    def __init__(self, config: GitHubCoreConfig):
        self.config = config
        self._session = requests.Session()
        self._cache: dict[str, tuple[float, GitHubReleasePrediction]] = {}

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.config.api_version,
            "User-Agent": "Zongzi-WebSearch",
        }
        token = os.getenv(self.config.token_env, "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _resolve_repository(self, query: str) -> tuple[str, str]:
        alias = self.config.repository_aliases.get(query.casefold())
        if alias:
            query = alias
        match = re.search(r"([\w.-]+/[\w.-]+)", query)
        if match:
            return match.group(1), ""
        response = self._session.get(
            f"{self.config.api_base.rstrip('/')}/search/repositories",
            params={"q": query, "per_page": 1},
            headers=self._headers(),
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        if not items:
            raise ValueError(f"未找到 GitHub 仓库：{query}")
        return str(items[0]["full_name"]), str(items[0].get("description") or "")

    def get_releases(self, repository: str) -> GitHubReleasePrediction:
        repository = repository.strip()
        cached = self._cache.get(repository.casefold())
        if cached and time.monotonic() - cached[0] <= self.config.cache_ttl_seconds:
            prediction = cached[1].model_copy(deep=True)
            prediction.cached = True
            return prediction

        full_name, description = self._resolve_repository(repository)
        response = self._session.get(
            f"{self.config.api_base.rstrip('/')}/repos/{full_name}/releases",
            params={"per_page": self.config.max_results},
            headers=self._headers(),
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        releases = []
        for value in response.json():
            body = " ".join(str(value.get("body") or "").split())
            releases.append(GitHubReleaseItemData(
                name=str(value.get("name") or value.get("tag_name") or "").strip(),
                tag_name=str(value.get("tag_name") or ""),
                url=str(value.get("html_url") or ""),
                published_at=str(value.get("published_at") or ""),
                prerelease=bool(value.get("prerelease")),
                summary=body[:1000],
            ))
        if not releases:
            tags_response = self._session.get(
                f"{self.config.api_base.rstrip('/')}/repos/{full_name}/tags",
                params={"per_page": self.config.max_results},
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
            )
            tags_response.raise_for_status()
            for value in tags_response.json():
                tag_name = str(value.get("name") or "").strip()
                if not tag_name:
                    continue
                releases.append(GitHubReleaseItemData(
                    name=tag_name,
                    tag_name=tag_name,
                    url=(
                        f"https://github.com/{full_name}/tree/"
                        f"{quote(tag_name, safe='')}"
                    ),
                    summary="Git tag（仓库未创建对应的 GitHub Release）",
                ))
        prediction = GitHubReleasePrediction(
            query=repository,
            repository=full_name,
            description=description,
            items=releases,
        )
        self._cache[repository.casefold()] = (
            time.monotonic(), prediction.model_copy(deep=True)
        )
        return prediction