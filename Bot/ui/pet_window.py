"""桌宠主窗口（悬停胶囊快捷栏）与自适应回复气泡。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from ui.mascot import ALL_PRESETS, BUILTIN_PRESETS, MascotCanvas, preset_key_for
from ui.theme import (
    HOVER_BAR_QSS,
    SPEECH_BUBBLE_QSS,
    _ui_font,
    create_styled_menu,
    pet_status_html,
    pet_status_pill_qss,
)


class SpeechBubble(QWidget):
    """桌宠身旁的自适应圆角回复气泡（带柔和小尖角与轻阴影）。"""

    clicked = Signal()

    def __init__(self, parent=None) -> None:
        flags = (
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._tail_side = "right"
        self._message = QLabel(self)
        self._message.setObjectName("SpeechBubbleLabel")
        self._message.setWordWrap(True)
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setFont(_ui_font(10))
        self._message.setMaximumWidth(320)
        self._message.setStyleSheet(SPEECH_BUBBLE_QSS)
        self._message.setCursor(Qt.CursorShape.PointingHandCursor)

        self._layout = QVBoxLayout(self)
        self._layout.addWidget(self._message)
        self._apply_tail_side("right")

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def _apply_tail_side(self, side: str) -> None:
        self._tail_side = "left" if side == "left" else "right"
        if self._tail_side == "left":
            self._layout.setContentsMargins(16, 8, 10, 12)
        else:
            self._layout.setContentsMargins(10, 8, 16, 12)

    def _build_bubble_path(self, body: QRectF) -> QPainterPath:
        l, t, r, b = body.left(), body.top(), body.right(), body.bottom()
        rad = min(14.0, body.width() * 0.35, body.height() * 0.35)
        avail_h = max(0.0, body.height() - 2.0 * rad)
        half_base = min(6.0, avail_h * 0.45)
        ty = max(t + rad + half_base, min(b - rad - half_base, t + 26.0))
        if avail_h < 4.0:
            ty = body.center().y()
            half_base = 0.0

        path = QPainterPath()
        path.moveTo(l + rad, t)
        path.lineTo(r - rad, t)
        path.quadTo(r, t, r, t + rad)
        if self._tail_side == "right" and half_base > 0.0:
            path.lineTo(r, ty - half_base)
            path.quadTo(r + 4.0, ty - 2.0, r + 8.0, ty)
            path.quadTo(r + 4.0, ty + 2.0, r, ty + half_base)
        path.lineTo(r, b - rad)
        path.quadTo(r, b, r - rad, b)
        path.lineTo(l + rad, b)
        path.quadTo(l, b, l, b - rad)
        if self._tail_side == "left" and half_base > 0.0:
            path.lineTo(l, ty + half_base)
            path.quadTo(l - 4.0, ty + 2.0, l - 8.0, ty)
            path.quadTo(l - 4.0, ty - 2.0, l, ty - half_base)
        path.lineTo(l, t + rad)
        path.quadTo(l, t, l + rad, t)
        path.closeSubpath()
        return path

    def paintEvent(self, event) -> None:  # noqa: ARG002
        w, h = float(self.width()), float(self.height())
        if w <= 24 or h <= 20:
            return
        if self._tail_side == "left":
            body = QRectF(11.0, 4.0, w - 16.0, h - 12.0)
        else:
            body = QRectF(5.0, 4.0, w - 16.0, h - 12.0)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 柔和多层投影
        painter.setPen(Qt.PenStyle.NoPen)
        for dy, expand, alpha in ((4.0, 2.0, 10), (2.5, 1.0, 16), (1.0, 0.2, 22)):
            shadow_path = self._build_bubble_path(body.adjusted(-expand, -expand + dy, expand, expand + dy))
            painter.setBrush(QColor(78, 60, 102, alpha))
            painter.drawPath(shadow_path)

        bubble_path = self._build_bubble_path(body)
        painter.setBrush(QColor(255, 253, 249, 250))
        painter.setPen(QPen(QColor("#d9cde6"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(bubble_path)
        painter.end()

    def show_message(self, message: str, pet_geometry, offset_x: int, offset_y: int) -> None:
        message = (message or "").strip()
        if not message:
            return
        if len(message) > 300:
            message = message[:297] + "…"
        self._message.setText(message)
        self._apply_tail_side("right")
        self.adjustSize()

        screen = QApplication.screenAt(pet_geometry.center()) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else pet_geometry

        left_x = pet_geometry.left() - self.width() - 6 + offset_x
        right_x = pet_geometry.right() + 6 + offset_x
        if left_x < available.left() and (available.right() - pet_geometry.right()) > (pet_geometry.left() - available.left()):
            self._apply_tail_side("left")
            self.adjustSize()
            x = right_x
        else:
            x = left_x

        y = pet_geometry.top() + 20 + offset_y
        max_x = max(available.left(), available.right() - self.width() + 1)
        max_y = max(available.top(), available.bottom() - self.height() + 1)
        x = min(max(x, available.left()), max_x)
        y = min(max(y, available.top()), max_y)
        self.move(x, y)
        self.update()
        self.show()
        self.raise_()
        self._hide_timer.start(min(12000, max(4500, len(message) * 35)))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.hide()
            self.clicked.emit()
        super().mousePressEvent(event)


class PetWindow(QWidget):
    """无边框透明桌宠窗口：平时保持干净陪伴，鼠标悬停时浮现半透明快捷栏。"""

    def __init__(
        self,
        bot_name,
        chat_window,
        bridge,
        request_exit,
        open_settings,
        always_on_top,
        bubble_offset_x,
        bubble_offset_y,
        *,
        desktop_config=None,
        on_toggle_mic=None,
        on_toggle_topmost=None,
        on_switch_avatar_mode=None,
        on_reset_position=None,
    ) -> None:
        super().__init__()
        self._chat_window = chat_window
        self._request_exit = request_exit
        self._open_settings = open_settings
        self._on_toggle_mic = on_toggle_mic
        self._on_toggle_topmost = on_toggle_topmost
        self._on_switch_avatar_mode = on_switch_avatar_mode
        self._on_reset_position = on_reset_position
        self._bubble_offset_x = bubble_offset_x
        self._bubble_offset_y = bubble_offset_y
        self._drag_offset = None
        self._allow_close = False
        self._has_tray = False
        self._context_menu_open = False
        self._mic_active = False
        self._current_state = "准备启动…"

        self._state_timer = QTimer(self)
        self._state_timer.setSingleShot(True)
        self._state_timer.timeout.connect(self._reset_transient_state)

        self._hover_hide_timer = QTimer(self)
        self._hover_hide_timer.setSingleShot(True)
        self._hover_hide_timer.timeout.connect(self._hide_hover_bar)

        self.setWindowTitle(bot_name)
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._speech_bubble = SpeechBubble(self)
        self._speech_bubble.clicked.connect(self.open_chat)

        if desktop_config is not None:
            self._mascot = MascotCanvas(
                self,
                avatar_mode=getattr(desktop_config, "avatar_mode", "vector"),
                avatar_path=getattr(desktop_config, "avatar_path", ""),
                scale=getattr(desktop_config, "pet_scale", 1.0),
            )
        else:
            self._mascot = MascotCanvas(self)
        self._mascot.set_state(self._current_state)
        self._status = QLabel(self)
        self._status.setTextFormat(Qt.TextFormat.RichText)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setFont(_ui_font(9))
        self._status.setText(pet_status_html("准备启动…"))
        self._status.setStyleSheet(pet_status_pill_qss("准备启动…"))

        # 悬停快捷栏固定占位容器，防止鼠标移入移出时猫娘产生高度跳动
        self._hover_slot = QWidget(self)
        self._hover_slot.setFixedHeight(36)
        slot_layout = QVBoxLayout(self._hover_slot)
        slot_layout.setContentsMargins(0, 2, 0, 0)
        slot_layout.setSpacing(0)

        self._hover_bar = QFrame(self._hover_slot)
        self._hover_bar.setObjectName("PetHoverBar")
        self._hover_bar.setStyleSheet(HOVER_BAR_QSS)
        bar_layout = QHBoxLayout(self._hover_bar)
        bar_layout.setContentsMargins(6, 3, 6, 3)
        bar_layout.setSpacing(2)

        self._chat_button = self._make_action_btn("💬", "打开聊天")
        self._chat_button.clicked.connect(self.open_chat)

        self._mic_button = self._make_action_btn("🎙️", "切换麦克风", checkable=True)
        self._mic_button.clicked.connect(self._handle_mic_click)

        self._topmost_button = self._make_action_btn("📌", "切换始终置顶", checkable=True)
        self._topmost_button.setChecked(bool(always_on_top))
        self._topmost_button.clicked.connect(self._handle_topmost_click)

        self._settings_button = self._make_action_btn("⚙️", "打开设置")
        self._settings_button.clicked.connect(self._open_settings)

        bar_layout.addWidget(self._chat_button)
        bar_layout.addWidget(self._mic_button)
        bar_layout.addWidget(self._topmost_button)
        bar_layout.addWidget(self._settings_button)

        slot_layout.addWidget(self._hover_bar, 0, Qt.AlignmentFlag.AlignCenter)
        self._hover_bar.hide()

        self._mascot.clicked.connect(self.open_chat)
        self._mascot.drag_started.connect(self._start_drag)
        self._mascot.drag_moved.connect(self._move_drag)

        bridge.state_changed.connect(self.set_state)
        bridge.emotion_changed.connect(self._mascot.set_emotion)
        bridge.bot_error.connect(self.show_error)
        bridge.bot_message.connect(self._show_bot_message)
        if hasattr(bridge, "mic_toggled"):
            bridge.mic_toggled.connect(self.set_microphone_active)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(4)
        layout.addWidget(self._mascot, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._hover_slot, 0, Qt.AlignmentFlag.AlignCenter)

        self._chat_button.setEnabled(self._chat_window.is_chat_enabled())
        if desktop_config is not None:
            self.apply_desktop_config(desktop_config)
        else:
            self._sync_window_size()

    def _make_action_btn(self, text: str, tooltip: str, *, checkable: bool = False) -> QPushButton:
        btn = QPushButton(text, self._hover_bar)
        btn.setObjectName("PetActionBtn")
        btn.setFont(_ui_font(10))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setCheckable(checkable)
        btn.setFixedHeight(28)
        return btn

    def _sync_window_size(self) -> None:
        width = max(224, self._mascot.width() + 28)
        height = self._mascot.height() + 78
        self.setFixedSize(width, height)

    def _clamp_to_screen(self) -> None:
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        max_x = max(area.left(), area.right() - self.width() + 1)
        max_y = max(area.top(), area.bottom() - self.height() + 1)
        clamped_x = min(max(self.x(), area.left()), max_x)
        clamped_y = min(max(self.y(), area.top()), max_y)
        if clamped_x != self.x() or clamped_y != self.y():
            self.move(clamped_x, clamped_y)

    def apply_desktop_config(self, desktop_config) -> None:
        """应用桌宠外观、缩放、透明度与气泡偏移配置。"""
        self._mascot.set_avatar_config(
            mode=getattr(desktop_config, "avatar_mode", "vector"),
            path=getattr(desktop_config, "avatar_path", ""),
            scale=getattr(desktop_config, "pet_scale", 1.0),
        )
        self._sync_window_size()
        if self.isVisible():
            self._clamp_to_screen()
        opacity = float(getattr(desktop_config, "pet_opacity", 1.0))
        self.setWindowOpacity(min(1.0, max(0.3, opacity)))
        self.set_bubble_offset(
            int(getattr(desktop_config, "bubble_offset_x", 0)),
            int(getattr(desktop_config, "bubble_offset_y", 0)),
        )
        self.set_always_on_top(bool(getattr(desktop_config, "always_on_top", True)))
        if self._mascot.fallback_message:
            self._mascot.setToolTip(self._mascot.fallback_message)
        else:
            self._mascot.setToolTip("")

    def is_hover_bar_visible(self) -> bool:
        return not self._hover_bar.isHidden()

    def _show_hover_bar(self) -> None:
        self._hover_hide_timer.stop()
        if not self._hover_bar.isVisible():
            self._hover_bar.show()

    def _hide_hover_bar(self) -> None:
        if not self._context_menu_open:
            self._hover_bar.hide()

    def enterEvent(self, event) -> None:
        self._show_hover_bar()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_hide_timer.start(350)
        super().leaveEvent(event)

    def _handle_mic_click(self) -> None:
        next_state = self._mic_button.isChecked()
        self.set_microphone_active(next_state)
        if callable(self._on_toggle_mic):
            self._on_toggle_mic(next_state)

    def _handle_topmost_click(self) -> None:
        next_state = self._topmost_button.isChecked()
        self.set_always_on_top(next_state)
        if callable(self._on_toggle_topmost):
            self._on_toggle_topmost(next_state)

    def set_microphone_active(self, active: bool) -> None:
        self._mic_active = bool(active)
        self._mic_button.setChecked(self._mic_active)
        self._mic_button.setToolTip("麦克风开启中（点击暂停）" if self._mic_active else "麦克风已暂停（点击开启）")

    def set_state(self, state: str) -> None:
        next_state = state or "待命中"
        if next_state == self._current_state:
            if next_state == "已回复":
                self._state_timer.start(3500)
            return
        self._current_state = next_state
        self._status.setText(pet_status_html(self._current_state))
        self._status.setStyleSheet(pet_status_pill_qss(self._current_state))
        self._mascot.set_state(self._current_state)
        self._state_timer.stop()
        if self._current_state == "已回复":
            self._state_timer.start(3500)

    def _reset_transient_state(self) -> None:
        if self._current_state == "已回复":
            self.set_state("已就绪")

    def show_error(self, message: str) -> None:
        self.set_state("遇到问题")
        logger.error("Desktop Bot error: {}", message)

    def _start_drag(self, global_position) -> None:
        self._drag_offset = global_position - self.frameGeometry().topLeft()

    def _move_drag(self, global_position) -> None:
        if self._drag_offset is not None:
            self.move(global_position - self._drag_offset)

    def open_chat(self) -> None:
        if not self._chat_window.is_chat_enabled():
            self.set_state("聊天窗口已关闭")
            return
        self._speech_bubble.hide()
        self._chat_window.show()
        self._chat_window.raise_()
        self._chat_window.activateWindow()

    def _show_bot_message(self, message: str) -> None:
        if self.isVisible() and not self._chat_window.isVisible():
            self._speech_bubble.show_message(
                message,
                self.frameGeometry(),
                self._bubble_offset_x,
                self._bubble_offset_y,
            )

    def set_bubble_offset(self, offset_x: int, offset_y: int) -> None:
        self._bubble_offset_x = offset_x
        self._bubble_offset_y = offset_y

    def set_chat_enabled(self, enabled: bool) -> None:
        self._chat_button.setEnabled(enabled)
        if not enabled:
            self._chat_window.hide()
        elif self._current_state == "聊天窗口已关闭":
            self.set_state("已就绪")

    def set_always_on_top(self, enabled: bool) -> None:
        target = bool(enabled)
        self._topmost_button.setChecked(target)
        flags = self.windowFlags()
        currently_topmost = bool(flags & Qt.WindowType.WindowStaysOnTopHint)
        if currently_topmost == target:
            return
        was_visible = self.isVisible()
        if target:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def contextMenuEvent(self, event) -> None:
        self._speech_bubble.hide()
        self._context_menu_open = True
        try:
            menu = create_styled_menu(self)
            open_chat = menu.addAction("💬 打开聊天")
            toggle_mic = menu.addAction("🎙️ 麦克风开启" if not self._mic_active else "🔇 暂停麦克风")
            topmost_action = QAction("📌 始终置顶", menu)
            topmost_action.setCheckable(True)
            topmost_action.setChecked(bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
            menu.addAction(topmost_action)

            mode_menu = menu.addMenu("🎨 切换形象")
            mode_menu.setFont(_ui_font(9))
            mode_actions = {}
            current_key = preset_key_for(self._mascot.requested_mode, self._mascot.avatar_path)
            for index, preset in enumerate(ALL_PRESETS):
                if index == len(BUILTIN_PRESETS):
                    mode_menu.addSeparator()
                act = QAction(preset.label, mode_menu)
                act.setCheckable(True)
                act.setChecked(current_key == preset.key)
                act.setToolTip(preset.description)
                mode_menu.addAction(act)
                mode_actions[act] = preset.key

            reset_pos = menu.addAction("📍 回到默认位置")
            settings = menu.addAction("⚙️ 设置中心")
            toggle_visible = menu.addAction("🫥 隐藏桌宠")
            menu.addSeparator()
            quit_action = menu.addAction("⏻ 退出")

            selected = menu.exec(event.globalPos())
            if selected == open_chat:
                self.open_chat()
            elif selected == toggle_mic:
                next_mic = not self._mic_active
                self.set_microphone_active(next_mic)
                if callable(self._on_toggle_mic):
                    self._on_toggle_mic(next_mic)
            elif selected == topmost_action:
                next_top = topmost_action.isChecked()
                self.set_always_on_top(next_top)
                if callable(self._on_toggle_topmost):
                    self._on_toggle_topmost(next_top)
            elif selected in mode_actions:
                if callable(self._on_switch_avatar_mode):
                    self._on_switch_avatar_mode(mode_actions[selected])
            elif selected == reset_pos:
                if callable(self._on_reset_position):
                    self._on_reset_position()
            elif selected == settings:
                self._open_settings()
            elif selected == toggle_visible:
                self.hide()
            elif selected == quit_action:
                self._request_exit()
        finally:
            self._context_menu_open = False
            self._hover_hide_timer.start(350)

    def hideEvent(self, event) -> None:
        self._speech_bubble.hide()
        self._hover_bar.hide()
        super().hideEvent(event)

    def cleanup(self) -> None:
        self._state_timer.stop()
        self._hover_hide_timer.stop()
        self._speech_bubble._hide_timer.stop()
        self._mascot.cleanup()

    def closeEvent(self, event) -> None:
        if self._allow_close:
            self.cleanup()
            event.accept()
        elif self._has_tray:
            event.ignore()
            self.hide()
        else:
            self.cleanup()
            event.accept()
            self._request_exit()
