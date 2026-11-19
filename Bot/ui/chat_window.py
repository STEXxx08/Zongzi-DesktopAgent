"""现代气泡卡片式文字对话窗口。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from condata.pipeline.asr import ASRPrediction
from event.event_data import PipelineASREvent
from event.event_emitter import emitter
from ui.mascot import render_avatar_thumbnail
from ui.theme import CHAT_WINDOW_QSS, _ui_font, pet_status_html


class ToggleSwitch(QCheckBox):
    """胶囊滑动开关（保持完整 QCheckBox 接口）。"""

    _TRACK_W = 32
    _TRACK_H = 18
    _THUMB_D = 14
    _GAP = 6

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text()) if self.text() else 0
        width = self._TRACK_W + (self._GAP + text_w if text_w else 0) + 4
        height = max(22, fm.height() + 4)
        return QSize(width, height)

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        h = float(self.height())
        track_y = (h - self._TRACK_H) / 2.0
        track_rect = QRectF(1.0, track_y, float(self._TRACK_W), float(self._TRACK_H))
        checked = self.isChecked()
        enabled = self.isEnabled()

        if checked:
            fill = QColor("#4d8f70") if enabled else QColor("#a6c9b8")
            border = QColor("#3f7a5e") if enabled else QColor("#94b8a7")
        else:
            fill = QColor("#e0d7ca") if enabled else QColor("#ece6dd")
            border = QColor("#d1c5b6")

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(track_rect, self._TRACK_H / 2.0, self._TRACK_H / 2.0)

        thumb_x = (1.0 + self._TRACK_W - self._THUMB_D - 2.0) if checked else 3.0
        thumb_y = (h - self._THUMB_D) / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(60, 45, 75, 28))
        painter.drawEllipse(QRectF(thumb_x, thumb_y + 1.0, float(self._THUMB_D), float(self._THUMB_D)))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(thumb_x, thumb_y, float(self._THUMB_D), float(self._THUMB_D)))

        if self.text():
            painter.setFont(self.font())
            painter.setPen(QColor("#453a4f" if checked else "#6f6479"))
            text_rect = QRectF(
                float(self._TRACK_W + self._GAP + 1),
                0.0,
                max(0.0, float(self.width()) - (self._TRACK_W + self._GAP + 1)),
                h,
            )
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())

        painter.end()


class _BubbleLabel(QLabel):
    """按实际最大可用宽度折行计算 sizeHint 的气泡文本控件。"""

    _PAD_W = 32
    _PAD_H = 18

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return self.wordWrap()

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        if not self.wordWrap():
            return super().heightForWidth(width)
        inner = self.fontMetrics().boundingRect(
            QRect(0, 0, max(40, width - self._PAD_W), 100000),
            Qt.TextFlag.TextWordWrap,
            self.text(),
        )
        return max(self.fontMetrics().height(), inner.height()) + self._PAD_H

    def sizeHint(self) -> QSize:  # noqa: N802
        max_w = self.maximumWidth()
        if not self.wordWrap() or max_w >= 16777215 or max_w <= 60:
            return super().sizeHint()
        inner = self.fontMetrics().boundingRect(
            QRect(0, 0, max(40, max_w - self._PAD_W), 100000),
            Qt.TextFlag.TextWordWrap,
            self.text(),
        )
        min_h = self.fontMetrics().height() + self._PAD_H
        return QSize(min(max_w, inner.width() + self._PAD_W), max(min_h, inner.height() + self._PAD_H))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()


class ChatTranscriptArea(QScrollArea):
    """基于 QScrollArea 的圆角气泡卡片消息流视图。"""

    def __init__(self, bot_name: str, bot_avatar: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatTranscript")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._bot_name = bot_name
        self._bot_avatar = bot_avatar
        self._msg_font = _ui_font(11)
        self._entries: list[str] = []
        self._bubble_labels: list[QLabel] = []
        self._avatar_labels: list[QLabel] = []
        self._row_widgets: list[QWidget] = []

        self._body = QWidget(self)
        self._body.setObjectName("ChatTranscriptBody")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(14, 14, 14, 14)
        self._body_layout.setSpacing(12)

        self._empty_hint = QLabel(f"和 {bot_name} 打个招呼吧 ~", self._body)
        self._empty_hint.setObjectName("ChatEmptyHint")
        self._empty_hint.setFont(_ui_font(10))
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._body_layout.addWidget(self._empty_hint)
        self._body_layout.addStretch(1)

        self.setWidget(self._body)

    def _max_bubble_width(self) -> int:
        vp = self.viewport()
        w = vp.width() if vp is not None else self.width()
        return max(180, int((w if w > 80 else 460) * 0.72))

    def _make_bubble_label(self, text: str, object_name: str) -> QLabel:
        label = _BubbleLabel(text, self._body)
        label.setObjectName(object_name)
        label.setFont(self._msg_font)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        label.setMaximumWidth(self._max_bubble_width())
        self._bubble_labels.append(label)
        return label

    def _insert_row(self, row: QWidget) -> None:
        self._empty_hint.hide()
        # 插在末尾 stretch 之前
        insert_idx = max(0, self._body_layout.count() - 1)
        self._body_layout.insertWidget(insert_idx, row)
        self._row_widgets.append(row)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def setFont(self, font: QFont) -> None:  # noqa: N802
        self._msg_font = QFont(font)
        super().setFont(font)
        for label in self._bubble_labels:
            label.setFont(self._msg_font)

    def set_bot_avatar(self, pixmap: QPixmap) -> None:
        self._bot_avatar = pixmap
        for avatar_lbl in self._avatar_labels:
            avatar_lbl.setPixmap(pixmap)

    def set_bot_name(self, name: str) -> None:
        self._bot_name = name
        self._empty_hint.setText(f"和 {name} 打个招呼吧 ~")

    def append_user_message(self, message: str) -> None:
        now_text = datetime.now().strftime("%H:%M")
        self._entries.append(f"你 ({now_text}): {message}")

        row = QWidget(self._body)
        outer = QHBoxLayout(row)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        meta = QLabel(now_text, row)
        meta.setObjectName("BubbleMeta")
        meta.setFont(_ui_font(8))
        meta.setAlignment(Qt.AlignmentFlag.AlignRight)

        bubble = self._make_bubble_label(message, "BubbleUser")
        col.addWidget(meta, 0, Qt.AlignmentFlag.AlignRight)
        col.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(col)

        self._insert_row(row)

    def append_bot_message(self, speaker: str, message: str, *, badge: str = "") -> None:
        now_text = datetime.now().strftime("%H:%M")
        tag = f" [{badge}]" if badge else ""
        self._entries.append(f"{speaker}{tag} ({now_text}): {message}")

        row = QWidget(self._body)
        outer = QHBoxLayout(row)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        avatar = QLabel(row)
        avatar.setFixedSize(28, 28)
        avatar.setPixmap(self._bot_avatar)
        self._avatar_labels.append(avatar)
        outer.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(2, 0, 0, 0)
        meta_row.setSpacing(6)

        name_lbl = QLabel(speaker, row)
        name_lbl.setObjectName("BubbleMeta")
        name_lbl.setFont(_ui_font(8, bold=True))
        meta_row.addWidget(name_lbl)

        if badge:
            emo_lbl = QLabel(badge, row)
            emo_lbl.setObjectName("BubbleEmotion")
            emo_lbl.setFont(_ui_font(8))
            meta_row.addWidget(emo_lbl)

        time_lbl = QLabel(now_text, row)
        time_lbl.setObjectName("BubbleMeta")
        time_lbl.setFont(_ui_font(8))
        meta_row.addWidget(time_lbl)
        meta_row.addStretch(1)

        bubble = self._make_bubble_label(message, "BubbleBot")
        col.addLayout(meta_row)
        col.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft)

        outer.addLayout(col)
        outer.addStretch(1)
        self._insert_row(row)

    def append_system_message(self, message: str) -> None:
        text = f"系统提示：{message}"
        self._entries.append(text)

        row = QWidget(self._body)
        outer = QHBoxLayout(row)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.addStretch(1)
        bubble = self._make_bubble_label(text, "BubbleSystem")
        bubble.setFont(_ui_font(9))
        bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(bubble)
        outer.addStretch(1)
        self._insert_row(row)

    def clear(self) -> None:
        self._entries.clear()
        self._bubble_labels.clear()
        self._avatar_labels.clear()
        for row in self._row_widgets:
            self._body_layout.removeWidget(row)
            row.deleteLater()
        self._row_widgets.clear()
        self._empty_hint.show()

    def toPlainText(self) -> str:  # noqa: N802
        return "\n".join(self._entries)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        max_w = self._max_bubble_width()
        for label in self._bubble_labels:
            label.setMaximumWidth(max_w)
            label.updateGeometry()


class ChatWindow(QDialog):
    """带头像状态栏、气泡卡片消息区与胶囊输入栏的聊天窗口。"""

    def __init__(
        self,
        bot_name,
        title,
        width,
        height,
        font_size,
        input_font_size,
        skip_tts,
        bridge,
        enabled=True,
        *,
        on_toggle_mic=None,
        on_open_settings=None,
        avatar_mode: str = "vector",
        avatar_path: str = "",
    ) -> None:
        super().__init__()
        self.setObjectName("ChatWindow")
        self.setStyleSheet(CHAT_WINDOW_QSS)
        self._bot_name = bot_name
        self._skip_tts = bool(skip_tts)
        self._chat_enabled = bool(enabled)
        self._ready = False
        self._current_emotion = "Default"
        self._on_toggle_mic = on_toggle_mic
        self._on_open_settings = on_open_settings

        self.setWindowTitle(title)
        self.resize(width, height)
        self.setMinimumSize(420, 300)

        # 1. 顶部头像状态栏
        self._header_bar = QFrame(self)
        self._header_bar.setObjectName("ChatHeaderBar")
        header_layout = QHBoxLayout(self._header_bar)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(10)

        self._avatar_label = QLabel(self._header_bar)
        self._avatar_label.setObjectName("ChatAvatar")
        self._avatar_label.setFixedSize(36, 36)
        self._avatar_label.setPixmap(render_avatar_thumbnail(avatar_mode, avatar_path, 36, circular=True))

        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(1)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        self._bot_title_label = QLabel(bot_name, self._header_bar)
        self._bot_title_label.setObjectName("ChatBotTitle")
        self._bot_title_label.setFont(_ui_font(11, bold=True))

        self._emotion_badge = QLabel("Default", self._header_bar)
        self._emotion_badge.setObjectName("ChatEmotionBadge")
        self._emotion_badge.setFont(_ui_font(8))

        title_row.addWidget(self._bot_title_label)
        title_row.addWidget(self._emotion_badge)
        title_row.addStretch(1)

        self._state_badge = QLabel(self._header_bar)
        self._state_badge.setObjectName("ChatStateBadge")
        self._state_badge.setTextFormat(Qt.TextFormat.RichText)
        self._state_badge.setFont(_ui_font(9))
        self._state_badge.setText(pet_status_html("启动中"))

        info_col.addLayout(title_row)
        info_col.addWidget(self._state_badge)

        self._mic_toggle_btn = QPushButton("🎙️ 麦克风", self._header_bar)
        self._mic_toggle_btn.setObjectName("ChatToolBtn")
        self._mic_toggle_btn.setFont(_ui_font(9))
        self._mic_toggle_btn.setCheckable(True)
        self._mic_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_toggle_btn.clicked.connect(self._handle_mic_toggle)

        self._clear_btn = QPushButton("🧹 清屏", self._header_bar)
        self._clear_btn.setObjectName("ChatToolBtn")
        self._clear_btn.setFont(_ui_font(9))
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.clicked.connect(self.clear_messages)

        self._settings_btn = QPushButton("⚙️ 设置", self._header_bar)
        self._settings_btn.setObjectName("ChatToolBtn")
        self._settings_btn.setFont(_ui_font(9))
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.clicked.connect(self._handle_open_settings)

        header_layout.addWidget(self._avatar_label)
        header_layout.addLayout(info_col)
        header_layout.addStretch(1)
        header_layout.addWidget(self._mic_toggle_btn)
        header_layout.addWidget(self._clear_btn)
        header_layout.addWidget(self._settings_btn)

        # 2. 消息气泡卡片区
        self._transcript = ChatTranscriptArea(
            bot_name,
            render_avatar_thumbnail(avatar_mode, avatar_path, 28, circular=True),
            self,
        )
        self._transcript.setFont(_ui_font(font_size))

        # 3. 底部胶囊输入栏
        self._composer = QFrame(self)
        self._composer.setObjectName("ChatComposer")
        self._composer.setProperty("focused", False)

        self._input = QLineEdit(self._composer)
        self._input.setObjectName("ChatInput")
        self._input.setFont(_ui_font(input_font_size))
        self._input.returnPressed.connect(self._send_message)
        self._input.installEventFilter(self)

        self._skip_tts_checkbox = ToggleSwitch("静音", self._composer)
        self._skip_tts_checkbox.setObjectName("ChatMuteBox")
        self._skip_tts_checkbox.setFont(_ui_font(9))
        self._skip_tts_checkbox.setToolTip("开启后文字聊天不触发语音合成")
        self._skip_tts_checkbox.setChecked(self._skip_tts)
        self._skip_tts_checkbox.toggled.connect(self._on_mute_toggled)

        self._send_button = QPushButton("发送", self._composer)
        self._send_button.setObjectName("ChatSendBtn")
        self._send_button.setFont(_ui_font(10))
        self._send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_button.clicked.connect(self._send_message)

        composer_layout = QHBoxLayout(self._composer)
        composer_layout.setContentsMargins(12, 5, 6, 5)
        composer_layout.setSpacing(8)
        composer_layout.addWidget(self._input, 1)
        composer_layout.addWidget(self._skip_tts_checkbox)
        composer_layout.addWidget(self._send_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self._header_bar)
        layout.addWidget(self._transcript, 1)
        layout.addWidget(self._composer)

        bridge.user_message.connect(self._append_user_message)
        bridge.bot_message.connect(self._append_bot_message)
        bridge.state_changed.connect(self._on_state_changed)
        bridge.emotion_changed.connect(self._on_emotion_changed)
        bridge.bot_error.connect(self._append_error_message)
        if hasattr(bridge, "mic_toggled"):
            bridge.mic_toggled.connect(self.set_microphone_active)

        self.set_runtime_ready(False)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._input and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._composer.setProperty("focused", event.type() == QEvent.Type.FocusIn)
            self._composer.style().unpolish(self._composer)
            self._composer.style().polish(self._composer)
        return super().eventFilter(watched, event)

    def _handle_mic_toggle(self) -> None:
        active = self._mic_toggle_btn.isChecked()
        if callable(self._on_toggle_mic):
            self._on_toggle_mic(active)

    def _handle_open_settings(self) -> None:
        if callable(self._on_open_settings):
            self._on_open_settings()

    def _on_mute_toggled(self, checked: bool) -> None:
        self._skip_tts = bool(checked)
        try:
            import manager.config_manager as config_manager

            if config_manager._config is not None:
                config_manager._config.service.chat_window.skip_tts = self._skip_tts
        except Exception:
            pass

    def _on_state_changed(self, state: str) -> None:
        self._state_badge.setText(pet_status_html(state or "待命中"))

    def _on_emotion_changed(self, emotion: str) -> None:
        clean = (emotion or "Default").strip() or "Default"
        self._current_emotion = clean
        self._emotion_badge.setText(clean)

    def set_microphone_active(self, active: bool) -> None:
        self._mic_toggle_btn.setChecked(bool(active))

    def set_bot_name(self, name: str) -> None:
        self._bot_name = name
        self._bot_title_label.setText(name)
        self._transcript.set_bot_name(name)

    def set_avatar(self, mode: str, path: str = "") -> None:
        self._avatar_label.setPixmap(render_avatar_thumbnail(mode, path, 36, circular=True))
        self._transcript.set_bot_avatar(render_avatar_thumbnail(mode, path, 28, circular=True))

    def apply_settings(self, config) -> None:
        self._skip_tts = bool(config.skip_tts)
        self._skip_tts_checkbox.setChecked(self._skip_tts)
        self.setWindowTitle(config.title)
        self.resize(config.width, config.height)
        self._transcript.setFont(_ui_font(config.font_size))
        self._input.setFont(_ui_font(config.input_font_size))
        self.set_chat_enabled(config.enable)

    def is_chat_enabled(self) -> bool:
        return self._chat_enabled

    def set_chat_enabled(self, enabled: bool) -> None:
        self._chat_enabled = bool(enabled)
        self._refresh_input_state()

    def set_runtime_ready(self, ready: bool) -> None:
        self._ready = bool(ready)
        if self._ready:
            self._state_badge.setText(pet_status_html("已就绪"))
        self._refresh_input_state()

    def _refresh_input_state(self) -> None:
        active = self._ready and self._chat_enabled
        self._input.setEnabled(active)
        self._send_button.setEnabled(active)
        if not self._chat_enabled:
            self._input.setPlaceholderText("聊天窗口已禁用")
        elif self._ready:
            self._input.setPlaceholderText(f"和 {self._bot_name} 说点什么…")
        else:
            self._input.setPlaceholderText("桌宠正在启动…")

    def _send_message(self) -> None:
        message = self._input.text().strip()
        if not message or not self._ready or not self._chat_enabled:
            return
        self._input.clear()
        # emitter.emit 只做入队（同步回调进线程池、协程进事件循环），本身不阻塞，
        # 因此不需要为每条消息新起一个线程。
        emitter.emit(
            PipelineASREvent(
                prediction=ASRPrediction(transcript=message),
                skip_tts=self._skip_tts,
                source="text_gui",
            )
        )

    def clear_messages(self) -> None:
        self._transcript.clear()

    def _append_user_message(self, message: str) -> None:
        if not message:
            return
        self._transcript.append_user_message(message)

    def _append_bot_message(self, message: str) -> None:
        if not message:
            return
        badge = self._current_emotion if self._current_emotion != "Default" else ""
        self._transcript.append_bot_message(self._bot_name, message, badge=badge)

    def _append_error_message(self, message: str) -> None:
        if not message:
            return
        self._transcript.append_system_message(message)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()
