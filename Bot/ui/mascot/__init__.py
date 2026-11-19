"""桌宠三模渲染器导出模块（按需懒加载各渲染器子模块）。"""

from __future__ import annotations

import importlib

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "MascotCanvas": ("ui.mascot.base", "MascotCanvas"),
    "MascotRendererBase": ("ui.mascot.base", "MascotRendererBase"),
    "normalize_emotion_kind": ("ui.mascot.base", "normalize_emotion_kind"),
    "normalize_state_key": ("ui.mascot.base", "normalize_state_key"),
    "draw_ground_shadow": ("ui.mascot.base", "draw_ground_shadow"),
    "draw_thinking_halo": ("ui.mascot.base", "draw_thinking_halo"),
    "draw_state_badge": ("ui.mascot.base", "draw_state_badge"),
    "Live2DRenderer": ("ui.mascot.live2d_view", "Live2DRenderer"),
    "ALL_PRESETS": ("ui.mascot.presets", "ALL_PRESETS"),
    "BUILTIN_PRESETS": ("ui.mascot.presets", "BUILTIN_PRESETS"),
    "AvatarPreset": ("ui.mascot.presets", "AvatarPreset"),
    "get_preset": ("ui.mascot.presets", "get_preset"),
    "preset_key_for": ("ui.mascot.presets", "preset_key_for"),
    "render_avatar_thumbnail": ("ui.mascot.presets", "render_avatar_thumbnail"),
    "resolve_preset": ("ui.mascot.presets", "resolve_preset"),
    "SpritePackRenderer": ("ui.mascot.sprite_pack", "SpritePackRenderer"),
    "resolve_resource_path": ("ui.mascot.sprite_pack", "resolve_resource_path"),
    "VectorCatgirlRenderer": ("ui.mascot.vector_catgirl", "VectorCatgirlRenderer"),
    "create_mascot_tray_icon": ("ui.mascot.vector_catgirl", "create_mascot_tray_icon"),
    "render_vector_preview": ("ui.mascot.vector_catgirl", "render_vector_preview"),
}

__all__ = [
    "MascotCanvas",
    "MascotRendererBase",
    "VectorCatgirlRenderer",
    "SpritePackRenderer",
    "Live2DRenderer",
    "AvatarPreset",
    "ALL_PRESETS",
    "BUILTIN_PRESETS",
    "get_preset",
    "preset_key_for",
    "resolve_preset",
    "render_avatar_thumbnail",
    "create_mascot_tray_icon",
    "render_vector_preview",
    "normalize_emotion_kind",
    "normalize_state_key",
    "draw_ground_shadow",
    "draw_thinking_halo",
    "draw_state_badge",
    "resolve_resource_path",
]


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_path, attr_name = target
    value = getattr(importlib.import_module(mod_path), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
