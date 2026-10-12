from typing import Literal

from pydantic import BaseModel, Field


class BrowserServiceConfig(BaseModel):
    save_raw_by_default: bool = Field(default=False, description="详细研究是否默认持久保存网页原文；关闭时仅返回临时正文供模型选择整理。")
    max_cached_pages: int = Field(default=20, ge=1, le=100, description="内存中临时网页正文数量上限，不占用 Sandbox 文件容量。")
    cache_ttl_seconds: float = Field(default=900, ge=1, le=86400, description="临时网页正文有效期，过期后需重新读取。")
    enable: bool = Field(default=True, description="开放详细网页检索及资料读取工具。")
    backend: Literal["auto", "http", "mcp"] = Field(default="auto", description="自动优先已配置的浏览器 MCP，否则使用 HTTP 正文读取。")
    mcp_server: str = Field(default="playwright", description="浏览器 MCP 服务名称。")
    archive_dir: str = Field(default="research", description="资料目录，相对于 Sandbox 根目录。")
    max_pages: int = Field(default=5, ge=1, le=20, description="单次研究最多读取的网页数量。")
    request_timeout: float = Field(default=20, ge=1, le=120, description="每个 HTTP 页面的网络读取期限（秒），不包含 DNS 解析。")
    max_redirects: int = Field(default=5, ge=0, le=10, description="HTTP 跳转次数上限。")
    max_page_bytes: int = Field(default=2097152, ge=1024, le=10485760, description="HTTP 页面下载字节上限，超过则报错。")
    max_text_chars: int = Field(default=100000, ge=1000, le=1000000, description="单页提取正文字符上限，超出部分明确标记为截断。")
    preview_chars: int = Field(default=1800, ge=100, le=3000, description="单页返回模型的正文预览字符数。")
    read_chunk_chars: int = Field(default=10000, ge=100, le=12000, description="存档分段读取字符上限。")
    max_catalog_entries: int = Field(default=20, ge=1, le=100, description="返回模型的资料目录条目上限。")
    max_query_chars: int = Field(default=2000, ge=1, le=10000, description="研究主题或搜索词长度上限。")