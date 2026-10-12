"""把不可信文本降级为数据：中和其中的机器协议标记。

工具结果、网页正文、MCP 返回值都可能包含与本地协议相同的标记文本
（例如一个网页里写着 `<tool_call>{...}</tool_call>`）。这些文本进入上下文后，
模型可能把它们当成真的指令照抄，而解析器只认原始 ASCII 标记。

做法：只处理真正的"标签形状"——`<tag>`、`</tag>` 以及未闭合的 `<tag...`，
把其中的 `< > / _` 换成等价全角字符。
- 模型仍能读懂（`＜tool_call＞` 一眼就知道是什么）；
- 但拼不出可被解析的 ASCII 标记，间接注入在结构上失效；
- JSON 正文、`sandbox_write` 这类字段名、普通比较符 `a < b` 都保持原样。

注意：只有"不可信内容"才走这条路（工具结果、环境观察）；系统提示词、
内置 Skill 说明、本地硬编码文案必须保持原样。
"""

import re
from typing import Callable, Iterable

_NEUTRALIZE_TABLE = str.maketrans({
    "<": "＜",
    ">": "＞",
    "/": "／",
    "_": "＿",
})

# 只匹配标签形状：`<name>`、`</name>`，或尚未闭合的 `<name ...` / `</na`（流式半截标记）
_MARKER_SPAN = re.compile(
    r"<(?P<close>/?)(?P<name>[A-Za-z][A-Za-z0-9_]*)(?:(?P<ws>\s+)[^<>\n]*)?>?"
)


def neutralize_markers(text: str) -> str:
    """中和文本中的机器协议标记写法，返回仍可阅读但无法被协议解析的文本。"""
    if not text:
        return text
    normalized = str(text)
    if "<" not in normalized:
        return normalized

    def _replace(match: "re.Match[str]") -> str:
        closed = match.group(0).endswith(">")
        prefix = "<" + (match.group("close") or "") + match.group("name")
        rest = match.group(0)[len(prefix):]
        if not closed and not match.group("ws"):
            # 没有闭合符也没有空白：可能是普通比较符（如 a < b），不动它
            return match.group(0)
        fragment = prefix[:-len(match.group("name"))] + match.group("name").translate(_NEUTRALIZE_TABLE)
        if closed:
            return fragment + rest[:-1].translate(_NEUTRALIZE_TABLE) + "＞"
        return fragment + rest

    return _MARKER_SPAN.sub(_replace, normalized)


def neutralize_all(values: Iterable[str]) -> list[str]:
    return [neutralize_markers(value) for value in values]


def wrap_source(renderer: Callable[[str], str], text: str | None) -> str | None:
    """先中和再渲染，供各上下文构建点复用。"""
    if not text:
        return text
    return renderer(neutralize_markers(text))
