"""桌宠前端（模块化架构、三模形象、六大设置中心、保注释写回与情绪驱动）的回归测试。

    conda activate MyAI
    python -m pytest Bot/tests/test_p1_desktop.py -v
"""

import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_BOT_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BOT_DIR.parent
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))


def _local_tmp_root() -> Path:
    candidates = []
    override = os.environ.get("DSH_TEST_TMP")
    if override:
        candidates.append(Path(override))
    candidates.extend([
        _REPO_DIR / ".temp" / "pytest_desktop",
        _BOT_DIR / ".temp" / "pytest_desktop",
        Path(tempfile.gettempdir()) / "dsh_test_tmp",
    ])
    for root in candidates:
        try:
            root.mkdir(parents=True, exist_ok=True)
            return root
        except OSError:
            continue
    raise RuntimeError("No writable temp directory found for desktop tests")


@pytest.fixture
def local_tmp_dir():
    path = _local_tmp_root() / f"case_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _flush_qt_events() -> None:
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass


# --------------------------------------------------------------------------- 启动开关与配置模型


def test_main_switch_reads_desktop_shell_enable():
    source = (_BOT_DIR / "main.py").read_text(encoding="utf-8")
    assert "desktop_cfg.enable" in source, "启动判据应读 service.desktop_shell.enable"
    assert "if text_input_cfg.enable:" not in source


def test_desktop_config_has_position_and_avatar_fields():
    from pydantic import ValidationError
    from services.config import DesktopShellConfig

    config = DesktopShellConfig()
    assert config.pet_x == -1 and config.pet_y == -1
    assert config.enable is True and config.exit_grace_seconds > 0
    assert config.avatar_mode == "vector"
    assert config.avatar_path == ""
    assert config.pet_scale == 1.0
    assert config.pet_opacity == 1.0

    with pytest.raises(ValidationError):
        DesktopShellConfig(avatar_mode="unknown_mode")
    with pytest.raises(ValidationError):
        DesktopShellConfig(pet_scale=3.5)
    with pytest.raises(ValidationError):
        DesktopShellConfig(pet_opacity=0.1)


# --------------------------------------------------------------------------- 保注释多级 YAML 写回


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
    assert section.get("custom_key") == "keep-me"
    assert parsed["service"]["text_input_gui"]["enable"] is True


def test_save_bot_settings_nested_multiline_and_lists_preserve_comments(local_tmp_dir):
    """全量保注释写回必须支持多层嵌套字典、列表与多行 system_prompt，同时保留注释和自定义键。"""
    import yaml

    from config import BotConfig
    from manager.config_manager import save_bot_settings

    yaml_text = (
        "# 顶部文件注释\n"
        "pipeline:\n"
        "  llm:\n"
        "    enable: true # 主模型开关\n"
        "    model_id: 'deepseek-chat'\n"
        "    api_key: null\n"
        "service:\n"
        "  desktop_shell:\n"
        "    enable: true\n"
        "    always_on_top: true\n"
        "  chat_window:\n"
        "    enable: true\n"
        "    title: '旧标题'\n"
        "character:\n"
        "  bot_name: '粽子' # 角色名注释\n"
        "  emotions:\n"
        "    - Default\n"
        "    - happy\n"
        "  chat:\n"
        "    system_prompt: 旧第一行\n"
        "      旧第二行\n"
        "    # 历史列表注释不能丢\n"
        "    injected_history: []\n"
        "memory:\n"
        "  enable: true\n"
        "  retrieval:\n"
        "    mode: 'off' # 检索模式注释\n"
        "    top_k: 4\n"
        "  user_custom_note: keep-this\n"
    )
    cfg_path = local_tmp_dir / "config.yaml"
    cfg_path.write_text(yaml_text, encoding="utf-8")

    parsed_cfg = save_bot_settings(
        {
            "pipeline": {
                "llm": {
                    "model_id": "deepseek-v4-flash",
                    "api_key": "sk-test-123",
                }
            },
            "character": {
                "bot_name": "小粽子",
                "emotions": ["Default", "happy", "shy"],
                "chat": {
                    "system_prompt": "第一行猫娘设定\n第二行补充说明",
                },
            },
            "memory": {
                "retrieval": {
                    "mode": "on",
                    "top_k": 6,
                }
            },
        },
        path=cfg_path,
    )

    saved_text = cfg_path.read_text(encoding="utf-8")
    assert "# 顶部文件注释" in saved_text
    assert "# 主模型开关" in saved_text
    assert "# 角色名注释" in saved_text
    assert "# 历史列表注释不能丢" in saved_text
    assert "# 检索模式注释" in saved_text

    reloaded = yaml.safe_load(saved_text)
    assert reloaded["pipeline"]["llm"]["model_id"] == "deepseek-v4-flash"
    assert reloaded["pipeline"]["llm"]["api_key"] == "sk-test-123"
    assert reloaded["character"]["bot_name"] == "小粽子"
    assert reloaded["character"]["emotions"] == ["Default", "happy", "shy"]
    assert reloaded["character"]["chat"]["system_prompt"] == "第一行猫娘设定\n第二行补充说明"
    assert reloaded["memory"]["retrieval"]["mode"] == "on"
    assert reloaded["memory"]["retrieval"]["top_k"] == 6
    assert reloaded["memory"]["user_custom_note"] == "keep-this"
    assert isinstance(parsed_cfg, BotConfig)
    assert parsed_cfg.character.bot_name == "小粽子"


# --------------------------------------------------------------------------- 三模桌宠形象渲染器


def test_mascot_vector_renderer_paints_all_states_and_emotions():
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from ui.mascot import MascotCanvas

    QApplication.instance() or QApplication([])
    canvas = MascotCanvas()
    try:
        assert canvas.active_mode == "vector"
        canvas.set_avatar_config("vector", "", 1.25)
        assert canvas.width() == round(MascotCanvas.BASE_WIDTH * 1.25)

        for state in ("待命中", "聆听中", "思考中", "正在说话", "遇到问题"):
            canvas.set_state(state)
            for emotion in ("Default", "happy", "sad", "angry", "shy", "surprised"):
                canvas.set_emotion(emotion)
                img = QImage(canvas.size(), QImage.Format.Format_ARGB32)
                img.fill(0)
                canvas.render(img)
                assert not img.isNull()
                assert img.pixelColor(img.width() // 2, img.height() // 2).alpha() > 0
    finally:
        canvas.cleanup()
        canvas.deleteLater()
        _flush_qt_events()


def test_mascot_sprite_pack_loads_assets_and_falls_back_on_missing(local_tmp_dir):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    from ui.mascot import MascotCanvas

    QApplication.instance() or QApplication([])
    default_img = local_tmp_dir / "default.png"
    happy_img = local_tmp_dir / "happy.png"
    for path, color in ((default_img, QColor("#aabbcc")), (happy_img, QColor("#ff99aa"))):
        img = QImage(32, 32, QImage.Format.Format_ARGB32)
        img.fill(color)
        assert img.save(str(path))

    (local_tmp_dir / "manifest.json").write_text(
        json.dumps({"default": "default.png", "emotions": {"happy": "happy.png"}}),
        encoding="utf-8",
    )

    canvas = MascotCanvas()
    try:
        canvas.set_avatar_config("sprite", str(local_tmp_dir), 1.0)
        assert canvas.active_mode == "sprite"
        assert canvas.fallback_message == ""
        canvas.set_emotion("happy")
        assert canvas._renderer._current_file == happy_img.resolve()

        # 指向不存在路径时必须安全回退为内置矢量猫娘，且提供原因说明
        canvas.set_avatar_config("sprite", str(local_tmp_dir / "not-exist"), 1.0)
        assert canvas.active_mode == "vector"
        assert "not-exist" in canvas.fallback_message
    finally:
        canvas.cleanup()
        canvas.deleteLater()
        _flush_qt_events()


def test_mascot_live2d_falls_back_gracefully_when_missing_backend(local_tmp_dir):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from ui.mascot import MascotCanvas

    QApplication.instance() or QApplication([])
    canvas = MascotCanvas()
    try:
        canvas.set_avatar_config("live2d", str(local_tmp_dir / "missing.model3.json"), 1.0)
        assert canvas.active_mode == "vector"
        assert canvas.fallback_message != ""
    finally:
        canvas.cleanup()
        canvas.deleteLater()
        _flush_qt_events()


def test_tray_icon_renders_non_empty_pixmap():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from ui.mascot import create_mascot_tray_icon

    QApplication.instance() or QApplication([])
    icon = create_mascot_tray_icon()
    assert not icon.isNull()
    assert not icon.pixmap(48, 48).isNull()


# --------------------------------------------------------------------------- 窗口交互与六大分类设置中心


def test_pet_window_hover_bar_and_desktop_config_apply():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from services.config import DesktopShellConfig
    from ui.bridge import BotUiBridge
    from ui.chat_window import ChatWindow
    from ui.pet_window import PetWindow

    QApplication.instance() or QApplication([])
    bridge = BotUiBridge()
    chat = ChatWindow("粽子", "测试聊天", 520, 360, 14, 14, False, bridge)
    pet = PetWindow(
        "粽子", chat, bridge, lambda: None, lambda: None, True, 0, 0,
        desktop_config=DesktopShellConfig(),
    )
    try:
        assert pet.is_hover_bar_visible() is False
        pet._show_hover_bar()
        assert pet.is_hover_bar_visible() is True
        pet._hide_hover_bar()
        assert pet.is_hover_bar_visible() is False

        cfg = DesktopShellConfig(pet_scale=1.5, pet_opacity=0.8, always_on_top=False)
        pet.apply_desktop_config(cfg)
        assert pet._mascot.width() == round(190 * 1.5)
        assert pytest.approx(pet.windowOpacity(), abs=0.05) == 0.8
        assert pet._topmost_button.isChecked() is False
    finally:
        bridge.close()
        pet.cleanup()
        chat.deleteLater()
        pet.deleteLater()
        _flush_qt_events()


def test_chat_window_cards_clear_and_mute_toggle():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from ui.bridge import BotUiBridge
    from ui.chat_window import ChatWindow

    QApplication.instance() or QApplication([])
    bridge = BotUiBridge()
    chat = ChatWindow("粽子", "测试聊天", 520, 360, 14, 14, False, bridge)
    try:
        chat._append_user_message("主人测试")
        chat._append_bot_message("喵呜收到")
        chat._append_error_message("测试警告")
        plain = chat._transcript.toPlainText()
        assert "主人测试" in plain
        assert "喵呜收到" in plain
        assert "测试警告" in plain

        chat._skip_tts_checkbox.setChecked(True)
        assert chat._skip_tts is True

        chat.clear_messages()
        assert chat._transcript.toPlainText().strip() == ""
    finally:
        bridge.close()
        chat.deleteLater()
        _flush_qt_events()


def test_settings_dialog_loads_six_pages_and_detects_restart():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from config import BotConfig
    from services.config import DesktopShellConfig
    from ui.config import ChatWindowConfig
    from ui.settings import DesktopSettingsDialog

    QApplication.instance() or QApplication([])

    # 1. 双参数构造兼容性与位置重置按钮
    legacy_dialog = DesktopSettingsDialog(DesktopShellConfig(), ChatWindowConfig())
    try:
        assert legacy_dialog._nav.count() == 6
        assert legacy_dialog.reset_position_requested is False
        legacy_dialog._request_default_position()
        assert legacy_dialog.reset_position_requested is True
        assert "pet_x" not in legacy_dialog.values()["desktop_shell"]
    finally:
        legacy_dialog.deleteLater()
        _flush_qt_events()

    # 2. 全量 BotConfig 编辑导出与热更新/需重启区分
    base_cfg = BotConfig()
    dialog = DesktopSettingsDialog(base_cfg)
    try:
        updates = dialog.bot_config_updates()
        assert set(updates.keys()) == {"pipeline", "service", "character", "system", "memory"}
        assert dialog.detect_restart_reasons(base_cfg) == []

        # 仅修改即时热生效项：不应触发重启提醒
        dialog._character_page._bot_name.setText("新猫娘")
        dialog._memory_page._top_k.setValue(8)
        assert dialog.detect_restart_reasons(base_cfg) == []

        # 修改需重启项：应准确返回对应原因
        dialog._appearance_page._shell_enabled.setChecked(False)
        dialog._audio_page._mic_hotkey.setText("f9")
        reasons = dialog.detect_restart_reasons(base_cfg)
        assert "桌宠桌面外壳启停" in reasons
        assert "麦克风切换热键" in reasons
    finally:
        dialog.deleteLater()
        _flush_qt_events()


# --------------------------------------------------------------------------- 位置持久化与弹窗层级


def test_desktop_shell_has_position_helpers():
    from ui.desktop_shell import DesktopShell

    for name in (
        "_restore_pet_position",
        "_position_visible",
        "_save_pet_position",
        "_default_pet_position",
        "_move_pet_to_default_position",
    ):
        assert callable(getattr(DesktopShell, name, None)), f"缺少 {name}"


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

    try:
        area = screen.availableGeometry()
        assert shell._position_visible(area.left() + 10, area.top() + 10) is True
        assert shell._position_visible(-50_000, -50_000) is False
        assert shell._position_visible(area.right() + 5_000, area.bottom() + 5_000) is False
    finally:
        pet.deleteLater()
        _flush_qt_events()


def test_show_popup_applies_modality_before_topmost():
    """弹窗必须先设模态、再设置顶，防止置顶桌宠遮住弹窗。"""
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog, QWidget

    from ui.desktop_shell import DesktopShell

    QApplication.instance() or QApplication([])
    shell = DesktopShell.__new__(DesktopShell)

    pet_top = QWidget()
    pet_top.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
    shell._pet_window = pet_top

    captured = {}

    class _FakeDialog(QDialog):
        def exec(self):  # noqa: D401
            captured["modality"] = self.windowModality()
            captured["topmost"] = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            return QDialog.DialogCode.Accepted

    dlg = _FakeDialog()
    try:
        assert shell.show_popup(dlg) == QDialog.DialogCode.Accepted
        assert captured["modality"] == Qt.WindowModality.ApplicationModal
        assert captured["topmost"] is True

        pet_top.setWindowFlags(Qt.WindowType.Tool)
        shell.show_popup(dlg)
        assert captured["topmost"] is False
    finally:
        dlg.deleteLater()
        pet_top.deleteLater()
        _flush_qt_events()


# --------------------------------------------------------------------------- 字体


def test_ui_font_specifies_cjk_families():
    pytest.importorskip("PySide6")
    from ui.desktop_shell import _ui_font

    font = _ui_font(14)
    families = font.families()
    assert families[0] == "Microsoft YaHei UI"
    assert "Microsoft YaHei" in families
    assert font.pointSize() == 14


# --------------------------------------------------------------------------- 情绪白名单与剥离


class _StubConfig:
    def __init__(self, emotions):
        self.character = type("Character", (), {"emotions": emotions})()


@pytest.fixture
def stub_bot():
    from bot_turn_orchestrator import BotTurnOrchestrator

    def _make(emotions):
        orchestrator = BotTurnOrchestrator.__new__(BotTurnOrchestrator)
        cfg = _StubConfig(emotions)
        orchestrator._config_provider = lambda: cfg
        return orchestrator

    return _make


def test_allowed_emotions_come_from_character_config(stub_bot):
    bot = stub_bot(["happy", "sad"])
    tags = bot._allowed_emotions()
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
    assert bot._resolve_emotion_tag("angry") is None
    assert bot._resolve_emotion_tag("") is None
    assert bot._resolve_emotion_tag(None) is None


def test_emotion_instruction_uses_config_whitelist():
    source = (_BOT_DIR / "bot_turn_orchestrator.py").read_text(encoding="utf-8")
    assert 'getattr(self.tts_prompt_manager, "sentiments", []) or []' not in source
    assert "emotion_tags = self._allowed_emotions()" in source


def test_default_emotion_constant_is_shared():
    from services.emotion_prefix import DEFAULT_EMOTION

    assert DEFAULT_EMOTION == "Default"


def test_emotion_tag_is_stripped_on_tool_round_too(stub_bot):
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
    bot = stub_bot(["Default", "happy"])
    text, emotion = bot._strip_response_emotion("<emotion>angry</emotion>哼", has_pending_tool=True)
    assert text == "哼"
    assert emotion is None


def test_tag_only_tool_round_keeps_a_neutral_acknowledgement(stub_bot):
    bot = stub_bot(["Default", "happy"])
    text, emotion = bot._strip_response_emotion("<emotion>happy</emotion>", has_pending_tool=True)
    assert text == "稍等，我处理一下。"
    assert emotion == "happy"


def test_plain_response_passes_through_untouched(stub_bot):
    bot = stub_bot(["Default", "happy"])
    assert bot._strip_response_emotion("普通回复", has_pending_tool=True) == ("普通回复", None)


def test_emotion_stripping_is_not_gated_by_tool_state():
    source = (_BOT_DIR / "bot_turn_orchestrator.py").read_text(encoding="utf-8")
    assert "not direct_return and not has_pending_tool" not in source
    assert "self._strip_response_emotion(" in source


def test_emotion_prefix_tolerates_leading_whitespace():
    from services.emotion_prefix import consume_emotion_prefix

    assert consume_emotion_prefix("\n<emotion>happy</emotion>嗨", flush=True) == (True, "嗨", "happy")
    assert consume_emotion_prefix(" \n<emo") == (False, "", None)
    assert consume_emotion_prefix(" 你好", flush=True) == (True, " 你好", None)


def test_tts_event_carries_ui_emotion_instead_of_prompt_sentiment():
    """只有 [Default] 参考音频时，TTS 提示的情绪恒为 Default，
    界面表情必须用模型给的情绪，不能被它顶掉。"""
    import types

    from bot_tts_scheduler import BotTTSScheduler
    from common.enumerator import Language
    from condata.pipeline.tts import TTSPrediction

    class _TtsSettings:
        cancel_on_new_input = False

    class _Pipeline:
        tts = _TtsSettings()

    class _Config:
        character = type("Character", (), {"emotions": ["Default", "happy"]})()
        pipeline = _Pipeline()

    class _Tts:
        def predict(self, query):
            return TTSPrediction(wave_data=b"", audio_type="wav")

    scheduler = BotTTSScheduler(
        config_provider=lambda: _Config(),
        tts_provider=lambda: _Tts(),
        tts_prompt_manager_provider=lambda: None,
        speaker_provider=lambda: None,
        mic_provider=lambda: None,
        lang_provider=lambda: Language.CHINESE,
    )
    captured = {}
    scheduler.play = lambda event: captured.update(emotion=event.emotion, transcript=event.transcript)

    prompt = types.SimpleNamespace(
        audio_path="a.wav", prompt_text="p", lang="zh", sentiment="Default"
    )
    try:
        scheduler.submit_segment(prompt, "回来啦", 1.0, "happy")
        scheduler.tts_thread_pool.shutdown(wait=True)
        assert captured["emotion"] == "happy"

        from concurrent.futures import ThreadPoolExecutor

        captured.clear()
        scheduler.tts_thread_pool = ThreadPoolExecutor(max_workers=1)
        scheduler.submit_segment(prompt, "嗯", 1.0)
        scheduler.tts_thread_pool.shutdown(wait=True)
        assert captured["emotion"] == "Default"
    finally:
        scheduler.tts_thread_pool.shutdown(wait=True)


# --------------------------------------------------------------------------- LLM / MLLM 二选一启动兼容


def test_agent_api_and_context_allow_mllm_only_startup(monkeypatch):
    """当 pipeline.llm.enable=False 且 pipeline.mllm.enable=True 时，导入与初始化均不得报错。"""
    import importlib

    import agent.api as agent_api
    import framework.context as fw_context
    from config import BotConfig
    from pipeline.base.base_sync import PipelineDisabledException
    from pipeline.mllm.mllm_sync import MLLMSyncPipeline

    mllm_only_cfg = BotConfig()
    mllm_only_cfg.pipeline.llm.enable = False
    mllm_only_cfg.pipeline.mllm.enable = True

    # 1. agent.api 顶层 import 阶段必须惰性，不触发 PipelineDisabledException
    none_cfg = BotConfig()
    none_cfg.pipeline.llm.enable = False
    none_cfg.pipeline.mllm.enable = False
    monkeypatch.setattr("manager.config_manager.get_config", lambda: none_cfg)
    importlib.reload(agent_api)  # 即使两者都关闭，仅 import 也不应抛异常

    with pytest.raises(PipelineDisabledException):
        agent_api._get_model()

    # 2. 仅启用 MLLM 时，_get_model() 应自动回退使用 MLLMSyncPipeline
    monkeypatch.setattr(agent_api, "get_config", lambda: mllm_only_cfg)
    monkeypatch.setattr(agent_api, "_model", None)
    monkeypatch.setattr(agent_api, "_model_signature", None)
    adapted = agent_api._get_model()
    assert isinstance(adapted._pipeline, MLLMSyncPipeline)

    # 3. BotContext 在仅启用 MLLM 时允许初始化，两者都关时才断言失败
    monkeypatch.setattr(fw_context, "get_config", lambda: none_cfg)
    with pytest.raises(AssertionError):
        fw_context.BotContext()

    mllm_only_cfg.pipeline.asr.enable = False
    mllm_only_cfg.pipeline.tts.enable = False
    mllm_only_cfg.pipeline.ocr.enable = False
    mllm_only_cfg.pipeline.img_cap.enable = False
    mllm_only_cfg.pipeline.vlm.enable = False
    mllm_only_cfg.service.microphone.enable = False
    monkeypatch.setattr(fw_context, "get_config", lambda: mllm_only_cfg)
    monkeypatch.setattr(fw_context.devices.headless, "is_headless", lambda: True)
    monkeypatch.setattr(fw_context, "is_headless", lambda: True)
    monkeypatch.setattr(fw_context, "SmartMicrophone", lambda **kwargs: object())
    monkeypatch.setattr(fw_context, "Speaker", lambda: object())
    ctx = fw_context.BotContext()
    assert ctx.llm is None


# --------------------------------------------------------------------------- 业务联动与边缘防呆


def test_bot_runner_mic_sync_and_disabled_guard():
    pytest.importorskip("PySide6")
    import threading
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication

    from event.event_data import CharacterStateEvent
    from ui.bridge import BotRunner, BotUiBridge

    QApplication.instance() or QApplication([])

    calls = []

    class _FakeMic:
        def __init__(self):
            self._open = False

        def is_set_talk_enabled_event(self):
            return self._open

        def set_talk_enabled_event(self):
            self._open = True
            calls.append(("set_talk", True))

        def unset_talk_enabled_event(self):
            self._open = False
            calls.append(("unset_talk", False))

        def force_commit(self, is_emit=False):
            calls.append(("force_commit", is_emit))

    class _FakeASR:
        def reset_accumulator(self):
            calls.append(("reset_acc",))

    mic_cfg = SimpleNamespace(enable=True)
    fake_bot = SimpleNamespace(
        mic=_FakeMic(),
        asr_controller=_FakeASR(),
        keyboard=SimpleNamespace(microphone_state_lock=threading.Lock()),
        get_runtime_config=lambda: SimpleNamespace(service=SimpleNamespace(microphone=mic_cfg)),
    )

    runner = BotRunner()
    runner._bot = fake_bot

    assert runner.is_microphone_recording() is False
    assert runner.set_microphone_enabled(True) is True
    assert runner.is_microphone_recording() is True
    assert ("reset_acc",) in calls and ("force_commit", False) in calls and ("set_talk", True) in calls

    calls.clear()
    assert runner.set_microphone_enabled(False) is False
    assert runner.is_microphone_recording() is False
    assert ("unset_talk", False) in calls and ("reset_acc",) in calls and ("force_commit", True) in calls

    # 禁用麦克风配置时尝试开麦必须被拦截
    mic_cfg.enable = False
    assert runner.set_microphone_enabled(True) is False
    assert runner.is_microphone_recording() is False

    # 验证 bridge 随 CharacterStateEvent 通过 provider 同步 F8 状态
    bridge = BotUiBridge()
    emitted = []
    bridge.mic_toggled.connect(lambda val: emitted.append(val))
    mic_cfg.enable = True
    runner.set_microphone_enabled(True)
    bridge.set_mic_state_provider(runner.is_microphone_recording)
    try:
        bridge._on_character_state(CharacterStateEvent(state="listening"))
        assert emitted == [True]
    finally:
        bridge.close()
        _flush_qt_events()


def test_chat_window_mute_syncs_runtime_config():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    import manager.config_manager as cfg_mgr
    from config import BotConfig
    from ui.bridge import BotUiBridge
    from ui.chat_window import ChatWindow

    QApplication.instance() or QApplication([])
    prev_cfg = cfg_mgr._config
    test_cfg = BotConfig()
    cfg_mgr._config = test_cfg
    bridge = BotUiBridge()
    chat = ChatWindow("粽子", "测试", 520, 360, 14, 14, False, bridge)
    try:
        chat._skip_tts_checkbox.setChecked(True)
        assert chat._skip_tts is True
        assert test_cfg.service.chat_window.skip_tts is True
        chat._skip_tts_checkbox.setChecked(False)
        assert test_cfg.service.chat_window.skip_tts is False
    finally:
        cfg_mgr._config = prev_cfg
        bridge.close()
        chat.deleteLater()
        _flush_qt_events()


def test_settings_dialog_marks_microphone_enable_as_restart_required():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from config import BotConfig
    from ui.settings import DesktopSettingsDialog

    QApplication.instance() or QApplication([])
    base_cfg = BotConfig()
    dialog = DesktopSettingsDialog(base_cfg)
    try:
        dialog._audio_page._mic_enable.setChecked(not base_cfg.service.microphone.enable)
        reasons = dialog.detect_restart_reasons(base_cfg)
        assert "麦克风总开关" in reasons
    finally:
        dialog.deleteLater()
        _flush_qt_events()


def test_pet_window_clamps_to_screen_after_scale_up():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from services.config import DesktopShellConfig
    from ui.bridge import BotUiBridge
    from ui.chat_window import ChatWindow
    from ui.pet_window import PetWindow

    app = QApplication.instance() or QApplication([])
    screen = app.primaryScreen()
    if screen is None:
        pytest.skip("无可用屏幕")

    bridge = BotUiBridge()
    chat = ChatWindow("粽子", "测试", 520, 360, 14, 14, False, bridge)
    pet = PetWindow(
        "粽子", chat, bridge, lambda: None, lambda: None, True, 0, 0,
        desktop_config=DesktopShellConfig(),
    )
    try:
        area = screen.availableGeometry()
        pet.move(area.right() - 10, area.bottom() - 10)
        pet._clamp_to_screen()
        assert pet.x() + pet.width() - 1 <= area.right()
        assert pet.y() + pet.height() - 1 <= area.bottom()
    finally:
        bridge.close()
        pet.cleanup()
        chat.deleteLater()
        pet.deleteLater()
        _flush_qt_events()


def test_avatar_presets_mapping_and_resolve():
    from ui.mascot import preset_key_for, resolve_preset

    assert preset_key_for("vector", "") == "vector"
    assert preset_key_for("sprite", "resources/mascot/gemini_q") == "sprite:gemini_q"
    assert preset_key_for("sprite", r"Bot\resources\mascot\deepseek_winter\\") == "sprite:deepseek_winter"
    assert preset_key_for("sprite", "D:/custom/skin") == "sprite"
    assert preset_key_for("live2d", "") == "live2d"

    assert resolve_preset("sprite:gemini_q", "D:/custom/skin") == ("sprite", "resources/mascot/gemini_q")
    assert resolve_preset("sprite", "resources/mascot/gemini_q") == ("sprite", "")
    assert resolve_preset("sprite", "D:/custom/skin") == ("sprite", "D:/custom/skin")


def test_builtin_sprite_packs_load_and_render_emotions_and_states():
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from ui.mascot import MascotCanvas, render_avatar_thumbnail

    QApplication.instance() or QApplication([])
    canvas = MascotCanvas()
    try:
        for pack_rel in ("resources/mascot/gemini_q", "resources/mascot/deepseek_winter"):
            canvas.set_avatar_config("sprite", pack_rel, 1.0)
            assert canvas.active_mode == "sprite"
            assert canvas.fallback_message == ""

            for state in ("待命中", "思考中", "聆听中", "遇到问题"):
                canvas.set_state(state)
                for emo in ("Default", "happy", "sad", "angry", "shy", "surprised"):
                    canvas.set_emotion(emo)
                    img = QImage(canvas.size(), QImage.Format.Format_ARGB32)
                    img.fill(0)
                    canvas.render(img)
                    assert img.pixelColor(img.width() // 2, img.height() // 2).alpha() > 0

            thumb = render_avatar_thumbnail("sprite", pack_rel, 40, circular=True)
            assert not thumb.isNull()
            assert thumb.width() == 40 and thumb.height() == 40
    finally:
        canvas.cleanup()
        canvas.deleteLater()
        _flush_qt_events()


def test_speech_bubble_paints_non_transparent_card():
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from ui.pet_window import SpeechBubble

    QApplication.instance() or QApplication([])
    bubble = SpeechBubble()
    try:
        bubble._message.setText("这是一条桌面回复气泡测试文本。")
        for side in ("right", "left"):
            bubble._apply_tail_side(side)
            bubble.adjustSize()
            img = QImage(bubble.size(), QImage.Format.Format_ARGB32)
            img.fill(0)
            bubble.render(img)
            assert img.pixelColor(img.width() // 2, img.height() // 2).alpha() > 200
    finally:
        bubble._hide_timer.stop()
        bubble.deleteLater()
        _flush_qt_events()


def test_chat_window_bubble_cards_avatar_and_toggle_switch():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from ui.bridge import BotUiBridge
    from ui.chat_window import ChatWindow, ToggleSwitch

    QApplication.instance() or QApplication([])
    bridge = BotUiBridge()
    chat = ChatWindow("粽子", "测试", 540, 420, 12, 12, False, bridge)
    try:
        assert isinstance(chat._skip_tts_checkbox, ToggleSwitch)
        assert chat._skip_tts_checkbox.sizeHint().width() >= 32
        chat._skip_tts_checkbox.click()
        assert chat._skip_tts is True

        chat.set_avatar("sprite", "resources/mascot/deepseek_winter")
        assert chat._avatar_label.pixmap() is not None
        assert not chat._avatar_label.pixmap().isNull()

        chat.set_bot_name("小粽")
        assert chat._bot_title_label.text() == "小粽"

        chat._on_emotion_changed("happy")
        chat._append_user_message("第一条消息")
        chat._append_bot_message("收到啦喵")
        chat._append_error_message("提示一条")
        plain = chat._transcript.toPlainText()
        assert "第一条消息" in plain
        assert "收到啦喵" in plain
        assert "happy" in plain
        assert "提示一条" in plain

        chat.clear_messages()
        assert chat._transcript.toPlainText().strip() == ""
    finally:
        bridge.close()
        chat.deleteLater()
        _flush_qt_events()


def test_appearance_page_preset_cards_sync_and_cleanup():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from services.config import DesktopShellConfig
    from ui.config import ChatWindowConfig
    from ui.settings.pages.appearance_page import AppearancePage

    QApplication.instance() or QApplication([])
    page = AppearancePage(DesktopShellConfig(), ChatWindowConfig())
    try:
        assert len(page._preset_cards) == 3
        assert page._preset_cards["vector"].isChecked() is True
        assert page._preset_cards["sprite:gemini_q"].isChecked() is False

        page._preset_cards["sprite:gemini_q"].click()
        vals = page.values()["desktop_shell"]
        assert vals["avatar_mode"] == "sprite"
        assert vals["avatar_path"] == "resources/mascot/gemini_q"
        assert page._preset_cards["vector"].isChecked() is False
        assert page._preset_cards["sprite:gemini_q"].isChecked() is True

        idx = page._avatar_preset.findData("sprite")
        assert idx >= 0
        page._avatar_preset.setCurrentIndex(idx)
        assert all(not card.isChecked() for card in page._preset_cards.values())
        assert page._avatar_path.isEnabled() is True
    finally:
        page.cleanup()
        page.deleteLater()
        _flush_qt_events()
