"""内置桌宠形象预设（矢量“粽子” + 内置图片立绘包）。

预设只是 (avatar_mode, avatar_path) 的组合，不改配置结构：
    vector                     -> ("vector", "")
    sprite:<pack_id>           -> ("sprite", "resources/mascot/<pack_id>")
    sprite / live2d            -> 自定义路径（沿用用户填写的 avatar_path）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

BUILTIN_SPRITE_ROOT = "resources/mascot"


@dataclass(frozen=True)
class AvatarPreset:
    key: str
    label: str
    mode: str
    path: str = ""
    description: str = ""


BUILTIN_PRESETS: tuple[AvatarPreset, ...] = (
    AvatarPreset("vector", "粽子 · 内置矢量猫娘", "vector", "", "Q 版矢量绘制，表情/状态动画最丰富，零依赖"),
    AvatarPreset(
        "sprite:gemini_q",
        "Gemini 娘 · 星饰猫耳",
        "sprite",
        f"{BUILTIN_SPRITE_ROOT}/gemini_q",
        "紫发猫耳 Q 版半身立绘，8 种表情",
    ),
    AvatarPreset(
        "sprite:deepseek_winter",
        "DeepSeek 娘 · 冬装小鲸鱼",
        "sprite",
        f"{BUILTIN_SPRITE_ROOT}/deepseek_winter",
        "鲸鱼连帽冬装 Q 版半身立绘，8 种表情",
    ),
)

CUSTOM_PRESETS: tuple[AvatarPreset, ...] = (
    AvatarPreset("sprite", "自定义图片立绘 / GIF / WebP", "sprite", "", "选择包含表情图片或 manifest.json 的目录"),
    AvatarPreset("live2d", "Live2D 模型（.model3.json）", "live2d", "", "需要安装 live2d-py"),
)

ALL_PRESETS: tuple[AvatarPreset, ...] = BUILTIN_PRESETS + CUSTOM_PRESETS


def _norm(path: str) -> str:
    return (path or "").strip().replace("\\", "/").rstrip("/").casefold()


def get_preset(key: str) -> AvatarPreset | None:
    for preset in ALL_PRESETS:
        if preset.key == key:
            return preset
    return None


def preset_key_for(mode: str, path: str) -> str:
    """根据当前配置反推出预设 key；匹配不到内置包时返回 sprite / live2d 自定义。"""
    mode = (mode or "vector").strip().lower()
    if mode == "vector":
        return "vector"
    if mode == "sprite":
        normalized = _norm(path)
        for preset in BUILTIN_PRESETS:
            if preset.mode != "sprite":
                continue
            target = _norm(preset.path)
            if normalized == target or normalized.endswith("/" + target):
                return preset.key
        return "sprite"
    if mode == "live2d":
        return "live2d"
    return "vector"


def resolve_preset(key: str, current_path: str = "") -> tuple[str, str]:
    """把预设 key 转成 (avatar_mode, avatar_path)。自定义项保留当前路径（内置包路径会被清空）。"""
    preset = get_preset(key)
    if preset is None:
        return "vector", ""
    if preset in BUILTIN_PRESETS:
        return preset.mode, preset.path
    keep = current_path
    if preset_key_for("sprite", current_path) != "sprite":
        keep = ""  # 从内置包切到“自定义”时，不把内置路径误当作用户路径
    return preset.mode, keep


def builtin_pack_dir(pack_id: str) -> Path:
    return Path(__file__).resolve().parents[2] / BUILTIN_SPRITE_ROOT / pack_id


def _pick_thumbnail_file(raw_path: str) -> Path | None:
    from ui.mascot.sprite_pack import _IMAGE_EXTS, resolve_resource_path

    resolved = resolve_resource_path(raw_path)
    if resolved is None:
        return None
    if resolved.is_file():
        return resolved if resolved.suffix.lower() in _IMAGE_EXTS else None
    if not resolved.is_dir():
        return None

    manifest_path = resolved / "manifest.json"
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            default_rel = loaded.get("default") if isinstance(loaded, dict) else None
            if isinstance(default_rel, str) and default_rel.strip():
                candidate = (resolved / default_rel.strip()).resolve()
                if candidate.is_file() and candidate.suffix.lower() in _IMAGE_EXTS:
                    return candidate
        except Exception:
            pass

    for ext in _IMAGE_EXTS:
        candidate = resolved / f"default{ext}"
        if candidate.is_file():
            return candidate
    for item in sorted(resolved.iterdir()):
        if item.is_file() and item.suffix.lower() in _IMAGE_EXTS:
            return item
    return None


def _crop_top_square(pix: QPixmap) -> QPixmap:
    pw, ph = pix.width(), pix.height()
    if pw <= 0 or ph <= 0:
        return pix
    side = min(pw, ph)
    sx = max(0, (pw - side) // 2)
    sy = 0 if pw >= ph else min(int((ph - side) * 0.12), ph - side)
    return pix.copy(sx, sy, side, side)


def render_avatar_thumbnail(
    mode: str,
    path: str = "",
    size: int = 40,
    *,
    circular: bool = True,
) -> QPixmap:
    """生成统一尺寸的形象缩略图（供聊天顶栏、消息气泡、设置导航与预设卡片共用）。"""
    from ui.mascot.vector_catgirl import render_vector_preview

    size = max(16, int(size))
    source: QPixmap | None = None
    if (mode or "").strip().lower() == "sprite":
        target = _pick_thumbnail_file(path)
        if target is not None:
            loaded = QPixmap(str(target))
            if not loaded.isNull():
                source = _crop_top_square(loaded)

    if source is None:
        source = render_vector_preview(size * 2 if circular else size, head_only=True)

    if not circular:
        if source.width() == size and source.height() == size:
            return source
        return source.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )

    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    margin = 1.2
    circle_rect = QRectF(margin, margin, size - margin * 2, size - margin * 2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#fcf9f3"))
    painter.drawEllipse(circle_rect)

    clip = QPainterPath()
    clip.addEllipse(circle_rect)
    painter.save()
    painter.setClipPath(clip)
    painter.drawPixmap(circle_rect, source, QRectF(source.rect()))
    painter.restore()

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#dfd5eb"), 1.2))
    painter.drawEllipse(circle_rect)
    painter.end()
    return out
