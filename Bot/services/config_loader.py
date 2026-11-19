"""轻量服务配置加载与 Pydantic 模型字典转换辅助。

注意：本模块刻意保持极简依赖（仅标准库 + yaml），禁止导入 `services.config` 或
业务重模块，以免在配置加载阶段引发循环导入或拖慢冷启动。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, TypeVar

import yaml

logger = logging.getLogger(__name__)

T = TypeVar("T")


def dump_pydantic_model(value: Any, *, allow_dict_attr: bool = False) -> dict | None:
    """将 Pydantic v1/v2 模型或字典转为普通 dict；无法识别时返回 None。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if allow_dict_attr:
        return dict(getattr(value, "__dict__", {}))
    return None


def load_yaml_section(
    section_path: tuple[str, ...],
    *,
    config_path: Path | None = None,
    label: str = "service",
) -> dict | None:
    """从 config.yaml 中按路径读取一段字典配置；缺失或失败返回 None。"""
    target = config_path or (Path(__file__).resolve().parents[1] / "config.yaml")
    if not target.exists():
        return None
    try:
        with target.open("r", encoding="utf-8") as file:
            node: Any = yaml.safe_load(file) or {}
        for key in section_path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node if isinstance(node, dict) else None
    except Exception as exc:
        logger.warning(f"Failed to load {label} config from {target}: {exc}")
        return None


def load_service_subconfig(
    section_key: str,
    config_cls: type[T],
    root_config: Any = None,
    *,
    config_path: Path | None = None,
    label: str | None = None,
) -> T:
    """优先从已加载的 root_config.service.<section_key> 读取，缺失时回退到 config.yaml。"""
    service_config = getattr(root_config, "service", None) if root_config is not None else None
    raw_config = dump_pydantic_model(getattr(service_config, section_key, None))
    if raw_config is None:
        raw_config = load_yaml_section(
            ("service", section_key),
            config_path=config_path,
            label=label or section_key,
        )
    try:
        return config_cls.model_validate(raw_config or {})
    except Exception as exc:
        logger.error(f"Invalid {label or section_key} config; using defaults: {exc}")
        return config_cls()
