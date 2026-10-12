import asyncio
import html
import os
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from condata.pipeline.asr import ASRPrediction
from event.event_data import PipelineASREvent
from event.event_emitter import emitter
from event.registry import EventKeyRegistry
from loguru import logger
from manager.config_manager import get_config, save_desktop_settings
from services.config import DesktopShellConfig
from ui.config import ChatWindowConfig


# 中文字体族优先级：显式指定，避免依赖系统"碰巧"回退到某个中文字体。
_CJK_FONT_FAMILIES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "思源黑体",
    "Source Han Sans SC",
    "Noto Sans CJK SC",
    "SimHei",
    "Segoe UI",
    "Sans Serif",
)


def _ui_font(size: int) -> QFont:
    """按 CJK 优先的字体链创建界面字体。

    只写 "Sans Serif" 时，部分 Windows 环境下中文会落到字形不全的字体上，
    因此这里显式给出候选字族，让 Qt 逐级回退。
    """
    font = QFont()
    font.setFamilies(list(_CJK_FONT_FAMILIES))
    font.setPointSize(int(size))
    return font


class BotUiBridge(QObject):
    user_message = Signal(str)
    bot_message = Signal(str)
    state_changed = Signal(str)
    emotion_changed = Signal(str)
    bot_error = Signal(str)

    def __init__(self):
        super().__init__()
        self._asr_listener = emitter.on(EventKeyRegistry.Pipeline.ASR)(self._on_asr)
        self._llm_listener = emitter.on(EventKeyRegistry.Pipeline.LLM)(self._on_llm)
        self._speaker_listener = emitter.on(EventKeyRegistry.Device.SPEAKER_PLAY)(self._on_speaker_play)
        self._character_listener = emitter.on(EventKeyRegistry.Character.STATE)(self._on_character_state)
        self._error_listener = emitter.on(EventKeyRegistry.System.SYSTEM_UNHANDLED_ERROR)(self._on_error)

    def _on_asr(self, event):
        if event.source != "desktop":
            self.user_message.emit(event.prediction.transcript or "")
        self.state_changed.emit("思考中")

    def _on_llm(self, event):
        self.bot_message.emit(event.prediction.response or "")

    def _on_speaker_play(self, event):
        self.state_changed.emit("正在说话")
        self.emotion_changed.emit(event.emotion or "Default")

    def _on_character_state(self, event):
        states = {
            "idle": "待命中",
            "listening": "聆听中",
            "thinking": "思考中",
            "replied": "已回复",
            "speaking": "正在说话",
        }
        self.state_changed.emit(states.get(event.state, event.state))
        if event.emotion:
            self.emotion_changed.emit(event.emotion)

    def _on_error(self, event):
        self.bot_error.emit(str(event.msg or "运行时发生错误"))
        self.state_changed.emit("遇到问题")

    def report_error(self, message):
        self.bot_error.emit(message)

    def close(self):
        emitter.off(EventKeyRegistry.Pipeline.ASR, self._asr_listener)
        emitter.off(EventKeyRegistry.Pipeline.LLM, self._llm_listener)
        emitter.off(EventKeyRegistry.Device.SPEAKER_PLAY, self._speaker_listener)
        emitter.off(EventKeyRegistry.Character.STATE, self._character_listener)
        emitter.off(EventKeyRegistry.System.SYSTEM_UNHANDLED_ERROR, self._error_listener)


class BotRunner(QObject):
    ready = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self._thread = None
        self._bot = None
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, name="BotRuntime", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            from bot import Bot

            bot = Bot()
            with self._lock:
                self._bot = bot
            if self._stop_requested.is_set():
                bot.request_stop()
            asyncio.run(self._serve(bot))
        except Exception as exc:
            logger.exception("Desktop Bot runtime failed")
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    async def _serve(self, bot):
        start_task = asyncio.create_task(bot.start())
        try:
            while not bot.runtime_ready and not start_task.done():
                await asyncio.sleep(0.05)
            if bot.runtime_ready:
                self.ready.emit()
            await start_task
        finally:
            if not start_task.done():
                bot.request_stop()
                await start_task
            await bot.stop()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def request_stop(self):
        self._stop_requested.set()
        with self._lock:
            bot = self._bot
        if bot is not None:
            bot.request_stop()


class MascotCanvas(QWidget):
    drag_started = Signal(QPoint)
    drag_moved = Signal(QPoint)
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(190, 178)
        self._state = "待命中"
        self._emotion = "Default"
        self._press_position = None
        self._drag_distance = 0
        self._animation_frame = 0
        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._advance_animation)

    def _advance_animation(self):
        self._animation_frame += 1
        self.update()

    def set_state(self, state):
        self._state = state
        if state in ("聆听中", "思考中", "正在说话"):
            self._animation_timer.start(120)
        else:
            self._animation_timer.stop()
            self._animation_frame = 0
        self.update()

    def set_emotion(self, emotion):
        self._emotion = (emotion or "Default").strip()
        self.update()

    def _emotion_kind(self):
        emotion = self._emotion.casefold()
        if any(tag in emotion for tag in ("开心", "高兴", "喜悦", "快乐", "happy", "joy", "excited", "love", "喜欢")):
            return "happy"
        if any(tag in emotion for tag in ("难过", "悲伤", "伤心", "sad", "sorrow", "cry")):
            return "sad"
        if any(tag in emotion for tag in ("生气", "愤怒", "angry", "anger", "mad")):
            return "angry"
        if any(tag in emotion for tag in ("害羞", "羞", "shy", "embarrass")):
            return "shy"
        if any(tag in emotion for tag in ("惊讶", "震惊", "surprise", "惊")):
            return "surprised"
        return "neutral"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = self._animation_frame
        if self._state == "正在说话":
            painter.translate(0, -2 if frame % 2 else 1)
        elif self._state == "聆听中":
            painter.translate(0, -1 if frame % 8 == 0 else 0)
        painter.setPen(QPen(QColor("#594a63"), 3))
        if self._state == "思考中":
            pulse = 65 + (frame % 8) * 7
            painter.setBrush(QColor(185, 160, 235, pulse))
            painter.drawEllipse(QRectF(18, 12, 154, 148))
        painter.setBrush(QColor("#f4c9a8"))
        painter.drawPolygon(QPolygonF([QPointF(42, 54), QPointF(45, 10), QPointF(78, 39)]))
        painter.drawPolygon(QPolygonF([QPointF(112, 39), QPointF(146, 10), QPointF(149, 55)]))
        painter.setBrush(QColor("#fff0dc"))
        painter.drawEllipse(QRectF(34, 34, 124, 112))
        painter.setBrush(QColor("#f3c5a6"))
        painter.drawEllipse(QRectF(49, 126, 94, 43))
        emotion_kind = self._emotion_kind()
        if emotion_kind in ("happy", "shy"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(235, 142, 151, 150))
            painter.drawEllipse(QRectF(48, 96, 22, 9))
            painter.drawEllipse(QRectF(126, 96, 22, 9))
        painter.setPen(QPen(QColor("#594a63"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if emotion_kind == "angry":
            painter.drawLine(67, 75, 80, 81)
            painter.drawLine(112, 81, 126, 75)
        elif emotion_kind == "sad":
            painter.drawLine(67, 80, 80, 75)
            painter.drawLine(112, 75, 126, 80)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#594a63"))
        blink = self._state == "聆听中" and frame % 25 in (0, 1)
        if blink:
            painter.setPen(QPen(QColor("#594a63"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(67, 87, 78, 87)
            painter.drawLine(114, 87, 125, 87)
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            painter.drawEllipse(QRectF(68, 80, 9, 13))
            painter.drawEllipse(QRectF(115, 80, 9, 13))
        painter.setPen(QPen(QColor("#b96f79"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if emotion_kind == "surprised":
            painter.setBrush(QColor("#b96f79"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(91, 103, 11, 14))
        elif self._state == "正在说话" and frame % 2 == 0:
            painter.setBrush(QColor("#b96f79"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(91, 105, 11, 10))
        else:
            mouth = QPainterPath()
            mouth.moveTo(91, 105)
            if emotion_kind == "sad":
                mouth.quadTo(96, 99, 101, 105)
            elif emotion_kind == "happy":
                mouth.quadTo(96, 116, 101, 105)
            else:
                mouth.quadTo(96, 112, 101, 105)
            painter.drawPath(mouth)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.globalPosition().toPoint()
            self._drag_distance = 0
            self.drag_started.emit(self._press_position)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current_position = event.globalPosition().toPoint()
            self._drag_distance = max(
                self._drag_distance,
                (current_position - self._press_position).manhattanLength(),
            )
            self.drag_moved.emit(current_position)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drag_distance < 8:
                self.clicked.emit()
            self._press_position = None
        super().mouseReleaseEvent(event)


class ChatWindow(QDialog):
    def __init__(self, bot_name, title, width, height, font_size, input_font_size, skip_tts, bridge, enabled=True):
        super().__init__()
        self._bot_name = bot_name
        self._skip_tts = skip_tts
        self._chat_enabled = enabled
        self._ready = False
        self.setWindowTitle(title)
        self.resize(width, height)
        self.setMinimumSize(420, 300)
        self._transcript = QTextBrowser(self)
        self._transcript.setOpenExternalLinks(False)
        self._transcript.setFont(_ui_font(font_size))
        self._transcript.setStyleSheet(
            "QTextBrowser { background: #fbf9fc; border: 1px solid #e7e0eb; "
            "border-radius: 10px; padding: 10px; }"
        )
        self._input = QLineEdit(self)
        self._input.setFont(_ui_font(input_font_size))
        self._input.returnPressed.connect(self._send_message)
        self._send_button = QPushButton("发送", self)
        self._send_button.clicked.connect(self._send_message)
        input_row = QHBoxLayout()
        input_row.addWidget(self._input, 1)
        input_row.addWidget(self._send_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self._transcript, 1)
        layout.addLayout(input_row)
        bridge.user_message.connect(self._append_user_message)
        bridge.bot_message.connect(self._append_bot_message)
        self.set_runtime_ready(False)

    def apply_settings(self, config):
        self._skip_tts = config.skip_tts
        self.setWindowTitle(config.title)
        self.resize(config.width, config.height)
        self._transcript.setFont(_ui_font(config.font_size))
        self._input.setFont(_ui_font(config.input_font_size))
        self.set_chat_enabled(config.enable)

    def is_chat_enabled(self):
        return self._chat_enabled

    def set_chat_enabled(self, enabled):
        self._chat_enabled = enabled
        self._refresh_input_state()

    def set_runtime_ready(self, ready):
        self._ready = ready
        self._refresh_input_state()

    def _refresh_input_state(self):
        active = self._ready and self._chat_enabled
        self._input.setEnabled(active)
        self._send_button.setEnabled(active)
        if not self._chat_enabled:
            self._input.setPlaceholderText("聊天窗口已禁用")
        elif self._ready:
            self._input.setPlaceholderText("和桌宠说点什么…")
        else:
            self._input.setPlaceholderText("桌宠正在启动…")

    def _send_message(self):
        message = self._input.text().strip()
        if not message or not self._ready or not self._chat_enabled:
            return
        self._input.clear()
        # emitter.emit 只做入队（同步回调进线程池、协程进事件循环），本身不阻塞，
        # 因此不需要为每条消息新起一个线程。
        emitter.emit(PipelineASREvent(
            prediction=ASRPrediction(transcript=message),
            skip_tts=self._skip_tts,
            source="text_gui",
        ))

    def _append_message(self, speaker, message, color):
        safe_speaker = html.escape(speaker)
        safe_message = html.escape(message).replace("\n", "<br>")
        self._transcript.append(
            f'<p style="margin: 6px 0; color: {color};"><b>{safe_speaker}:</b> {safe_message}</p>'
        )

    def _append_user_message(self, message):
        self._append_message("你", message, "#3974ad")

    def _append_bot_message(self, message):
        self._append_message(self._bot_name, message, "#43805e")

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class SpeechBubble(QWidget):
    def __init__(self, parent=None):
        flags = (
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._message = QLabel(self)
        self._message.setWordWrap(True)
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setMaximumWidth(320)
        self._message.setStyleSheet(
            "QLabel { color: #443b4a; background: #fffefb; "
            "border: 1px solid #d9cde3; border-radius: 12px; padding: 10px 12px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._message)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_message(self, message, pet_geometry, offset_x, offset_y):
        message = message.strip()
        if not message:
            return
        if len(message) > 300:
            message = message[:297] + "…"
        self._message.setText(message)
        self.adjustSize()

        screen = QApplication.screenAt(pet_geometry.center()) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else pet_geometry
        x = pet_geometry.left() - self.width() - 12 + offset_x
        y = pet_geometry.top() + 24 + offset_y
        max_x = max(available.left(), available.right() - self.width() + 1)
        max_y = max(available.top(), available.bottom() - self.height() + 1)
        x = min(max(x, available.left()), max_x)
        y = min(max(y, available.top()), max_y)
        self.move(x, y)
        self.show()
        self.raise_()
        self._hide_timer.start(min(12000, max(4500, len(message) * 35)))


class DesktopSettingsDialog(QDialog):
    def __init__(self, desktop_config, chat_config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("桌宠设置")
        self.setMinimumWidth(360)
        self._shell_enabled = QCheckBox("启用桌宠界面")
        self._shell_enabled.setChecked(desktop_config.enable)
        self._always_on_top = QCheckBox("桌宠窗口始终置顶")
        self._always_on_top.setChecked(desktop_config.always_on_top)
        self._bubble_offset_x = self._number_field(desktop_config.bubble_offset_x, -2400, 2400)
        self._bubble_offset_y = self._number_field(desktop_config.bubble_offset_y, -1600, 1600)
        self._chat_enabled = QCheckBox("启用聊天窗口")
        self._chat_enabled.setChecked(chat_config.enable)
        self._title = QLineEdit(chat_config.title)
        self._width = self._number_field(chat_config.width, 420, 2400)
        self._height = self._number_field(chat_config.height, 300, 1600)
        self._font_size = self._number_field(chat_config.font_size, 8, 36)
        self._input_font_size = self._number_field(chat_config.input_font_size, 8, 36)
        self._skip_tts = QCheckBox("文字聊天时不播放语音")
        self._skip_tts.setChecked(chat_config.skip_tts)
        self._reset_pet_position = QCheckBox("保存后把桌宠移回默认位置（主屏右下角）")

        form = QFormLayout()
        form.addRow("桌宠", self._shell_enabled)
        form.addRow("窗口", self._always_on_top)
        self._bubble_offset_x.setToolTip("相对默认位置微调：正数向右，负数向左（像素）")
        self._bubble_offset_y.setToolTip("相对默认位置微调：正数向下，负数向上（像素）")
        form.addRow("气泡水平微调（像素）", self._bubble_offset_x)
        form.addRow("气泡垂直微调（像素）", self._bubble_offset_y)
        form.addRow("位置", self._reset_pet_position)
        form.addRow("聊天", self._chat_enabled)
        form.addRow("聊天标题", self._title)
        form.addRow("窗口宽度", self._width)
        form.addRow("窗口高度", self._height)
        form.addRow("对话字号", self._font_size)
        form.addRow("输入字号", self._input_font_size)
        form.addRow("语音", self._skip_tts)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        reset_button = buttons.addButton("回到默认位置", QDialogButtonBox.ButtonRole.ResetRole)
        reset_button.setToolTip("把桌宠移回主屏右下角（保存设置后生效）")
        reset_button.clicked.connect(self._request_default_position)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _request_default_position(self):
        self._reset_pet_position.setChecked(True)

    @property
    def reset_position_requested(self) -> bool:
        return self._reset_pet_position.isChecked()

    @staticmethod
    def _number_field(value, minimum, maximum):
        field = QSpinBox()
        field.setRange(minimum, maximum)
        field.setValue(value)
        return field

    def values(self):
        return {
            "desktop_shell": {
                "enable": self._shell_enabled.isChecked(),
                "always_on_top": self._always_on_top.isChecked(),
                "bubble_offset_x": self._bubble_offset_x.value(),
                "bubble_offset_y": self._bubble_offset_y.value(),
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


class PetWindow(QWidget):
    def __init__(
        self, bot_name, chat_window, bridge, request_exit, open_settings,
        always_on_top, bubble_offset_x, bubble_offset_y,
    ):
        super().__init__()
        self._chat_window = chat_window
        self._request_exit = request_exit
        self._open_settings = open_settings
        self._bubble_offset_x = bubble_offset_x
        self._bubble_offset_y = bubble_offset_y
        self._drag_offset = None
        self._allow_close = False
        self._state_timer = QTimer(self)
        self._state_timer.setSingleShot(True)
        self._state_timer.timeout.connect(self._reset_transient_state)
        self._has_tray = False
        self.setWindowTitle(bot_name)
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(224, 282)
        self._speech_bubble = SpeechBubble(self)

        self._mascot = MascotCanvas()
        self._status = QLabel("准备启动…", self)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet(
            "QLabel { color: #594a63; background: rgba(255, 255, 255, 220); "
            "border-radius: 10px; padding: 4px 8px; }"
        )
        self._chat_button = QPushButton("打开聊天", self)
        self._chat_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chat_button.setStyleSheet(
            "QPushButton { color: white; background: #9278b8; border: none; "
            "border-radius: 13px; padding: 7px 18px; } "
            "QPushButton:hover { background: #8064a8; }"
        )
        self._chat_button.clicked.connect(self.open_chat)
        self._mascot.clicked.connect(self.open_chat)
        self._mascot.drag_started.connect(self._start_drag)
        self._mascot.drag_moved.connect(self._move_drag)
        bridge.state_changed.connect(self.set_state)
        bridge.emotion_changed.connect(self._mascot.set_emotion)
        bridge.bot_error.connect(self.show_error)
        bridge.bot_message.connect(self._show_bot_message)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 2, 12, 6)
        layout.setSpacing(2)
        layout.addWidget(self._mascot, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)
        layout.addWidget(self._chat_button, 0, Qt.AlignmentFlag.AlignCenter)
        self._settings_button = QPushButton("设置", self)
        self._settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_button.clicked.connect(self._open_settings)
        layout.addWidget(self._settings_button, 0, Qt.AlignmentFlag.AlignCenter)
        self._chat_button.setEnabled(self._chat_window.is_chat_enabled())

    def set_state(self, state):
        self._status.setText(state)
        self._mascot.set_state(state)
        self._state_timer.stop()
        if state == "已回复":
            self._state_timer.start(3500)

    def _reset_transient_state(self):
        if self._status.text() == "已回复":
            self.set_state("已就绪")

    def show_error(self, message):
        self.set_state("遇到问题")
        logger.error("Desktop Bot error: {}", message)

    def _start_drag(self, global_position):
        self._drag_offset = global_position - self.frameGeometry().topLeft()

    def _move_drag(self, global_position):
        if self._drag_offset is not None:
            self.move(global_position - self._drag_offset)

    def open_chat(self):
        if not self._chat_window.is_chat_enabled():
            self.set_state("聊天窗口已关闭")
            return
        self._speech_bubble.hide()
        self._chat_window.show()
        self._chat_window.raise_()
        self._chat_window.activateWindow()

    def _show_bot_message(self, message):
        if self.isVisible() and not self._chat_window.isVisible():
            self._speech_bubble.show_message(
                message,
                self.frameGeometry(),
                self._bubble_offset_x,
                self._bubble_offset_y,
            )

    def set_bubble_offset(self, offset_x, offset_y):
        self._bubble_offset_x = offset_x
        self._bubble_offset_y = offset_y

    def set_chat_enabled(self, enabled):
        self._chat_button.setEnabled(enabled)
        if not enabled:
            self._chat_window.hide()
        elif self._status.text() == "聊天窗口已关闭":
            self.set_state("已就绪")

    def set_always_on_top(self, enabled):
        was_visible = self.isVisible()
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def contextMenuEvent(self, event):
        self._speech_bubble.hide()
        menu = QMenu(self)
        open_chat = menu.addAction("打开聊天")
        settings = menu.addAction("设置")
        toggle_visible = menu.addAction("隐藏桌宠")
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        selected = menu.exec(event.globalPos())
        if selected == open_chat:
            self.open_chat()
        elif selected == settings:
            self._open_settings()
        elif selected == toggle_visible:
            self.hide()
        elif selected == quit_action:
            self._request_exit()

    def hideEvent(self, event):
        self._speech_bubble.hide()
        super().hideEvent(event)

    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
        elif self._has_tray:
            event.ignore()
            self.hide()
        else:
            event.accept()
            self._request_exit()


class DesktopShell(QObject):
    def __init__(self, bot_name, title, width, height, font_size, input_font_size, skip_tts, always_on_top):
        super().__init__()
        self._app = QApplication.instance() or QApplication(sys.argv[:1])
        self._app.setQuitOnLastWindowClosed(False)
        self._bridge = BotUiBridge()
        self._runner = BotRunner()
        self._exiting = False
        self._restart_after_stop = False
        self._restart_launched = False
        desktop_config = get_config().service.desktop_shell
        # 退出看门狗：只有收尾彻底卡死才会开火，正常收尾会在 _on_bot_finished 里取消。
        self._exit_grace_ms = int(float(getattr(desktop_config, "exit_grace_seconds", 45.0)) * 1000)
        self._exit_watchdog = QTimer(self)
        self._exit_watchdog.setSingleShot(True)
        self._exit_watchdog.timeout.connect(self._force_exit_on_timeout)
        self._chat_window = ChatWindow(
            bot_name, title, width, height, font_size, input_font_size, skip_tts, self._bridge,
            enabled=get_config().service.chat_window.enable,
        )
        self._pet_window = PetWindow(
            bot_name, self._chat_window, self._bridge, self.request_exit,
            self.open_settings, always_on_top,
            desktop_config.bubble_offset_x, desktop_config.bubble_offset_y,
        )
        self._tray = None
        self._create_tray(bot_name)
        self._restore_pet_position(desktop_config)
        self._runner.ready.connect(self._on_bot_ready)
        self._runner.failed.connect(self._bridge.report_error)
        self._runner.finished.connect(self._on_bot_finished)
        self._pet_window.show()
        self._runner.start()

    def _default_pet_position(self):
        """桌宠默认位置：主屏右下角内缩 24 像素。没有屏幕信息时返回 None。"""
        screen = self._app.primaryScreen()
        if screen is None:
            return None
        available = screen.availableGeometry()
        return (
            available.right() - self._pet_window.width() - 24,
            available.bottom() - self._pet_window.height() - 24,
        )

    def _move_pet_to_default_position(self):
        """把桌宠移回默认位置（设置对话框的"回到默认位置"按钮）。"""
        position = self._default_pet_position()
        if position is None:
            return False
        self._pet_window.move(*position)
        logger.info(f"桌宠已回到默认位置：({position[0]}, {position[1]})")
        return True

    def _restore_pet_position(self, desktop_config):
        """恢复上次保存的桌宠位置；没有记录或已不在可见范围内时落到主屏右下角。"""
        saved_x = int(getattr(desktop_config, "pet_x", -1))
        saved_y = int(getattr(desktop_config, "pet_y", -1))
        if saved_x >= 0 and saved_y >= 0:
            if self._position_visible(saved_x, saved_y):
                self._pet_window.move(saved_x, saved_y)
                logger.info(f"已恢复桌宠位置：({saved_x}, {saved_y})")
                return
            logger.warning(
                f"保存的桌宠位置 ({saved_x}, {saved_y}) 不在任何屏幕的可见范围内，改回默认位置。"
            )
        self._move_pet_to_default_position()

    def _position_visible(self, x: int, y: int) -> bool:
        """坐标是否与任一屏幕的可用区域相交（拔掉外接屏后不至于把桌宠留在屏幕外）。"""
        width = self._pet_window.width()
        height = self._pet_window.height()
        for screen in self._app.screens():
            area = screen.availableGeometry()
            if x + width > area.left() and x < area.right() and y + height > area.top() and y < area.bottom():
                return True
        return False

    def _save_pet_position(self):
        """退出时保存桌宠位置（只在退出时写，避免频繁改动 config.yaml）。"""
        try:
            config = get_config()
            position = self._pet_window.pos()
            desktop_config = DesktopShellConfig.model_validate({
                **config.service.desktop_shell.model_dump(),
                "pet_x": int(position.x()),
                "pet_y": int(position.y()),
            })
            save_desktop_settings(desktop_config, config.service.chat_window)
            logger.info(f"已保存桌宠位置：({position.x()}, {position.y()})")
        except Exception as exc:
            logger.warning(f"保存桌宠位置失败（不影响退出）：{exc}")

    def _pet_is_topmost(self) -> bool:
        """桌宠当前是否处于“始终置顶”状态。"""
        return bool(self._pet_window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def show_popup(self, popup, *, modal=Qt.WindowModality.ApplicationModal):
        """所有弹窗（对话框、消息框、以后新增的）的统一入口。

        顺序不能改：Qt 的 setWindowModality() 会重建窗口标志，先把
        WindowStaysOnTopHint 设上去再改模态，置顶标志会被静默丢掉——桌宠
        开着“始终置顶”时，弹窗就会被压在猫猫下面。所以模态先设、置顶后设。
        """
        popup.setWindowModality(modal)
        flags = popup.windowFlags()
        if self._pet_is_topmost():
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        popup.setWindowFlags(flags)
        return popup.exec()

    def open_settings(self):
        self._pet_window._speech_bubble.hide()
        config = get_config()
        dialog = DesktopSettingsDialog(
            config.service.desktop_shell, config.service.chat_window, self._pet_window
        )
        if self.show_popup(dialog) != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values["chat_window"]["title"]:
            self._show_message_box(
                QMessageBox.Icon.Warning, "标题无效", "聊天窗口标题不能为空。"
            )
            return
        try:
            desktop_config = DesktopShellConfig.model_validate({
                **config.service.desktop_shell.model_dump(), **values["desktop_shell"]
            })
            chat_config = ChatWindowConfig.model_validate({
                **config.service.chat_window.model_dump(), **values["chat_window"]
            })
            restart_required = desktop_config.enable != config.service.desktop_shell.enable
            save_desktop_settings(desktop_config, chat_config)
        except Exception as exc:
            logger.exception("Unable to save desktop settings")
            self._show_message_box(
                QMessageBox.Icon.Critical, "保存失败", f"配置未保存：{exc}"
            )
            return

        self._chat_window.apply_settings(chat_config)
        self._pet_window.set_chat_enabled(chat_config.enable)
        self._pet_window.set_bubble_offset(
            desktop_config.bubble_offset_x, desktop_config.bubble_offset_y
        )
        self._pet_window.set_always_on_top(desktop_config.always_on_top)
        if getattr(dialog, "reset_position_requested", False):
            self._move_pet_to_default_position()
        if restart_required:
            answer = self._show_message_box(
                QMessageBox.Icon.Question,
                "需要重启",
                "桌宠启停设置已保存。是否现在重启程序以应用？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._restart_after_stop = True
                self.request_exit()
            else:
                self._show_message_box(
                    QMessageBox.Icon.Information,
                    "设置已保存",
                    "其他设置已即时生效；桌宠启停将在下次启动时生效。",
                )
        else:
            self._show_message_box(
                QMessageBox.Icon.Information, "设置已保存", "所有更改已即时生效。"
            )

    def _show_message_box(
        self, icon, title, text, buttons=QMessageBox.StandardButton.Ok, default_button=None
    ):
        message_box = QMessageBox(icon, title, text, buttons, self._pet_window)
        if default_button is not None:
            message_box.setDefaultButton(default_button)
        return self.show_popup(message_box)

    def _create_tray(self, bot_name):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self._create_tray_icon(), self._pet_window)
        self._pet_window._has_tray = True
        self._tray.setToolTip(bot_name)
        menu = QMenu()
        open_chat = QAction("打开聊天", menu)
        open_chat.triggered.connect(self._pet_window.open_chat)
        show_pet = QAction("显示桌宠", menu)
        show_pet.triggered.connect(self._pet_window.show)
        hide_pet = QAction("隐藏桌宠", menu)
        hide_pet.triggered.connect(self._pet_window.hide)
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.request_exit)
        menu.addAction(open_chat)
        menu.addAction(show_pet)
        menu.addAction(hide_pet)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    @staticmethod
    def _create_tray_icon():
        pixmap = QPixmap(48, 48)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#594a63"), 2))
        painter.setBrush(QColor("#fff0dc"))
        painter.drawEllipse(QRectF(6, 10, 36, 34))
        painter.drawPolygon(QPolygonF([QPointF(10, 21), QPointF(12, 4), QPointF(23, 14)]))
        painter.drawPolygon(QPolygonF([QPointF(25, 14), QPointF(37, 4), QPointF(39, 21)]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#594a63"))
        painter.drawEllipse(QRectF(16, 24, 3, 4))
        painter.drawEllipse(QRectF(29, 24, 3, 4))
        painter.end()
        return QIcon(pixmap)

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._pet_window.show()
            self._pet_window.raise_()

    def _on_bot_ready(self):
        self._chat_window.set_runtime_ready(True)
        self._pet_window.set_state("已就绪")

    def _on_bot_finished(self):
        if self._exiting:
            self._exit_watchdog.stop()
            self._bridge.close()
            self._launch_restart()
            self._app.quit()

    def _force_exit_on_timeout(self):
        logger.error(
            f"⏱️ 退出收尾超过 {self._exit_grace_ms / 1000:.0f} 秒仍未完成，强制结束进程。"
            "（通常说明有线程卡在无法中断的调用里；最后一次记忆快照可能未落盘）"
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    def _launch_restart(self):
        if not self._restart_after_stop or self._restart_launched:
            return
        self._restart_launched = True
        try:
            subprocess.Popen([sys.executable, *sys.argv], cwd=os.getcwd())
        except Exception as exc:
            logger.exception("Unable to restart desktop application")
            self._show_message_box(
                QMessageBox.Icon.Critical,
                "重启失败",
                f"自动重启失败，请手动重新启动程序：{exc}",
            )

    def request_exit(self):
        if self._exiting:
            return
        self._exiting = True
        # 只在退出时记录桌宠位置（拖动过程中不写配置）
        self._save_pet_position()
        self._pet_window._allow_close = True
        self._chat_window.hide()
        self._pet_window._speech_bubble.hide()
        self._pet_window.hide()
        if self._tray is not None:
            self._tray.hide()
        self._runner.request_stop()
        if not self._runner.is_running():
            self._bridge.close()
            self._launch_restart()
            self._app.quit()
            return
        # 收尾可能卡在无法中断的线程里（界面已经全隐藏，用户无法再操作），
        # 因此起一个一次性看门狗：正常收尾会取消它，超时才强退。
        logger.info(
            f"正在收尾（含最后一次记忆快照落盘），最长等待 {self._exit_grace_ms / 1000:.0f} 秒。"
        )
        self._exit_watchdog.start(self._exit_grace_ms)

    def exec(self):
        return self._app.exec()


def run_desktop_shell(bot_name, title, width, height, font_size, input_font_size, skip_tts, always_on_top=True):
    shell = DesktopShell(
        bot_name, title, width, height, font_size, input_font_size, skip_tts, always_on_top
    )
    return shell.exec()