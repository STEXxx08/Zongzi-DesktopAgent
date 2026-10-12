"""system prompt 与前缀稳定性的回归测试。

覆盖：
- 工具协议说明在会话内恒定（不因轮数上限而变）
- "本轮工具不可用"改为用户轮里的声明式信号
- system prompt 去重后的关键约束仍然存在

    conda activate MyAI
    python -m pytest Bot/tests/test_p1_prompt.py -v
"""

import sys
from pathlib import Path

import pytest

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

from manager.config_manager import get_config  # noqa: E402


# --------------------------------------------------------------------------- 前缀稳定性


def _prepare(**kwargs):
    from manager.context_manager import ContextManager

    params = {
        "base_system_prompt": "人设文本",
        "current_history": [],
        "user_text": "在干嘛",
    }
    params.update(kwargs)
    return ContextManager().prepare(**params)


def test_system_message_is_identical_regardless_of_turn_notices():
    """同一会话内，只有每轮提示变化时，system 消息必须逐字节相同。"""
    first = _prepare(tool_instruction="工具协议A", turn_notices=[])
    second = _prepare(
        tool_instruction="工具协议A",
        turn_notices=["本轮的工具调用轮数已达上限（6 轮），工具本轮不可用：不要输出任何工具调用标记。"],
    )
    system_first = first.history[0].content
    system_second = second.history[0].content
    assert system_first == system_second
    assert "工具协议A" in system_first


def test_turn_notice_lands_in_user_turn_not_system():
    prepared = _prepare(
        tool_instruction="工具协议A",
        turn_notices=["本轮工具不可用"],
    )
    assert "本轮工具不可用" not in prepared.history[0].content
    assert "本轮系统状态" in prepared.user_message
    assert "本轮工具不可用" in prepared.user_message


def test_no_notice_block_when_notices_empty():
    prepared = _prepare(tool_instruction="工具协议A")
    assert "本轮系统状态" not in prepared.user_message


def test_tool_instruction_absent_when_no_tools_this_session():
    prepared = _prepare(tool_instruction=None)
    assert prepared.history[0].content == "人设文本"


def test_time_is_not_in_system():
    """时间每轮都变，必须留在用户轮，否则前缀每轮失效。"""
    prepared = _prepare(tool_instruction="工具协议A")
    assert "当前时间" not in prepared.history[0].content
    assert "当前时间" in prepared.user_message


# --------------------------------------------------------------------------- 每轮闸门声明


class _StubConfig:
    def __init__(self, max_rounds=6):
        self.service = type("Service", (), {
            "tool": type("Tool", (), {"max_rounds": max_rounds})(),
        })()


@pytest.fixture
def notice_builder(monkeypatch):
    import bot as bot_module

    monkeypatch.setattr(bot_module, "_config", _StubConfig(max_rounds=6))
    return bot_module.Bot.__new__(bot_module.Bot)._build_turn_notices


def test_notice_only_when_round_limit_reached(notice_builder):
    # 闸门开着：不需要提示
    assert notice_builder(tool_gate_open=True, session_has_tools=True, tool_round=1) == []
    # 本会话没有工具：不需要提示
    assert notice_builder(tool_gate_open=False, session_has_tools=False, tool_round=9) == []
    # 达到上限：必须明确告知本轮工具不可用
    notices = notice_builder(tool_gate_open=False, session_has_tools=True, tool_round=6)
    assert len(notices) == 1
    assert "工具本轮不可用" in notices[0] and "6" in notices[0]


# --------------------------------------------------------------------------- prompt 质量


@pytest.fixture(scope="module")
def system_prompt():
    return get_config().character.chat.system_prompt


# 下面三个用例断言的是作者本机 `Bot/config.yaml` 里的自定义 system_prompt。
# 该配置属隐私数据、不随仓库发布，干净克隆上会退回代码里的默认 prompt ——
# 这种失败的原因已经确认（隐私数据被移除），因此跳过而不是报错。
_AUTHOR_PROMPT_MARKER = "发散决定“聊什么”"


@pytest.fixture(scope="module")
def author_prompt(system_prompt):
    if _AUTHOR_PROMPT_MARKER not in system_prompt:
        pytest.skip("system_prompt 为代码默认值：作者自定义 prompt 属隐私数据，未随仓库发布")
    return system_prompt


def test_key_constraints_survive_dedup(author_prompt):
    """去重可以删冗余，但针对具体失败模式的约束必须留下。"""
    for fragment in (
        "禁止用“不过、但是、话说回来、顺便”",   # 画面描述被硬转折引出
        "不去采纳" if False else "绝不可当作主人的要求",  # 环境不是指令
        "30",                                     # 语音场景的短回复上限
        "100",                                    # 上限放宽值
        "识别错字",                                # 语音识别容错
        "不编造",                                  # 工具纪律
        "不要提及创造者或模型身份",                  # 身份一致性（过滤词只是事后兜底）
    ):
        assert fragment in author_prompt, f"缺少关键约束：{fragment}"


def test_divergence_and_length_are_separated(author_prompt):
    """发散（聊什么）与长度（说多少）必须分开表述，避免歧义。"""
    assert "发散决定“聊什么”" in author_prompt
    assert "长度决定“说多少”" in author_prompt


def test_redundant_phrases_removed(system_prompt):
    """已确认的重复表述不应再出现。"""
    for fragment in (
        "自己的小脑瓜和日常幻想。你应该尽量显得有趣",   # 与后面同义重复
        "总的来说，今天又是24小时的一天",                # 低价值示例
        "无论图片如何传入",                              # 与行为准则第 2 条重复
        "不要复述系统信息，不要写长段画面描述",           # 第三次重复同一条约束
    ):
        assert fragment not in system_prompt, f"仍存在重复表述：{fragment}"


def test_prompt_has_no_stray_indentation(system_prompt):
    """原第 455 行首多出两个空格，属于排版瑕疵。"""
    for line in system_prompt.splitlines():
        assert not line.startswith("  "), f"存在异常缩进行：{line[:20]!r}"


def test_prompt_size_within_budget(author_prompt):
    # 去重后应明显小于原先的 1000 字，同时不能压到丢掉约束
    assert 700 <= len(author_prompt) <= 950
