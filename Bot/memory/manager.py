# memory/manager.py
import math
import shutil
import threading
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Any
from urllib.parse import urlparse, urlunparse

from loguru import logger
from condata.pipeline.llm import Conversation, LLMQuery, LLMPrediction, RoleEnum
from condata.pipeline.mllm import MLLMQuery, MLLMPrediction  # 新增

# 注意：这里从你现有的 pipeline 模块导入，无需修改底层代码
from pipeline.llm.llm_sync import LLMSyncPipeline
from pipeline.mllm.mllm_sync import MLLMSyncPipeline
from pipeline.llm.config import LLMPipelineConfig
from pipeline.mllm.config import MLLMPipelineConfig

try:
    from .config import MemoryConfig
except ImportError:
    from config import MemoryConfig


@dataclass
class SnapshotSaveResult:
    file_path: Path
    extracted_memory: str


@dataclass
class PendingHistoryCompaction:
    message_count: int
    extracted_memory: str


class MemoryExtractionError(RuntimeError):
    """记忆抽取失败。表示本次快照不可用：既不写盘，也不参与历史压缩。"""


# 抽取连续失败后的重试间隔（单位：保存周期），避免坏配置时每个周期都空跑一次模型。
_EXTRACTION_RETRY_BACKOFF_CYCLES = (0, 1, 2, 4, 8)

# 单批合并的总尝试次数（首次 + 重试）；全部失败则中止整次合并且不归档。
_MERGE_BATCH_ATTEMPTS = 2

# 压缩游标的下限（一条 system 提示 + 一条注入历史）：压缩只能动用户轮次，
# 绝不能把 system 提示词或注入历史替换成摘要。
_USER_TURN_FLOOR = 2


def _dump_model_config(config: Any) -> dict:
    if config is None:
        return {}
    if isinstance(config, dict):
        return dict(config)
    if hasattr(config, "model_dump"):
        return config.model_dump()
    if hasattr(config, "dict"):
        return config.dict()
    return dict(getattr(config, "__dict__", {}))


def _clone_model_config(base_config: Any, overrides: dict) -> Any:
    if not overrides:
        return base_config

    if hasattr(base_config, "model_copy"):
        return base_config.model_copy(update=overrides)
    if hasattr(base_config, "copy"):
        return base_config.copy(update=overrides)

    data = _dump_model_config(base_config)
    data.update(overrides)
    try:
        return base_config.__class__(**data)
    except Exception:
        return data


def _derive_stream_predict_url(predict_url: str) -> str:
    parsed = urlparse(predict_url)
    if parsed.path.endswith("/predict"):
        path = parsed.path[:-len("/predict")] + "/stream-predict"
    else:
        path = parsed.path.rstrip("/") + "/stream-predict"
    return urlunparse(parsed._replace(path=path))


def _ensure_memory_model_urls(overrides: dict) -> dict:
    if "predict_url" in overrides and "stream_predict_url" not in overrides:
        overrides = dict(overrides)
        overrides["stream_predict_url"] = _derive_stream_predict_url(str(overrides["predict_url"]))
    return overrides



def resolve_memory_model_configs(global_config: Any) -> dict:
    pipeline_config = global_config.pipeline
    memory_config = global_config.memory

    def get_overrides(task_config: Any) -> tuple[Optional[str], dict]:
        if not task_config:
            return None, {}

        data = _dump_model_config(task_config)
        model_type = str(data.pop("type", "llm")).lower()
        model_enabled = data.pop("enable", False)
        data.pop("llm", None)
        data.pop("mllm", None)
        if not model_enabled:
            return None, {}

        overrides = {key: value for key, value in data.items() if value not in (None, {}, [])}
        overrides = _ensure_memory_model_urls(overrides)
        overrides.setdefault("enable", True)
        return model_type, overrides

    memory_model = getattr(memory_config, "memory_model", None)
    model_type, overrides = get_overrides(memory_model)

    if model_type == "mllm":
        return {
            "llm_config": None,
            "mllm_config": _clone_model_config(pipeline_config.mllm, overrides),
            "merge_llm_config": None,
            "merge_mllm_config": None,
        }

    if model_type == "llm":
        return {
            "llm_config": _clone_model_config(pipeline_config.llm, overrides),
            "mllm_config": None,
            "merge_llm_config": None,
            "merge_mllm_config": None,
        }

    if getattr(pipeline_config.mllm, "enable", False):
        memory_llm_config, memory_mllm_config = None, pipeline_config.mllm
    else:
        memory_llm_config, memory_mllm_config = pipeline_config.llm, None

    return {
        "llm_config": memory_llm_config,
        "mllm_config": memory_mllm_config,
        "merge_llm_config": None,
        "merge_mllm_config": None,
    }

# 记忆提取专用的 system_prompt（严格剥离情景和角色扮演）
EXTRACT_SYSTEM_PROMPT = (
    "你是一个严格的信息提取器，负责将当前对话片段提炼为结构化的事实记忆。\n"
    "输出必须严格遵循以下3级Markdown列表格式（缩进使用2个空格）：\n\n"
    "**格式规范**：\n"
    "1. [YYYY-MM]: （按月份分组，例如 [2026-06]）\n"
    "   - [DD]: （按日期分组，例如 - [18]:）\n"
    "       - HH:MM 具体事件描述（如果事件具有具体时刻（如'晚上8点月色很美'）, 或具有强烈时间感（如‘这个奖励每天准时刷新’） 在条目前加 HH:MM，否则只写日期。）\n\n"
    "**提取规则**：\n"
    "- 输出的记忆里不能把assisstant称为‘助手’，要用‘我’来指代assisstant。\n"
    "- 注意区分‘我’(assisstant)讲的话和‘用户’(user)讲的话。\n"
    "- ***区分示例***：\n"
    "assistant： Voicemeeter设备名拼错成Vicinity Input了! user: 才没有呢，是识别出错了。 -> 提取为：我说Voicemeeter设备名拼错了，用户澄清是识别出错了。\n"
    "assistant： Voicemeeter设备名拼错成Vicinity Input了! user: (不回复或回复无关) -> 提取为：我表示Voicemeeter设备名拼错了（或者直接不提取）\n"
    "- 仅提取关于用户本人的客观事实、偏好、习惯、状态变化、计划、遇到的重要\非日常事件，或与我相关的关键信息（如用户给我起的昵称，我表现出的性格特点或习惯）。如果对话内容有信息量，可以对对话内容总结，作为一条记忆\n"
    "- 忽略纯客套话（如'你好'、'再见'）和日常状态（如'我饿了'、'现在几点'——除非用户明确要求记住）。\n"
    "- 如果某条信息带有明确时间（如'下周考试'、'明天出差'），保留该时间表述。\n"
    "- 检查对话内容的合理性，明显存在逻辑混乱，缺乏意义的表述，如果不能高可信度的理解（如常见错字），则忽略。\n"
    "- 对话发生的时间以时间戳时间为准，不要对话以外的信息做推理或猜测。\n"
    "- 提取信息时，要确保描述客观事实，不应该包含主观判断或推测。（如‘用户在XX时XX分看了XX视频’，而不是‘用户有观看xx视频的习惯’）\n"
    "- 如果对话中没有新的可提取信息，输出 '无新信息'。\n\n"
    "**过往 core_memory（只读参考，不得将 core_memory 中已有的信息当作当前对话的新信息输出）**：\n"
    "<core_memory>\n"
    "{core_memory}\n"
    "</core_memory>\n"
)

# 合并专用的 system_prompt
MERGE_SYSTEM_PROMPT = (
    "你是一名严格的记忆档案管理员，负责将多段历史记忆快照合并为一份主档案（core_memory）。\n"
    "输出必须严格保持与输入相同的缩进层级和 Markdown 列表格式。\n\n"
    "- 输出的记忆里不能把assisstant称为‘助手’，要用‘我’来指代assisstant。\n"
    "**核心任务**：\n"
    "1. 将同一月份的数据合并到同一个 [YYYY-MM] 下，同一日期的数据合并到同一个 [DD] 下。\n"
    "2. 根据跨快照的出现频率和性质，**将长期稳定的事实提升至 [长期] 区域**，并归档临时性事件。\n\n"
    "**提升至 [长期] 的判定标准**：\n"
    "- 如果某个事实（如'喜欢喝美式咖啡'、'职业是程序员'）在至少3天的记忆中重复出现，并可能属于属于身份/持久偏好（如姓名、家庭关系、爱好、职业），则将其从月度归档中**提取出来**，放入 [长期] 区域。\n"
    "- [长期] 条目格式：'- 事实内容 (自YYYY-MM-DD起, 最后确认于YYYY-MM-DD)'，其中起始时间为第一次出现日期，最后确认时间为最近一次出现日期。\n"
    "- 临时事件（如'下周考试'、'今天下雨'）**不得**归入 [长期]，应保留在对应的月份/日期中。\n\n"
    "3. 对同一主题的记忆进行合并（并列补充 vs 真正冲突）\n\n"
    "- **并列补充**：如果新记忆与旧记忆属于同一类别（例如'喜欢喝美式咖啡'、'喜欢喝茶'、'喜欢喝拿铁'），且旧记忆中没有明确否定，则合并为一条复合描述，例如：'用户喜欢喝美式咖啡、茶和拿铁'。\n"
    "- **真正冲突**：仅当新记忆明确表示否定或替代旧记忆时（如'戒咖啡了'、'现在不喜欢喝茶了'），才使用'曾经……现在……'格式保留变迁历史，例如：'用户曾经喜欢喝美式咖啡 (自2024-06-10起)，现在戒咖啡 (自2026-06-18起)'。\n\n"
    "**时效性**：\n"
    "- 月份标题按时间**倒序**排列（最新的月份在最上面）。\n\n"
    "**去重与遗忘**：\n"
    "- 对旧的记忆，为了控制记忆大小和模拟遗忘，需要进行合并或删除，条件如下：\n"
    "   - 几乎完全重复的条目直接合并或将新的删除。\n"
    "   - 距今一周以前的条目，信息重复的直接合并或将旧的删除。信息质量低(即大概率聊天不会再提到的杂乱信息)的条目压缩细节\n"
    "   - 距今一个月以前的条目，信息基本重复的直接合并或将旧的删除，信息质量低(即大概率聊天不会再提到的杂乱信息)的条目压缩细节或直接删除。\n"
    "**输出示例（必须模仿此缩进）**：\n"
    "[长期]:\n"
    "  - 用户喜欢喝美式咖啡 (自2024-06-10起, 最后确认于2026-06-18)\n"
    "[2026-06]:\n"
    "  - [18]:\n"
    "      - 用户介绍自己叫赛艇\n"
    "      - 20:00 用户说月色很美\n"
    "  - [17]:\n"
    "      - 用户表示喜欢喝美式咖啡\n"
)

class MemoryManager:
    def __init__(
            self,
            config: MemoryConfig,
            llm_config: Optional[Any] = None,
            mllm_config: Optional[Any] = None,
            merge_llm_config: Optional[Any] = None,
            merge_mllm_config: Optional[Any] = None,
    ):
        self.config = config
        self.data_dir = config.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.messages_per_save = int(config.save_interval_rounds) * 2
        if self.messages_per_save <= 0:
            raise ValueError("memory.save_interval_rounds must be greater than 0.")

        delay_saves = self._get_compaction_delay_saves()
        self.max_history = int(config.max_history)
        self.max_compaction_cursor: Optional[int] = None
        if delay_saves > 0:
            max_cursor_one_based = self.max_history - self.messages_per_save * delay_saves + 1
            if max_cursor_one_based < 1:
                raise ValueError(
                    "Invalid memory compaction settings: max_history must be at least "
                    "save_interval_rounds * 2 * compact_history_after_save "
                    f"({self.max_history} < {self.messages_per_save * delay_saves})."
                )
            self.max_compaction_cursor = max_cursor_one_based - 1

        self.use_mllm = mllm_config is not None
        if self.use_mllm:
            self._mllm = MLLMSyncPipeline(mllm_config)
            self._llm = None
            logger.info(
                f"MemoryManager using MLLM config for memory extraction: "
                f"{getattr(mllm_config, 'model_id', '<unknown>')} "
                f"({getattr(mllm_config, 'predict_url', '<no predict_url>')})"
            )
        else:
            self._llm = LLMSyncPipeline(llm_config)
            self._mllm = None
            logger.info(
                f"MemoryManager using LLM config for memory extraction: "
                f"{getattr(llm_config, 'model_id', '<unknown>')} "
                f"({getattr(llm_config, 'predict_url', '<no predict_url>')})"
            )

        self.merge_use_mllm = merge_mllm_config is not None
        if self.merge_use_mllm:
            self._merge_mllm = MLLMSyncPipeline(merge_mllm_config)
            self._merge_llm = None
            logger.info(f"MemoryManager using MLLM config for memory merge: {getattr(merge_mllm_config, 'model_id', '<unknown>')}")
        elif merge_llm_config is not None:
            self._merge_llm = LLMSyncPipeline(merge_llm_config)
            self._merge_mllm = None
            logger.info(f"MemoryManager using LLM config for memory merge: {getattr(merge_llm_config, 'model_id', '<unknown>')}")
        else:
            self.merge_use_mllm = self.use_mllm
            self._merge_llm = self._llm
            self._merge_mllm = self._mllm
            logger.info("MemoryManager using extraction model config for memory merge.")

        # 线程池：用于异步保存，max_workers=1 保证顺序执行，避免 IO/计算冲突
        self._executor = ThreadPoolExecutor(max_workers=1) if config.async_save else None
        self._lock = threading.Lock()

        # 运行时状态
        self.core_memory_text = self._load_core_memory()
        self.rounds_since_save = 0   # 自上次保存以来的对话轮数
        self.compaction_cursor = _USER_TURN_FLOOR   # 下一个待压缩批次的起始下标（不得进入注入块）
        self._min_compaction_cursor = _USER_TURN_FLOOR  # 注入块边界，压缩游标不得低于它
        self._pending_history_compactions: List[PendingHistoryCompaction] = []
        # 抽取失败退避：连续失败次数与跳过计数（失败时不写盘、不压缩，历史原样保留）
        self._extraction_consecutive_failures = 0
        self._extraction_skip_cycles_remaining = 0

        logger.info(f"🧠 MemoryManager initialized. Core memory length: {len(self.core_memory_text)} chars.")

    # ---------- 私有方法 ----------
    def _load_core_memory(self) -> str:
        """加载 core_memory.md，不存在则返回空字符串"""
        core_path = self.data_dir / "core_memory.md"
        logger.debug(f"Loading core memory from: {core_path}")
        if core_path.exists():
            try:
                return core_path.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.error(f"Failed to load core memory: {e}")
        return ""

    def _save_core_memory(self, content: str) -> None:
        """覆盖写入 core_memory.md，并在写入前将旧版本备份至 archive/core_memory.md（覆盖旧备份）"""
        core_path = self.data_dir / "core_memory.md"
        with self._lock:
            if core_path.exists():
                archive_dir = self.data_dir / "archive"
                archive_dir.mkdir(parents=True, exist_ok=True)
                backup_path = archive_dir / "core_memory.md"
                try:
                    shutil.copy2(core_path, backup_path)
                    logger.info(f"🧠 Old core memory backed up to {backup_path}")
                except Exception as e:
                    logger.error(f"Failed to backup core memory: {e}")
    
            core_path.write_text(content, encoding="utf-8")
            self.core_memory_text = content
            logger.info(f"🧠 Core memory updated (length: {len(content)} chars).")

    def _format_conversation(self, messages: List[Conversation]) -> str:
        """将 Conversation 列表格式化为纯文本对话"""
        lines = []
        for msg in messages:
            role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            if role in ["user", "assistant"]:
                lines.append(f"{role}: {msg.content}")
        return "\n".join(lines)

    def _predict_with_model(self, text: str, system_msg: Conversation, use_mllm: bool, llm: Any, mllm: Any) -> Any:
        if use_mllm:
            query = MLLMQuery(text=text, history=[system_msg], images=[])
            object.__setattr__(query, "_harness_context", {"source": "memory"})
            return mllm.predict(query)

        query = LLMQuery(text=text, history=[system_msg])
        object.__setattr__(query, "_harness_context", {"source": "memory"})
        return llm.predict(query)

    def _save_snapshot_sync(self, new_messages: List[Conversation]) -> Optional[SnapshotSaveResult]:
        """
        同步执行记忆快照保存（在子线程中运行）。

        返回保存结果；抽取失败时抛出 MemoryExtractionError，并且【不写盘】。
        这样调用方不会压缩历史，也不会把失败伪装成"无新信息"的摘要，
        保证这 20 条真实对话原样留在历史里（超上限时由 update_history 自然丢弃最早的）。
        """
        if not new_messages:
            return None

        logger.debug(f"Saving memory snapshot with {len(new_messages)} messages.")
        dialog_text = self._format_conversation(new_messages)
        if not dialog_text.strip():
            return None

        now = datetime.now()
        date_context = f"现在的时间是 {now.strftime('%Y%m%d_%H%M%S')}。请将以下对话中可提取的信息按上述日期归档。\n\n"
        full_text = date_context + dialog_text

        core_memory = (self.get_core_memory() or "").strip()
        system_msg = Conversation(
            role=RoleEnum.system,
            content=EXTRACT_SYSTEM_PROMPT.replace(
                "{core_memory}",
                core_memory or "（暂无 core_memory）",
            ),
        )
        try:
            prediction = self._predict_with_model(
                text=full_text,
                system_msg=system_msg,
                use_mllm=self.use_mllm,
                llm=self._llm,
                mllm=self._mllm,
            )
        except Exception as exc:
            # 抽取模型不可用（URL 配错、服务未启动、超时等）：统一成一种失败信号。
            raise MemoryExtractionError(f"记忆抽取模型调用失败：{exc}") from exc
        response = getattr(prediction, "response", None) if prediction else None
        if response is None or not str(response).strip():
            raise MemoryExtractionError("记忆抽取模型返回空响应")

        # 模型明确回答"无新信息"是正常结果，按成功处理（与抽取异常严格区分）。
        extracted_memory = str(response).strip()

        # 使用微秒避免同一秒内的多次保存相互覆盖。
        timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
        meta = f"---\ntimestamp: {timestamp}\nsource: conversation_snapshot\n---\n"
        content = meta + extracted_memory

        file_path = self.data_dir / f"memory_{timestamp}.md"
        file_path.write_text(content, encoding="utf-8")
        logger.info(f"🧠 Memory snapshot saved: {file_path.name}")
        return SnapshotSaveResult(file_path=file_path, extracted_memory=extracted_memory)

    # ---------- 公共方法 ----------
    def get_core_memory(self) -> str:
        return self.core_memory_text

    def reset_saved_index(self, injected_len: int) -> None:
        """记录注入块长度（system 提示 + injected_history 的条数）。

        压缩游标不得低于注入块长度，否则会把 system 提示词或注入历史替换成摘要。
        """
        with self._lock:
            self.rounds_since_save = 0
            self._min_compaction_cursor = max(int(injected_len), _USER_TURN_FLOOR)
            self.compaction_cursor = max(self.compaction_cursor, self._min_compaction_cursor)
            self._pending_history_compactions.clear()

    def _build_compacted_history(
            self,
            current_history: List[Conversation],
            start_idx: int,
            end_idx: int,
            extracted_memory: str,
    ) -> List[Conversation]:
        if not extracted_memory:
            extracted_memory = "无新信息"
        compacted_message = Conversation(
            role=RoleEnum.assistant,
            content=f"[已提取的历史记忆摘要]\n{extracted_memory}"
        )
        return current_history[:start_idx] + [compacted_message] + current_history[end_idx:]

    def _get_compaction_delay_saves(self) -> int:
        try:
            delay_saves = int(self.config.compact_history_after_save)
        except (TypeError, ValueError):
            delay_saves = 0
        return max(delay_saves, 0)

    def _compact_history_after_save(
            self,
            history_snapshot,
            message_count: int,
            extracted_memory: str,
    ) -> bool:
        """把一段历史替换为摘要。

        整个"读-改-写"由 history_snapshot.commit_history_update 在持有历史锁的情况下完成：
        - 压缩游标在锁内按【提交时刻】的真实历史重新校验（P1-3：游标不再漂移）；
        - 前台同时追加的消息不会因为压缩写回而丢失（P1-1）。
        锁内只做内存列表操作，不调用模型、不写磁盘。
        """
        if message_count <= 0:
            return False

        with self._lock:
            start_idx = max(self.compaction_cursor, self._min_compaction_cursor)

        outcome = {"compacted": False, "next_cursor": None}

        def _transform(current_history: List[Conversation]):
            if start_idx < 0 or start_idx + message_count > len(current_history):
                logger.error(
                    "Skip memory history compaction because the compaction cursor is out of range: "
                    f"cursor={start_idx}, message_count={message_count}, "
                    f"history_len={len(current_history)}."
                )
                return None
            compacted = self._build_compacted_history(
                current_history=current_history,
                start_idx=start_idx,
                end_idx=start_idx + message_count,
                extracted_memory=(extracted_memory or "无新信息").strip() or "无新信息",
            )
            next_cursor = start_idx + 1
            if self.max_compaction_cursor is not None:
                next_cursor = min(next_cursor, self.max_compaction_cursor)
            outcome["compacted"] = True
            outcome["next_cursor"] = max(next_cursor, self._min_compaction_cursor)
            return compacted

        try:
            committed = history_snapshot.commit_history_update(_transform)
        except Exception as e:
            logger.error(f"Failed to compact chat history after memory save: {e}")
            return False

        if not committed or not outcome["compacted"]:
            return False

        with self._lock:
            self.compaction_cursor = outcome["next_cursor"]
        logger.info(
            f"🧠 Compacted chat history messages [{start_idx}:{start_idx + message_count}); "
            f"next compaction cursor: {outcome['next_cursor']}."
        )
        return True

    def _queue_history_compaction(
            self,
            history_snapshot,
            snapshot_data: List[Conversation],
            extracted_memory: str,
    ) -> None:
        delay_saves = self._get_compaction_delay_saves()
        if delay_saves <= 0 or not snapshot_data:
            return

        with self._lock:
            self._pending_history_compactions.append(
                PendingHistoryCompaction(
                    message_count=len(snapshot_data),
                    extracted_memory=(extracted_memory or "无新信息").strip() or "无新信息",
                )
            )
            if len(self._pending_history_compactions) < delay_saves:
                return
            pending = self._pending_history_compactions[0]

        compacted = self._compact_history_after_save(
            history_snapshot=history_snapshot,
            message_count=pending.message_count,
            extracted_memory=pending.extracted_memory,
        )
        if compacted:
            with self._lock:
                if self._pending_history_compactions and self._pending_history_compactions[0] is pending:
                    self._pending_history_compactions.pop(0)

    def maybe_save_snapshot(
            self,
            current_history: List[Conversation],
            injected_len: int,
            force: bool = False,
            history_snapshot=None,
    ) -> None:
        if not self.config.enable:
            return

        with self._lock:
            if not force:
                self.rounds_since_save += 1
                if self._extraction_skip_cycles_remaining > 0:
                    # 连续失败退避窗口：本周期不调用抽取模型，历史原样保留。
                    self._extraction_skip_cycles_remaining -= 1
                    return
                if self.rounds_since_save < self.config.save_interval_rounds:
                    return

            rounds_to_save = self.rounds_since_save
            if rounds_to_save <= 0:
                return

            requested_messages = rounds_to_save * 2
            available_messages = max(len(current_history) - injected_len, 0)
            snapshot_count = min(requested_messages, available_messages)
            if snapshot_count <= 0:
                return
            snapshot_data = current_history[-snapshot_count:].copy()

            should_compact = (
                self._get_compaction_delay_saves() > 0
                and history_snapshot is not None
            )

            if not should_compact:
                self.rounds_since_save = 0
                if self.config.async_save and self._executor:
                    self._executor.submit(self._save_snapshot_sync, snapshot_data)
                else:
                    try:
                        result = self._save_snapshot_sync(snapshot_data)
                    except MemoryExtractionError as exc:
                        self._register_extraction_failure(exc)
                    else:
                        if result:
                            self._register_extraction_success()
                return

            self.rounds_since_save = 0

        if self.config.async_save and self._executor:
            future = self._executor.submit(self._save_snapshot_sync, snapshot_data)
            future.add_done_callback(
                lambda f, data=snapshot_data: self._on_snapshot_saved_for_compaction(
                    f, history_snapshot, data
                )
            )
        else:
            try:
                result = self._save_snapshot_sync(snapshot_data)
            except Exception as exc:
                self._register_extraction_failure(exc)
                return
            if result:
                self._register_extraction_success()
                self._queue_history_compaction(
                    history_snapshot=history_snapshot,
                    snapshot_data=snapshot_data,
                    extracted_memory=result.extracted_memory,
                )

    def _register_extraction_failure(self, error: BaseException) -> None:
        """记录一次抽取失败并计算退避窗口；失败期间不压缩、不写盘。"""
        with self._lock:
            self._extraction_consecutive_failures += 1
            failures = self._extraction_consecutive_failures
            index = min(failures, len(_EXTRACTION_RETRY_BACKOFF_CYCLES) - 1)
            self._extraction_skip_cycles_remaining = _EXTRACTION_RETRY_BACKOFF_CYCLES[index]
            skip_cycles = self._extraction_skip_cycles_remaining
        logger.error(
            f"🧠 记忆抽取失败（连续第 {failures} 次）：{error}。"
            f"本次不写快照、不压缩历史；历史原文保留，"
            f"将在 {skip_cycles} 个保存周期后重试（约 {skip_cycles * int(self.config.save_interval_rounds)} 轮对话）。"
        )
        if failures >= 3:
            logger.error(
                "🧠 记忆抽取连续失败已达 3 次，请检查 memory.memory_model 的 predict_url / model_id 是否可用；"
                "在此之前对话历史不会被压缩（超过 max_history 时只保留最新的部分）。"
            )

    def _register_extraction_success(self) -> None:
        with self._lock:
            self._extraction_consecutive_failures = 0
            self._extraction_skip_cycles_remaining = 0

    def _on_snapshot_saved_for_compaction(
            self,
            future: Future,
            history_snapshot,
            snapshot_data: List[Conversation],
    ) -> None:
        try:
            result = future.result()
        except MemoryExtractionError as exc:
            self._register_extraction_failure(exc)
            return
        except Exception as exc:
            self._register_extraction_failure(exc)
            return

        if not result:
            # 无有效对话内容，不算失败。
            return

        self._register_extraction_success()
        self._queue_history_compaction(
            history_snapshot=history_snapshot,
            snapshot_data=snapshot_data,
            extracted_memory=result.extracted_memory,
        )

    def merge_all(self) -> bool:
        if not self.config.enable:
            logger.warning("Memory is disabled, skip merge.")
            return False

        try:
            snapshot_files = sorted(self.data_dir.glob("memory_*.md"))
            if not snapshot_files:
                logger.info("No snapshot files to merge.")
                return True

            logger.info(f"🧠 Merging {len(snapshot_files)} memory snapshots...")

            batch_size = self.config.merge_batch_size
            merged_texts = []
            failed_batches = []
            for i in range(0, len(snapshot_files), batch_size):
                batch = snapshot_files[i:i+batch_size]
                batch_contents = []
                for f in batch:
                    content = f.read_text(encoding="utf-8")
                    body = self._extract_body_from_snapshot(content)
                    if body:
                        batch_contents.append(body)

                if not batch_contents:
                    continue

                combined = "\n\n---\n\n".join(batch_contents)
                system_msg = Conversation(role=RoleEnum.system, content=MERGE_SYSTEM_PROMPT)
                names = [f.name for f in batch]

                # 失败先原地重试一次；两次都失败才视为该批失败。
                merged_text = None
                for attempt in range(_MERGE_BATCH_ATTEMPTS):
                    try:
                        pred = self._predict_with_model(
                            text=combined,
                            system_msg=system_msg,
                            use_mllm=self.merge_use_mllm,
                            llm=self._merge_llm,
                            mllm=self._merge_mllm,
                        )
                    except Exception as exc:
                        logger.warning(
                            f"Batch merge raised on attempt {attempt + 1}/{_MERGE_BATCH_ATTEMPTS} "
                            f"for {names}: {exc}"
                        )
                        pred = None
                    response = getattr(pred, "response", None) if pred else None
                    if response and str(response).strip():
                        merged_text = str(response).strip()
                        break
                    logger.warning(
                        f"Batch merge returned empty on attempt {attempt + 1}/{_MERGE_BATCH_ATTEMPTS} "
                        f"for {names}"
                    )

                if merged_text is None:
                    failed_batches.append(names)
                else:
                    merged_texts.append(merged_text)

            if failed_batches:
                # 任一批失败就整体中止：不写 core_memory、不归档任何快照文件，
                # 让这批信息留在 data/ 里等待下次重跑，而不是被静默丢弃。
                logger.error(
                    f"🧠 记忆合并中止：{len(failed_batches)} 个批次（重试后仍失败）未产出结果，"
                    f"本次不更新 core_memory、不归档任何快照文件。失败批次：{failed_batches}"
                )
                return False

            if not merged_texts:
                logger.info("No mergeable memory content found.")
                return True

            current_core = self.get_core_memory()
            # merged_texts 是列表：必须 join 后再拼进 prompt（连同下面的核心记忆2 分支），
            # 否则 Python 的 list repr（['...', '...']）会污染合并输入。
            merged_text = "\n\n".join(text for text in merged_texts if text)
            if current_core:
                final_combined = f"核心记忆1：\n{current_core}\n\n核心记忆2：\n{merged_text}\n\n"
            else:
                final_combined = merged_text

            merge_instruction = (
                "严格遵循上方的规则，将所有核心记忆合并为一份最终的 core_memory。\n"
                "必须重新评估旧记忆：允许删除过时、重复或低质量的条目，压缩冗余表述。"
)

            system_msg = Conversation(role=RoleEnum.system, content=MERGE_SYSTEM_PROMPT + "\n\n" + merge_instruction)
            final_pred = self._predict_with_model(
                text=final_combined,
                system_msg=system_msg,
                use_mllm=self.merge_use_mllm,
                llm=self._merge_llm,
                mllm=self._merge_mllm,
            )
            if not final_pred or not final_pred.response:
                logger.error("Final core memory merge failed.")
                return False

            self._save_core_memory(final_pred.response)

            archive_dir = self.data_dir / "archive"
            archive_dir.mkdir(exist_ok=True)
            for f in snapshot_files:
                dest = archive_dir / f.name
                if dest.exists():
                    dest = archive_dir / f"{f.stem}_{datetime.now().strftime('%H%M%S')}.md"
                f.rename(dest)

            logger.info(f"🧠 Merge completed. {len(snapshot_files)} files archived.")
            return True

        except Exception as e:
            logger.exception(f"Merge all memories error: {e}")
            return False
        
    def _extract_body_from_snapshot(self, content: str) -> str:
        lines = content.splitlines()
        if not lines:
            return ""
        if lines[0].strip() != "---":
            return content.strip()
        end_index = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_index = i
                break
        if end_index == -1:
            return content.strip()
        body_lines = lines[end_index+1:]
        return "\n".join(body_lines).strip()
    
    def shutdown(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=True)
            logger.debug("🧠 MemoryManager executor shut down.")