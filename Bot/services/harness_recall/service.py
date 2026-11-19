"""Harness 工具记录回读：把"本次运行内执行过的工具调用"暴露给模型（`harness_recall`）。

动机：工具原文只进当轮的 `TOOL_RESULT` 块，不写对话历史、不写长期记忆
（`bot.py` 里 `has_pending_tool` 分支只缓存当前 turn）。所以多轮工具链之后，
模型看不见上一轮到底返回了什么；用户说"那个结果不对，你去核一下"，模型手里
一张牌都没有。记录在 harness 日志里（`Bot/docs/HARNESS_LOGGING.md`），但它是
append-only 的审计文件，**不能整份塞给模型**，也不能整份读进内存。

三条边界就是本模块的全部设计：

1. **窗口**：只读"本次进程启动之后写入"的部分。左边界取一次性字节偏移
   （`boot_offset`），不是时间戳也不是 call_id——模型记不住 32 位 hex，但这个
   偏移是进程内的确定性事实。再按 `conversation_id` 收窄，跨会话不外泄。
   读取路径取 `service.harness.path`（与写入侧同一份配置、同一套相对路径规则），
   热重载后随配置一起变；不硬编码、也不另存第二份路径。
2. **预算**：从文件末尾按 256 KiB 块倒着扫，先做字节级预筛（`kind=tool` /
   `event=finished` / `conversation_id`），命中的行才 `json.loads`。行数、字节数、
   条数三个上限任一触顶都在结果里显式标注，不假装读全了。
3. **模糊**：模型拿不到 `call_id`。所以两段式——默认给最近若干次调用的索引
   （序号、工具名、参数/结果摘要），再按 `ordinal` 或 `call_id` 取全文。
   `harness_recall({})` 零参数就有答案，覆盖"检查上一轮的工具调用"。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from loguru import logger

from services.harness_recall.config import HarnessRecallConfig
from services.tool_runtime import ToolDefinition, ToolRuntime

TOOL_NAME = "harness_recall"

TOOL_DESCRIPTION = (
    "回读**本次运行**里已经执行过的工具调用（参数和结果）。"
    "不确定上一轮工具返回了什么、觉得结果不对想核对、或用户追问“刚才那个查询”时使用。"
    "默认返回最近几次调用的索引（带序号），确认要看哪条后再用 ordinal 取全文。"
)

MODEL_INSTRUCTION = (
    "【工具记录回读】harness_recall 只能看到**本次运行**、**本次对话**的工具调用记录；"
    "它先给索引（#序号、工具名、参数与结果摘要），确认要看哪条再用 ordinal 取全文。"
    "工具原文不在对话历史里，反复查询也改变不了这一点，不要为了猜参数空转。"
)

# 日志行在此之上做字节级预筛，避免把几千行都 json.loads 一遍。
_CHUNK_BYTES = 256 * 1024
_TOOL_FINISHED = b'"kind": "tool"'
_EVENT_FINISHED = b'"event": "finished"'

# 同一组查询参数在这个时间内重复出现，直接提示"结果就在上一轮"。
_REPEAT_WINDOW_SECONDS = 120.0


def resolve_log_path(project_root: Path | str, relative_path: str) -> Path:
    """按**写入侧同一套规则**把配置里的相对路径解析成绝对路径。

    写入侧的规则在 `condata.harness.configure_harness`：必须是应用目录内的相对路径，
    链接也不能越出应用根。这里照抄同样的两条校验，保证读的就是写的那一个文件；
    唯一例外是 `enable=false`（没有 writer）与打开失败，那两种情况读侧会明确报出来，
    不会静默返回空。
    """
    relative = Path(str(relative_path))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("harness 日志路径必须是应用目录内的相对路径")
    root = Path(project_root).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError("harness 日志路径不能越出应用目录")
    return target


def boot_offset(project_root: Path | str, relative_path: str) -> int:
    """进程启动时日志文件的字节长度——回读窗口的左边界。

    必须在 `configure_harness` 之后调用一次，且**配置热重载时不要重新取**：
    重取会把窗口清零，症状就是"工具突然什么都查不到"。
    """
    try:
        return max(int(resolve_log_path(project_root, relative_path).stat().st_size), 0)
    except (OSError, ValueError):
        return 0


@dataclass
class _ScanStats:
    scanned_bytes: int = 0
    scanned_lines: int = 0
    notes: list[str] = field(default_factory=list)
    record_cap_hit: bool = False


def _iter_recent_lines(
    path: Path,
    start_offset: int,
    stats: _ScanStats,
    *,
    max_scan_bytes: int,
    max_scan_lines: int,
) -> Iterator[bytes]:
    """从文件末尾向前逐块产出原始行（最新在前），不整文件读入内存。"""
    try:
        size = path.stat().st_size
    except OSError:
        stats.notes.append("日志文件读不到")
        return
    if start_offset < 0 or start_offset > size:
        # 文件被轮转或截断：窗口起点已不在当前文件里，退化成从文件开头扫。
        stats.notes.append("启动窗口的起点已不在当前日志里（发生过轮转），已从文件开头扫描")
        start_offset = 0
    start = max(start_offset, size - max_scan_bytes)
    if start > start_offset:
        stats.notes.append(f"已到字节上限（{max_scan_bytes // 1024} KB），只看最近一段")
    position = size
    buffer = b""
    with path.open("rb") as handle:
        while position > start:
            chunk_size = min(_CHUNK_BYTES, position - start)
            position -= chunk_size
            handle.seek(position)
            block = handle.read(chunk_size)
            if position + chunk_size == size and block.endswith(b"\n"):
                block = block[:-1]
            buffer = block + buffer
            lines = buffer.split(b"\n")
            buffer = lines.pop(0)  # 头部可能是半行，留给下一块拼
            stats.scanned_bytes += chunk_size
            for raw in reversed(lines):
                stats.scanned_lines += 1
                yield raw
                if stats.scanned_lines >= max_scan_lines:
                    stats.notes.append(f"已到行数上限（{max_scan_lines} 行）")
                    return
    if buffer:
        stats.scanned_lines += 1
        yield buffer


def _render_payload(value: Any, limit: int) -> str:
    """把日志字段渲染成人话；认 condata 写入侧的两种包装形状。"""
    if value is None:
        return "（未记录）"
    note = ""
    if isinstance(value, dict) and "truncated" in value and "text" in value:
        text = str(value.get("text") or "")
        note = f"…（原文 {value.get('original_chars', '?')} 字符，日志侧已截断）"
    elif isinstance(value, dict) and "bytes" in value and "content" in value:
        return str(value.get("content") or "")
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + f"…（共 {len(text)} 字符）"
    return text + note


class HarnessRecallService:
    """把"回读工具记录"注册成共享工具运行时里的一个只读工具。"""

    def __init__(
        self,
        runtime: ToolRuntime,
        config_provider: Callable[[], Any],
        *,
        project_root: Path | str,
        session_offset: int = 0,
    ):
        self.runtime = runtime
        self._config_provider = config_provider
        self._project_root = Path(project_root)
        self._session_offset = int(session_offset)
        self._session_path: Path | None = None
        self._conversation_provider: Callable[[], str | None] = lambda: None
        self._lock = threading.Lock()
        self._last_index: list[dict] = []
        self._last_key: tuple | None = None
        self._last_at = 0.0
        self._register()

    # ---------- 配置 ----------

    def _config(self) -> HarnessRecallConfig:
        return self._config_provider().service.harness_recall

    def update_config(self) -> None:
        """配置热重载后重新评估是否应向 ToolRuntime 注册 harness_recall。"""
        self._register()

    def _tool_config(self):
        return self._config_provider().service.tool

    def _log_path(self) -> tuple[Path | None, str]:
        """读路径 == 写路径：同一份配置、同一套相对路径规则、同一次热重载。"""
        harness = self._config_provider().service.harness
        if not harness.enable:
            return None, "写入未启用（service.harness.enable=false）"
        try:
            return resolve_log_path(self._project_root, harness.path), ""
        except ValueError:
            return None, "service.harness.path 不合法（必须是应用目录内的相对路径）"

    def model_instruction(self) -> str | None:
        return MODEL_INSTRUCTION if self._config().enable else None

    def set_conversation_provider(self, provider: Callable[[], str | None]) -> None:
        self._conversation_provider = provider

    # ---------- 注册 ----------

    def _register(self) -> None:
        self.runtime.unregister(TOOL_NAME)
        if not self._config().enable or not self._config_provider().service.harness.enable:
            # 写入关掉时注册只会白吃一轮工具预算（结果永远是"写入未启用"），不如不注册。
            logger.info("工具记录回读未注册（service.harness_recall.enable 或 service.harness.enable 为 false）。")
            return
        self.runtime.register(ToolDefinition(
            name=TOOL_NAME,
            description=TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["turn", "recent", "session"],
                        "description": "窗口：turn=当前这一轮；recent=最近几个 turn（默认）；session=本次运行的全部。",
                    },
                    "turns_back": {
                        "type": "integer",
                        "description": "scope=recent 时往前看几个 turn，默认 2。",
                    },
                    "round": {
                        "type": "integer",
                        "description": "只保留某个工具轮次的记录（可选）。",
                    },
                    "tool": {
                        "type": "string",
                        "description": "只保留某个工具名的记录，例如 web_search（可选）。",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "按字面包含过滤参数与结果，多个词用空格分隔表示同时满足（可选）。",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "索引最多返回几条，默认取配置上限。",
                    },
                    "ordinal": {
                        "type": "integer",
                        "description": "取上一次索引里第 N 条（从 1 开始）的完整记录。",
                    },
                    "call_id": {
                        "type": "string",
                        "description": "精确取某个 call_id 的完整记录（可选，通常用 ordinal 更省事）。",
                    },
                },
                "additionalProperties": False,
            },
            tags=frozenset({"harness", "read", "diagnostic"}),
            model_invocable=True,
            handler=self.recall,
        ))
        logger.info("工具记录回读已注册：harness_recall（model_invocable）。")

    # ---------- 工具入口 ----------

    def recall(self, arguments: dict) -> str:
        args = dict(arguments or {})
        config = self._config()
        call_id = str(args.get("call_id") or "").strip()
        ordinal = args.get("ordinal")
        if call_id or isinstance(ordinal, int):
            return self._fetch_full(config, call_id=call_id, ordinal=ordinal)
        return self._index(config, args)

    # ---------- 索引（模式 A） ----------

    def _index(self, config: HarnessRecallConfig, args: dict) -> str:
        key = (
            args.get("scope"), args.get("turns_back"), args.get("round"),
            args.get("tool"), args.get("keyword"), args.get("limit"),
        )
        now = time.monotonic()
        with self._lock:
            if key == self._last_key and now - self._last_at < _REPEAT_WINDOW_SECONDS:
                return (
                    "【参数与上一次完全相同】结果已经在你上一轮的工具结果里了。"
                    "要看更早的记录请把 scope 改成 session；要收窄就加 keyword / round / tool；"
                    "要全文就用索引里的 ordinal。"
                )
            self._last_key, self._last_at = key, now

        scope = str(args.get("scope") or "recent").strip().lower()
        if scope not in {"turn", "recent", "session"}:
            scope = "recent"
        limit = self._clamp_limit(args.get("limit"), config.max_records)
        # 多扫一条：用来判断"窗口里还有更早的调用没展开"，不能只看 max_records 就说齐全。
        records, stats = self._scan(limit_records=config.max_records + 1)

        if not records:
            return self._empty_message(stats, config)

        if scope != "session":
            if scope == "turn":
                turns_back = 0
            else:
                raw_turns = args.get("turns_back")
                if isinstance(raw_turns, int):
                    turns_back = min(max(raw_turns, 0), config.max_turns_back)
                else:
                    turns_back = min(config.default_turns_back, config.max_turns_back)
            records = self._slice_turns(records, turns_back)

        records = self._apply_filters(records, args)
        if not records:
            return self._empty_message(stats, config, filtered=True)

        selected = records[:limit]
        if len(records) > limit:
            total = f"至少 {len(records)}" if stats.record_cap_hit else str(len(records))
            stats.notes.append(
                f"命中 {total} 条，只返回最近 {limit} 条；更早的请用 keyword / round / tool 收窄"
            )
        elif stats.record_cap_hit:
            stats.notes.append("窗口里还有更早的调用没展开，可用 scope=session 或加过滤条件")
        with self._lock:
            self._last_index = selected
        return self._render_index(selected, stats, config)

    # ---------- 取全文（模式 B） ----------

    def _fetch_full(self, config: HarnessRecallConfig, *, call_id: str, ordinal: Any) -> str:
        record: dict | None = None
        if call_id:
            records, stats = self._scan(call_id=call_id, limit_records=1)
            record = records[0] if records else None
            if record is None:
                return (
                    f"【没找到 call_id={call_id} 的记录】它可能不属于本次运行或本次对话。"
                    "先不带参数调用一次 harness_recall 取索引，再用其中的序号。"
                )
        else:
            try:
                position = int(ordinal)
            except (TypeError, ValueError):
                position = 0
            with self._lock:
                cached = list(self._last_index)
            if not 1 <= position <= len(cached):
                return (
                    f"【序号 {ordinal} 不在上一次索引里】上一次索引共 {len(cached)} 条。"
                    "先不带参数调用一次 harness_recall 取索引，再用其中的序号。"
                )
            record = cached[position - 1]
        return self._render_full(record, config)

    # ---------- 扫描 ----------

    def _scan(
        self,
        *,
        call_id: str = "",
        limit_records: int | None = None,
    ) -> tuple[list[dict], _ScanStats]:
        config = self._config()
        stats = _ScanStats()
        path, problem = self._log_path()
        if path is None:
            stats.notes.append(problem)
            return [], stats
        if self._session_path is None:
            self._session_path = path
        elif path != self._session_path:
            stats.notes.append("写入路径在本次运行中被改过，窗口按新文件的起点计算")
            self._session_offset = 0
            self._session_path = path

        conversation = (self._conversation_provider() or "").strip()
        needles = [_TOOL_FINISHED, _EVENT_FINISHED]
        if conversation:
            needles.append(b'"conversation_id": "' + conversation.encode("utf-8") + b'"')
        if call_id:
            needles.append(b'"call_id": "' + call_id.encode("utf-8") + b'"')

        records: list[dict] = []
        for raw in _iter_recent_lines(
            path,
            self._session_offset,
            stats,
            max_scan_bytes=config.max_scan_bytes,
            max_scan_lines=config.max_scan_lines,
        ):
            if not raw or any(needle not in raw for needle in needles):
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if record.get("kind") != "tool" or record.get("event") != "finished":
                continue
            if call_id and record.get("call_id") != call_id:
                continue
            records.append(record)
            if limit_records is not None and len(records) >= limit_records:
                stats.record_cap_hit = True
                break
        return records, stats

    # ---------- 过滤 ----------

    @staticmethod
    def _clamp_limit(value: Any, ceiling: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            return ceiling
        return min(max(value, 1), ceiling)

    @staticmethod
    def _slice_turns(records: list[dict], turns_back: int) -> list[dict]:
        """records 是时间倒序；按 turn 分组，只留最近 (turns_back + 1) 个 turn。"""
        seen: list[Any] = []
        kept: list[dict] = []
        for record in records:
            turn = record.get("turn_id")
            if turn not in seen:
                if len(seen) > turns_back:
                    break
                seen.append(turn)
            kept.append(record)
        return kept

    def _apply_filters(self, records: list[dict], args: dict) -> list[dict]:
        round_no = args.get("round")
        tool = str(args.get("tool") or "").strip()
        words = [item.casefold() for item in str(args.get("keyword") or "").split() if item]
        result: list[dict] = []
        for record in records:
            if isinstance(round_no, int) and not isinstance(round_no, bool) \
                    and record.get("round") != round_no:
                continue
            if tool and str(record.get("name") or "") != tool:
                continue
            if words:
                blob = " ".join((
                    _render_payload(record.get("request"), 100000),
                    _render_payload(record.get("response"), 100000),
                )).casefold()
                if not all(word in blob for word in words):
                    continue
            result.append(record)
        return result

    # ---------- 渲染 ----------

    def _render_index(
        self,
        records: list[dict],
        stats: _ScanStats,
        config: HarnessRecallConfig,
    ) -> str:
        head = (
            f"【本次运行内的工具调用 {len(records)} 条"
            f"（扫描 {stats.scanned_lines} 行 / {stats.scanned_bytes // 1024} KB）】"
        )
        lines = [head]
        if stats.notes:
            lines.append("；".join(stats.notes))
        summary = config.max_summary_chars
        for position, record in enumerate(records, start=1):
            status = "成功" if record.get("status") == "success" else "失败"
            body = (
                _render_payload(record.get("response"), summary)
                if record.get("status") == "success"
                else self._render_error(record, summary)
            )
            lines.append(
                f"#{position} | {record.get('call_id')} | "
                f"turn={record.get('turn_id')} round={record.get('round')} | "
                f"{record.get('name')} | "
                f"参数：{_render_payload(record.get('request'), summary)} | "
                f"结果（{status}）：{body}"
            )
        lines.append("要全文就再调用一次并带上 ordinal（如 ordinal=2），或直接给 call_id。")
        return "\n".join(lines)

    def _render_full(self, record: dict, config: HarnessRecallConfig) -> str:
        limit = int(self._tool_config().max_result_chars)
        status = "成功" if record.get("status") == "success" else "失败"
        body = (
            _render_payload(record.get("response"), limit)
            if record.get("status") == "success"
            else self._render_error(record, limit)
        )
        text = "\n".join((
            f"【工具调用 {record.get('call_id')}】{record.get('name')} "
            f"turn={record.get('turn_id')} round={record.get('round')} "
            f"状态={status} 耗时={record.get('duration_ms')}ms 时间={record.get('timestamp')}",
            f"参数：{_render_payload(record.get('request'), limit)}",
            f"结果：{body}",
        ))
        if len(text) > limit:
            text = text[:limit] + "\n【工具结果已截断】"
        return text

    @staticmethod
    def _render_error(record: dict, limit: int) -> str:
        error = record.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("type") or "未知错误")
            return _render_payload(message, limit)
        return _render_payload(error, limit) if error is not None else "（未记录错误信息）"

    def _empty_message(
        self,
        stats: _ScanStats,
        config: HarnessRecallConfig,
        *,
        filtered: bool = False,
    ) -> str:
        note = "；".join(stats.notes) if stats.notes else "无额外说明"
        if filtered:
            head = "【窗口内有工具记录，但当前过滤条件 0 命中】"
            advice = "可以去掉 keyword / round / tool，或把 scope 改成 session 扩大窗口。"
        else:
            head = "【窗口内没有可用的工具记录】"
            advice = (
                "如果本次运行确实还没执行过工具，这是正常的；"
                "否则请确认 service.harness.enable=true、service.harness.include_payloads=true。"
            )
        return (
            f"{head}扫描 {stats.scanned_lines} 行 / {stats.scanned_bytes // 1024} KB（{note}）。{advice}"
        )
