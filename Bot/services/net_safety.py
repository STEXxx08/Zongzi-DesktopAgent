"""出站地址校验：拒绝本机、内网、保留地址与混合解析，并支持把连接钉在已校验地址上。

浏览器 HTTP 读取与 MCP 客户端共用这一份逻辑，避免两处规则不一致：
- 浏览器只允许 80/443；
- MCP 允许自定义端口，因此端口策略由调用方传入。
"""

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PinnedTarget:
    """已校验的出站目标：域名、解析后固定使用的地址、端口。"""

    url: str
    host: str
    port: int
    address: str
    scheme: str


def public_target(
    url: str,
    *,
    allowed_ports: frozenset[int] | None = None,
    hosts: list[str] | None = None,
) -> PinnedTarget:
    """校验 URL 是否指向公共地址，并返回固定连接地址。

    逐跳校验由调用方在每次跳转时重新调用本函数完成；DNS 重绑定由"连接固定到
    已校验地址"缓解。`hosts` 仅用于测试注入解析结果，正常运行不要传。
    """
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 for c in url) or "\\" in url:
        raise ValueError("URL 格式无效")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise PermissionError("仅允许无账号密码的 HTTP/HTTPS 地址")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if allowed_ports is not None and port not in allowed_ports:
        allowed = "、".join(str(item) for item in sorted(allowed_ports))
        raise PermissionError(f"地址端口受限，仅允许 {allowed}")

    if hosts is None:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    else:
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port)) for host in hosts]
    if not addresses:
        raise ValueError("域名无法解析")

    for _, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        embedded = getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None)
        if not ip.is_global or ip.is_reserved or ip.is_multicast or getattr(ip, "is_site_local", False) or (embedded and not embedded.is_global) or getattr(ip, "teredo", None):
            raise PermissionError("禁止访问本机、内网、保留地址或混合解析域名")
    return PinnedTarget(
        url=url,
        host=parsed.hostname,
        port=port,
        address=addresses[0][4][0],
        scheme=parsed.scheme,
    )


class PinnedHTTPConnection(http.client.HTTPConnection):
    """把连接固定到已校验地址，避免校验后再解析（DNS 重绑定）。"""

    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class PinnedHTTPSConnection(PinnedHTTPConnection):
    def connect(self):
        super().connect()
        try:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
        except Exception:
            self.sock.close()
            raise


def pinned_connection(target: PinnedTarget, timeout):
    """按目标协议返回已固定的连接对象。"""
    if target.scheme == "https":
        return PinnedHTTPSConnection(target.host, target.port, target.address, timeout)
    return PinnedHTTPConnection(target.host, target.port, target.address, timeout)
