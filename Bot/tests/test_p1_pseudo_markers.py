"""伪系统块过滤的离线用例（真实事故回归，2026-10-12 23:20:56）。

事故现场：模型在正常回复后追加自造的 `<system>` 计划块，整段进了气泡 / TTS / 历史。
这些用例锁住三条性质：
1. 完整块被丢弃，正文保留；
2. 流式下未闭合的块不提前放行，收尾时丢弃（不朗读半截内部块）；
3. 普通文本、代码块、以及"只是提到 system 这个词"的表达不受影响。
"""

from common.pseudo_markers import strip_pseudo_blocks, iter_visible_segments

LEAKED = (
    "喵呜…日记里嘛，偷偷用个中二称号不行吗！才、才不是本人自称的怪事啦～\n"
    "<system>\n"
    "1. 用户纠正我之前的回复\"我才不念呢\"与当前语境不符。\n"
    "2. 用户指出日记署名\"傲娇猫娘小粽子\"这种自称在常规日记里显得奇怪。\n"
    "3. 我需要承认这点并表现出害羞/被戳穿的小情绪，保持简短。\n"
    "4. 标签选 shy，语气自然带点撒娇和狡辩。\n"
    "5. 字数控制在三十多字左右。\n"
    "</system>"
)


def _feed_stream(chunks, flush_at_end=True):
    """模拟流式消费：返回 (可见文本, 未判定而暂存的尾部)。

    每块都按同样的顺序调用 iter_visible_segments：把可见部分累加，
    剩余部分（未闭合伪块 / 半截标签）留到下一块继续判定。
    """
    buffer = ""
    visible = ""
    for chunk in chunks:
        buffer += chunk
        segments = iter_visible_segments(buffer, flush=False)
        visible += "".join(segment for segment, _ in segments)
        consumed = segments[-1][1] if segments else 0
        buffer = buffer[consumed:]
    if flush_at_end:
        segments = iter_visible_segments(buffer, flush=True)
        visible += "".join(segment for segment, _ in segments)
        consumed = segments[-1][1] if segments else 0
        buffer = buffer[consumed:]
    return visible, buffer


def test_strip_full_block_keeps_body():
    assert strip_pseudo_blocks(LEAKED) == "喵呜…日记里嘛，偷偷用个中二称号不行吗！才、才不是本人自称的怪事啦～\n"


def test_strip_is_case_and_space_insensitive():
    text = "正文< SYSTEM >内部</ system >尾巴"
    assert strip_pseudo_blocks(text) == "正文尾巴"


def test_strip_attributes_variant():
    assert strip_pseudo_blocks('<system lang="zh">内部</system>正文') == "正文"


def test_strip_drops_unclosed_block():
    # 模型被截断，只写了开头：半截内部块不能朗读。
    assert strip_pseudo_blocks("正文\n<analysis>\n这里是没写完的思考") == "正文\n"


def test_multiple_blocks_and_other_names():
    text = "A<thinking>t</thinking>B<plan>p</plan>C"
    assert strip_pseudo_blocks(text) == "ABC"


def test_plain_text_and_angle_brackets_untouched():
    text = "比较大小 a < b，还有 <emotion>shy</emotion> 标签和 <div> 这种普通标签。"
    assert strip_pseudo_blocks(text) == text


def test_stream_chunked_leak_is_hidden():
    # 标签被切碎成多个数据块，且正文先到、块后到。
    chunks = ["喵呜…日记里嘛～", "\n<sys", "tem>\n1. 用户纠正", "我…\n</sys", "tem>"]
    visible, tail = _feed_stream(chunks)
    assert visible == "喵呜…日记里嘛～\n"
    assert tail == ""


def test_stream_unclosed_block_not_released_midstream():
    # 只收到起始标签：这一段既不能显示也不能丢，必须暂存。
    visible, tail = _feed_stream(["正文<sys"], flush_at_end=False)
    assert visible == "正文"
    assert tail == "<sys"

    # 下一块补齐，块内内容全程不可见。
    buffer = tail + "tem>内部</system>"
    visible, tail = _feed_stream([buffer])
    assert visible == ""
    assert tail == ""


def test_stream_text_after_closed_block_still_visible():
    chunks = ["<system>内部</system>", "尾巴"]
    visible, tail = _feed_stream(chunks)
    assert visible == "尾巴"
    assert tail == ""


def test_partial_angle_is_not_held_forever():
    # 普通的小于号不应该被当成伪标签开头而卡住。
    visible, tail = _feed_stream(["3 < 5 是成立的"], flush_at_end=False)
    assert visible == "3 < 5 是成立的"
    assert tail == ""


BODY = "喵呜…日记里嘛，偷偷用个中二称号不行吗！才、才不是本人自称的怪事啦～\n"


def test_bot_extract_keeps_tool_protocol_working_and_drops_leak():
    """端到端：接入点必须同时保住工具协议解析，并丢掉伪系统块。

    这条用例锁的是集成点本身——只测 `strip_pseudo_blocks` 的话，
    `_extract_tool_visible_text` 里"机器协议暂存尾部要先拼回去"这一步回归了也测不出来。

    注意 `flush` 的语义：非流式/收尾调用（bot.py 里显式传 `flush=True`）必须把伪块丢掉；
    流式中间调用（`flush=False`）则允许把未判定的尾部暂存给下一段数据。
    """
    from bot import Bot
    from services.tool_model_gateway import ModelToolGateway

    class _Stub:
        model_tool_gateway = ModelToolGateway.__new__(ModelToolGateway)
        web_search_service = None
        skill_service = None

    _Stub.model_tool_gateway.marker = "tool_call"
    extract = Bot._extract_tool_visible_text
    stub = _Stub()

    # 伪块被丢掉后留下的那个换行会保留：正文与工具标记之间的空白由调用方 strip。
    visible, pending = extract(stub, LEAKED + "\n<tool_call>{\"name\": \"a\"}</tool_call>", flush=True)
    assert visible == BODY + "\n"
    assert pending == ""

    # 未收完的机器协议标记仍要暂存，交给下一段流式数据补齐（流式路径 flush=False）。
    visible, pending = extract(stub, "正文<tool_ca")
    assert visible == "正文"
    assert pending == "<tool_ca"

    # 收尾时若 Text 只含正文 + 伪块，伪块整块消失。
    visible, pending = extract(stub, LEAKED, flush=True)
    assert visible == BODY
    assert pending == ""


def test_tool_marker_after_plain_braces_is_still_hidden():
    """机器协议拦截不能被正文里的普通花括号打断。

    历史缺陷（同一次排查中发现）：`extract_stream_visible_text` 的"未带标记的工具对象"
    扫描在遇到第一个**不成形**的花括号时直接 `break`，于是它判断不了后面的机器标记，
    把整段（含 `<tool_call>` 与参数）当可见文本送给气泡和 TTS。
    正文里的花括号（模型写到一半的 JSON、报错堆栈、昵称）是常见的，所以这条必须锁住。
    """
    from services.tool_model_gateway import ModelToolGateway

    response = '主人这个 { 应该是笔误吧？\n<tool_call>{"name": "a", "arguments": {}}</tool_call>'
    visible, _ = ModelToolGateway.extract_stream_visible_text(
        response, flush=True, markers=("tool_call",)
    )
    assert "<tool_call>" not in visible
    # 花括号本身也被当成"暂存点"由调用方再判定一次，所以只到它前面为止。
    assert visible == "主人这个 "

    # 只长得像工具对象的坏 JSON 仍然要整体隐藏（不能因为修上面那条就放它过去）。
    shaped = '前文\n{"name": "a", "arguments": {"path": '
    visible, pending = ModelToolGateway.extract_stream_visible_text(
        shaped, flush=True, markers=("tool_call",)
    )
    assert visible == "前文\n"
    assert pending == ""
