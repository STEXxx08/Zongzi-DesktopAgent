from pydantic import BaseModel, Field


class GitHubCoreConfig(BaseModel):
    api_base: str = "https://api.github.com"
    token_env: str = "GITHUB_TOKEN"
    api_version: str = "2026-03-10"
    max_results: int = Field(default=5, ge=1, le=20)
    timeout_seconds: float = Field(default=8.0, ge=0.5, le=30.0)
    cache_ttl_seconds: int = Field(default=600, ge=0, le=86400)
    repository_aliases: dict[str, str] = Field(default_factory=dict)