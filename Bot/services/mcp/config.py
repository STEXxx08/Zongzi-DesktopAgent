from typing import Literal

from pydantic import BaseModel, Field


class MCPServiceConfig(BaseModel):
    enable: bool = Field(default=True, description="是否开放 MCP 服务与本地资料读取。")
    root: str = Field(default="MCP", description="MCP 配置目录，相对于 Bot 应用目录。")
    request_timeout: float = Field(default=15.0, gt=0, le=120, description="远程请求超时秒数。")
    max_response_bytes: int = Field(default=524288, ge=1024, le=4194304, description="MCP 响应字节上限。")
    max_config_bytes: int = Field(default=65536, ge=1024, le=1048576, description="服务配置文件大小上限。")
    max_servers: int = Field(default=16, ge=1, le=100, description="服务数量上限。")
    max_items: int = Field(default=100, ge=1, le=1000, description="远程工具或资源列表条目上限。")
    max_pages: int = Field(default=10, ge=1, le=100, description="列表分页请求上限。")
    max_resource_bytes: int = Field(default=262144, ge=1, le=4194304, description="本地资料单文件字节上限。")
    max_list_entries: int = Field(default=100, ge=1, le=1000, description="本地资料列表条目上限。")


class MCPServerConfig(BaseModel):
    enabled: bool = False
    transport: Literal["streamable_http"] = "streamable_http"
    url: str
    protocol_version: Literal["2025-11-25", "2025-06-18", "2025-03-26"] = "2025-11-25"
    headers_env: dict[str, str] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    allow_resource_read: bool = False