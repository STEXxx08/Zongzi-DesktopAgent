"""设置页 1：外观与桌宠配置。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.mascot import (
    ALL_PRESETS,
    BUILTIN_PRESETS,
    MascotCanvas,
    get_preset,
    preset_key_for,
    render_avatar_thumbnail,
    resolve_preset,
)
from ui.settings.widgets import (
    PathPickerEdit,
    SettingsCard,
    SliderSpinRow,
    build_settings_page,
    combo_value,
    make_checkbox,
    make_combo,
    make_float_spin,
    make_int_spin,
    make_line_edit,
)
from ui.theme import _ui_font

_PREVIEW_EMOTIONS = (
    ("默认", "Default"),
    ("开心", "happy"),
    ("害羞", "shy"),
    ("生气", "angry"),
    ("难过", "sad"),
    ("惊讶", "surprised"),
)


class AvatarPreviewPanel(QFrame):
    """形象实时预览：可切换表情，点击形象触发互动动画。"""

    def __init__(self, parent=None, *, defer_initial_avatar: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("AvatarPreviewPanel")
        self._canvas = MascotCanvas(
            self,
            avatar_mode=None if defer_initial_avatar else "vector",
            scale=0.9,
        )
        self._hint = QLabel("", self)
        self._hint.setObjectName("SettingsCardDesc")
        self._hint.setFont(_ui_font(9))
        self._hint.setWordWrap(True)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        chips = QHBoxLayout()
        chips.setSpacing(4)
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        for idx, (label, emotion) in enumerate(_PREVIEW_EMOTIONS):
            btn = QPushButton(label, self)
            btn.setObjectName("EmotionChip")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFont(_ui_font(9))
            btn.clicked.connect(lambda _=False, emo=emotion: self._canvas.set_emotion(emo))
            self._chip_group.addButton(btn, idx)
            chips.addWidget(btn)
            if idx == 0:
                btn.setChecked(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addWidget(self._canvas, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._hint)
        layout.addLayout(chips)

    def show_avatar(self, mode: str, path: str, hint: str) -> None:
        if mode == "live2d":
            # Live2D 需要 OpenGL 子窗口，设置页里只给提示，避免嵌套 GL 上下文
            self._canvas.set_avatar_config("vector", "", 0.9)
            self._hint.setText("Live2D 模型请保存后在桌宠上查看效果（此处显示默认形象）")
            return
        self._canvas.set_avatar_config(mode, path, 0.9)
        fallback = self._canvas.fallback_message
        self._hint.setText(f"⚠ {fallback}" if fallback else hint)

    def cleanup(self) -> None:
        self._canvas.cleanup()


_CARD_SHORT_LABELS = {
    "vector": "矢量粽子",
    "sprite:gemini_q": "Gemini 娘",
    "sprite:deepseek_winter": "DeepSeek 冬装",
}


class _PresetCardButton(QPushButton):
    """带圆形缩略图与短标题的内置形象快捷选择卡片。"""

    def __init__(self, preset_key: str, mode: str, path: str, label: str, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        self.preset_key = preset_key
        self.setObjectName("PresetCard")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setMinimumHeight(88)

        thumb = QLabel(self)
        thumb.setPixmap(render_avatar_thumbnail(mode, path, 48, circular=True))
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._title = QLabel(label, self)
        self._title.setFont(_ui_font(9))
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(thumb, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title)


class AppearancePage(QWidget):
    def __init__(self, desktop_config, chat_config, parent=None) -> None:
        super().__init__(parent)

        # 形象选择卡片
        avatar_card = SettingsCard(
            "桌宠形象",
            "选择内置形象，或使用自定义立绘包 / Live2D 模型。点击预览中的形象可以试试互动动画。",
            self,
        )
        current_mode = getattr(desktop_config, "avatar_mode", "vector")
        current_path = getattr(desktop_config, "avatar_path", "")
        self._custom_path = "" if preset_key_for(current_mode, current_path).startswith("sprite:") else current_path

        self._avatar_preset = make_combo(
            [(preset.label, preset.key) for preset in ALL_PRESETS],
            preset_key_for(current_mode, current_path),
        )
        self._avatar_path = PathPickerEdit(
            self._custom_path,
            mode="dir",
            placeholder="自定义立绘：选择包含表情图片或 manifest.json 的目录；Live2D：选择 .model3.json",
        )
        self._preview = AvatarPreviewPanel(self, defer_initial_avatar=True)

        cards_row = QWidget(self)
        cards_layout = QHBoxLayout(cards_row)
        cards_layout.setContentsMargins(0, 2, 0, 2)
        cards_layout.setSpacing(10)
        self._preset_cards: dict[str, _PresetCardButton] = {}
        for preset in BUILTIN_PRESETS:
            short_name = _CARD_SHORT_LABELS.get(preset.key, preset.label)
            card_btn = _PresetCardButton(
                preset.key,
                preset.mode,
                preset.path,
                short_name,
                f"{preset.label}：{preset.description}",
                cards_row,
            )
            card_btn.clicked.connect(lambda _=False, pk=preset.key: self._select_preset_key(pk))
            self._preset_cards[preset.key] = card_btn
            cards_layout.addWidget(card_btn, 1)

        self._avatar_preset.currentIndexChanged.connect(self._sync_avatar_path_mode)
        self._avatar_path.path_changed.connect(self._on_custom_path_changed)

        avatar_card.add_widget_row(self._preview)
        avatar_card.add_widget_row(cards_row)
        avatar_card.add_row("形象预设", self._avatar_preset)
        avatar_card.add_row(
            "自定义资源路径",
            self._avatar_path,
            tooltip="仅自定义立绘 / Live2D 需要填写；内置形象无需路径",
        )
        self._sync_avatar_path_mode()

        # 桌宠窗口卡片
        pet_card = SettingsCard("桌宠窗口", "桌宠缩放、透明度、置顶与气泡位置等窗口行为。", self)
        self._shell_enabled = make_checkbox("启用桌宠桌面外壳（关闭则仅控制台运行）", desktop_config.enable)
        self._pet_scale = SliderSpinRow(
            getattr(desktop_config, "pet_scale", 1.0), 0.5, 2.0, step=0.05, decimals=2
        )
        self._pet_opacity = SliderSpinRow(
            getattr(desktop_config, "pet_opacity", 1.0), 0.3, 1.0, step=0.05, decimals=2
        )
        self._always_on_top = make_checkbox("桌宠窗口始终置顶", desktop_config.always_on_top)

        self._bubble_offset_x = make_int_spin(desktop_config.bubble_offset_x, -2400, 2400)
        self._bubble_offset_y = make_int_spin(desktop_config.bubble_offset_y, -1600, 1600)
        self._exit_grace_seconds = make_float_spin(
            getattr(desktop_config, "exit_grace_seconds", 60.0), 5.0, 600.0, step=5.0, decimals=1
        )
        self._reset_pet_position = make_checkbox("保存后把桌宠移回默认位置（主屏右下角）")

        pet_card.add_row("桌宠模式总开关", self._shell_enabled, restart_required=True)
        pet_card.add_row("桌宠缩放倍率", self._pet_scale)
        pet_card.add_row("桌宠不透明度", self._pet_opacity)
        pet_card.add_row("窗口置顶", self._always_on_top)
        pet_card.add_row(
            "气泡水平偏移 (px)",
            self._bubble_offset_x,
            tooltip="相对默认位置微调：正数向右，负数向左",
        )
        pet_card.add_row(
            "气泡垂直偏移 (px)",
            self._bubble_offset_y,
            tooltip="相对默认位置微调：正数向下，负数向上",
        )
        pet_card.add_row("退出收尾等待 (秒)", self._exit_grace_seconds)
        pet_card.add_row("位置重置", self._reset_pet_position)

        # 聊天窗口卡片
        chat_card = SettingsCard("文字聊天窗口", "配置文字交互窗口的大小、字体与静音开关。", self)
        self._chat_enabled = make_checkbox("启用文字聊天窗口", chat_config.enable)

        self._title = make_line_edit(chat_config.title)
        self._width = make_int_spin(chat_config.width, 420, 2400, step=20)
        self._height = make_int_spin(chat_config.height, 300, 1600, step=20)
        self._font_size = make_int_spin(chat_config.font_size, 8, 36)
        self._input_font_size = make_int_spin(chat_config.input_font_size, 8, 36)
        self._skip_tts = make_checkbox("文字聊天时不播放语音（静音回复）", chat_config.skip_tts)

        chat_card.add_row("启用聊天窗", self._chat_enabled)
        chat_card.add_row("聊天窗标题", self._title)
        chat_card.add_row("默认宽度 (px)", self._width)
        chat_card.add_row("默认高度 (px)", self._height)
        chat_card.add_row("对话字号 (pt)", self._font_size)
        chat_card.add_row("输入字号 (pt)", self._input_font_size)
        chat_card.add_row("静音模式", self._skip_tts)

        build_settings_page(self, [avatar_card, pet_card, chat_card])

    def _current_avatar(self) -> tuple[str, str]:
        key = combo_value(self._avatar_preset)
        if key in ("sprite", "live2d"):
            return key, self._avatar_path.text()
        return resolve_preset(key, "")

    def _on_custom_path_changed(self, path: str) -> None:
        self._custom_path = path
        mode, avatar_path = self._current_avatar()
        preset = get_preset(combo_value(self._avatar_preset))
        self._preview.show_avatar(mode, avatar_path, preset.description if preset else "")

    def _select_preset_key(self, key: str) -> None:
        idx = self._avatar_preset.findData(key)
        if idx >= 0:
            self._avatar_preset.setCurrentIndex(idx)

    def _sync_avatar_path_mode(self) -> None:
        key = combo_value(self._avatar_preset)
        for card_key, card_btn in self._preset_cards.items():
            card_btn.blockSignals(True)
            card_btn.setChecked(card_key == key)
            card_btn.blockSignals(False)
        if key == "live2d":
            self._avatar_path.setEnabled(True)
            self._avatar_path.set_mode("file", "Live2D 模型 (*.model3.json);;所有文件 (*.*)")
        elif key == "sprite":
            self._avatar_path.setEnabled(True)
            self._avatar_path.set_mode("dir")
        else:
            self._avatar_path.setEnabled(False)
        mode, avatar_path = self._current_avatar()
        preset = get_preset(key)
        self._preview.show_avatar(mode, avatar_path, preset.description if preset else "")

    def cleanup(self) -> None:
        self._preview.cleanup()

    def request_reset_position(self) -> None:
        self._reset_pet_position.setChecked(True)

    @property
    def reset_position_requested(self) -> bool:
        return self._reset_pet_position.isChecked()

    def values(self) -> dict:
        avatar_mode, avatar_path = self._current_avatar()
        return {
            "desktop_shell": {
                "enable": self._shell_enabled.isChecked(),
                "avatar_mode": avatar_mode,
                "avatar_path": avatar_path,
                "pet_scale": self._pet_scale.value(),
                "pet_opacity": self._pet_opacity.value(),
                "always_on_top": self._always_on_top.isChecked(),
                "bubble_offset_x": self._bubble_offset_x.value(),
                "bubble_offset_y": self._bubble_offset_y.value(),
                "exit_grace_seconds": float(self._exit_grace_seconds.value()),
            },
            "chat_window": {
                "enable": self._chat_enabled.isChecked(),
                "title": self._title.text().strip(),
                "width": self._width.value(),
                "height": self._height.value(),
                "font_size": self._font_size.value(),
                "input_font_size": self._input_font_size.value(),
                "skip_tts": self._skip_tts.isChecked(),
            },
        }
