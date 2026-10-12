"""桌宠本体（后续项）的回归测试。

覆盖：启动开关读 desktop_shell.enable、位置持久化（保存字段 + 可见性判断 + 不越界写坏配置）、
情绪白名单改为 character.emotions、界面字体显式指定 CJK 字族。

    conda activate MyAI
    python -m pytest Bot/tests/test_p1_desktop.py -v
"""

import sys
from pathlib import Path

import pytest

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))


# --------------------------------------------------------------------------- 启动开关


def test_main_switch_reads_desktop_shell_enable():
    source = (_BOT_DIR / "main.py").read_text(encoding="utf-8")
    assert "desktop_cfg.enable" in source, "启动判据应读 service.desktop_shell.enable"
    # 旧的误读：把 text_input_gui.enable 当启动开关（仅用于决定聊天窗是否可用）
    assert "if text_input_cfg.enable:" not in source


def test_desktop_config_has_position_fields():
    from services.config import DesktopShellConfig

    config = DesktopShellConfig()
    assert config.pet_x == -1 and config.pet_y == -1
    assert config.enable is True and config.exit_grace_seconds > 0


# --------------------------------------------------------------------------- 位置持久化


def test_save_desktop_settings_keeps_other_keys_and_unknown_fields():
    """写入位置不能抹掉未知字段（例如用户自加的注释/键），也不能越界。"""
    import yaml

    from manager.config_manager import _replace_section_values
    from services.config import DesktopShellConfig

    original = (
        "service:\n"
        "  desktop_shell:\n"
        "    enable: true # 桌宠开关\n"
        "    always_on_top: true\n"
        "    bubble_offset_x: 0\n"
        "    bubble_offset_y: 0\n"
        "    exit_grace_seconds: 60\n"
        "    pet_x: -1 # 上次位置\n"
        "    pet_y: -1\n"
        "    custom_key: keep-me\n"
        "  text_input_gui:\n"
        "    enable: true\n"
    )
    config = DesktopShellConfig.model_validate({
        "enable": True, "always_on_top": True, "bubble_offset_x": 0, "bubble_offset_y": 0,
        "exit_grace_seconds": 60, "pet_x": 321, "pet_y": 654,
    })
    updated = _replace_section_values(original, "desktop_shell", config.model_dump(), indent=2)
    parsed = yaml.safe_load(updated)

    section = parsed["service"]["desktop_shell"]
    assert section["pet_x"] == 321 and section["pet_y"] == 654
    assert section["enable"] is True
    # 未知键必须保留（只改自己认识的字段）
    assert section.get("custom_key") == "keep-me"
    # 紧邻的下一段不能被吞掉
    assert parsed["service"]["text_input_gui"]["enable"] is True


def test_desktop_shell_has_position_helpers():
    source = (_BOT_DIR / "ui" / "desktop_shell.py").read_text(encoding="utf-8")
    for name in ("_restore_pet_position", "_position_visible", "_save_pet_position",
                 "_default_pet_position", "_move_pet_to_default_position"):
        assert f"def {name}" in source, f"缺少 {name}"
    # 保存只发生在退出路径
    assert "self._save_pet_position()" in source
    # 设置对话框的"回到默认位置"按钮要真的接上移动逻辑
    assert 'buttons.addButton("回到默认位置"' in source
    assert "if getattr(dialog, \"reset_position_requested\", False):" in source


def test_settings_dialog_reset_button_sets_flag():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from services.config import DesktopShellConfig
    from ui.config import ChatWindowConfig
    from ui.desktop_shell import DesktopSettingsDialog

    app = QApplication.instance() or QApplication([])
    dialog = DesktopSettingsDialog(DesktopShellConfig(), ChatWindowConfig())
    try:
        assert dialog.reset_position_requested is False
        dialog._request_default_position()
        assert dialog.reset_position_requested is True
        # values() 不会带回位置字段（位置是勾选项，不是数值）
        assert "pet_x" not in dialog.values()["desktop_shell"]
    finally:
        dialog.deleteLater()


def test_position_visible_checks_screen_bounds():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QWidget

    from ui.desktop_shell import DesktopShell

    app = QApplication.instance() or QApplication([])
    screen = app.primaryScreen()
    if screen is None:
        pytest.skip("无可用屏幕")

    shell = DesktopShell.__new__(DesktopShell)
    shell._app = app
    pet = QWidget()
    pet.resize(176, 200)
    shell._pet_window = pet

    area = screen.availableGeometry()
    assert shell._position_visible(area.left() + 10, area.top() + 10) is True
    # 明显落在所有屏幕之外：应当判定为不可见
    assert shell._position_visible(-50_000, -50_000) is False
    assert shell._position_visible(area.right() + 5_000, area.bottom() + 5_000) is False


# --------------------------------------------------------------------------- 情绪白名单


class _StubConfig:
    def __init__(self, emotions):
        self.character = type("Character", (), {"emotions": emotions})()


@pytest.fixture
def stub_bot(monkeypatch):
    import bot as bot_module

    def _make(emotions):
        monkeypatch.setattr(bot_module, "_config", _StubConfig(emotions))
        return bot_module.Bot.__new__(bot_module.Bot)

    return _make


def test_allowed_emotions_come_from_character_config(stub_bot):
    bot = stub_bot(["happy", "sad"])
    tags = bot._allowed_emotions()
    # Default 必须始终在列表里（TTS 回落与界面兜底都依赖它）
    assert tags[0] == "Default"
    assert set(tags) == {"Default", "happy", "sad"}


def test_allowed_emotions_falls_back_when_config_empty(stub_bot):
    bot = stub_bot([])
    assert bot._allowed_emotions() == ["Default"]


def test_resolve_emotion_tag_accepts_whitelist_case_insensitively(stub_bot):
    bot = stub_bot(["Default", "happy", "surprised"])
    assert bot._resolve_emotion_tag("Happy") == "happy"
    assert bot._resolve_emotion_tag("surprised") == "surprised"


def test_resolve_emotion_tag_rejects_unknown(stub_bot):
    bot = stub_bot(["Default", "happy"])
    # 不在白名单：保持原有行为（返回 None，不改情绪）
    assert bot._resolve_emotion_tag("angry") is None
    assert bot._resolve_emotion_tag("") is None
    assert bot._resolve_emotion_tag(None) is None


def test_emotion_instruction_uses_config_whitelist():
    source = (_BOT_DIR / "bot.py").read_text(encoding="utf-8")
    # 6 处旧读取（TTS 参考音频文件名）都应改成配置白名单
    assert 'getattr(self.tts_prompt_manager, "sentiments", []) or []' not in source
    assert "emotion_tags = self._allowed_emotions()" in source


def test_default_emotion_constant_is_shared():
    from services.emotion_prefix import DEFAULT_EMOTION

    assert DEFAULT_EMOTION == "Default"


def test_emotion_tag_is_stripped_on_tool_round_too(stub_bot):
    """工具轮的初次回复同样要剥标签，否则 <emotion>…</emotion> 会漏进气泡与历史。"""
    bot = stub_bot(["Default", "happy"])
    text, emotion = bot._strip_response_emotion(
        "<emotion>happy</emotion>喵，我去查一下", has_pending_tool=True
    )
    assert text == "喵，我去查一下"
    assert emotion == "happy"
    assert "<emotion>" not in text


def test_emotion_tag_still_stripped_on_final_reply(stub_bot):
    bot = stub_bot(["Default", "happy"])
    text, emotion = bot._strip_response_emotion("<emotion>happy</emotion>回来啦", has_pending_tool=False)
    assert text == "回来啦"
    assert emotion == "happy"


def test_unknown_emotion_tag_is_stripped_and_ignored(stub_bot):
    """白名单外的标签：文本仍要干净，情绪保持 None（沿用原行为）。"""
    bot = stub_bot(["Default", "happy"])
    text, emotion = bot._strip_response_emotion("<emotion>angry</emotion>哼", has_pending_tool=True)
    assert text == "哼"
    assert emotion is None


def test_tag_only_tool_round_keeps_a_neutral_acknowledgement(stub_bot):
    """模型只回了情绪标签就去调工具：不能把空白送进气泡。"""
    bot = stub_bot(["Default", "happy"])
    text, emotion = bot._strip_response_emotion("<emotion>happy</emotion>", has_pending_tool=True)
    assert text == "稍等，我处理一下。"
    assert emotion == "happy"


def test_plain_response_passes_through_untouched(stub_bot):
    bot = stub_bot(["Default", "happy"])
    assert bot._strip_response_emotion("普通回复", has_pending_tool=True) == ("普通回复", None)


def test_emotion_stripping_is_not_gated_by_tool_state():
    source = (_BOT_DIR / "bot.py").read_text(encoding="utf-8")
    # 旧实现：`if not direct_return and not has_pending_tool:` 包住剥离逻辑 → 工具轮漏标签
    assert "not direct_return and not has_pending_tool" not in source
    assert "self._strip_response_emotion(" in source


def test_emotion_prefix_tolerates_leading_whitespace():
    """模型在 <emotion> 前多打一个换行时，标签同样要被识别并剥离。"""
    from services.emotion_prefix import consume_emotion_prefix

    assert consume_emotion_prefix("\n<emotion>happy</emotion>嗨", flush=True) == (True, "嗨", "happy")
    # 流式首块还只到半个标签：继续暂存，不释放任何可见文本
    assert consume_emotion_prefix(" \n<emo") == (False, "", None)
    # 没有标签时原样返回，不受前导空白影响
    assert consume_emotion_prefix(" 你好", flush=True) == (True, " 你好", None)


# --------------------------------------------------------------------------- 字体


def test_ui_font_specifies_cjk_families():
    pytest.importorskip("PySide6")
    from ui.desktop_shell import _ui_font

    font = _ui_font(14)
    families = font.families()
    assert families[0] == "Microsoft YaHei UI"
    assert "Microsoft YaHei" in families
    assert font.pointSize() == 14


def test_desktop_shell_no_longer_uses_plain_sans_serif():
    source = (_BOT_DIR / "ui" / "desktop_shell.py").read_text(encoding="utf-8")
    assert 'QFont("Sans Serif"' not in source
    assert "def _send_message" in source
    # 每条消息不再新起线程
    assert "TextInputEvent" not in source

# --------------------------------------------------------------------------- 弹窗图层


def test_popup_helper_sets_modality_before_topmost():
    """弹窗必须先设模态、再设置顶：Qt 的 setWindowModality 会重建窗口标志，
    先设的 WindowStaysOnTopHint 会被静默丢掉，桌宠置顶时弹窗就藏在猫猫下面。"""
    source = (_BOT_DIR / "ui" / "desktop_shell.py").read_text(encoding="utf-8")
    body = source.split("def show_popup", 1)[1].split("def open_settings", 1)[0]
    assert "setWindowModality" in body and "WindowStaysOnTopHint" in body
    assert body.index("setWindowModality") < body.index("WindowStaysOnTopHint")


def test_all_popups_go_through_show_popup():
    """弹窗的置顶姿态只能在 show_popup 里设一次，新增弹窗也必须走同一个入口。"""
    source = (_BOT_DIR / "ui" / "desktop_shell.py").read_text(encoding="utf-8")
    assert "return self.show_popup(message_box)" in source
    assert "if self.show_popup(dialog) != QDialog.DialogCode.Accepted:" in source
    # 调用点自己再动窗口标志就会重演"模态把置顶抹掉"的老问题
    assert "message_box.setWindowFlag" not in source
    assert "message_box.setWindowModality" not in source
    assert "dialog.setWindowFlag" not in source


# --------------------------------------------------------------------------- 情绪 → 界面


def test_tts_event_carries_ui_emotion_instead_of_prompt_sentiment(monkeypatch):
    """只有 [Default] 参考音频时，TTS 提示的情绪恒为 Default，
    界面表情必须用模型给的情绪，不能被它顶掉。"""
    import threading
    import types
    from concurrent.futures import ThreadPoolExecutor

    import bot as bot_module

    class _TtsSettings:
        cancel_on_new_input = False

    class _Pipeline:
        tts = _TtsSettings()

    class _Config:
        character = type("Character", (), {"emotions": ["Default", "happy"]})()
        pipeline = _Pipeline()

    monkeypatch.setattr(bot_module, "_config", _Config())

    bot = bot_module.Bot.__new__(bot_module.Bot)
    bot._tts_lock = threading.Lock()
    bot._tts_task_counter = 0
    bot._tts_pending_futures = []
    bot._current_tts_future = None
    bot.tts_thread_pool = ThreadPoolExecutor(max_workers=1)

    from condata.pipeline.tts import TTSPrediction

    class _Tts:
        def predict(self, query):
            return TTSPrediction(wave_data=b"", audio_type="wav")

    bot.tts = _Tts()
    captured = {}
    bot.play_tts = lambda event: captured.update(emotion=event.emotion, transcript=event.transcript)

    prompt = types.SimpleNamespace(
        audio_path="a.wav", prompt_text="p", lang="zh", sentiment="Default"
    )
    try:
        bot._tts_without_block(prompt, "回来啦", 1.0, "happy")
        bot.tts_thread_pool.shutdown(wait=True)
        assert captured["emotion"] == "happy"

        # 不传界面情绪时保持旧行为：回落到参考音频的情绪
        captured.clear()
        bot.tts_thread_pool = ThreadPoolExecutor(max_workers=1)
        bot._tts_without_block(prompt, "嗯", 1.0)
        bot.tts_thread_pool.shutdown(wait=True)
        assert captured["emotion"] == "Default"
    finally:
        bot.tts_thread_pool.shutdown(wait=True)

