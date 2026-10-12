import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable

from services.tool_runtime import (
    ToolCall,
    ToolResult,
    ToolRuntime,
)

logger = logging.getLogger(__name__)


class ToolCoordinationStatus(str, Enum):
    PASS_THROUGH = "pass_through"
    STARTED = "started"
    BUFFERED = "buffered"
    REJECTED = "rejected"


class ToolCoordinatorPhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    FOLLOWUP = "followup"


@dataclass(frozen=True)
class ToolCoordinationSubmission:
    status: ToolCoordinationStatus
    calls: tuple[ToolCall, ...] = ()


BatchReadyCallback = Callable[[Any, list[Any], list[ToolResult]], None]
DeferredCallback = Callable[[list[Any]], None]


@dataclass
class _ActiveBatch:
    calls: tuple[ToolCall, ...]
    primary: Any
    on_ready: BatchReadyCallback
    on_deferred: DeferredCallback
    results: dict[str, ToolResult] = field(default_factory=dict)
    buffered: list[Any] = field(default_factory=list)
    deferred: list[Any] = field(default_factory=list)


class ToolCoordinator:
    """围绕无状态 ToolRuntime 提供对话级工具调度。"""

    def __init__(
        self,
        runtime: ToolRuntime,
        *,
        max_rounds: int = 5,
        max_calls_per_round: int = 8,
    ):
        self.runtime = runtime
        self._lock = threading.RLock()
        self._phase = ToolCoordinatorPhase.IDLE
        self._active: _ActiveBatch | None = None
        self.update_limits(
            max_rounds=max_rounds,
            max_calls_per_round=max_calls_per_round,
        )

    def update_limits(
        self,
        *,
        max_rounds: int,
        max_calls_per_round: int,
    ) -> None:
        """更新准入限制，不中断当前正在执行的工具批次。"""
        with self._lock:
            self.max_rounds = max(1, int(max_rounds))
            self.max_calls_per_round = max(1, int(max_calls_per_round))

    @property
    def phase(self) -> ToolCoordinatorPhase:
        with self._lock:
            return self._phase

    def is_busy(self) -> bool:
        return self.phase != ToolCoordinatorPhase.IDLE

    def admit(self, payload: Any) -> ToolCoordinationStatus:
        with self._lock:
            active = self._active
            if active is None:
                return ToolCoordinationStatus.PASS_THROUGH
            target = (
                active.buffered
                if self._phase == ToolCoordinatorPhase.RUNNING
                else active.deferred
            )
            target.append(payload)
            return ToolCoordinationStatus.BUFFERED

    def submit(
        self,
        calls: ToolCall | Iterable[ToolCall],
        *,
        primary: Any,
        on_ready: BatchReadyCallback,
        on_deferred: DeferredCallback,
        continue_active: bool = False,
    ) -> ToolCoordinationSubmission:
        batch_calls = (calls,) if isinstance(calls, ToolCall) else tuple(calls)
        if not batch_calls:
            raise ValueError("至少需要一个工具调用")
        if len(batch_calls) > self.max_calls_per_round:
            logger.warning(
                "工具批次包含 %d 个调用，超过配置上限 %d，已拒绝",
                len(batch_calls),
                self.max_calls_per_round,
            )
            return ToolCoordinationSubmission(status=ToolCoordinationStatus.REJECTED)
        if any(call.round > self.max_rounds for call in batch_calls):
            logger.warning(
                "工具调用轮数超过配置上限 %d，已拒绝",
                self.max_rounds,
            )
            return ToolCoordinationSubmission(status=ToolCoordinationStatus.REJECTED)

        with self._lock:
            if self._active is not None:
                if not continue_active:
                    return ToolCoordinationSubmission(status=self.admit(primary))
                if self._phase != ToolCoordinatorPhase.FOLLOWUP:
                    return ToolCoordinationSubmission(
                        status=ToolCoordinationStatus.REJECTED
                    )
                previous = self._active
                if not self._is_same_turn(previous.calls, batch_calls):
                    return ToolCoordinationSubmission(
                        status=ToolCoordinationStatus.REJECTED
                    )
                inherited = list(previous.deferred)
                previous.deferred.clear()
                active = _ActiveBatch(
                    calls=batch_calls,
                    primary=primary,
                    on_ready=on_ready,
                    on_deferred=on_deferred,
                    buffered=inherited,
                )
                self._active = active
                self._phase = ToolCoordinatorPhase.RUNNING
            else:
                active = _ActiveBatch(
                    calls=batch_calls,
                    primary=primary,
                    on_ready=on_ready,
                    on_deferred=on_deferred,
                )
                self._active = active
                self._phase = ToolCoordinatorPhase.RUNNING

        try:
            for call in batch_calls:
                future = self.runtime.submit(call)
                future.add_done_callback(
                    lambda completed, call_id=call.call_id: self._on_completed(
                        active, call_id, completed
                    )
                )
        except Exception:
            with self._lock:
                if self._active is active:
                    self._active = None
                    self._phase = ToolCoordinatorPhase.IDLE
            raise

        return ToolCoordinationSubmission(
            status=ToolCoordinationStatus.STARTED,
            calls=batch_calls,
        )

    @staticmethod
    def _is_same_turn(
        current: tuple[ToolCall, ...],
        following: tuple[ToolCall, ...],
    ) -> bool:
        current_call = current[0]
        following_call = following[0]
        return (
            current_call.conversation_id == following_call.conversation_id
            and current_call.turn_id == following_call.turn_id
            and following_call.round > current_call.round
        )

    def _on_completed(self, active: _ActiveBatch, call_id: str, future) -> None:
        try:
            result = future.result()
        except Exception as exc:
            call = next(item for item in active.calls if item.call_id == call_id)
            result = ToolResult(call=call, error=str(exc))

        with self._lock:
            if self._active is not active:
                return
            active.results[call_id] = result
            if len(active.results) < len(active.calls):
                return
            self._phase = ToolCoordinatorPhase.FOLLOWUP
            buffered = list(active.buffered)
            ordered_results = [active.results[call.call_id] for call in active.calls]

        try:
            active.on_ready(active.primary, buffered, ordered_results)
        except Exception:
            logger.exception("工具批次的后续处理失败")
        finally:
            with self._lock:
                if self._active is active:
                    deferred = list(active.deferred)
                    self._active = None
                    self._phase = ToolCoordinatorPhase.IDLE
                else:
                    # 后续回调已经启动下一轮工具调用，新批次继承了本次
                    # LLM 调用期间收到的输入，因此旧批次无需再处理它们。
                    deferred = []
            if deferred:
                try:
                    active.on_deferred(deferred)
                except Exception:
                    logger.exception("延后工具请求处理失败")