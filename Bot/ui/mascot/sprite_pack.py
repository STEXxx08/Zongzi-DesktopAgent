"""外部图片立绘 / 动图皮肤包（PNG / JPG / GIF / WebP）渲染器。"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QMovie, QPainter, QPixmap

from ui.mascot.base import (
    MascotRendererBase,
    draw_ground_shadow,
    draw_state_badge,
    draw_thinking_halo,
)

_IMAGE_EXTS = (".webp", ".gif", ".png", ".jpg", ".jpeg")


def resolve_resource_path(raw_path: str) -> Path | None:
    """解析资源路径，兼容绝对路径以及相对于 Bot/ 或仓库根目录的相对路径。"""
    clean = (raw_path or "").strip()
    if not clean:
        return None
    candidate = Path(clean)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    bot_dir = Path(__file__).resolve().parents[2]
    repo_dir = bot_dir.parent
    for base in (Path.cwd(), bot_dir, repo_dir):
        full = (base / candidate).resolve()
        if full.exists():
            return full
    return None


class SpritePackRenderer(MascotRendererBase):
    """支持单张立绘、命名约定皮肤目录以及 manifest.json 配置的图片/动图渲染器。"""

    mode_name = "sprite"
    _FADE_TOTAL = 4

    def __init__(self, avatar_path: str) -> None:
        super().__init__()
        self._raw_path = (avatar_path or "").strip()
        self._resolved: Path | None = resolve_resource_path(self._raw_path)
        self._reason: str = ""
        self._manifest: dict = {}
        self._files_by_stem: dict[str, Path] = {}
        self._fallback_file: Path | None = None
        self._current_file: Path | None = None
        self._pixmap_cache: dict[Path, QPixmap] = {}
        self._movie: QMovie | None = None
        self._movie_frame: QPixmap | None = None
        self._fade_from: QPixmap | None = None
        self._fade_ticks: int = 0

        self._scan()

    def _scan(self) -> None:
        if not self._raw_path:
            self._reason = "未指定图片立绘路径，已自动回退为默认矢量猫娘。"
            return
        if self._resolved is None:
            self._reason = f"找不到立绘路径「{self._raw_path}」，已自动回退为默认矢量猫娘。"
            return

        if self._resolved.is_file():
            if self._resolved.suffix.lower() not in _IMAGE_EXTS:
                self._reason = f"不支持的立绘文件格式「{self._resolved.suffix}」，请使用 PNG/GIF/WebP/JPG。"
                return
            self._fallback_file = self._resolved
            self._select_asset()
            return

        if not self._resolved.is_dir():
            self._reason = f"无效的立绘路径「{self._raw_path}」。"
            return

        manifest_path = self._resolved / "manifest.json"
        if manifest_path.is_file():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._manifest = loaded
            except Exception as exc:
                self._reason = f"解析立绘清单 manifest.json 失败：{exc}"
                return

        for item in sorted(self._resolved.iterdir()):
            if item.is_file() and item.suffix.lower() in _IMAGE_EXTS:
                stem = item.stem.casefold()
                # 按扩展名优先级去重保留
                if stem not in self._files_by_stem:
                    self._files_by_stem[stem] = item
                if self._fallback_file is None:
                    self._fallback_file = item

        if not self._files_by_stem and not self._manifest:
            self._reason = f"目录「{self._resolved}」下未找到 PNG/GIF/WebP/JPG 图片资源。"
            return

        self._select_asset()
        if self._current_file is None:
            self._reason = f"目录「{self._resolved}」下未找到可展示的默认图片。"

    def is_available(self) -> bool:
        return self._current_file is not None and not self._reason

    def fallback_reason(self) -> str:
        return self._reason

    def _resolve_manifest_entry(self, entry: str | None) -> Path | None:
        if not entry or self._resolved is None:
            return None
        target = (self._resolved / entry).resolve()
        if target.is_file() and target.suffix.lower() in _IMAGE_EXTS:
            return target
        return None

    def _pick_target_file(self) -> Path | None:
        if self._resolved is None:
            return None
        if self._resolved.is_file():
            return self._fallback_file

        state_key = self.state_key().casefold()
        emotion_key = self.emotion_kind().casefold()
        raw_emotion = self._emotion.strip().casefold()

        # 1. manifest.json 优先匹配
        if self._manifest:
            frames_map = self._manifest.get("frames") or {}
            emotions_map = self._manifest.get("emotions") or {}
            states_map = self._manifest.get("states") or {}
            candidates = (
                frames_map.get(f"{state_key}_{raw_emotion}"),
                frames_map.get(f"{state_key}_{emotion_key}"),
                emotions_map.get(raw_emotion),
                emotions_map.get(emotion_key),
                states_map.get(state_key),
                self._manifest.get("default"),
            )
            for rel in candidates:
                matched = self._resolve_manifest_entry(rel)
                if matched is not None:
                    return matched

        # 2. 目录约定命名回退
        stem_candidates = [
            f"{state_key}_{raw_emotion}",
            f"{state_key}_{emotion_key}",
            raw_emotion,
            emotion_key,
            state_key,
            "default",
            "idle",
        ]
        for stem in stem_candidates:
            if stem in self._files_by_stem:
                return self._files_by_stem[stem]
        return self._fallback_file

    def _select_asset(self) -> None:
        target = self._pick_target_file()
        if target == self._current_file:
            return
        # 记录切换前的画面，用于表情交叉淡入
        previous = self._current_pixmap()
        if previous is not None and not previous.isNull():
            self._fade_from = QPixmap(previous)
            self._fade_ticks = self._FADE_TOTAL
        self._stop_movie()
        self._current_file = target
        if target is None:
            return

        if target.suffix.lower() in (".gif", ".webp"):
            movie = QMovie(str(target))
            if movie.isValid():
                movie.frameChanged.connect(self._on_movie_frame)
                movie.start()
                self._movie = movie
                self._movie_frame = movie.currentPixmap()
                return
            movie.deleteLater()

        if target not in self._pixmap_cache:
            pix = QPixmap(str(target))
            if not pix.isNull():
                self._pixmap_cache[target] = pix

    def _on_movie_frame(self, _frame_number: int) -> None:
        if self._movie is not None:
            self._movie_frame = self._movie.currentPixmap()
            self.request_repaint()

    def _stop_movie(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
            self._movie_frame = None

    def set_state(self, state: str) -> None:
        super().set_state(state)
        self._select_asset()

    def set_emotion(self, emotion: str) -> None:
        super().set_emotion(emotion)
        self._select_asset()

    def _current_pixmap(self) -> QPixmap | None:
        if self._movie is not None:
            return self._movie_frame
        if self._current_file is None:
            return None
        return self._pixmap_cache.get(self._current_file)

    def tick(self, frame: int) -> None:
        super().tick(frame)
        if self._fade_ticks > 0:
            self._fade_ticks -= 1
            if self._fade_ticks == 0:
                self._fade_from = None

    @staticmethod
    def _fit_rect(pixmap: QPixmap, box: QRectF) -> QRectF:
        pw, ph = max(1, pixmap.width()), max(1, pixmap.height())
        ratio = min(box.width() / pw, box.height() / ph)
        w, h = pw * ratio, ph * ratio
        # 底部对齐：半身像“坐”在状态胶囊上方
        return QRectF(box.x() + (box.width() - w) / 2.0, box.bottom() - h, w, h)

    def paint(self, painter: QPainter, logical_rect: QRectF) -> None:
        pixmap = self._current_pixmap()
        if pixmap is None or pixmap.isNull():
            return

        frame = self._frame
        state = self._state
        dy = math.sin(frame * 0.16) * 1.4
        squash = 1.0
        if state == "正在说话":
            phase = (frame % 4) / 4.0
            squash = 1.0 + math.sin(phase * math.tau) * 0.012
            dy += -1.6 if frame % 2 == 0 else 0.6
        elif state == "聆听中":
            dy = math.sin(frame * 0.28) * 1.2
        if self._interaction_ticks > 0:
            dy -= math.sin(self._interaction_ticks * 0.6) * 3.0
            squash = 1.0 + math.sin(self._interaction_ticks * 0.9) * 0.025

        box = QRectF(6, 4, logical_rect.width() - 12, logical_rect.height() - 10)
        painter.save()
        # 地面投影
        draw_ground_shadow(
            painter,
            QRectF(30, logical_rect.height() - 13, logical_rect.width() - 60, 12),
            center=QPointF(logical_rect.width() / 2, logical_rect.height() - 6),
            radius=60.0,
            alpha=60,
        )

        if state == "思考中":
            draw_thinking_halo(
                painter,
                logical_rect.center(),
                88.0,
                frame,
                rx=92.0,
                ry=88.0,
            )

        # 以底边中心为锚点做轻微挤压回弹
        anchor_x, anchor_y = logical_rect.width() / 2, box.bottom()
        painter.translate(anchor_x, anchor_y + dy)
        painter.scale(1.0 / squash, squash)
        painter.translate(-anchor_x, -anchor_y)

        if self._fade_from is not None and self._fade_ticks > 0:
            t = self._fade_ticks / self._FADE_TOTAL
            painter.setOpacity(t)
            painter.drawPixmap(self._fit_rect(self._fade_from, box), self._fade_from, QRectF(self._fade_from.rect()))
            painter.setOpacity(1.0 - t)
        painter.drawPixmap(self._fit_rect(pixmap, box), pixmap, QRectF(pixmap.rect()))
        painter.setOpacity(1.0)
        painter.restore()

        draw_state_badge(painter, logical_rect, frame, state, top_y=12.0)

    def cleanup(self) -> None:
        self._stop_movie()
        self._pixmap_cache.clear()
        self._fade_from = None
