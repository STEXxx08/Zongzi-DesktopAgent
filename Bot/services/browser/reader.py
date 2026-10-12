import json
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from services.net_safety import PinnedHTTPConnection, PinnedHTTPSConnection, public_target


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    text: str
    backend: str
    truncated: bool = False


def public_url(url: str):
    """只允许公共网页；每次 HTTP 跳转重新校验，并固定已校验的连接地址。

    具体校验规则与 MCP 客户端共用 services/net_safety.py，这里只固定网页端口策略。
    """
    try:
        target = public_target(url, allowed_ports=frozenset({80, 443}))
    except PermissionError as exc:
        message = str(exc)
        if "端口" in message:
            raise PermissionError("网页读取仅允许 80 和 443 端口") from None
        if "账号密码" in message:
            raise PermissionError("仅允许无账号密码的公共 HTTP/HTTPS 网页") from None
        raise
    return urlsplit(target.url), target.address, target.port


class _PinnedHTTP(PinnedHTTPConnection):
    pass


class _PinnedHTTPS(PinnedHTTPSConnection):
    pass


class _ArticleParser(HTMLParser):
    """提取静态 HTML 正文，不执行页面脚本；优先 article/main 区域。"""

    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "form", "svg", "template"}
    BLOCK = {"p", "div", "section", "article", "main", "li", "tr", "h1", "h2", "h3", "h4", "br", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.body = []
        self.article = []
        self.title = []

    def handle_starttag(self, tag, attrs):
        if tag not in {"br", "hr", "img", "meta", "link", "input", "source", "wbr", "area", "base", "embed", "param", "track", "col"}:
            self.stack.append(tag)
        if tag in self.BLOCK:
            self._append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK:
            self._append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self._append("\n")
        if tag in self.stack:
            reverse_index = self.stack[::-1].index(tag)
            del self.stack[len(self.stack) - reverse_index - 1:]

    def _append(self, data):
        if "title" in self.stack:
            self.title.append(data)
        elif not self.SKIP.intersection(self.stack) and "head" not in self.stack:
            self.body.append(data)
            if {"article", "main"}.intersection(self.stack):
                self.article.append(data)

    def handle_data(self, data):
        self._append(data)

    def result(self):
        def normalize(parts):
            lines = [re.sub(r"[\t\r\f\v ]+", " ", line).strip() for line in "".join(parts).splitlines()]
            return "\n".join(line for line in lines if line)
        preferred = normalize(self.article)
        return normalize(self.title), preferred or normalize(self.body)


class HTTPPageReader:
    def read(self, url, config):
        deadline = time.monotonic() + config.request_timeout
        for hop in range(config.max_redirects + 1):
            parsed, address, port = public_url(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("网页读取超时")
            connection_cls = _PinnedHTTPS if parsed.scheme == "https" else _PinnedHTTP
            connection = connection_cls(parsed.hostname.encode("idna").decode("ascii"), port, address, remaining)
            try:
                target = quote(urlunsplit(("", "", parsed.path or "/", parsed.query, "")), safe="/%?=&:+;,@!$'()*[]~-")
                connection.request("GET", target, headers={"User-Agent": "BotResearch/0.5", "Accept": "text/html,text/plain", "Accept-Encoding": "identity"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    if hop == config.max_redirects or not response.getheader("Location"):
                        raise ValueError("网页重定向次数超限或缺少目标")
                    url = urljoin(url, response.getheader("Location"))
                    continue
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"网页返回 HTTP {response.status}")
                content_type = response.getheader("Content-Type", "").lower()
                if content_type.split(";")[0].strip() not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise ValueError("当前仅支持 HTML 或纯文本网页，不支持 PDF、下载及二进制文件")
                if response.getheader("Content-Encoding", "identity").lower() not in {"identity", ""}:
                    raise ValueError("网页未遵守 identity 编码要求")
                chunks = []
                size = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("网页读取超时")
                    if connection.sock:
                        connection.sock.settimeout(remaining)
                    # read1 避免持续慢速响应让一次 read 无限等待。
                    chunk = response.read1(min(65536, config.max_page_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > config.max_page_bytes:
                        raise ValueError("网页下载超过字节上限")
                    chunks.append(chunk)
                data = b"".join(chunks)
                charset_match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type)
                if charset_match is None:
                    charset_match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", data[:4096].decode("ascii", "ignore"), re.I)
                charset = charset_match.group(1) if charset_match else "utf-8"
                try:
                    text = data.decode(charset, errors="replace")
                except LookupError:
                    text = data.decode("utf-8", errors="replace")
                title = parsed.hostname
                if "html" in content_type:
                    parser = _ArticleParser()
                    parser.feed(text)
                    title, text = parser.result()
                text = text.strip()
                if not text:
                    raise ValueError("页面没有可提取正文；可能需要浏览器渲染或登录")
                return WebPage(url, title or parsed.hostname, text[:config.max_text_chars], "http", len(text) > config.max_text_chars)
            finally:
                connection.close()


class MCPPageReader:
    def __init__(self, mcp):
        self.mcp = mcp

    def read(self, url, config):
        public_url(url)
        # 固定只读提取函数，不接受模型提供的 JavaScript，不写远程服务文件。
        function = """() => {
            const root = document.querySelector('article') || document.querySelector('main') || document.body;
            const text = root ? root.innerText : '';
            return {url: location.href, title: document.title,
                text: text.slice(0, LIMIT), truncated: text.length > LIMIT};
        }""".replace("LIMIT", str(config.max_text_chars))
        results = self.mcp.call_tool_sequence(config.mcp_server, [
            ("browser_navigate", {"url": url}),
            ("browser_evaluate", {"function": function}),
        ])
        result = results[-1]
        payload = result.get("structuredContent")
        if not isinstance(payload, dict) or "text" not in payload:
            payload = None
            for item in result.get("content", []):
                if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
                    continue
                text = item["text"]
                # Playwright 的 JSON 结果前后可带 Markdown 标题或代码块。
                for match in re.finditer(r"\{", text):
                    try:
                        candidate, _ = json.JSONDecoder().raw_decode(text, match.start())
                    except ValueError:
                        continue
                    if isinstance(candidate, dict) and {"url", "title", "text"} <= candidate.keys():
                        payload = candidate
                        break
                if payload is not None:
                    break
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str) or not payload["text"].strip():
            raise ValueError("浏览器 MCP 未返回可识别的页面正文")
        public_url(payload.get("url", ""))
        text = payload["text"].strip()
        return WebPage(payload["url"], str(payload.get("title") or url), text[:config.max_text_chars], "mcp", bool(payload.get("truncated")) or len(text) > config.max_text_chars)