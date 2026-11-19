"""桌宠多后端形象渲染基类与宿主画布组件。"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QVBoxLayout, QWidget


def normalize_emotion_kind(emotion: str | None) -> str:
    """将各种中英文情绪标签归一化为标准 UI 表情类别。"""
    text = (emotion or "Default").strip().casefold()
    if any(tag in text for tag in ("开心", "高兴", "喜悦", "快乐", "happy", "joy", "excited", "love", "喜欢")):
        return "happy"
    if any(tag in text for tag in ("难过", "悲伤", "伤心", "委屈", "sad", "sorrow", "cry")):
        return "sad"
    if any(tag in text for tag in ("生气", "愤怒", "生气气", "angry", "anger", "mad")):
        return "angry"
    if any(tag in text for tag in ("害羞", "羞", "脸红", "shy", "embarrass")):
        return "shy"
    if any(tag in text for tag in ("惊讶", "震惊", "surprise", "惊")):
        return "surprised"
    return "neutral"


def normalize_state_key(state: str | None) -> str:
    """将界面显示状态映射为统一状态键，供外部立绘与 Live2D 动作匹配。"""
    text = (state or "").strip()
    mapping = {
        "待命中": "idle",
        "已就绪": "idle",
        "准备启动…": "idle",
        "聆听中": "listening",
        "思考中": "thinking",
        "正在说话": "speaking",
        "已回复": "replied",
        "遇到问题": "error",
        "聊天窗口已关闭": "idle",
    }
    return mapping.get(text, "idle")


def draw_ground_shadow(
    painter: QPainter,
    ellipse_rect: QRectF,
    *,
    center: QPointF | None = None,
    radius: float = 60.0,
    alpha: int = 70,
) -> None:
    """绘制桌宠底部的柔和地面投影。"""
    c = center if center is not None else ellipse_rect.center()
    shadow = QRadialGradient(c.x(), c.y(), radius)
    shadow.setColorAt(0.0, QColor(84, 70, 96, alpha))
    shadow.setColorAt(1.0, QColor(84, 70, 96, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(shadow))
    painter.drawEllipse(ellipse_rect)


def draw_thinking_halo(
    painter: QPainter,
    center: QPointF,
    radius: float,
    frame: int,
    *,
    rx: float | None = None,
    ry: float | None = None,
) -> None:
    """绘制思考状态下的柔和紫色脉冲光环。"""
    pulse = 40 + int((math.sin(frame * 0.35) + 1.0) * 22)
    halo = QRadialGradient(center.x(), center.y(), radius)
    halo.setColorAt(0.6, QColor(152, 126, 206, 0))
    halo.setColorAt(0.86, QColor(152, 126, 206, pulse))
    halo.setColorAt(1.0, QColor(152, 126, 206, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(halo))
    painter.drawEllipse(center, rx if rx is not None else radius, ry if ry is not None else radius)


def draw_state_badge(
    painter: QPainter,
    rect: QRectF,
    frame: int,
    state: str,
    *,
    top_y: float = 12.0,
) -> None:
    """在右上角绘制思考/聆听/报错状态徽章（矢量与立绘模式共用）。"""
    if state not in ("思考中", "聆听中", "遇到问题"):
        return
    bx = rect.width() - 40.0
    painter.save()
    if state == "思考中":
        step = frame % 4
        painter.setPen(QPen(QColor("#8a74b0"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QColor(255, 253, 249, 240))
        painter.drawEllipse(QRectF(bx - 2.0, top_y + 38.0, 6.0, 6.0))
        painter.drawEllipse(QRectF(bx + 4.0, top_y + 25.0, 9.0, 9.0))
        painter.drawRoundedRect(QRectF(bx, top_y, 36.0, 20.0), 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        for idx in range(3):
            painter.setBrush(QColor("#8a74b0") if idx <= step else QColor("#d3c8e4"))
            painter.drawEllipse(QRectF(bx + 8.0 + idx * 8.0, top_y + 8.0, 4.0, 4.0))
    elif state == "聆听中":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = rect.width() - 24.0, top_y + 14.0
        for idx in range(3):
            alpha = 200 if (frame // 2) % 3 >= idx else 60
            painter.setPen(QPen(QColor(77, 150, 118, alpha), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            r = 5.0 + idx * 4.5
            painter.drawArc(QRectF(cx - r, cy - r, r * 2.0, r * 2.0), -40 * 16, 80 * 16)
    elif state == "遇到问题":
        painter.setPen(QPen(QColor("#c8525d"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(QColor("#fdecef"))
        ex = rect.width() - 34.0
        painter.drawEllipse(QRectF(ex, top_y, 24.0, 24.0))
        painter.drawLine(QPointF(ex + 12.0, top_y + 5.0), QPointF(ex + 12.0, top_y + 14.0))
        painter.drawPoint(QPointF(ex + 12.0, top_y + 19.0))
    painter.restore()


class MascotRendererBase:
    """桌宠渲染器抽象接口（矢量 / 外部立绘 / Live2D 共用）。"""

    mode_name: str = "base"

    def __init__(self) -> None:
        self._state: str = "待命中"
        self._emotion: str = "Default"
        self._frame: int = 0
        self._interaction_ticks: int = 0
        self._update_callback = None

    def set_update_callback(self, callback) -> None:
        self._update_callback = callback

    def request_repaint(self) -> None:
        if callable(self._update_callback):
            self._update_callback()

    def is_available(self) -> bool:
        return True

    def fallback_reason(self) -> str:
        return ""

    def embedded_widget(self) -> QWidget | None:
        """若后端使用原生子控件（如 Live2D OpenGL 视图），返回该控件。"""
        return None

    def set_state(self, state: str) -> None:
        self._state = state or "待命中"

    def set_emotion(self, emotion: str) -> None:
        self._emotion = (emotion or "Default").strip() or "Default"

    def emotion_kind(self) -> str:
        return normalize_emotion_kind(self._emotion)

    def state_key(self) -> str:
        return normalize_state_key(self._state)

    def trigger_interaction(self) -> None:
        self._interaction_ticks = 10

    def tick(self, frame: int) -> None:
        self._frame = frame
        if self._interaction_ticks > 0:
            self._interaction_ticks -= 1

    def paint(self, painter: QPainter, logical_rect: QRectF) -> None:
        """在逻辑坐标系（190×178）内执行绘制。"""
        raise NotImplementedError

    def cleanup(self) -> None:
        pass


class MascotCanvas(QWidget):
    """桌宠画布容器：托管拖拽/点击手势、多后端切换与自动平滑降级。"""

    BASE_WIDTH = 190
    BASE_HEIGHT = 178

    drag_started = Signal(QPoint)
    drag_moved = Signal(QPoint)
    clicked = Signal()

    def __init__(
        self,
        parent=None,
        *,
        avatar_mode: str | None = "vector",
        avatar_path: str = "",
        scale: float = 1.0,
    ) -> None:
        super().__init__(parent)
        self._state = "待命中"
        self._emotion = "Default"
        self._requested_mode = "vector"
        self._active_mode = "vector"
        self._avatar_path = ""
        self._scale = min(2.0, max(0.5, float(scale or 1.0)))
        self._fallback_message = ""
        self._press_position: QPoint | None = None
        self._drag_distance = 0
        self._animation_frame = 0

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._renderer: MascotRendererBase | None = None
        self._embedded_widget: QWidget | None = None

        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._animation_timer.start(140)

        self.setFixedSize(
            max(95, round(self.BASE_WIDTH * self._scale)),
            max(89, round(self.BASE_HEIGHT * self._scale)),
        )
        if avatar_mode is not None:
            self.set_avatar_config(avatar_mode, avatar_path, self._scale)

    @property
    def active_mode(self) -> str:
        return self._active_mode

    @property
    def requested_mode(self) -> str:
        return self._requested_mode

    @property
    def avatar_path(self) -> str:
        return self._avatar_path

    @property
    def fallback_message(self) -> str:
        return self._fallback_message

    def _create_renderer(self, mode: str, path: str) -> MascotRendererBase:
        if mode == "sprite":
            from ui.mascot.sprite_pack import SpritePackRenderer

            return SpritePackRenderer(path)
        if mode == "live2d":
            from ui.mascot.live2d_view import Live2DRenderer

            return Live2DRenderer(path)
        from ui.mascot.vector_catgirl import VectorCatgirlRenderer

        return VectorCatgirlRenderer()

    def set_avatar_config(self, mode: str = "vector", path: str = "", scale: float = 1.0) -> None:
        normalized_mode = (mode or "vector").strip().lower()
        if normalized_mode not in ("vector", "sprite", "live2d"):
            normalized_mode = "vector"
        clamped_scale = min(2.0, max(0.5, float(scale or 1.0)))
        clean_path = (path or "").strip()

        same_backend = (
            self._renderer is not None
            and normalized_mode == self._requested_mode
            and clean_path == self._avatar_path
        )
        self._scale = clamped_scale
        self.setFixedSize(
            max(95, round(self.BASE_WIDTH * self._scale)),
            max(89, round(self.BASE_HEIGHT * self._scale)),
        )
        if same_backend:
            self.update()
            return

        self._requested_mode = normalized_mode
        self._avatar_path = clean_path
        self._install_renderer(normalized_mode, clean_path)

    def _install_renderer(self, mode: str, path: str) -> None:
        from loguru import logger
        from ui.mascot.vector_catgirl import VectorCatgirlRenderer

        if self._embedded_widget is not None:
            self._layout.removeWidget(self._embedded_widget)
            self._embedded_widget.deleteLater()
            self._embedded_widget = None

        if self._renderer is not None:
            self._renderer.cleanup()

        candidate = self._create_renderer(mode, path)
        self._fallback_message = ""
        if not candidate.is_available():
            reason = candidate.fallback_reason() or f"{mode} 模式不可用，已回退默认矢量猫娘"
            candidate.cleanup()
            logger.warning("桌宠渲染器回退（{}）：{}", mode, reason)
            self._fallback_message = reason
            candidate = VectorCatgirlRenderer()
            self._active_mode = "vector"
        else:
            self._active_mode = candidate.mode_name

        candidate.set_update_callback(self.update)
        candidate.set_state(self._state)
        candidate.set_emotion(self._emotion)
        self._renderer = candidate

        embedded = candidate.embedded_widget()
        if embedded is not None:
            embedded.setParent(self)
            embedded.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._layout.addWidget(embedded)
            self._embedded_widget = embedded

        self.update()

    def _advance_animation(self) -> None:
        self._animation_frame += 1
        if self._renderer is not None:
            self._renderer.tick(self._animation_frame)
        self.update()

    def set_state(self, state: str) -> None:
        self._state = state or "待命中"
        if self._state in ("聆听中", "思考中", "正在说话"):
            self._animation_timer.setInterval(85)
        else:
            self._animation_timer.setInterval(140)
        if self._renderer is not None:
            self._renderer.set_state(self._state)
        self.update()

    def set_emotion(self, emotion: str) -> None:
        self._emotion = (emotion or "Default").strip() or "Default"
        if self._renderer is not None:
            self._renderer.set_emotion(self._emotion)
        self.update()

    def _emotion_kind(self) -> str:
        return normalize_emotion_kind(self._emotion)

    def paintEvent(self, event) -> None:
        if self._embedded_widget is not None or self._renderer is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if not math.isclose(self._scale, 1.0):
            painter.scale(self._scale, self._scale)
        self._renderer.paint(painter, QRectF(0, 0, self.BASE_WIDTH, self.BASE_HEIGHT))
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.globalPosition().toPoint()
            self._drag_distance = 0
            self.drag_started.emit(self._press_position)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current_position = event.globalPosition().toPoint()
            self._drag_distance = max(
                self._drag_distance,
                (current_position - self._press_position).manhattanLength(),
            )
            self.drag_moved.emit(current_position)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drag_distance < 8:
                if self._renderer is not None:
                    self._renderer.trigger_interaction()
                self.clicked.emit()
            self._press_position = None
        super().mouseReleaseEvent(event)

    def cleanup(self) -> None:
        self._animation_timer.stop()
        if self._renderer is not None:
            self._renderer.cleanup()
            self._renderer = None

    def closeEvent(self, event) -> None:
        self.cleanup()
        super().closeEvent(event)
