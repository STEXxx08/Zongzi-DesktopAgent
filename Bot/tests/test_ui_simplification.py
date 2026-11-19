"""UI 层精简与公共方法离线回归测试。"""

import subprocess
import sys
from enum import Enum
from pathlib import Path

import pytest

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))


def _flush_qt_events() -> None:
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass


def test_settings_widgets_factories_and_enum_unwrap():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    from ui.settings.widgets import (
        SettingsCard,
        build_settings_page,
        combo_value,
        make_checkbox,
        make_combo,
        make_line_edit,
    )

    class _Mode(str, Enum):
        FAST = "fast"
        SAFE = "safe"

    QApplication.instance() or QApplication([])
    cb = make_checkbox("启用选项", True, tooltip="提示文本")
    assert cb.isChecked() is True
    assert cb.toolTip() == "提示文本"

    edit = make_line_edit("abc", placeholder="请输入")
    assert edit.text() == "abc"
    assert edit.placeholderText() == "请输入"

    combo = make_combo([("快速", "fast"), ("安全", "safe")], _Mode.SAFE)
    assert combo_value(combo) == "safe"

    page = QWidget()
    card = SettingsCard("分组", "说明", page)
    layout = build_settings_page(page, [card])
    try:
        assert page.objectName() == "SettingsPageBody"
        assert isinstance(layout, QVBoxLayout)
        assert layout.count() == 2  # card + trailing stretch
    finally:
        page.deleteLater()
        cb.deleteLater()
        edit.deleteLater()
        combo.deleteLater()
        _flush_qt_events()


def test_theme_create_styled_menu_and_qss_intact():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMenu, QWidget

    from ui.theme import (
        CHAT_WINDOW_QSS,
        CONTEXT_MENU_QSS,
        MESSAGE_BOX_QSS,
        PALETTE,
        SETTINGS_DIALOG_QSS,
        create_styled_menu,
        pet_status_pill_qss,
    )

    QApplication.instance() or QApplication([])
    parent = QWidget()
    menu = create_styled_menu(parent)
    try:
        assert isinstance(menu, QMenu)
        assert menu.parent() is parent
        assert menu.styleSheet() == CONTEXT_MENU_QSS
        assert PALETTE["accent_purple_hover"] in MESSAGE_BOX_QSS
        assert PALETTE["accent_purple_hover"] in SETTINGS_DIALOG_QSS
        assert "#957bb8" in CHAT_WINDOW_QSS
        assert PALETTE["text_primary"] in pet_status_pill_qss("待命中")
    finally:
        parent.deleteLater()
        _flush_qt_events()


def test_mascot_and_desktop_shell_lazy_imports_in_subprocess():
    """验证导入 ui.mascot 与 ui.desktop_shell 时不会急切加载 Live2D/SpritePack/设置弹窗模块。"""
    code = (
        "import sys\n"
        "import ui.mascot as m\n"
        "assert 'ui.mascot.live2d_view' not in sys.modules\n"
        "assert 'ui.mascot.sprite_pack' not in sys.modules\n"
        "assert 'ui.mascot.vector_catgirl' not in sys.modules\n"
        "import ui.desktop_shell as ds\n"
        "assert 'ui.settings.dialog' not in sys.modules\n"
        "assert ds.DesktopSettingsDialog.__name__ == 'DesktopSettingsDialog'\n"
        "assert 'ui.settings.dialog' in sys.modules\n"
        "assert m.Live2DRenderer.__name__ == 'Live2DRenderer'\n"
        "assert 'ui.mascot.live2d_view' in sys.modules\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_BOT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"


def test_mascot_canvas_defer_and_shared_badge_paint():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from ui.mascot.base import (
        MascotCanvas,
        draw_ground_shadow,
        draw_state_badge,
        draw_thinking_halo,
    )

    QApplication.instance() or QApplication([])
    deferred = MascotCanvas(avatar_mode=None, scale=0.9)
    try:
        assert deferred._renderer is None
        assert deferred.width() == round(MascotCanvas.BASE_WIDTH * 0.9)
        deferred.set_avatar_config("vector", "", 0.9)
        assert deferred.active_mode == "vector"
    finally:
        deferred.cleanup()
        deferred.deleteLater()
        _flush_qt_events()

    img = QImage(190, 178, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        draw_ground_shadow(painter, QRectF(30, 164, 130, 14), center=QPointF(95, 172))
        draw_thinking_halo(painter, QPointF(95, 92), 86.0, frame=4)
        for st in ("思考中", "聆听中", "遇到问题"):
            draw_state_badge(painter, QRectF(0, 0, 190, 178), frame=3, state=st)
    finally:
        painter.end()
    assert img.pixelColor(95, 170).alpha() > 0


def test_chat_window_accepts_initial_avatar_without_extra_render(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    import ui.chat_window as cw_mod
    from ui.bridge import BotUiBridge
    from ui.chat_window import ChatWindow

    QApplication.instance() or QApplication([])
    calls = []

    def _fake_thumb(mode, path="", size=40, *, circular=True):
        calls.append((mode, path, size, circular))
        return QPixmap(size, size)

    monkeypatch.setattr(cw_mod, "render_avatar_thumbnail", _fake_thumb)
    bridge = BotUiBridge()
    chat = ChatWindow(
        "粽子",
        "测试",
        520,
        360,
        14,
        14,
        False,
        bridge,
        avatar_mode="sprite",
        avatar_path="resources/mascot/gemini_q",
    )
    try:
        assert calls == [
            ("sprite", "resources/mascot/gemini_q", 36, True),
            ("sprite", "resources/mascot/gemini_q", 28, True),
        ]
    finally:
        bridge.close()
        chat.deleteLater()
        _flush_qt_events()


def test_pet_window_skips_redundant_topmost_and_state_stylesheet(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from services.config import DesktopShellConfig
    from ui.bridge import BotUiBridge
    from ui.chat_window import ChatWindow
    from ui.pet_window import PetWindow

    QApplication.instance() or QApplication([])
    bridge = BotUiBridge()
    cfg = DesktopShellConfig(always_on_top=True)
    chat = ChatWindow("粽子", "测试", 520, 360, 14, 14, False, bridge)
    pet = PetWindow(
        "粽子",
        chat,
        bridge,
        lambda: None,
        lambda: None,
        True,
        0,
        0,
        desktop_config=cfg,
    )
    try:
        flag_calls = []
        orig_set_flags = pet.setWindowFlags
        monkeypatch.setattr(pet, "setWindowFlags", lambda f: (flag_calls.append(f), orig_set_flags(f)))
        pet.set_always_on_top(True)
        assert flag_calls == []
        pet.set_always_on_top(False)
        assert len(flag_calls) == 1

        qss_calls = []
        orig_set_qss = pet._status.setStyleSheet
        monkeypatch.setattr(pet._status, "setStyleSheet", lambda s: (qss_calls.append(s), orig_set_qss(s)))
        pet.set_state("聆听中")
        pet.set_state("聆听中")
        assert len(qss_calls) == 1

        pet.set_state("已回复")
        assert len(qss_calls) == 2
        assert pet._state_timer.isActive() is True
    finally:
        bridge.close()
        pet.cleanup()
        chat.deleteLater()
        pet.deleteLater()
        _flush_qt_events()


def test_appearance_page_does_not_double_install_renderer(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from services.config import DesktopShellConfig
    from ui.config import ChatWindowConfig
    from ui.mascot.base import MascotCanvas
    from ui.settings.pages.appearance_page import AppearancePage

    QApplication.instance() or QApplication([])
    install_calls = []
    orig_install = MascotCanvas._install_renderer

    def _spy_install(self, mode, path):
        install_calls.append((mode, path))
        return orig_install(self, mode, path)

    monkeypatch.setattr(MascotCanvas, "_install_renderer", _spy_install)
    page = AppearancePage(DesktopShellConfig(avatar_mode="sprite", avatar_path="resources/mascot/gemini_q"), ChatWindowConfig())
    try:
        assert install_calls == [("sprite", "resources/mascot/gemini_q")]
    finally:
        page.cleanup()
        page.deleteLater()
        _flush_qt_events()
