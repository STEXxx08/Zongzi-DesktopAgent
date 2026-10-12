from typing import List

from common.enumerator import Language


def is_blank(s: str) -> bool:
    """Check if a string is None, empty, or contains only whitespace."""
    return s is None or not s.strip() or s == ""


# def split_by_punc(text: str, lang: Language) -> List[str]:
#     if lang == Language.ZH:
#         cut_punc = "，。！？"
#     elif lang == Language.JA:
#         cut_punc = "、。！？"
#     else:
#         cut_punc = ",.!?"
# 
#     def punc_cut(text: str, punc: str):
#         texts = []
#         last = -1
#         for i in range(len(text)):
#             if text[i] in punc:
#                 try:
#                     texts.append(text[last + 1: i])
#                 except IndexError:
#                     continue
#                 last = i
#         return texts
# 
#     return punc_cut(text, cut_punc)

def split_by_punc(text: str, lang: Language) -> List[str]:
    if lang == Language.ZH:
        cut_punc = "，。！？"
    elif lang == Language.JA:
        cut_punc = "、。！？"
    else:
        cut_punc = ",.!?"

    texts = []
    last = 0
    for i, ch in enumerate(text):
        if ch in cut_punc:
            if i > last:
                texts.append(text[last:i].strip())
            last = i + 1
    # 处理末尾剩余部分
    if last < len(text):
        texts.append(text[last:].strip())
    return [t for t in texts if t]
