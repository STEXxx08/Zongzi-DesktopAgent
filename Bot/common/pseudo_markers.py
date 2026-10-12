"""隐藏模型在正文里自造的"伪系统块"（`<system>…</system>` 之类）。

背景（真实事故，2026-10-12 23:20:56）：
模型在给出正常回复之后，又追加了一段自造的内部计划——

    喵呜…日记里嘛，偷偷用个中二称号不行吗！才、才不是本人自称的怪事啦～
    <system>
    1. 用户纠正我之前的回复"我才不念呢"与当前语境不符。
    …
    4. 标签选 shy，语气自然带点撒娇和狡辩。
    5. 字数控制在三十多字左右。
    </system>

这段文本是模型自己吐出来的助手内容（不是本地协议、不是提示词拼装错误），
而现有过滤只认识本应用自己的机器协议标记
（`<harness_tool_call>` / `<web_tool>` / `<skill_*>`，见 `ModelToolGateway.extract_stream_visible_text`），
于是它整段进了气泡、进了 TTS、也进了历史——主人会把猫娘的心理活动当成她说的话。

处理原则与 `untrusted.py` 相反：这里不是中和，而是**整块丢弃**。
理由：这些名字（system / analysis / plan / thinking …）在本应用里不对应任何可见正文，
模型把它们写出来就是在"泄漏自己的提示词/思考"，朗读出来必然是事故。
识别时要求成对标签（或流式未收完）；半截前缀按流式规则暂存，避免误伤代码块里的普通文本。
"""

import re

# 会当作"内部块"整块丢弃的标签名。全部小写，匹配时忽略大小写。
PSEUDO_BLOCK_NAMES: tuple[str, ...] = (
    "system",
    "system_reminder",
    "analysis",
    "plan",
    "planning",
    "reasoning",
    "thinking",
    "thought",
    "scratchpad",
    "inner_monologue",
    "reflection",
)

_NAMES_PATTERN = "|".join(re.escape(name) for name in sorted(PSEUDO_BLOCK_NAMES, key=len, reverse=True))

# 起始标签：`<system>`、`< system >`、`<system lang="zh">` 都算，但不吃 `<systemx>`。
_OPEN_TAG = re.compile(rf"<\s*(?P<name>{_NAMES_PATTERN})(?:\s[^<>]*)?\s*>", re.IGNORECASE)


def _is_partial_pseudo_tag(buffer: str) -> bool:
    """buffer 整体是不是一个"还没收完的伪标签开头"，例如 `<sys`、`</thin`。"""
    tail = re.sub(r"\s+", "", buffer).casefold()
    if not tail.startswith("<"):
        return False
    body = tail[1:]
    if body.startswith("/"):
        body = body[1:]
    if not body:
        return True
    return any(name.startswith(body) for name in PSEUDO_BLOCK_NAMES)


def _pending_suffix(buffer: str) -> int:
    """buffer 末尾有多少个字符可能是伪标签开头、需要暂存到下一块（0 表示不暂存）。"""
    start = buffer.rfind("<")
    if start < 0:
        return 0
    return len(buffer) - start if _is_partial_pseudo_tag(buffer[start:]) else 0


def iter_visible_segments(buffer: str, flush: bool = False):
    """把 buffer 拆成可见片段，并在每次产出时用游标标明"已判定到哪里"。

    返回列表，每项是 `(可见文本, 已消费长度)`：`buffer[:已消费长度]` 里凡是可见的
    都已经交出去了，剩下的部分（`buffer[已消费长度:]`）调用方必须原样传给下一次
    调用——它要么是未闭合的伪块，要么是可能拼成标签的半截前缀。

    两个刻意的设计：
    - 返回列表而不是生成器：可见文本可能为空，而"可见为空"与"剩余为空"无法用同一个
      返回值表达，生成器一旦被提前中断就再也拿不到游标，调用方会把整条缓冲区又当一遍
      新文本处理（早期版本正是这样把伪块放回去的）；
    - 收尾那一次产出即使可见文本为空也一定会出现，表示"这条 buffer 判定完了"。

    `flush=False`（流式中）时未闭合的伪块与半截标签都留在剩余部分；
    `flush=True`（收尾/非流式）时未闭合的伪块直接丢弃——模型被截断时留下的半截
    内部块同样不该被朗读。
    """
    segments: list[tuple[str, int]] = []
    consumed = 0
    total = len(buffer or "")
    while consumed < total:
        opened = _OPEN_TAG.search(buffer, consumed)
        if opened is None:
            break

        closing = re.compile(
            rf"<\s*/\s*{re.escape(opened.group('name'))}\s*>", re.IGNORECASE
        ).search(buffer, opened.end())
        if closing is None:
            # 没有配对的结束标签：要么还在流式传输（含起始标签一起暂存等下一块），
            # 要么模型就写到一半结束了（连块内文本一起丢弃）。
            if flush:
                if opened.start() > consumed:
                    segments.append((buffer[consumed:opened.start()], opened.start()))
            return segments

        if opened.start() > consumed:
            segments.append((buffer[consumed:opened.start()], opened.start()))
        consumed = closing.end()

    if consumed >= total:
        # 文本可能整段都是伪块、或正好以伪块收尾：此时没有任何可见文本，
        # 仍要记录一次"已判定完"，调用方才不会把整条 buffer 当成未判定。
        segments.append(("", total))
        return segments

    rest = buffer[consumed:]
    if not flush:
        hold = _pending_suffix(rest)
        if hold:
            if len(rest) > hold:
                segments.append((rest[: len(rest) - hold], total - hold))
            return segments
    segments.append((rest, total))
    return segments


def strip_pseudo_blocks(text: str) -> str:
    """非流式用法：一次性丢弃全部伪系统块（含未闭合的尾巴）。"""
    return "".join(segment for segment, _ in iter_visible_segments(text or "", flush=True))
