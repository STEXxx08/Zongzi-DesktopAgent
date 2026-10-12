import json
import os
import re
import threading
import time
from urllib.parse import urlsplit

from services.mcp.config import MCPServerConfig, MCPServiceConfig
from services.net_safety import pinned_connection, public_target


class MCPHTTPClient:
    """支持握手型 MCP 的有限 Streamable HTTP 客户端，不支持服务端主动请求。

    出站安全（与浏览器读取共用 services/net_safety.py）：
    - 启动时校验地址，拒绝解析到本机、内网、保留地址的域名；
    - 连接固定到已校验的地址，避免校验后再次解析（DNS 重绑定）；
    - http.client 不跟随重定向，因此不存在"校验后被 3xx 跳到内网"的路径。
    """

    def __init__(self, server: MCPServerConfig, config: MCPServiceConfig, *, resolver=None):
        parsed = urlsplit(server.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("MCP URL 必须使用 HTTP 或 HTTPS")
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("MCP URL 不得包含账号、密码、查询凭据或片段")
        loopback = parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme == "http" and not loopback:
            raise ValueError("非本机 MCP 服务必须使用 HTTPS")
        self.server = server
        self.config = config
        self._lock = threading.RLock()
        try:
            hosts = None if resolver is None else ([resolver] if isinstance(resolver, str) else list(resolver))
            self.target = public_target(server.url, hosts=hosts)
        except PermissionError as exc:
            raise ValueError(f"MCP 服务地址不允许访问：{exc}") from None
        self._sequence = 0
        self._session_id = None
        self._initialized = False
        self._closed = False
        self.capabilities = {}

    def _headers(self):
        headers = {
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.server.protocol_version,
        }
        reserved = {name.lower() for name in headers} | {"mcp-session-id", "host", "content-length"}
        for name, variable in self.server.headers_env.items():
            if name.lower() in reserved or not re.fullmatch(r"[A-Za-z0-9-]+", name):
                raise ValueError("MCP 自定义请求头无效或覆盖了协议字段")
            value = os.environ.get(variable)
            if value is None:
                raise ValueError("MCP 认证所需环境变量未设置")
            if not value.isascii() or any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("MCP 认证环境变量包含无效请求头字符")
            headers[name] = value
        if self._session_id:
            headers["MCP-Session-Id"] = self._session_id
        return headers

    @staticmethod
    def _result(message, request_id):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("MCP 返回了无效 JSON-RPC 消息")
        if "method" in message:
            if "id" in message:
                raise ValueError("当前 MCP 客户端不支持服务端主动请求")
            return None
        if message.get("id") != request_id:
            raise ValueError("MCP 响应 ID 不匹配")
        if "error" in message:
            raise RuntimeError("MCP 请求失败：服务端返回 JSON-RPC 错误")
        result = message.get("result")
        if not isinstance(result, dict):
            raise ValueError("MCP 响应缺少对象类型 result")
        return result

    def _post(self, method: str, params: dict | None = None, *, notification: bool = False):
        self._sequence += 1
        request_id = self._sequence
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = request_id
        if params is not None:
            payload["params"] = params
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > self.config.max_response_bytes:
            raise ValueError("MCP 请求超过消息大小上限")

        path = urlsplit(self.server.url).path or "/"
        headers = self._headers()
        headers["Content-Length"] = str(len(encoded))
        deadline = time.monotonic() + self.config.request_timeout
        connection = pinned_connection(self.target, self.config.request_timeout)
        try:
            with connection:
                connection.request("POST", path, body=encoded, headers=headers)
                response = connection.getresponse()
                if response.status >= 300:
                    raise RuntimeError(f"MCP HTTP 请求失败：状态码 {response.status}")
                session = response.getheader("MCP-Session-Id")
                if session:
                    if not session.isascii() or any(ord(character) < 33 or ord(character) > 126 for character in session):
                        raise ValueError("MCP 会话 ID 无效")
                    self._session_id = session
                if notification:
                    if response.status not in {200, 202, 204}:
                        raise ValueError("MCP 通知未被接受")
                    return None
                content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
                if content_type not in {"application/json", "text/event-stream"}:
                    raise ValueError("MCP 响应类型不受支持")
                data = b""
                total = 0
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError("MCP 请求超过总等待时限")
                    chunk = response.read(min(8192, self.config.max_response_bytes + 1 - total))
                    total += len(chunk)
                    if total > self.config.max_response_bytes:
                        raise ValueError("MCP 响应超过大小上限")
                    data += chunk
                    if content_type == "text/event-stream":
                        data = data.replace(b"\r\n", b"\n")
                        while b"\n\n" in data:
                            event, data = data.split(b"\n\n", 1)
                            lines = [line[5:].lstrip() for line in event.split(b"\n") if line.startswith(b"data:")]
                            if lines:
                                result = self._result(json.loads(b"\n".join(lines)), request_id)
                                if result is not None:
                                    return result
                    if not chunk:
                        break
                if content_type == "text/event-stream":
                    raise ValueError("MCP 事件流未返回请求结果")
                return self._result(json.loads(data), request_id)
        except TimeoutError:
            raise
        except OSError:
            raise RuntimeError("MCP 连接失败或超时") from None

    def initialize(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("MCP 客户端已关闭")
            if self._initialized:
                return
            try:
                result = self._post("initialize", {
                    "protocolVersion": self.server.protocol_version,
                    "capabilities": {}, "clientInfo": {"name": "Bot", "version": "1.0"},
                })
                if result.get("protocolVersion") != self.server.protocol_version:
                    raise ValueError("MCP 服务协商的协议版本与配置不一致")
                capabilities = result.get("capabilities", {})
                if not isinstance(capabilities, dict):
                    raise ValueError("MCP capabilities 必须为对象")
                self._post("notifications/initialized", notification=True)
                self.capabilities = capabilities
                self._initialized = True
            except Exception:
                self._session_id = None
                raise

    def request(self, method: str, params: dict | None = None):
        with self._lock:
            self.initialize()
            capability = method.split("/", 1)[0]
            if capability not in self.capabilities:
                raise ValueError("MCP 服务未声明所需能力")
            return self._post(method, params)

    def list_items(self, method: str, field: str) -> list[dict]:
        items = []
        cursor = None
        seen = set()
        for _ in range(self.config.max_pages):
            result = self.request(method, {"cursor": cursor} if cursor else {})
            page = result.get(field)
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise ValueError("MCP 列表响应格式无效")
            items.extend(page)
            if len(items) > self.config.max_items:
                raise ValueError("MCP 列表超过条目上限")
            cursor = result.get("nextCursor")
            if not cursor:
                return items
            if not isinstance(cursor, str) or cursor in seen:
                raise ValueError("MCP 分页游标无效或重复")
            seen.add(cursor)
        raise ValueError("MCP 列表超过分页上限")

    def close(self):
        with self._lock:
            self._closed = True
            if self._session_id:
                try:
                    path = urlsplit(self.server.url).path or "/"
                    connection = pinned_connection(self.target, self.config.request_timeout)
                    with connection:
                        connection.request("DELETE", path, headers=self._headers())
                        connection.getresponse().read()
                except Exception:
                    pass
            self._session_id = None
            self._initialized = False
