"""Live2D (.model3.json) 渲染器：支持 live2d-py OpenGL 渲染并在缺省依赖时平滑回退。"""

from __future__ import annotations

import importlib
import json
import math
from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from ui.mascot.base import MascotRendererBase
from ui.mascot.sprite_pack import resolve_resource_path

_DEFAULT_LIVE2D_REL = "Resources/models/live2d/hiyori.model3.json"


def _find_default_live2d_model() -> Path | None:
    bot_dir = Path(__file__).resolve().parents[2]
    repo_dir = bot_dir.parent
    for candidate in (
        repo_dir / _DEFAULT_LIVE2D_REL,
        bot_dir / _DEFAULT_LIVE2D_REL,
        bot_dir / "resources/models/live2d/hiyori.model3.json",
    ):
        if candidate.is_file():
            return candidate.resolve()
    return None


def _probe_live2d_backend():
    """探测可用的 Live2D 绑定（live2d-py v3 + PySide6 QOpenGLWidget）。"""
    try:
        live2d_v3 = importlib.import_module("live2d.v3")
        gl_mod = importlib.import_module("PySide6.QtOpenGLWidgets")
        q_opengl_widget = getattr(gl_mod, "QOpenGLWidget")
        return live2d_v3, q_opengl_widget
    except Exception:
        return None, None


class Live2DRenderer(MascotRendererBase):
    """兼容 Cubism 3/4 `.model3.json` 的 Live2D 桌宠渲染器。"""

    mode_name = "live2d"

    def __init__(self, avatar_path: str = "") -> None:
        super().__init__()
        self._raw_path = (avatar_path or "").strip()
        self._reason: str = ""
        self._model_path: Path | None = None
        self._gl_widget: QWidget | None = None
        self._live2d_mod = None
        self._model = None

        self._init_backend()

    def _init_backend(self) -> None:
        if self._raw_path:
            resolved = resolve_resource_path(self._raw_path)
        else:
            resolved = _find_default_live2d_model()

        if resolved is None or not resolved.is_file():
            target_desc = self._raw_path or _DEFAULT_LIVE2D_REL
            self._reason = f"未找到 Live2D 模型文件「{target_desc}」，已自动回退为内置矢量猫娘。"
            return

        if not resolved.name.lower().endswith(".model3.json"):
            self._reason = f"「{resolved.name}」不是有效的 .model3.json Live2D 模型描述文件。"
            return

        try:
            json.loads(resolved.read_text(encoding="utf-8"))
        except Exception as exc:
            self._reason = f"读取 Live2D 模型配置失败（{exc}），已回退为内置矢量猫娘。"
            return

        self._model_path = resolved
        live2d_v3, q_opengl_widget = _probe_live2d_backend()
        if live2d_v3 is None or q_opengl_widget is None:
            self._reason = (
                f"已定位 Live2D 模型「{resolved.name}」，但当前环境未安装 live2d-py 渲染库"
                "（可执行 pip install live2d-py 启用），已自动回退为内置矢量猫娘。"
            )
            return

        self._live2d_mod = live2d_v3
        self._gl_widget = self._build_gl_widget(live2d_v3, q_opengl_widget, resolved)

    def _build_gl_widget(self, live2d_v3, q_opengl_widget, model_path: Path) -> QWidget:
        renderer_ref = self

        class _Live2DGLCanvas(q_opengl_widget):
            def initializeGL(self) -> None:  # noqa: N802
                live2d_v3.init()
                if hasattr(live2d_v3, "glewInit"):
                    live2d_v3.glewInit()
                model = live2d_v3.LAppModel()
                model.LoadModelJson(str(model_path))
                model.Resize(self.width(), self.height())
                renderer_ref._model = model

            def resizeGL(self, w: int, h: int) -> None:  # noqa: N802
                if renderer_ref._model is not None:
                    renderer_ref._model.Resize(w, h)

            def paintGL(self) -> None:  # noqa: N802
                live2d_v3.clearBuffer()
                model = renderer_ref._model
                if model is None:
                    return
                if renderer_ref._state == "正在说话":
                    mouth = 0.25 + 0.55 * abs(math.sin(renderer_ref._frame * 0.6))
                    model.SetParameterValue("ParamMouthOpenY", mouth)
                model.Update()
                model.Draw()

        return _Live2DGLCanvas()

    def is_available(self) -> bool:
        return self._gl_widget is not None and not self._reason

    def fallback_reason(self) -> str:
        return self._reason

    def embedded_widget(self) -> QWidget | None:
        return self._gl_widget

    def set_emotion(self, emotion: str) -> None:
        super().set_emotion(emotion)
        if self._model is not None and hasattr(self._model, "SetExpression"):
            try:
                self._model.SetExpression(self._emotion)
            except Exception:
                pass

    def trigger_interaction(self) -> None:
        super().trigger_interaction()
        if self._model is not None and hasattr(self._model, "StartRandomMotion"):
            try:
                self._model.StartRandomMotion("TapBody", 2)
            except Exception:
                pass

    def tick(self, frame: int) -> None:
        super().tick(frame)
        if self._gl_widget is not None:
            self._gl_widget.update()

    def paint(self, painter: QPainter, logical_rect: QRectF) -> None:
        # 使用嵌入式 QOpenGLWidget 渲染，无需在父画布重绘
        pass

    def cleanup(self) -> None:
        if self._live2d_mod is not None and hasattr(self._live2d_mod, "dispose"):
            try:
                self._live2d_mod.dispose()
            except Exception:
                pass
        self._model = None
        if self._gl_widget is not None:
            self._gl_widget.deleteLater()
            self._gl_widget = None
