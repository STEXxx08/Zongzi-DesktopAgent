import logging
from condata.harness import CallSpan
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from services.intent_router.config import IntentDefinition, IntentRouterConfig
from services.tool_runtime import ToolCall, ToolRuntime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteDecision:
    intent: str
    action: str
    score: float
    margin: float
    tool_call: ToolCall | None = None
    acknowledgement: str | None = None

    # Compatibility properties for existing integrations.
    @property
    def name(self) -> str:
        return self.intent

    @property
    def tool(self) -> str | None:
        return self.tool_call.name if self.tool_call is not None else None

    @property
    def query(self) -> str:
        if self.tool_call is None:
            return ""
        value = next(iter(self.tool_call.arguments.values()), "")
        return str(value)


IntentMatch = RouteDecision

# ONNX 允许把权重放在模型文件外的 *.onnx_data 里（optimum 导出默认这么做）。
# 文件名字符串在 protobuf 里是裸存的，因此可以直接从字节里扫出来。
_EXTERNAL_DATA_PATTERN = re.compile(rb"[\w.\-]{1,128}\.onnx_data")


def resolve_model_dir(model_path: str) -> Path:
    """解析模型目录：相对路径先按启动目录找，找不到再按 Bot 项目根找。"""
    directory = Path(model_path)
    if directory.is_absolute() or directory.exists():
        return directory
    candidate = Path(__file__).resolve().parents[2] / directory
    return candidate if candidate.exists() else directory


def missing_external_data(model_file: Path) -> list[str]:
    """列出模型引用、但同目录下并不存在的 *.onnx_data 外置权重文件。

    onnxruntime 找不到外置权重时抛出的并不是"文件不存在"，而是拼装错误信息
    时按 UTF-8 解码非 ASCII 路径失败产生的 UnicodeDecodeError，真实原因被完全
    掩盖。所以这里先自己检查一遍，把缺文件的情况翻译成人话。
    """
    try:
        payload = model_file.read_bytes()
    except OSError:
        return []
    names = {
        match.decode("ascii", "ignore")
        for match in _EXTERNAL_DATA_PATTERN.findall(payload)
    }
    return sorted(name for name in names if not (model_file.parent / name).exists())


class _ONNXBGEEmbedder:
    def __init__(self, config: IntentRouterConfig):
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "BGE intent router requires onnxruntime and transformers"
            ) from exc

        model_dir = resolve_model_dir(config.model_path)
        model_file = model_dir / config.onnx_model
        if not model_file.exists():
            raise FileNotFoundError(f"BGE ONNX model not found: {model_file}")
        absent_weights = missing_external_data(model_file)
        if absent_weights:
            raise FileNotFoundError(
                f"BGE ONNX model {model_file.name} needs external weight file(s) "
                f"{', '.join(absent_weights)} next to it in {model_file.parent}; only "
                f"{model_file.name} was downloaded (see services/intent_router/README.md)"
            )

        options = ort.SessionOptions()
        options.intra_op_num_threads = config.intra_op_threads
        options.inter_op_num_threads = config.inter_op_threads
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            self.session = ort.InferenceSession(
                str(model_file),
                sess_options=options,
                providers=config.providers,
            )
        except UnicodeDecodeError as exc:
            # onnxruntime 在 Windows 上拼装错误信息时按 UTF-8 解码 ANSI 路径，
            # 只要模型路径含中文，真实错误就会变成这个解码异常。
            raise RuntimeError(
                f"onnxruntime could not load {model_file}; the underlying error is masked by "
                f"a path-encoding bug in onnxruntime (non-ASCII path or unreadable model "
                f"directory): {exc}"
            ) from exc
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(model_dir), local_files_only=True
        )
        self.max_length = config.max_length
        self.input_names = {item.name for item in self.session.get_inputs()}

    def __call__(self, texts: list[str]) -> np.ndarray:
        encoded = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="np",
        )
        inputs = {
            name: np.asarray(value, dtype=np.int64)
            for name, value in encoded.items()
            if name in self.input_names
        }
        output = self.session.run(None, inputs)[0]
        embeddings = output[:, 0, :] if output.ndim == 3 else output
        return self._normalize(np.asarray(embeddings, dtype=np.float32))

    @staticmethod
    def _normalize(values: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        return values / np.maximum(norms, 1e-12)


class BGEIntentRouter:
    def __init__(
        self,
        config: IntentRouterConfig,
        embedder: Callable[[list[str]], np.ndarray] | None = None,
        tool_runtime: ToolRuntime | None = None,
        auto_initialize: bool = True,
    ):
        self.config = config
        self.enabled = config.enable
        self.available = False
        self._embedder = embedder
        self.tool_runtime = tool_runtime
        self._example_embeddings: list[np.ndarray] = []
        if self.enabled and auto_initialize:
            self.initialize()

    def initialize(self):
        if not self.enabled or self.available:
            return
        try:
            self._embedder = self._embedder or _ONNXBGEEmbedder(self.config)
            example_embeddings = []
            for intent in self.config.intents:
                embeddings = np.asarray(self._embedder(intent.examples), dtype=np.float32)
                example_embeddings.append(self._normalize(embeddings))
            self._example_embeddings = example_embeddings
            self.available = bool(self.config.intents)
            logger.info(
                f"BGE intent router initialized: intents={len(self.config.intents)}, "
                f"model={self.config.model_path}"
            )
        except Exception as exc:
            logger.error(f"BGE intent router disabled after initialization failure: {exc}")
            self.available = False

    @staticmethod
    def _normalize(values: np.ndarray) -> np.ndarray:
        if values.ndim == 1:
            values = values[None, :]
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        return values / np.maximum(norms, 1e-12)

    @property
    def allow_keyword_fallback(self) -> bool:
        """Whether the legacy keyword router may handle the current input.

        Enabling the semantic router is an explicit choice to replace legacy
        keyword matching.  A model initialization failure must therefore fall
        through to cloud model routing unless keyword fallback was explicitly
        requested in configuration.
        """
        return not self.enabled or self.config.fallback_to_keywords

    def route(self, text: str, *, context: dict | None = None) -> RouteDecision | None:
        if not self.enabled or not self.available or not text.strip():
            return None
        with CallSpan("model", "BGEIntentRouter.route", source="local_router",
                      model_id=self.config.model_path, request={"text": text}, **(context or {})) as span:
            result = self._route(text)
            span.response = result
            return result

    def _route(self, text: str) -> RouteDecision | None:
        if not self.enabled or not self.available or not text.strip():
            return None
        query_embedding = self._normalize(
            np.asarray(self._embedder([text]), dtype=np.float32)
        )[0]
        scores = [
            float(np.max(examples @ query_embedding))
            for examples in self._example_embeddings
        ]
        order = np.argsort(scores)[::-1]
        best_index = int(order[0])
        best_score = scores[best_index]
        second_score = scores[int(order[1])] if len(order) > 1 else -1.0
        margin = best_score - second_score
        if (
            best_score < self.config.confidence_threshold
            or margin < self.config.margin_threshold
        ):
            logger.debug(
                f"Intent router rejected: score={best_score:.3f}, margin={margin:.3f}, "
                f"text={text}"
            )
            return None

        intent = self.config.intents[best_index]
        if intent.action == "none":
            return None
        query = self._extract_query(text, intent)
        is_tool_action = intent.action in {"tool", "web_tool"}
        if is_tool_action and not intent.tool:
            logger.warning(f"Intent '{intent.name}' has no tool configured")
            return None
        if is_tool_action and self.tool_runtime is not None:
            if self.tool_runtime.get(intent.tool) is None:
                logger.warning(
                    f"Intent '{intent.name}' references unknown tool: {intent.tool}"
                )
                return None
        tool_call = None
        if is_tool_action:
            tool_call = ToolCall(
                name=intent.tool,
                arguments={intent.argument_name: query},
                source=f"local_intent:{intent.name}",
            )
        match = RouteDecision(
            intent=intent.name,
            action="tool" if is_tool_action else intent.action,
            score=best_score,
            margin=margin,
            tool_call=tool_call,
            acknowledgement=intent.acknowledgement,
        )
        logger.info(
            f"Intent matched: name={match.name}, tool={match.tool}, "
            f"score={match.score:.3f}, margin={match.margin:.3f}, query={match.query}"
        )
        return match

    def classify(self, text: str) -> RouteDecision | None:
        """Backward-compatible alias for callers not yet using route()."""
        return self.route(text)

    @staticmethod
    def _extract_query(text: str, intent: IntentDefinition) -> str:
        query = text.strip()
        for pattern in intent.strip_patterns:
            try:
                query = re.sub(pattern, " ", query, flags=re.IGNORECASE)
            except re.error as exc:
                logger.warning(
                    f"Invalid strip pattern for intent '{intent.name}': {pattern}: {exc}"
                )
        # Intent-specific patterns remove semantic words such as "天气".  Then
        # remove only command/politeness text at the boundaries so tool arguments
        # like a city, game name, or repository remain intact.
        query = re.sub(
            r"^(?:(?:那|那么|你|再|请|请你|帮我|麻烦(?:你)?|给我|"
            r"能不能|可以(?:帮我)?|搜一下|搜索一下|搜|搜索|"
            r"查一下|查询一下|查|查询|看一下|看看|看下)\s*)+",
            "",
            query.strip(),
            flags=re.IGNORECASE,
        )
        query = re.sub(r"^的\s*", "", query.strip())
        query = re.sub(r"(?:好不好|怎么样|吧|啊|呀|呢|吗)+$", "", query.strip())
        query = re.sub(r"\s*的$", "", query.strip())
        query = re.sub(r"\s+", " ", query).strip(" 　，,。.!！?？:：")
        return query or intent.default_query