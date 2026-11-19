"""Zongzi-DesktopAgent 前端统一主题色板、字体链与 QSS 样式表。

视觉方向：奶油米白底 + 粽叶绿 / 暮光紫双主色，大圆角卡片、轻描边、柔和层次，
与 Q 版猫娘形象的“软萌、干净”画风保持一致。
"""

import html
from pathlib import Path

from PySide6.QtCore import QDir
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QMenu

_UI_RES_DIR = Path(__file__).resolve().parents[1] / "resources" / "ui"
QDir.addSearchPath("zongzi_ui", str(_UI_RES_DIR))


# 中文字体族优先级：显式指定，避免依赖系统"碰巧"回退到某个中文字体。
_CJK_FONT_FAMILIES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "思源黑体",
    "Source Han Sans SC",
    "Noto Sans CJK SC",
    "SimHei",
    "Segoe UI",
    "Segoe UI Emoji",
    "Sans Serif",
)


def _ui_font(size: int, *, bold: bool = False) -> QFont:
    """按 CJK 优先的字体链创建界面字体。

    只写 "Sans Serif" 时，部分 Windows 环境下中文会落到字形不全的字体上，
    因此这里显式给出候选字族，让 Qt 逐级回退。
    """
    font = QFont()
    font.setFamilies(list(_CJK_FONT_FAMILIES))
    font.setPointSize(int(size))
    if bold:
        font.setWeight(QFont.Weight.DemiBold)
    return font


# "粽子"猫娘主题色板
PALETTE = {
    "bg_cream": "#fbf8f3",
    "bg_window": "#f7f3ec",
    "bg_card": "#ffffff",
    "bg_subtle": "#f3eee6",
    "bg_hover": "#ece5da",
    "border_soft": "#e8e0d4",
    "border_strong": "#dcd2c4",
    "border_focus": "#8d76b2",
    "text_primary": "#3f3548",
    "text_secondary": "#6f6479",
    "text_muted": "#9a8ea6",
    "accent_purple": "#876cab",
    "accent_purple_hover": "#745899",
    "accent_purple_soft": "#f1ebf8",
    "accent_bamboo": "#4d8f70",
    "accent_bamboo_hover": "#3f7a5e",
    "accent_bamboo_soft": "#e6f2ec",
    "accent_amber": "#d97736",
    "accent_amber_soft": "#fff1e4",
    "accent_danger": "#c8525d",
    "accent_danger_soft": "#fdecef",
}

# 状态 -> 指示色
STATE_COLORS = {
    "待命中": "#4d8f70",
    "已就绪": "#4d8f70",
    "准备启动…": "#c49a45",
    "启动中": "#c49a45",
    "聆听中": "#3b82c4",
    "思考中": "#876cab",
    "正在说话": "#d9738c",
    "已回复": "#4d8f70",
    "遇到问题": "#c8525d",
    "麦克风未启用": "#c8525d",
    "聊天窗口已关闭": "#9a8ea6",
}


def state_color(state: str) -> str:
    return STATE_COLORS.get(state, "#876cab")


def pet_status_pill_qss(state: str = "待命中") -> str:
    """生成桌宠底部轻量化状态胶囊的样式。"""
    border = state_color(state)
    return (
        "QLabel {"
        f"  color: {PALETTE['text_primary']};"
        "  background: rgba(255, 253, 249, 236);"
        f"  border: 1px solid {border};"
        "  border-radius: 11px;"
        "  padding: 2px 11px 3px 9px;"
        "  font-weight: 600;"
        "}"
    )


def pet_status_html(state: str) -> str:
    """状态胶囊富文本：彩色圆点 + 状态文字。"""
    safe_state = html.escape(state or "")
    return f'<span style="color:{state_color(state)};">●</span>&nbsp;{safe_state}'


HOVER_BAR_QSS = """
QFrame#PetHoverBar {
    background: rgba(255, 252, 247, 242);
    border: 1px solid #e3d9cb;
    border-radius: 17px;
}
QPushButton#PetActionBtn {
    color: #453a4f;
    background: transparent;
    border: none;
    border-radius: 13px;
    min-width: 30px;
    padding: 3px 6px;
    font-size: 13px;
}
QPushButton#PetActionBtn:hover {
    background: #f1ebf8;
}
QPushButton#PetActionBtn:pressed {
    background: #e4daf1;
}
QPushButton#PetActionBtn:checked {
    background: #e6f2ec;
}
QPushButton#PetActionBtn:disabled {
    color: #b4a9be;
}
"""


SPEECH_BUBBLE_QSS = """
QLabel#SpeechBubbleLabel {
    color: #3f3548;
    background: transparent;
    padding: 10px 14px;
}
"""


CONTEXT_MENU_QSS = """
QMenu {
    background: #fffdf9;
    border: 1px solid #e3d9cb;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    color: #3f3548;
    padding: 6px 22px 6px 12px;
    border-radius: 7px;
    margin: 1px 0;
}
QMenu::item:selected {
    background: #f1ebf8;
    color: #5f468a;
}
QMenu::item:disabled {
    color: #b4a9be;
}
QMenu::separator {
    height: 1px;
    background: #ede6db;
    margin: 5px 8px;
}
QMenu::indicator {
    width: 14px;
    height: 14px;
    margin-left: 4px;
}
QMenu::indicator:checked {
    image: none;
    background: #876cab;
    border: 3px solid #fffdf9;
    border-radius: 7px;
}
QMenu::right-arrow {
    width: 8px;
    height: 8px;
}
"""


def create_styled_menu(parent=None, *, font_size: int = 9) -> QMenu:
    """创建应用统一主题样式的上下文/托盘菜单。"""
    menu = QMenu(parent)
    menu.setStyleSheet(CONTEXT_MENU_QSS)
    menu.setFont(_ui_font(font_size))
    return menu


_SCROLLBAR_QSS = """
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 0;
}
QScrollBar::handle:vertical {
    background: #ddd3c6;
    border-radius: 4px;
    min-height: 32px;
}
QScrollBar::handle:vertical:hover {
    background: #c9bdae;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0 4px 2px 4px;
}
QScrollBar::handle:horizontal {
    background: #ddd3c6;
    border-radius: 4px;
    min-width: 32px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
    width: 0;
}
"""


_TOOLTIP_QSS = """
QToolTip {
    color: #3f3548;
    background: #fffdf9;
    border: 1px solid #e3d9cb;
    border-radius: 6px;
    padding: 4px 8px;
}
"""


_PRIMARY_BTN_GRADIENT = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #957bb8, stop:1 #7d62a3)"


CHAT_WINDOW_QSS = (
    """
QDialog#ChatWindow {
    background: #f7f3ec;
}
QFrame#ChatHeaderBar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffffff, stop:1 #f6f1fa);
    border: 1px solid #e8e0d4;
    border-radius: 14px;
}
QLabel#ChatAvatar {
    background: transparent;
}
QLabel#ChatBotTitle {
    color: #3f3548;
    font-weight: 700;
}
QLabel#ChatStateBadge {
    color: #5c5068;
    background: transparent;
    padding: 0;
}
QLabel#ChatEmotionBadge {
    color: #745899;
    background: #f1ebf8;
    border-radius: 8px;
    padding: 1px 8px;
}
QPushButton#ChatToolBtn {
    color: #5c5068;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 5px 9px;
}
QPushButton#ChatToolBtn:hover {
    background: #f1ebf8;
    border-color: #e4daf1;
}
QPushButton#ChatToolBtn:checked {
    color: #3a7559;
    background: #e6f2ec;
    border-color: #b9dcc8;
}
QScrollArea#ChatTranscript {
    background: #ffffff;
    border: 1px solid #e8e0d4;
    border-radius: 14px;
    padding: 4px;
}
QWidget#ChatTranscriptBody {
    background: #ffffff;
    border-radius: 13px;
}
QLabel#ChatEmptyHint {
    color: #b0a5ba;
}
QLabel#BubbleMeta {
    color: #9a8ea6;
}
QLabel#BubbleUser {
    color: #ffffff;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #957bb8, stop:1 #7d62a3);
    border-radius: 14px;
    border-top-right-radius: 4px;
    padding: 8px 12px;
}
QLabel#BubbleBot {
    color: #3f3548;
    background: #f6f9f4;
    border: 1px solid #dcebdf;
    border-radius: 14px;
    border-top-left-radius: 4px;
    padding: 8px 12px;
}
QLabel#BubbleSystem {
    color: #b33f4a;
    background: #fdecef;
    border: 1px solid #f3c7ce;
    border-radius: 10px;
    padding: 5px 12px;
}
QLabel#BubbleEmotion {
    color: #745899;
    background: #f1ebf8;
    border-radius: 6px;
    padding: 0 6px;
}
QFrame#ChatComposer {
    background: #ffffff;
    border: 1px solid #e3d9cb;
    border-radius: 16px;
}
QFrame#ChatComposer[focused="true"] {
    border: 1.5px solid #a690c7;
}
QLineEdit#ChatInput {
    background: transparent;
    color: #3f3548;
    border: none;
    padding: 6px 4px;
    selection-background-color: #d9cdea;
}
QLineEdit#ChatInput:disabled {
    color: #9a8ea6;
}
QCheckBox#ChatMuteBox {
    color: #6f6479;
}
QPushButton#ChatSendBtn {
    color: #ffffff;
    background: """
    + _PRIMARY_BTN_GRADIENT
    + """;
    border: none;
    border-radius: 13px;
    padding: 6px 16px;
    font-weight: 600;
}
QPushButton#ChatSendBtn:hover {
    background: #745899;
}
QPushButton#ChatSendBtn:pressed {
    background: #654b88;
}
QPushButton#ChatSendBtn:disabled {
    background: #d4cade;
}
"""
    + _SCROLLBAR_QSS
    + _TOOLTIP_QSS
)


SETTINGS_DIALOG_QSS = (
    """
QDialog#SettingsDialog {
    background: #f7f3ec;
    color: #3f3548;
}
QFrame#SettingsSidebar {
    background: #fffdf9;
    border: 1px solid #e8e0d4;
    border-radius: 14px;
}
QLabel#SettingsBrandTitle {
    color: #3f3548;
    font-weight: 700;
}
QLabel#SettingsBrandSub {
    color: #9a8ea6;
}
QListWidget#SettingsNavList {
    background: transparent;
    border: none;
    padding: 2px;
    outline: none;
}
QListWidget#SettingsNavList::item {
    color: #564a62;
    border-radius: 10px;
    padding: 9px 12px;
    margin: 2px 0;
}
QListWidget#SettingsNavList::item:hover {
    background: #f3eee6;
}
QListWidget#SettingsNavList::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #957bb8, stop:1 #8068a6);
    color: #ffffff;
    font-weight: 600;
}
QLabel#SettingsPageTitle {
    color: #3f3548;
    font-weight: 700;
}
QLabel#SettingsPageSub {
    color: #8a7e96;
}
QFrame#SettingsDivider {
    background: #ede6db;
    min-height: 1px;
    max-height: 1px;
    border: none;
}
QScrollArea#SettingsScrollArea {
    background: transparent;
    border: none;
}
QScrollArea#SettingsScrollArea > QWidget > QWidget {
    background: transparent;
}
QWidget#SettingsPageBody {
    background: transparent;
}
QFrame#SettingsCard {
    background: #ffffff;
    border: 1px solid #ebe3d7;
    border-radius: 14px;
}
QLabel#SettingsCardTitle {
    color: #3f3548;
    font-weight: 700;
    font-size: 13px;
    padding-left: 8px;
    border-left: 3px solid #876cab;
}
QLabel#SettingsCardDesc {
    color: #8a7e96;
    font-size: 11px;
}
QFrame#AvatarPreviewPanel {
    background: qradialgradient(cx:0.5, cy:0.45, radius:0.75, stop:0 #ffffff, stop:1 #f4eff8);
    border: 1px dashed #dcd0ea;
    border-radius: 14px;
}
QPushButton#EmotionChip {
    color: #6f6479;
    background: #ffffff;
    border: 1px solid #e3d9cb;
    border-radius: 11px;
    padding: 3px 10px;
}
QPushButton#EmotionChip:hover {
    border-color: #c7b6df;
    color: #5f468a;
}
QPushButton#EmotionChip:checked {
    color: #ffffff;
    background: #876cab;
    border-color: #876cab;
}
QPushButton#PresetCard {
    background: #ffffff;
    color: #3f3548;
    border: 1px solid #e5dcd0;
    border-radius: 12px;
    padding: 8px 6px;
    font-weight: 500;
}
QPushButton#PresetCard:hover {
    background: #faf6fe;
    border-color: #cab8e2;
}
QPushButton#PresetCard:checked {
    background: #f6f1fc;
    border: 2px solid #876cab;
    color: #5f468a;
    font-weight: 600;
}
QPushButton#PresetCard QLabel {
    color: #3f3548;
}
QPushButton#PresetCard:checked QLabel {
    color: #5f468a;
    font-weight: 600;
}
QLabel#RestartBadge {
    color: #b45309;
    background: #fff1e4;
    border: 1px solid #f5cba7;
    border-radius: 6px;
    padding: 1px 5px;
    font-size: 10px;
    font-weight: 600;
}
QLabel#SettingsFooterHint {
    color: #8a7e96;
}
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #fdfcfa;
    color: #3f3548;
    border: 1px solid #ddd3c6;
    border-radius: 8px;
    padding: 5px 9px;
    selection-background-color: #d9cdea;
    selection-color: #3f3548;
}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {
    border-color: #cbbfd9;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1.5px solid #8d76b2;
    background: #ffffff;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background: #f4f0ea;
    color: #aaa0b4;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: url(zongzi_ui:chevron_down.svg);
    width: 10px;
    height: 10px;
}
QComboBox QAbstractItemView {
    background: #fffdf9;
    border: 1px solid #e3d9cb;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: #f1ebf8;
    selection-color: #5f468a;
    outline: none;
}
QCheckBox {
    color: #3f3548;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 5px;
    border: 1.5px solid #cbbfd9;
    background: #ffffff;
}
QCheckBox::indicator:hover {
    border-color: #8d76b2;
}
QCheckBox::indicator:checked {
    background: #876cab;
    border-color: #876cab;
    image: url(zongzi_ui:check.svg);
}
QSlider::groove:horizontal {
    height: 6px;
    border-radius: 3px;
    background: #ebe4d9;
}
QSlider::sub-page:horizontal {
    border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a690c7, stop:1 #876cab);
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: #ffffff;
    border: 2px solid #876cab;
}
QSlider::handle:horizontal:hover {
    background: #f1ebf8;
}
QPushButton {
    background: #f6f2eb;
    color: #3f3548;
    border: 1px solid #ddd3c6;
    border-radius: 9px;
    padding: 6px 14px;
}
QPushButton:hover {
    background: #eee7dc;
    border-color: #cfc3b3;
}
QPushButton:pressed {
    background: #e6ddcf;
}
QPushButton:checked {
    background: #f1ebf8;
    border-color: #c7b6df;
    color: #5f468a;
}
QPushButton#PrimaryBtn {
    background: """
    + _PRIMARY_BTN_GRADIENT
    + """;
    color: #ffffff;
    border: none;
    font-weight: 600;
    padding: 6px 18px;
}
QPushButton#PrimaryBtn:hover {
    background: """
    + PALETTE["accent_purple_hover"]
    + """;
}
QPushButton#PrimaryBtn:pressed {
    background: #654b88;
}
"""
    + _SCROLLBAR_QSS
    + _TOOLTIP_QSS
)


MESSAGE_BOX_QSS = f"""
QMessageBox {{
    background: {PALETTE['bg_cream']};
    color: {PALETTE['text_primary']};
}}
QMessageBox QLabel {{
    color: {PALETTE['text_primary']};
}}
QMessageBox QPushButton {{
    background: #f6f2eb;
    color: {PALETTE['text_primary']};
    border: 1px solid #ddd3c6;
    border-radius: 9px;
    padding: 6px 16px;
    min-width: 68px;
}}
QMessageBox QPushButton:hover {{
    background: #eee7dc;
    border-color: #cfc3b3;
}}
QMessageBox QPushButton:default {{
    background: {_PRIMARY_BTN_GRADIENT};
    color: #ffffff;
    border: none;
    font-weight: 600;
}}
QMessageBox QPushButton:default:hover {{
    background: {PALETTE['accent_purple_hover']};
}}
"""
