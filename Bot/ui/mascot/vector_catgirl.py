"""默认 Q 版矢量猫娘“粽子”渲染器（零外部资源依赖）。

画风参考 Q 版设定图：大头身比、渐变大猫眼与多层高光、按色相着色的柔和描边、
毛绒猫耳、柔顺长卷发与呆毛；人设元素保持“粽子”——米白糯米色长发、粽叶绿毛绒披肩、
小粽子发饰、紫色蝴蝶结、金色铃铛、蓬松猫尾与小虎牙。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

from ui.mascot.base import (
    MascotRendererBase,
    draw_ground_shadow,
    draw_state_badge,
    draw_thinking_halo,
)

# ---------------------------------------------------------------- 色板
HAIR_TOP = QColor("#fbf0e1")
HAIR_MID = QColor("#f1dcc2")
HAIR_TIP = QColor("#e2bf9a")
HAIR_SHADE = QColor(196, 150, 112, 70)
LINE_HAIR = QColor("#8e664f")
LINE_SKIN = QColor("#b9806f")
LINE_DARK = QColor("#3a2a2d")
LINE_CLOTH = QColor("#2c5a45")
SKIN_TOP = QColor("#fff7ef")
SKIN_BOTTOM = QColor("#ffe6d6")
EAR_INNER_TOP = QColor("#f6a9b6")
EAR_INNER_BOTTOM = QColor("#fde0e2")
CAPE_TOP = QColor("#76bb97")
CAPE_BOTTOM = QColor("#3f7f62")
FUR = QColor("#fffdf8")
FUR_SHADE = QColor("#e7e4ee")
ACCENT_PURPLE = QColor("#876cab")
IRIS_TOP = QColor("#1d4a3b")
IRIS_MID = QColor("#3c8b67")
IRIS_LOW = QColor("#86cf9c")
IRIS_GLOW = QColor("#e4f7bf")

CX = 95.0  # 画布水平中轴


def _pen(color: QColor, width: float, *, cap=Qt.PenCapStyle.RoundCap) -> QPen:
    return QPen(color, width, Qt.PenStyle.SolidLine, cap, Qt.PenJoinStyle.RoundJoin)


def _mx(x: float, right: bool) -> float:
    """左右镜像：右侧部件由左侧坐标对称得到。"""
    return 2 * CX - x if right else x


def _hair_gradient(y0: float, y1: float) -> QLinearGradient:
    grad = QLinearGradient(0, y0, 0, y1)
    grad.setColorAt(0.0, HAIR_TOP)
    grad.setColorAt(0.55, HAIR_MID)
    grad.setColorAt(1.0, HAIR_TIP)
    return grad


class VectorCatgirlRenderer(MascotRendererBase):
    """Q 版 QPainter 矢量猫娘“粽子”。逻辑坐标系 190×178。"""

    mode_name = "vector"

    # ------------------------------------------------------------ 主流程
    def paint(self, painter: QPainter, logical_rect: QRectF) -> None:
        frame = self._frame
        state = self._state
        emotion = self.emotion_kind()

        breath = math.sin(frame * 0.16)
        body_dy = breath * 1.1
        head_dy = breath * 1.6
        if state == "正在说话":
            head_dy += (-1.8, 0.6, -0.8)[frame % 3]
        elif state == "聆听中":
            head_dy = math.sin(frame * 0.28) * 1.3
        if self._interaction_ticks > 0:
            head_dy -= math.sin(self._interaction_ticks * 0.6) * 3.0

        tilt = 0.0
        if state == "思考中":
            tilt = 4.0 + math.sin(frame * 0.12) * 1.0
        elif state == "聆听中":
            tilt = -3.0
        elif emotion == "sad":
            tilt = -2.0
        if self._interaction_ticks > 0:
            tilt += math.sin(self._interaction_ticks * 0.9) * 4.5

        self._draw_backdrop(painter, frame, state, emotion)

        painter.save()
        painter.translate(0.0, body_dy)
        self._draw_tail(painter, frame, state, emotion)

        # 头部组（后发）先画在身体后方
        painter.save()
        self._apply_head_transform(painter, head_dy - body_dy, tilt)
        self._draw_back_hair(painter, frame)
        painter.restore()

        self._draw_body(painter, frame, state)

        painter.save()
        self._apply_head_transform(painter, head_dy - body_dy, tilt)
        self._draw_ears(painter, frame, state, emotion)
        self._draw_face(painter, emotion)
        self._draw_blush(painter, emotion)
        self._draw_eyes(painter, frame, state, emotion)
        self._draw_mouth(painter, frame, state, emotion)
        self._draw_side_locks(painter, frame)
        self._draw_bangs(painter)
        self._draw_brows(painter, emotion)
        self._draw_ahoge(painter, frame, emotion)
        self._draw_zongzi_clip(painter, frame)
        painter.restore()

        self._draw_paws(painter, frame, state)
        painter.restore()

        self._draw_effects(painter, frame, state, emotion)

    @staticmethod
    def _apply_head_transform(painter: QPainter, dy: float, tilt: float) -> None:
        painter.translate(0.0, dy)
        if tilt:
            painter.translate(CX, 128.0)
            painter.rotate(tilt)
            painter.translate(-CX, -128.0)

    # ------------------------------------------------------------ 背景
    def _draw_backdrop(self, painter: QPainter, frame: int, state: str, emotion: str) -> None:
        painter.save()
        draw_ground_shadow(
            painter,
            QRectF(30, 164, 130, 14),
            center=QPointF(CX, 172),
            radius=60.0,
            alpha=70,
        )

        if state == "思考中":
            draw_thinking_halo(painter, QPointF(CX, 92), 86.0, frame)
        elif state == "聆听中":
            for idx in range(2):
                phase = ((frame * 0.06) + idx * 0.5) % 1.0
                radius = 62 + phase * 26
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(77, 150, 118, int(120 * (1.0 - phase))), 2.0))
                painter.drawEllipse(QPointF(CX, 92), radius, radius * 0.96)
        elif emotion == "happy":
            glow = QRadialGradient(CX, 92, 84)
            glow.setColorAt(0.55, QColor(255, 226, 170, 0))
            glow.setColorAt(0.86, QColor(255, 214, 150, 52))
            glow.setColorAt(1.0, QColor(255, 214, 150, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(QRectF(11, 8, 168, 168))
        painter.restore()

    # ------------------------------------------------------------ 尾巴
    def _draw_tail(self, painter: QPainter, frame: int, state: str, emotion: str) -> None:
        lively = emotion == "happy" or state == "正在说话"
        speed = 0.32 if lively else 0.15
        amp = 7.0 if lively else 4.0
        sway = math.sin(frame * speed) * amp
        droop = 22.0 if emotion == "sad" else 0.0
        puff = 1.25 if emotion in ("angry", "surprised") else 1.0

        path = QPainterPath()
        path.moveTo(136, 158)
        path.cubicTo(162, 166, 180, 150 + droop * 0.4 + sway * 0.3, 176, 126 + droop + sway * 0.6)
        path.cubicTo(173, 108 + droop + sway, 160, 102 + droop + sway, 156, 112 + droop + sway * 0.9)

        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(LINE_HAIR, 13.5 * puff))
        painter.drawPath(path)
        grad = QLinearGradient(136, 160, 176, 110 + droop)
        grad.setColorAt(0.0, HAIR_TIP)
        grad.setColorAt(0.6, HAIR_MID)
        grad.setColorAt(1.0, HAIR_TOP)
        painter.setPen(QPen(QBrush(grad), 10.6 * puff, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)

        # 白色尾尖
        tip = QPainterPath()
        steps = 10
        for i in range(steps + 1):
            point = path.pointAtPercent(0.8 + 0.2 * i / steps)
            if i == 0:
                tip.moveTo(point)
            else:
                tip.lineTo(point)
        painter.setPen(_pen(FUR, 10.6 * puff))
        painter.drawPath(tip)

        # 毛流高光
        shine = QPainterPath()
        for i in range(9):
            point = path.pointAtPercent(0.2 + 0.06 * i)
            if i == 0:
                shine.moveTo(point.x() - 2.2, point.y() - 1.0)
            else:
                shine.lineTo(point.x() - 2.2, point.y() - 1.0)
        painter.setPen(_pen(QColor(255, 255, 255, 120), 2.0))
        painter.drawPath(shine)

        # 尾根粽叶绿蝴蝶结
        bow_pos = path.pointAtPercent(0.16)
        self._draw_bow(painter, bow_pos.x(), bow_pos.y() - 1, 0.62, CAPE_TOP, LINE_CLOTH)
        painter.restore()

    # ------------------------------------------------------------ 头发
    def _draw_back_hair(self, painter: QPainter, frame: int) -> None:
        sway = math.sin(frame * 0.14) * 1.6
        path = QPainterPath()
        path.moveTo(CX, 24)
        path.cubicTo(58, 24, 36, 44, 36, 76)
        path.cubicTo(34, 100, 24, 118, 26, 140 + sway * 0.4)
        # 左侧波浪发梢
        path.quadTo(20 + sway, 156, 32 + sway, 166)
        path.quadTo(36 + sway, 156, 42, 162)
        path.quadTo(46, 170, 56, 168)
        path.lineTo(134, 168)
        path.quadTo(144, 170, 148, 162)
        path.quadTo(154 - sway, 156, 158 - sway, 166)
        path.quadTo(170 - sway, 156, 164, 140 - sway * 0.4)
        path.cubicTo(166, 118, 156, 100, 154, 76)
        path.cubicTo(154, 44, 132, 24, CX, 24)
        path.closeSubpath()

        painter.save()
        painter.setPen(_pen(LINE_HAIR, 2.0))
        painter.setBrush(QBrush(_hair_gradient(24, 168)))
        painter.drawPath(path)
        # 内层阴影：后发更深一点，衬托脸部层次
        painter.setClipPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(HAIR_SHADE)
        painter.drawEllipse(QRectF(42, 60, 106, 120))
        # 发丝线
        painter.setPen(_pen(QColor(142, 102, 79, 90), 1.1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for x0, x1 in ((40, 32), (46, 40), (150, 158), (144, 150)):
            strand = QPainterPath()
            strand.moveTo(x0, 92)
            strand.quadTo(x0 - (4 if x0 < CX else -4), 128, x1, 158)
            painter.drawPath(strand)
        painter.restore()

    def _draw_side_locks(self, painter: QPainter, frame: int) -> None:
        sway = math.sin(frame * 0.14 + 0.6) * 1.4
        painter.save()
        for right in (False, True):
            s = -sway if right else sway
            # 外层主鬓发：顺着脸颊柔和垂落，发尾带微卷S形外翘
            lock = QPainterPath()
            lock.moveTo(_mx(46, right), 74)
            lock.cubicTo(_mx(37, right), 92, _mx(38, right), 108, _mx(39 + s * 0.3, right), 124)
            lock.cubicTo(_mx(35 + s * 0.7, right), 138, _mx(33 + s, right), 150, _mx(41 + s, right), 163)
            lock.quadTo(_mx(42 + s, right), 154, _mx(48 + s * 0.7, right), 148)
            lock.cubicTo(_mx(53 + s * 0.4, right), 136, _mx(52, right), 122, _mx(55, right), 108)
            lock.cubicTo(_mx(57, right), 96, _mx(57, right), 86, _mx(55, right), 78)
            lock.closeSubpath()
            painter.setPen(_pen(LINE_HAIR, 1.9))
            painter.setBrush(QBrush(_hair_gradient(72, 164)))
            painter.drawPath(lock)

            # 贴脸内收碎发小发束（增强 Q 版圆脸层次感）
            wisp = QPainterPath()
            wisp.moveTo(_mx(52, right), 84)
            wisp.cubicTo(_mx(47, right), 100, _mx(46, right), 116, _mx(55, right), 128)
            wisp.cubicTo(_mx(52, right), 116, _mx(53, right), 102, _mx(57, right), 88)
            wisp.closeSubpath()
            painter.setPen(_pen(LINE_HAIR, 1.5))
            painter.setBrush(QBrush(_hair_gradient(84, 128)))
            painter.drawPath(wisp)

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_pen(QColor(142, 102, 79, 105), 1.0))
            strand = QPainterPath()
            strand.moveTo(_mx(49, right), 96)
            strand.quadTo(_mx(46, right), 114, _mx(44 + s * 0.6, right), 134)
            painter.drawPath(strand)

            painter.setPen(_pen(QColor(255, 255, 255, 135), 1.6))
            shine = QPainterPath()
            shine.moveTo(_mx(43 + s * 0.5, right), 126)
            shine.quadTo(_mx(41 + s * 0.8, right), 137, _mx(43 + s, right), 145)
            painter.drawPath(shine)
        painter.restore()

    def _draw_bangs(self, painter: QPainter) -> None:
        path = QPainterPath()
        path.moveTo(42, 90)
        path.cubicTo(34, 42, 66, 23, CX, 23)
        path.cubicTo(124, 23, 156, 42, 148, 90)
        # 柔和弧形内收发束（右 -> 左），中央一缕柔尖垂于眉眼之间
        path.quadTo(144, 82, 138, 94)
        path.quadTo(135, 83, 130, 77)
        path.cubicTo(128, 85, 123, 90, 117, 93)
        path.quadTo(116, 82, 110, 74)
        path.cubicTo(108, 84, 103, 91, 95.5, 97)
        path.cubicTo(88, 91, 82, 84, 80, 74)
        path.quadTo(74, 82, 73, 93)
        path.cubicTo(67, 90, 62, 85, 60, 77)
        path.quadTo(55, 83, 52, 94)
        path.quadTo(46, 82, 42, 90)
        path.closeSubpath()

        painter.save()
        # 刘海落在额头的柔和微影
        painter.save()
        painter.translate(0.0, 3.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(208, 162, 140, 48))
        painter.drawPath(path)
        painter.restore()

        painter.setPen(_pen(LINE_HAIR, 2.0))
        painter.setBrush(QBrush(_hair_gradient(23, 97)))
        painter.drawPath(path)

        painter.setClipPath(path)
        # 头顶环状高光（天使环）
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(QColor(255, 255, 255, 175), 4.0))
        ring = QRectF(52, 34, 86, 44)
        painter.drawArc(ring, 28 * 16, 34 * 16)
        painter.drawArc(ring, 70 * 16, 40 * 16)
        painter.drawArc(ring, 118 * 16, 32 * 16)
        # 柔和分束发丝线
        painter.setPen(_pen(QColor(142, 102, 79, 95), 1.05))
        for (x0, y0), (cx_, cy_), (x1, y1) in (
            ((82, 58), (80, 68), (80, 77)),
            ((108, 58), (110, 68), (110, 77)),
            ((63, 62), (61, 70), (60, 78)),
            ((127, 62), (129, 70), (130, 78)),
            ((95, 64), (95, 76), (95.5, 90)),
        ):
            strand = QPainterPath()
            strand.moveTo(x0, y0)
            strand.quadTo(cx_, cy_, x1, y1)
            painter.drawPath(strand)
        # 刘海下缘渐变暗部
        painter.setPen(Qt.PenStyle.NoPen)
        band = QLinearGradient(0, 70, 0, 98)
        band.setColorAt(0.0, QColor(214, 170, 132, 0))
        band.setColorAt(1.0, QColor(214, 170, 132, 65))
        painter.setBrush(QBrush(band))
        painter.drawRect(QRectF(40, 70, 110, 30))
        painter.restore()

    def _draw_ahoge(self, painter: QPainter, frame: int, emotion: str) -> None:
        sway = math.sin(frame * 0.22) * 2.4
        path = QPainterPath()
        path.moveTo(94, 27)
        if emotion == "surprised":
            path.cubicTo(92, 14, 98, 6, 96, 0)
        elif emotion == "sad":
            path.cubicTo(92, 20, 80, 16, 76, 24)
        else:
            path.cubicTo(91, 12, 105 + sway, 4, 109 + sway, 13)
            path.cubicTo(111 + sway, 21, 100 + sway, 22, 101 + sway * 0.8, 15)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(LINE_HAIR, 4.4))
        painter.drawPath(path)
        painter.setPen(_pen(HAIR_TOP, 2.2))
        painter.drawPath(path)
        painter.restore()

    # ------------------------------------------------------------ 猫耳
    def _draw_ears(self, painter: QPainter, frame: int, state: str, emotion: str) -> None:
        twitch = 0.0
        if state == "聆听中" and frame % 14 in (2, 3):
            twitch = 3.0
        elif self._interaction_ticks > 0:
            twitch = 2.4 * math.sin(self._interaction_ticks * 1.2)
        droop = {"sad": 14.0, "angry": 7.0, "shy": 4.0}.get(emotion, 0.0)
        perk = -3.0 if emotion == "surprised" else 0.0

        painter.save()
        for right in (False, True):
            painter.save()
            pivot = QPointF(_mx(64, right), 48)
            painter.translate(pivot)
            sign = 1.0 if right else -1.0
            painter.rotate(sign * (droop + perk - twitch * 1.4))
            painter.translate(-pivot)

            outer = QPainterPath()
            outer.moveTo(_mx(46, right), 66)
            outer.cubicTo(_mx(36, right), 46, _mx(33, right), 24, _mx(39, right), 7)
            outer.cubicTo(_mx(56, right), 12, _mx(72, right), 24, _mx(84, right), 38)
            outer.closeSubpath()
            painter.setPen(_pen(LINE_HAIR, 2.0))
            painter.setBrush(QBrush(_hair_gradient(7, 66)))
            painter.drawPath(outer)

            inner = QPainterPath()
            inner.moveTo(_mx(50, right), 58)
            inner.cubicTo(_mx(43, right), 42, _mx(41, right), 27, _mx(43, right), 15)
            inner.cubicTo(_mx(55, right), 21, _mx(66, right), 30, _mx(75, right), 41)
            inner.closeSubpath()
            inner_grad = QLinearGradient(0, 15, 0, 58)
            inner_grad.setColorAt(0.0, EAR_INNER_TOP)
            inner_grad.setColorAt(1.0, EAR_INNER_BOTTOM)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(inner_grad))
            painter.drawPath(inner)

            # 耳内白色绒毛
            painter.setPen(_pen(QColor(255, 255, 255, 235), 2.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for (x0, y0), (cx_, cy_), (x1, y1) in (
                ((52, 56), (47, 46), (49, 34)),
                ((57, 54), (55, 44), (58, 34)),
                ((62, 52), (63, 44), (67, 38)),
            ):
                fluff = QPainterPath()
                fluff.moveTo(_mx(x0, right), y0)
                fluff.quadTo(_mx(cx_, right), cy_, _mx(x1, right), y1)
                painter.drawPath(fluff)
            painter.restore()
        painter.restore()

    # ------------------------------------------------------------ 脸
    def _draw_face(self, painter: QPainter, emotion: str) -> None:
        face = QPainterPath()
        face.moveTo(48, 82)
        face.cubicTo(46, 113, 64, 132, CX, 133)
        if emotion == "angry":
            # 鼓腮：右脸颊更圆润鼓起
            face.cubicTo(129, 133, 148, 116, 142, 82)
        else:
            face.cubicTo(126, 132, 144, 113, 142, 82)
        face.cubicTo(142, 48, 48, 48, 48, 82)
        face.closeSubpath()

        grad = QLinearGradient(0, 60, 0, 132)
        grad.setColorAt(0.0, SKIN_TOP)
        grad.setColorAt(1.0, SKIN_BOTTOM)
        painter.save()
        painter.setPen(_pen(LINE_SKIN, 2.0))
        painter.setBrush(QBrush(grad))
        painter.drawPath(face)
        painter.restore()

    def _draw_blush(self, painter: QPainter, emotion: str) -> None:
        strength = {"happy": 1.0, "shy": 1.35, "angry": 0.9}.get(emotion, 0.6)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for cx in (62.0, 128.0):
            grad = QRadialGradient(cx, 114, 11)
            grad.setColorAt(0.0, QColor(246, 140, 156, min(255, int(150 * strength))))
            grad.setColorAt(1.0, QColor(246, 140, 156, 0))
            painter.setBrush(QBrush(grad))
            painter.drawEllipse(QRectF(cx - 12, 108, 24, 12))
        # 动漫式腮红斜线
        painter.setPen(_pen(QColor(232, 112, 132, min(255, int(170 * strength))), 1.2))
        for cx in (62.0, 128.0):
            for dx in (-4.5, 0.0, 4.5):
                painter.drawLine(QPointF(cx + dx - 1.4, 116), QPointF(cx + dx + 1.4, 111.5))
        painter.restore()

    def _draw_brows(self, painter: QPainter, emotion: str) -> None:
        if emotion not in ("angry", "sad", "surprised", "shy"):
            return
        painter.save()
        painter.setPen(_pen(QColor(142, 102, 79, 200), 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for right in (False, True):
            inner_x, outer_x = _mx(84, right), _mx(66, right)
            if emotion == "angry":
                painter.drawLine(QPointF(outer_x, 80), QPointF(inner_x, 85))
            elif emotion in ("sad", "shy"):
                painter.drawLine(QPointF(outer_x, 84), QPointF(inner_x, 79))
            else:
                arc = QPainterPath()
                arc.moveTo(outer_x, 81)
                arc.quadTo(_mx(75, right), 75, inner_x, 80)
                painter.drawPath(arc)
        painter.restore()

    # ------------------------------------------------------------ 眼睛
    def _draw_eyes(self, painter: QPainter, frame: int, state: str, emotion: str) -> None:
        painter.save()
        if emotion == "happy":
            # 闭眼轻笑 ^ ^
            painter.setPen(_pen(LINE_DARK, 3.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for cx in (74.0, 116.0):
                arc = QPainterPath()
                arc.moveTo(cx - 10, 104)
                arc.quadTo(cx, 90, cx + 10, 104)
                painter.drawPath(arc)
            painter.restore()
            return

        blink = (frame % 32 in (26, 27)) and emotion != "surprised"
        if blink:
            painter.setPen(_pen(LINE_DARK, 2.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for right, cx in ((False, 74.0), (True, 116.0)):
                arc = QPainterPath()
                arc.moveTo(cx - 11, 100)
                arc.quadTo(cx, 108, cx + 11, 100)
                painter.drawPath(arc)
                painter.drawLine(QPointF(_mx(63, right), 100), QPointF(_mx(60, right), 97))
            painter.restore()
            return

        look_x, look_y = 0.0, 0.0
        if emotion == "shy":
            look_x = -2.4
        elif state == "思考中":
            look_x, look_y = 2.0, -2.2
        elif state == "聆听中":
            look_x = 1.2

        for right, cx in ((False, 74.0), (True, 116.0)):
            self._draw_single_eye(painter, cx, 100.0, right, emotion, look_x, look_y)
        painter.restore()

    def _draw_single_eye(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        right: bool,
        emotion: str,
        look_x: float,
        look_y: float,
    ) -> None:
        surprised = emotion == "surprised"
        w, h = (23.0, 27.0) if surprised else (22.0, 25.0)
        outer_sign = 1.0 if right else -1.0

        white = QPainterPath()
        white.addEllipse(QRectF(cx - w / 2, cy - h / 2, w, h))

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#fffaf6"))
        painter.drawPath(white)

        painter.setClipPath(white)
        iris_scale = 0.78 if surprised else 1.0
        iw, ih = 18.0 * iris_scale, 23.0 * iris_scale
        ix, iy = cx + look_x, cy + 1.0 + look_y
        iris_rect = QRectF(ix - iw / 2, iy - ih / 2, iw, ih)
        grad = QLinearGradient(0, iris_rect.top(), 0, iris_rect.bottom())
        grad.setColorAt(0.0, IRIS_TOP)
        grad.setColorAt(0.45, IRIS_MID)
        grad.setColorAt(0.82, IRIS_LOW)
        grad.setColorAt(1.0, IRIS_GLOW)
        painter.setPen(_pen(QColor("#1b3a2f"), 1.0))
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(iris_rect)

        # 上眼睑投影
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(16, 40, 32, 120))
        painter.drawEllipse(QRectF(cx - w / 2 - 2, cy - h / 2 - 6, w + 4, 12))

        # 猫瞳（竖椭圆）
        pw = 3.2 if surprised else 6.0
        ph = 8.0 if surprised else 13.0
        painter.setBrush(QColor("#11291f"))
        painter.drawEllipse(QRectF(ix - pw / 2, iy - ph / 2 - 0.5, pw, ph))

        # 虹膜下缘反光弧
        painter.setBrush(QColor(236, 255, 214, 150))
        painter.drawEllipse(QRectF(ix - 6, iy + ih * 0.18, 12, ih * 0.26))

        # 高光：大椭圆 + 小圆 + 星点
        painter.setBrush(QColor(255, 255, 255, 245))
        painter.drawEllipse(QRectF(ix - 7.6, iy - 9.0, 7.2, 8.4))
        painter.drawEllipse(QRectF(ix + 2.4, iy + 3.4, 3.4, 3.4))
        self._draw_sparkle(painter, ix + 4.6, iy - 5.6, 2.4, QColor(255, 255, 255, 230))

        if emotion == "sad":
            # 噙泪：下半部水光
            painter.setBrush(QColor(170, 220, 255, 120))
            painter.drawEllipse(QRectF(cx - w / 2, cy + 4, w, 10))
            painter.setPen(_pen(QColor(255, 255, 255, 200), 1.2))
            painter.drawLine(QPointF(cx - 5, cy + 8), QPointF(cx + 5, cy + 8))
        painter.restore()

        # 半垂眼（生气 / 鼓腮）：用肤色眼睑盖住上部
        lid_top = cy - h / 2 - 1.5
        if emotion == "angry":
            lid = QPainterPath()
            inner_x = cx - outer_sign * (w / 2 + 2)
            outer_x = cx + outer_sign * (w / 2 + 2)
            lid.moveTo(inner_x, cy - 1)
            lid.lineTo(outer_x, cy - 7)
            lid.lineTo(outer_x, cy - h)
            lid.lineTo(inner_x, cy - h)
            lid.closeSubpath()
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(SKIN_TOP)
            painter.drawPath(lid)
            painter.setPen(_pen(LINE_DARK, 3.0))
            painter.drawLine(QPointF(inner_x + outer_sign * 1.5, cy - 1.5), QPointF(outer_x, cy - 7.2))
            painter.restore()
        else:
            # 上睫毛：粗弧线 + 外眼角上挑
            lash = QPainterPath()
            lash.moveTo(cx - outer_sign * (w / 2 - 1), cy - h / 2 + 5)
            lash.quadTo(cx, lid_top - 3.0, cx + outer_sign * (w / 2 + 0.5), cy - h / 2 + 4)
            lash.lineTo(cx + outer_sign * (w / 2 + 3.5), cy - h / 2 + 0.5)
            painter.save()
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_pen(LINE_DARK, 3.2))
            painter.drawPath(lash)
            # 下睫毛短线
            painter.setPen(_pen(QColor(58, 42, 45, 150), 1.2))
            lower = QPainterPath()
            lower.moveTo(cx + outer_sign * (w / 2 - 1), cy + h / 2 - 6)
            lower.quadTo(cx + outer_sign * (w / 2 - 4), cy + h / 2, cx + outer_sign * 2, cy + h / 2 + 0.5)
            painter.drawPath(lower)
            painter.restore()

        if emotion == "sad":
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(150, 205, 250, 220))
            tear = QPainterPath()
            tx = cx + outer_sign * (w / 2 - 3)
            tear.moveTo(tx, cy + 10)
            tear.quadTo(tx + 3.2, cy + 15, tx, cy + 17)
            tear.quadTo(tx - 3.2, cy + 15, tx, cy + 10)
            painter.drawPath(tear)
            painter.restore()

    # ------------------------------------------------------------ 嘴
    def _draw_mouth(self, painter: QPainter, frame: int, state: str, emotion: str) -> None:
        mx, my = CX, 121.0
        painter.save()
        line = _pen(QColor("#8c4a55"), 1.8)

        if state == "正在说话" and frame % 4 in (0, 1, 2):
            h = (7.5, 4.8, 6.2)[frame % 4 % 3]
            self._draw_open_mouth(painter, mx, my - 1.5, 9.0, h, fang=True)
            painter.restore()
            return

        if emotion == "happy":
            self._draw_open_mouth(painter, mx, my - 1.5, 10.0, 6.5, fang=True)
        elif emotion == "surprised":
            painter.setPen(line)
            painter.setBrush(QColor("#e57f8c"))
            painter.drawEllipse(QRectF(mx - 3.6, my - 3.0, 7.2, 8.0))
        elif emotion == "sad":
            painter.setPen(line)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(mx - 5, my + 2.5)
            path.quadTo(mx - 2.5, my - 1.5, mx, my + 0.5)
            path.quadTo(mx + 2.5, my - 1.5, mx + 5, my + 2.5)
            painter.drawPath(path)
        elif emotion == "angry":
            # 鼓腮嘟嘴
            painter.setPen(line)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(mx - 3.5, my)
            path.quadTo(mx - 1.0, my - 2.5, mx + 0.5, my)
            path.quadTo(mx + 2.0, my + 2.5, mx + 4.0, my - 0.5)
            painter.drawPath(path)
        elif emotion == "shy":
            painter.setPen(line)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(mx - 6, my)
            path.quadTo(mx - 3, my - 2.4, mx, my)
            path.quadTo(mx + 3, my + 2.4, mx + 6, my)
            painter.drawPath(path)
        else:
            # 经典猫嘴 ω + 小虎牙
            painter.setPen(line)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(mx - 6, my - 1.0)
            path.quadTo(mx - 3, my + 3.4, mx, my - 0.6)
            path.quadTo(mx + 3, my + 3.4, mx + 6, my - 1.0)
            painter.drawPath(path)
            fang = QPainterPath()
            fang.moveTo(mx - 4.6, my + 1.0)
            fang.lineTo(mx - 3.2, my + 3.6)
            fang.lineTo(mx - 2.0, my + 1.4)
            painter.setPen(_pen(QColor("#b88a8a"), 0.8))
            painter.setBrush(QColor("#ffffff"))
            painter.drawPath(fang)
        painter.restore()

    @staticmethod
    def _draw_open_mouth(painter: QPainter, mx: float, my: float, w: float, h: float, *, fang: bool) -> None:
        mouth = QPainterPath()
        mouth.moveTo(mx - w / 2, my)
        mouth.quadTo(mx, my - 1.0, mx + w / 2, my)
        mouth.quadTo(mx + w / 2 - 1.0, my + h, mx, my + h)
        mouth.quadTo(mx - w / 2 + 1.0, my + h, mx - w / 2, my)
        mouth.closeSubpath()
        painter.setPen(_pen(QColor("#8c4a55"), 1.6))
        painter.setBrush(QColor("#c9505f"))
        painter.drawPath(mouth)
        painter.save()
        painter.setClipPath(mouth)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f59aa6"))
        painter.drawEllipse(QRectF(mx - w * 0.35, my + h * 0.45, w * 0.7, h * 0.8))
        if fang:
            tooth = QPainterPath()
            tooth.moveTo(mx - w / 2 + 1.4, my + 0.2)
            tooth.lineTo(mx - w / 2 + 2.8, my + 3.0)
            tooth.lineTo(mx - w / 2 + 4.2, my + 0.2)
            painter.setBrush(QColor("#ffffff"))
            painter.drawPath(tooth)
        painter.restore()

    # ------------------------------------------------------------ 身体
    def _draw_body(self, painter: QPainter, frame: int, state: str) -> None:
        painter.save()
        cape = QPainterPath()
        cape.moveTo(62, 128)
        cape.cubicTo(48, 136, 38, 156, 36, 180)
        cape.lineTo(154, 180)
        cape.cubicTo(152, 156, 142, 136, 128, 128)
        cape.closeSubpath()
        grad = QLinearGradient(0, 128, 0, 178)
        grad.setColorAt(0.0, CAPE_TOP)
        grad.setColorAt(1.0, CAPE_BOTTOM)
        painter.setPen(_pen(LINE_CLOTH, 2.0))
        painter.setBrush(QBrush(grad))
        painter.drawPath(cape)

        # 披肩印花：两片小粽叶 + 中缝
        painter.save()
        painter.setClipPath(cape)
        for lx, ly, angle in ((58, 160, -35), (132, 158, 35)):
            painter.save()
            painter.translate(lx, ly)
            painter.rotate(angle)
            leaf = QPainterPath()
            leaf.moveTo(-6, 0)
            leaf.quadTo(0, -4.2, 6, 0)
            leaf.quadTo(0, 4.2, -6, 0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(214, 240, 222, 170))
            painter.drawPath(leaf)
            painter.setPen(_pen(QColor(63, 127, 98, 160), 0.8))
            painter.drawLine(QPointF(-4.5, 0), QPointF(4.5, 0))
            painter.restore()
        painter.setPen(_pen(QColor(30, 80, 58, 110), 1.2))
        painter.drawLine(QPointF(CX, 150), QPointF(CX, 180))
        # 侧边暗部
        shade = QLinearGradient(36, 0, 154, 0)
        shade.setColorAt(0.0, QColor(20, 60, 44, 70))
        shade.setColorAt(0.3, QColor(20, 60, 44, 0))
        shade.setColorAt(0.7, QColor(20, 60, 44, 0))
        shade.setColorAt(1.0, QColor(20, 60, 44, 70))
        painter.setBrush(QBrush(shade))
        painter.drawRect(QRectF(30, 126, 130, 56))
        painter.restore()

        # 毛绒领口（底带 + 大圆团合并成蓬松云朵）
        fur = QPainterPath()
        band = QPainterPath()
        band.moveTo(56, 128)
        band.quadTo(CX, 140, 134, 128)
        band.lineTo(134, 134)
        band.quadTo(CX, 147, 56, 134)
        band.closeSubpath()
        fur.addPath(band)
        for i in range(8):
            t = i / 7.0
            x = 57 + 76 * t
            y = 131 + math.sin(t * math.pi) * 7.5
            r = 7.0 if i in (0, 7) else 8.0
            fur.addEllipse(QPointF(x, y), r, r * 0.85)
        fur.setFillRule(Qt.FillRule.WindingFill)
        fur = fur.simplified()
        painter.setPen(_pen(QColor("#a7a2ba"), 1.4))
        painter.setBrush(FUR)
        painter.drawPath(fur)
        painter.save()
        painter.setClipPath(fur)
        painter.setPen(_pen(QColor(205, 200, 222, 170), 1.1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(8):
            t = i / 7.0
            x = 57 + 76 * t
            y = 131 + math.sin(t * math.pi) * 7.5
            painter.drawArc(QRectF(x - 4.5, y - 1.0, 9.0, 6.0), 200 * 16, 140 * 16)
        painter.restore()

        # 紫色蝴蝶结 + 金铃铛
        bell_dx = math.sin(frame * 0.5) * 1.6 if state == "正在说话" else math.sin(frame * 0.12) * 0.5
        self._draw_bow(painter, CX, 141.5, 1.0, QColor("#9a7fc0"), QColor("#4f3d6b"))
        bell = QRectF(CX - 6.2 + bell_dx, 145.0, 12.4, 12.4)
        bell_grad = QRadialGradient(CX - 2 + bell_dx, 148.5, 9)
        bell_grad.setColorAt(0.0, QColor("#fff4bf"))
        bell_grad.setColorAt(0.55, QColor("#f5c256"))
        bell_grad.setColorAt(1.0, QColor("#c98a26"))
        painter.setPen(_pen(QColor("#7a5420"), 1.5))
        painter.setBrush(QBrush(bell_grad))
        painter.drawEllipse(bell)
        painter.setPen(_pen(QColor("#7a5420"), 1.1))
        painter.drawLine(QPointF(CX - 5.6 + bell_dx, 150.6), QPointF(CX + 5.6 + bell_dx, 150.6))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#5a3d1a"))
        painter.drawEllipse(QRectF(CX - 1.3 + bell_dx, 152.2, 2.6, 2.6))
        painter.setBrush(QColor(255, 255, 255, 210))
        painter.drawEllipse(QRectF(CX - 3.6 + bell_dx, 146.8, 3.0, 2.2))
        painter.restore()

    def _draw_paws(self, painter: QPainter, frame: int, state: str) -> None:
        """胸前一对小猫爪（说话时轻轻挥动）。"""
        painter.save()
        wave = math.sin(frame * 0.6) * 1.8 if state == "正在说话" else 0.0
        for right, cx in ((False, 80.0), (True, 110.0)):
            dy = -wave if right else 0.0
            paw = QPainterPath()
            paw.addEllipse(QPointF(cx, 169 + dy), 8.0, 6.2)
            for tx, ty in ((-5.2, 163.4), (0.0, 161.6), (5.2, 163.4)):
                paw.addEllipse(QPointF(cx + tx, ty + dy), 3.6, 3.4)
            paw.setFillRule(Qt.FillRule.WindingFill)
            paw = paw.simplified()
            grad = QLinearGradient(0, 158 + dy, 0, 176 + dy)
            grad.setColorAt(0.0, QColor("#fffaf3"))
            grad.setColorAt(1.0, QColor("#f3e2cf"))
            painter.setPen(_pen(LINE_HAIR, 1.6))
            painter.setBrush(QBrush(grad))
            painter.drawPath(paw)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(245, 160, 175, 210))
            painter.drawEllipse(QPointF(cx, 170 + dy), 3.4, 2.5)
            for tx, ty in ((-4.8, 164.4), (0.0, 162.8), (4.8, 164.4)):
                painter.drawEllipse(QPointF(cx + tx, ty + dy), 1.3, 1.2)
        painter.restore()

    def _draw_zongzi_clip(self, painter: QPainter, frame: int) -> None:
        """右额侧的小粽子发饰（带表情）。"""
        bob = math.sin(frame * 0.2 + 1.0) * 0.6
        ox, oy = 126.0, 46.0 + bob
        painter.save()
        painter.translate(ox, oy)
        painter.rotate(14)
        body = QPainterPath()
        body.moveTo(0, -11)
        body.quadTo(2.2, -11.5, 3.4, -9)
        body.lineTo(10.5, 5.5)
        body.quadTo(11.5, 9, 7.5, 9)
        body.lineTo(-7.5, 9)
        body.quadTo(-11.5, 9, -10.5, 5.5)
        body.lineTo(-3.4, -9)
        body.quadTo(-2.2, -11.5, 0, -11)
        body.closeSubpath()
        grad = QLinearGradient(0, -11, 0, 9)
        grad.setColorAt(0.0, QColor("#9ad3b1"))
        grad.setColorAt(1.0, QColor("#4c9070"))
        painter.setPen(_pen(LINE_CLOTH, 1.5))
        painter.setBrush(QBrush(grad))
        painter.drawPath(body)
        # 叶脉
        painter.setPen(_pen(QColor(255, 255, 255, 120), 0.9))
        painter.drawLine(QPointF(-1.0, -8), QPointF(-6.5, 6))
        # 紫色系绳
        painter.setPen(_pen(ACCENT_PURPLE, 1.8))
        painter.drawLine(QPointF(-8.4, 2.0), QPointF(8.4, 2.0))
        # 小表情
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1f3a2e"))
        painter.drawEllipse(QRectF(-4.2, -2.8, 2.0, 2.4))
        painter.drawEllipse(QRectF(2.2, -2.8, 2.0, 2.4))
        painter.setBrush(QColor(246, 150, 166, 200))
        painter.drawEllipse(QRectF(-7.0, 0.0, 2.8, 1.6))
        painter.drawEllipse(QRectF(4.2, 0.0, 2.8, 1.6))
        painter.setPen(_pen(QColor("#1f3a2e"), 0.9))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        smile = QPainterPath()
        smile.moveTo(-1.2, -0.4)
        smile.quadTo(0, 1.0, 1.2, -0.4)
        painter.drawPath(smile)
        painter.restore()

    @staticmethod
    def _draw_bow(painter: QPainter, cx: float, cy: float, scale: float, fill: QColor, line: QColor) -> None:
        painter.save()
        painter.translate(cx, cy)
        painter.scale(scale, scale)
        painter.setPen(_pen(line, 1.4))
        painter.setBrush(fill)
        for sign in (-1.0, 1.0):
            wing = QPainterPath()
            wing.moveTo(0, 0)
            wing.cubicTo(sign * 4, -6, sign * 10, -5.5, sign * 9.5, 0)
            wing.cubicTo(sign * 10, 5.5, sign * 4, 6, 0, 0)
            wing.closeSubpath()
            painter.drawPath(wing)
            tail = QPainterPath()
            tail.moveTo(sign * 1.0, 1.0)
            tail.lineTo(sign * 5.5, 8.5)
            tail.lineTo(sign * 3.0, 8.0)
            painter.drawPath(tail)
        painter.drawEllipse(QRectF(-2.6, -2.6, 5.2, 5.2))
        painter.restore()

    # ------------------------------------------------------------ 特效
    def _draw_effects(self, painter: QPainter, frame: int, state: str, emotion: str) -> None:
        painter.save()
        if emotion == "happy":
            offset = math.sin(frame * 0.4) * 2.0
            self._draw_sparkle(painter, 24, 46 - offset, 6.5, QColor("#f3b84c"))
            self._draw_sparkle(painter, 166, 56 + offset, 5.0, QColor("#e88ca6"))
            self._draw_sparkle(painter, 30, 70 + offset, 3.2, QColor("#9ad3b1"))
        elif emotion == "shy":
            self._draw_heart(painter, 160, 60 - (frame % 12) * 0.6, 5.5, QColor(240, 128, 150, 220))
        elif emotion == "angry":
            painter.setPen(_pen(QColor("#e0505e"), 2.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            base = QPointF(150, 40)
            for dx, dy, a0 in ((-4, -4, 270), (4, -4, 180), (-4, 4, 0), (4, 4, 90)):
                painter.drawArc(QRectF(base.x() + dx - 4, base.y() + dy - 4, 8, 8), a0 * 16, 90 * 16)
        elif emotion == "surprised":
            painter.setPen(_pen(QColor("#e8875d"), 2.8))
            painter.drawLine(QPointF(160, 22), QPointF(162, 36))
            painter.drawPoint(QPointF(163, 42))
        elif emotion == "sad":
            painter.setPen(_pen(QColor(120, 140, 190, 150), 1.4))
            for x in (30, 36, 42):
                painter.drawLine(QPointF(x, 34), QPointF(x, 46))

        draw_state_badge(painter, QRectF(0, 0, 190, 178), frame, state, top_y=18.0)
        painter.restore()

    @staticmethod
    def _draw_sparkle(painter: QPainter, cx: float, cy: float, r: float, color: QColor) -> None:
        painter.save()
        path = QPainterPath()
        path.moveTo(cx, cy - r)
        path.quadTo(cx, cy, cx + r, cy)
        path.quadTo(cx, cy, cx, cy + r)
        path.quadTo(cx, cy, cx - r, cy)
        path.quadTo(cx, cy, cx, cy - r)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)
        painter.restore()

    @staticmethod
    def _draw_heart(painter: QPainter, cx: float, cy: float, r: float, color: QColor) -> None:
        painter.save()
        path = QPainterPath()
        path.moveTo(cx, cy + r)
        path.cubicTo(cx - r * 1.6, cy, cx - r * 0.9, cy - r * 1.3, cx, cy - r * 0.4)
        path.cubicTo(cx + r * 0.9, cy - r * 1.3, cx + r * 1.6, cy, cx, cy + r)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)
        painter.restore()


def render_vector_preview(size: int = 64, *, head_only: bool = True) -> QPixmap:
    """用矢量渲染器绘制一张静态头像（托盘图标 / 设置页缩略图共用）。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer = VectorCatgirlRenderer()
    renderer.tick(6)  # 避开眨眼帧
    if head_only:
        src = QRectF(18, 2, 154, 154)
    else:
        src = QRectF(0, 0, 190, 178)
    scale = size / max(src.width(), src.height())
    painter.scale(scale, scale)
    painter.translate(-src.left() + (max(src.width(), src.height()) - src.width()) / 2,
                      -src.top() + (max(src.width(), src.height()) - src.height()) / 2)
    renderer.paint(painter, QRectF(0, 0, 190, 178))
    painter.end()
    return pixmap


def create_mascot_tray_icon() -> QIcon:
    """系统托盘图标：与矢量猫娘同一画风的 Q 版头像。"""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        icon.addPixmap(render_vector_preview(size))
    return icon
