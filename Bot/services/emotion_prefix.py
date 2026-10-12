EMOTION_PREFIX = "<emotion>"
EMOTION_SUFFIX = "</emotion>"
MAX_EMOTION_PREFIX_LENGTH = 256
# 中性/默认情绪标签：所有白名单都包含它，TTS 找不到对应参考音频时也回落到它。
DEFAULT_EMOTION = "Default"


def consume_emotion_prefix(buffer: str, flush: bool = False) -> tuple[bool, str, str | None]:
    # 标签前的空白/换行不影响语义：模型经常在 <emotion> 前多打一个换行，只按原样
    # startswith 判断会让标签漏进气泡、历史和 TTS。这里只让“标签识别”忽略前导空白，
    # 未发现标签时仍原样返回 buffer。
    probe = buffer.lstrip()
    if probe.startswith(EMOTION_PREFIX):
        closing_index = probe.find(EMOTION_SUFFIX, len(EMOTION_PREFIX))
        if closing_index < 0:
            if not flush and len(probe) <= MAX_EMOTION_PREFIX_LENGTH:
                return False, "", None
            return True, probe[len(EMOTION_PREFIX):], None
        emotion = probe[len(EMOTION_PREFIX):closing_index].strip()
        visible_text = probe[closing_index + len(EMOTION_SUFFIX):].lstrip("\r\n \t")
        return True, visible_text, emotion

    if not flush and EMOTION_PREFIX.startswith(probe):
        return False, "", None
    return True, buffer, None


def normalize_emotion(emotion: str | None, allowed: list[str], default: str) -> str:
    if not emotion:
        return default
    allowed_by_casefold = {item.casefold(): item for item in allowed if item}
    return allowed_by_casefold.get(emotion.strip().casefold(), default)


def parse_emotion_prefix(
    text: str,
    allowed: list[str],
    default: str,
) -> tuple[str, str, bool]:
    _, visible_text, emotion = consume_emotion_prefix(text, flush=True)
    return visible_text, normalize_emotion(emotion, allowed, default), emotion is not None