import logging
from typing import Any

from pydantic import BaseModel, Field

from services.config_loader import load_service_subconfig

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


def load_intent_router_config(root_config: Any = None) -> IntentRouterConfig:
    return load_service_subconfig(
        "intent_router",
        IntentRouterConfig,
        root_config,
        label="intent-router",
    )