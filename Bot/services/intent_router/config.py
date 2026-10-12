import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class IntentDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    action: str = Field(pattern="^(tool|web_tool|vision|none)$")
    tool: str | None = None
    argument_name: str = Field(default="query", pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    examples: list[str] = Field(min_length=1)
    strip_patterns: list[str] = Field(default_factory=list)
    default_query: str = ""
    acknowledgement: str | None = None


class IntentRouterConfig(BaseModel):
    enable: bool = False
    backend: str = Field(default="onnx", pattern="^onnx$")
    model_path: str = "models/bge-small-zh-v1.5-onnx"
    onnx_model: str = "onnx/model_quantized.onnx"
    providers: list[str] = Field(default_factory=lambda: ["CPUExecutionProvider"])
    max_length: int = Field(default=64, ge=8, le=512)
    intra_op_threads: int = Field(default=1, ge=1, le=16)
    inter_op_threads: int = Field(default=1, ge=1, le=16)
    confidence_threshold: float = Field(default=0.72, ge=-1.0, le=1.0)
    margin_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    fallback_to_keywords: bool = False
    intents: list[IntentDefinition] = Field(default_factory=list)


def _plain(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return None


def _yaml_fallback() -> dict | None:
    bot_config = Path(__file__).resolve().parents[2] / "config.yaml"
    for path in (bot_config,):
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as file:
                root = yaml.safe_load(file) or {}
            value = root.get("service", {}).get("intent_router")
            if isinstance(value, dict):
                return value
        except Exception as exc:
            logger.warning(f"Failed to load intent-router config from {path}: {exc}")
    return None


def load_intent_router_config(root_config: Any = None) -> IntentRouterConfig:
    service = getattr(root_config, "service", None) if root_config is not None else None
    raw = _plain(getattr(service, "intent_router", None))
    if raw is None:
        raw = _yaml_fallback()
    try:
        return IntentRouterConfig.model_validate(raw or {})
    except Exception as exc:
        logger.error(f"Invalid intent-router config; router disabled: {exc}")
        return IntentRouterConfig()