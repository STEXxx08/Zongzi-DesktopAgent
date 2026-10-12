import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping
from uuid import uuid4
from condata.harness import CallSpan


ToolHandler = Callable[[Mapping[str, Any]], Any]


class ToolRuntimeError(RuntimeError):
    pass


def _type_problem(value: Any, expected: str) -> str | None:
    """轻量 JSON Schema 类型检查；bool 是 int 的子类，必须单独判断。"""
    if expected == "string":
        return None if isinstance(value, str) else "必须是字符串"
    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return "必须是整数"
        return None
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "必须是数字"
        return None
    if expected == "boolean":
        return None if isinstance(value, bool) else "必须是布尔值"
    if expected == "array":
        return None if isinstance(value, list) else "必须是数组"
    if expected == "object":
        return None if isinstance(value, dict) else "必须是对象"
    if expected == "null":
        return None if value is None else "必须是 null"
    return None


def schema_problem(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> str | None:
    """按工具声明的 schema 校验参数，返回第一处人话描述的问题；无问题返回 None。

    只做必填、类型、enum、additionalProperties 四项检查——足以挡住模型最常见的
    参数错误，又不引入额外依赖。
    """
    if not isinstance(arguments, Mapping):
        return "arguments 必须是 JSON 对象"

    required = schema.get("required") or []
    missing = [key for key in required if key not in arguments]
    if missing:
        return f"缺少必填参数：{', '.join(str(key) for key in missing)}"

    properties = schema.get("properties") or {}
    for key, value in arguments.items():
        expected = properties.get(key)
        if expected is None:
            if schema.get("additionalProperties") is False:
                allowed = "、".join(str(name) for name in properties) or "无"
                return f"不接受参数“{key}”（该工具允许的参数：{allowed}）"
            continue
        if not isinstance(expected, Mapping):
            continue
        wanted = expected.get("type")
        if isinstance(wanted, str):
            problem = _type_problem(value, wanted)
            if problem:
                return f"参数“{key}”{problem}（实际收到 {type(value).__name__}）"
        allowed_values = expected.get("enum")
        if isinstance(allowed_values, list) and value not in allowed_values:
            rendered = "、".join(repr(item) for item in allowed_values)
            return f"参数“{key}”只能是 {rendered} 之一，实际收到 {value!r}"
    return None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    handler: ToolHandler = field(repr=False, compare=False)
    input_schema: dict[str, Any] = field(default_factory=dict)
    tags: frozenset[str] = field(default_factory=frozenset)
    # 仅控制通用文本调用入口；false 不注销工具，也不关闭旧协议或内部调用。
    # 默认不暴露，标签和已加载的 Skill 都不会授予该入口的调用资格。
    model_invocable: bool = False

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name):
            raise ValueError(f"工具名称无效：{self.name}")
        if type(self.model_invocable) is not bool:
            raise ValueError("model_invocable 必须是布尔值")

    def model_schema(self) -> dict[str, Any]:
        """返回原生 tools API 的函数工具定义，不用作文本协议目录。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema or {
                    "type": "object",
                    "properties": {},
                },
            },
        }

    def text_schema(self) -> dict[str, Any]:
        """文本协议的扁平目录，避免暗示模型原生函数调用包装层。"""
        return {
            "name": self.name,
            "description": self.description,
            "arguments_schema": self.input_schema or {"type": "object", "properties": {}},
        }


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    source: str = "runtime"
    conversation_id: str | None = None
    turn_id: int | None = None
    round: int = 1
    call_id: str = field(default_factory=lambda: f"call_{uuid4().hex}")

    def __post_init__(self):
        if self.turn_id is not None and self.turn_id < 1:
            raise ValueError("turn_id 必须从 1 开始")
        if self.round < 1:
            raise ValueError("round 必须从 1 开始")

    def with_context(
        self,
        *,
        conversation_id: str,
        turn_id: int,
        round: int = 1,
    ) -> "ToolCall":
        """附加对话追踪信息，同时保持原有 call_id 不变。"""
        return replace(
            self,
            conversation_id=conversation_id,
            turn_id=turn_id,
            round=round,
        )


@dataclass(frozen=True)
class ToolResult:
    call: ToolCall
    output: Any = None
    error: str | None = None
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


class ToolRuntime:
    """轻量工具注册中心，支持同步调用和按需创建的异步执行器。"""

    def __init__(self, max_workers: int = 4):
        self._tools: dict[str, ToolDefinition] = {}
        self._lock = threading.RLock()
        self._max_workers = max(1, int(max_workers))
        self._executor: ThreadPoolExecutor | None = None
        self._closed = False

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def register(self, definition: ToolDefinition, *, replace: bool = False) -> None:
        with self._lock:
            if definition.name in self._tools and not replace:
                raise ValueError(f"工具已注册：{definition.name}")
            self._tools[definition.name] = definition

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._tools.pop(name, None) is not None

    def get(self, name: str) -> ToolDefinition | None:
        with self._lock:
            return self._tools.get(name)

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        with self._lock:
            return tuple(self._tools.values())

    @property
    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._tools)

    def invoke(self, call: ToolCall) -> ToolResult:
        started = time.perf_counter()
        with CallSpan("tool", call.name, call_id=call.call_id, source=call.source,
                      conversation_id=call.conversation_id, turn_id=call.turn_id,
                      round=call.round, request=call.arguments) as span:
            try:
                definition = self.get(call.name)
                if definition is None:
                    raise ToolRuntimeError(f"Unknown tool: {call.name}")
                problem = schema_problem(definition.input_schema or {}, call.arguments)
                if problem:
                    # 校验失败时给模型一句能自我纠正的说明，而不是工具内部的原始堆栈（P1-12）
                    raise ToolRuntimeError(f"参数校验未通过（工具 {call.name}）：{problem}")
                output = definition.handler(dict(call.arguments))
                span.response = output
                return ToolResult(call=call, output=output, duration_ms=(time.perf_counter() - started) * 1000)
            except Exception as exc:
                span.fail(exc)
                error = str(exc)
            return ToolResult(
                call=call, error=error,
                duration_ms=(time.perf_counter() - started) * 1000,
            )

    def submit(self, call: ToolCall) -> Future[ToolResult]:
        """异步执行工具，不在此处附加对话调度策略。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("工具运行时已经关闭")
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix="ToolRuntime",
                )
            executor = self._executor
        return executor.submit(self.invoke, call)

    def invoke_or_raise(self, call: ToolCall) -> Any:
        result = self.invoke(call)
        if not result.ok:
            raise ToolRuntimeError(result.error)
        return result.output

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        with self._lock:
            self._closed = True
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=cancel_futures)