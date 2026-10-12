import json
import logging
import re
import threading
from pathlib import Path

from services.mcp.client import MCPHTTPClient
from services.mcp.config import MCPServerConfig, MCPServiceConfig
from services.sandbox.config import SandboxServiceConfig
from services.sandbox.filesystem import RestrictedFilesystem
from services.tool_runtime import ToolDefinition, ToolRuntime


logger = logging.getLogger(__name__)


class MCPService:
    """可信连接配置、远程 MCP 能力及本地只读资料的统一入口。"""

    TOOL_NAMES = ("mcp_servers", "mcp_tools", "mcp_call", "mcp_resources", "mcp_read_resource",
                  "mcp_list_documents", "mcp_read_document")

    def __init__(self, config: MCPServiceConfig, runtime: ToolRuntime, *, project_root: Path):
        self.runtime = runtime
        self.project_root = project_root.resolve()
        self._lock = threading.RLock()
        self._clients = {}
        self._servers = {}
        self.update_config(config)

    def update_config(self, config: MCPServiceConfig):
        with self._lock:
            self.shutdown()
            self.config = config
            self._servers = {}
            root_policy = SandboxServiceConfig(enable=config.enable, root=config.root, allow_write=False)
            self._config_files = RestrictedFilesystem(root_policy, project_root=self.project_root)
            self.root = self._config_files.root
            self.documents = RestrictedFilesystem(SandboxServiceConfig(
                enable=config.enable, root=str(self.root / "resources"), allow_write=False,
                max_file_bytes=config.max_resource_bytes, max_list_entries=config.max_list_entries,
            ), project_root=self.project_root)
            if config.enable:
                try:
                    self._servers = self._load_servers()
                except Exception as exc:
                    logger.error(f"MCP 服务配置文件不可用，远程服务已停用；本地只读资料仍可用：{exc}")
            for name in self.TOOL_NAMES:
                self.runtime.unregister(name)
            if config.enable:
                self._register_tools()

    def _load_servers(self):
        path = self._config_files._path("servers.json")
        if not path.exists():
            return {}
        with path.open("rb") as stream:
            data = stream.read(self.config.max_config_bytes + 1)
        if len(data) > self.config.max_config_bytes:
            raise ValueError("MCP 服务配置超过大小上限")
        payload = json.loads(data)
        servers = payload.get("mcpServers", {})
        if not isinstance(servers, dict) or len(servers) > self.config.max_servers:
            raise ValueError("MCP 服务配置格式或服务数量无效")
        result = {}
        # 逐服务隔离：单个服务配置写错只停用它自己，其余服务照常启用，
        # 并明确指出是哪个服务、错在哪里（P1-9）。
        for name, values in servers.items():
            try:
                if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
                    raise ValueError("MCP 服务名称无效")
                server = MCPServerConfig.model_validate(values)
                if not server.enabled:
                    logger.info(f"MCP 服务已禁用，跳过：{name}")
                    continue
                MCPHTTPClient(server, self.config)
                result[name] = server
            except Exception as exc:
                logger.error(f"MCP 服务“{name}”配置无效，已停用该服务（其余服务不受影响）：{exc}")
                continue
        return result

    def _client(self, server: str):
        with self._lock:
            if not self.config.enable or server not in self._servers:
                raise PermissionError("MCP 服务未配置或未启用")
            if server not in self._clients:
                self._clients[server] = MCPHTTPClient(self._servers[server], self.config)
            return self._clients[server]

    def list_servers(self):
        with self._lock:
            return [{"name": name, "allowed_tools": item.allowed_tools,
                     "allow_resource_read": item.allow_resource_read}
                    for name, item in self._servers.items()]

    def list_tools(self, server: str):
        client = self._client(server)
        allowed = client.server.allowed_tools
        return [item for item in client.list_items("tools/list", "tools") if item.get("name") in allowed]

    def call_tool(self, server: str, name: str, arguments: dict):
        client = self._client(server)
        if name not in client.server.allowed_tools:
            raise PermissionError("MCP 工具不在用户配置白名单内")
        if not isinstance(arguments, dict):
            raise ValueError("MCP arguments 必须为对象")
        result = client.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError("MCP 工具执行失败（isError=true）")
        return result

    def call_tool_sequence(self, server: str, calls: list[tuple[str, dict]]):
        """串行保持同一浏览器会话，避免导航和正文提取被其他请求插入。"""
        client = self._client(server)
        for name, arguments in calls:
            if name not in client.server.allowed_tools or not isinstance(arguments, dict):
                raise PermissionError("MCP 工具序列不在白名单内或参数无效")
        results = []
        # 已获取客户端后不再获取服务锁，避免与配置重载形成锁顺序反转。
        with client._lock:
            for name, arguments in calls:
                result = client.request("tools/call", {"name": name, "arguments": arguments})
                if result.get("isError"):
                    raise RuntimeError("MCP 工具序列执行失败（isError=true）")
                results.append(result)
        return results

    def list_resources(self, server: str):
        client = self._client(server)
        if not client.server.allow_resource_read:
            raise PermissionError("未开放该 MCP 服务的资源读取权限")
        return client.list_items("resources/list", "resources")

    def read_resource(self, server: str, uri: str):
        if not isinstance(uri, str):
            raise ValueError("资源 URI 必须为字符串")
        available = self.list_resources(server)
        if uri not in {item.get("uri") for item in available}:
            raise PermissionError("只能读取 MCP 服务列出的资源 URI")
        return self._client(server).request("resources/read", {"uri": uri})

    def _register_tools(self):
        definitions = [
            ("mcp_servers", "列出用户已配置并启用的 MCP 服务。", {}, [], lambda: self.list_servers()),
            ("mcp_tools", "列出 MCP 服务的白名单工具及参数格式。", {"server": "string"}, ["server"], self.list_tools),
            ("mcp_call", "调用指定 MCP 服务的白名单工具，返回远程结果。",
             {"server": "string", "name": "string", "arguments": "object"}, ["server", "name", "arguments"], self.call_tool),
            ("mcp_resources", "列出指定远程 MCP 服务可读的资源 URI。", {"server": "string"}, ["server"], self.list_resources),
            ("mcp_read_resource", "读取该远程服务已列出的资源 URI；不是本地文件路径。", {"server": "string", "uri": "string"}, ["server", "uri"], self.read_resource),
            ("mcp_list_documents", "列出本地 MCP/resources 的只读资料；不访问远程服务。", {"path": "string"}, [], self.documents.list_directory),
            ("mcp_read_document", "按相对于 MCP/resources 的路径读取本地 UTF-8 资料；不包含服务凭据。",
             {"path": "string"}, ["path"], self.documents.read_text),
        ]
        for name, description, properties, required, handler in definitions:
            self.runtime.register(ToolDefinition(
                name=name, description=description,
                input_schema={"type": "object", "properties": {key: {"type": value} for key, value in properties.items()},
                              "required": required, "additionalProperties": False},
                tags=frozenset({"mcp"}),
                model_invocable=True,
                handler=lambda args, operation=handler: operation(**args),
            ))

    def model_instruction(self) -> str | None:
        if not self.config.enable:
            return None
        return (
            "【MCP 公共规则】仅在当前用户任务需要时调用；远程服务权限由用户配置和白名单限定。"
            "Sandbox 自主授权不扩展 MCP 的远程权限。"
            "resources/read_resource 面向远程 URI；list_documents/read_document 面向本地只读资料。"
        )

    def shutdown(self):
        with self._lock:
            for client in self._clients.values():
                client.close()
            self._clients = {}