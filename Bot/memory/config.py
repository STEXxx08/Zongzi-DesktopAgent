import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from pydantic import ConfigDict, field_validator, model_validator
except ImportError:  # pragma: no cover - pydantic v1 兼容
    ConfigDict = None
    field_validator = None
    model_validator = None


if ConfigDict is not None:
    class MemoryModelConfig(BaseModel):
        model_config = ConfigDict(extra="allow")

        enable: bool = Field(default=False, description="是否启用该记忆任务的单独模型配置")
        type: str = Field(default="llm", description="模型类型：llm 或 mllm")
else:
    class MemoryModelConfig(BaseModel):
        enable: bool = Field(default=False, description="是否启用该记忆任务的单独模型配置")
        type: str = Field(default="llm", description="模型类型：llm 或 mllm")

        class Config:
            extra = "allow"


_RETRIEVAL_MODES = ("off", "observe", "on")


class RetrievalConfig(BaseModel):
    """记忆检索（RAG）配置。

    字段全部用**宽松类型**声明：方案 §5 要求参数不合法时 WARN + 取安全值，
    而不是抛异常打断启动，所以校验放在 `_normalize()` 里做（由构造后的
    model_validator 调用）。
    """

    # off = 只受字数约束的全量注入；observe = 与 off 注入逐字节一致但记录命中；
    # on = 常驻窗口 + 检索块
    mode: str = Field(default="off", description="off | observe | on")
    # ---- 常驻窗口（D1）：日期与条数互为上界，裁剪单位是「天」 ----
    min_recent_days: int = Field(default=3, description="无条件保底：现实日期最近 k 天内的条目始终注入")
    recent_days: int = Field(default=30, description="自然窗口：日期边界")
    max_recent_entries: int = Field(default=100, description="自然窗口：条数边界")
    long_term_always_inject: bool = Field(default=True, description="[长期] 恒等式，不受窗口/字数削减影响")
    pin_always_inject: bool = Field(default=True, description="[标记] 恒等式")
    # ---- 检索 ----
    top_k: int = Field(default=4, description="单轮检索返回的条目数")
    same_day_expand: int = Field(default=3, description="命中条目带出同日其他条目的上限")
    min_score: float = Field(default=0.50, description="低于该余弦分数不注入")
    max_chars: int = Field(default=1200, description="单轮检索块字符上限")
    # ---- 随机触发 ----
    random_probability: float = Field(default=0.05)
    random_probability_sparse: float = Field(default=0.30)
    sparse_resident_entries: int = Field(default=5, description="常驻条目数 ≤ 此值视为稀疏")
    random_repeat_ttl_hours: float = Field(default=6.0, description="同一检索块在该小时内不重复注入")
    # ---- 向量模型（与 intent_router 共用同一份模型与 session）----
    model_path: str = Field(default="models/bge-small-zh-v1.5-onnx")
    onnx_model: str = Field(default="onnx/model_quantized.onnx")
    providers: list[str] = Field(default_factory=lambda: ["CPUExecutionProvider"])
    max_length: int = Field(default=128)
    intra_op_threads: int = Field(default=1)
    inter_op_threads: int = Field(default=1)
    # 嵌入失败后的重试探 interval（秒），指数退避到 retry_max_seconds
    retry_initial_seconds: float = Field(default=60.0)
    retry_max_seconds: float = Field(default=600.0)

    if field_validator is not None:
        @field_validator("mode", mode="before")
        @classmethod
        def _coerce_mode(cls, value):
            # YAML 1.1 里 `mode: on` 会被解析成布尔 True、`mode: off` 解析成 False。
            # 这里直接接受这种写法，避免配置文件里少写一对引号就让 Bot 起不来。
            if isinstance(value, bool):
                return "on" if value else "off"
            return value

    if model_validator is not None:
        @model_validator(mode="after")
        def _normalize(self):  # noqa: D401 - pydantic 校验钩子
            return normalize_retrieval_config(self)

    def normalized(self) -> "RetrievalConfig":
        """返回一份校验并夹紧过参数值的副本。"""
        return normalize_retrieval_config(self)


def _safe_int(value, default: int, minimum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        logger.warning(f"memory.retrieval 参数不是整数（{value!r}），改用安全值 {default}")
        return default
    if minimum is not None and result < minimum:
        logger.warning(f"memory.retrieval 参数 {result} 小于最小值 {minimum}，改用安全值 {default}")
        return default
    return result


def _safe_float(value, default: float, low: float | None = None, high: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        logger.warning(f"memory.retrieval 参数不是数字（{value!r}），改用安全值 {default}")
        return default
    if low is not None and result < low:
        logger.warning(f"memory.retrieval 参数 {result} 小于 {low}，改用安全值 {default}")
        return default
    if high is not None and result > high:
        logger.warning(f"memory.retrieval 参数 {result} 大于 {high}，改用安全值 {default}")
        return default
    return result


def normalize_retrieval_config(config: "RetrievalConfig") -> "RetrievalConfig":
    """方案 §5 的参数校验：不合法则 WARN + 取安全值，绝不抛异常打断启动。"""
    mode = str(getattr(config, "mode", "off") or "off").strip().lower()
    if mode not in _RETRIEVAL_MODES:
        logger.warning(f"memory.retrieval.mode={mode!r} 不是 off/observe/on，按 off 处理")
        mode = "off"
    config.mode = mode

    config.min_recent_days = _safe_int(config.min_recent_days, 3, minimum=0)
    config.recent_days = _safe_int(config.recent_days, 30, minimum=0)
    config.max_recent_entries = _safe_int(config.max_recent_entries, 100, minimum=1)
    config.sparse_resident_entries = _safe_int(config.sparse_resident_entries, 5, minimum=0)
    config.top_k = _safe_int(config.top_k, 4, minimum=1)
    config.same_day_expand = _safe_int(config.same_day_expand, 3, minimum=0)
    config.max_chars = _safe_int(config.max_chars, 1200, minimum=1)
    config.max_length = _safe_int(config.max_length, 128, minimum=8)
    config.intra_op_threads = _safe_int(config.intra_op_threads, 1, minimum=1)
    config.inter_op_threads = _safe_int(config.inter_op_threads, 1, minimum=1)
    config.min_score = _safe_float(config.min_score, 0.50, low=-1.0, high=1.0)
    config.random_probability = _safe_float(config.random_probability, 0.05, low=0.0, high=1.0)
    config.random_probability_sparse = _safe_float(
        config.random_probability_sparse, 0.30, low=0.0, high=1.0
    )
    config.random_repeat_ttl_hours = _safe_float(
        config.random_repeat_ttl_hours, 6.0, low=0.0
    )
    config.retry_initial_seconds = _safe_float(config.retry_initial_seconds, 60.0, low=1.0)
    config.retry_max_seconds = _safe_float(config.retry_max_seconds, 600.0, low=1.0)
    if config.retry_max_seconds < config.retry_initial_seconds:
        config.retry_max_seconds = config.retry_initial_seconds

    if config.min_recent_days > config.recent_days:
        logger.warning(
            f"memory.retrieval.min_recent_days({config.min_recent_days}) > "
            f"recent_days({config.recent_days})：日期边界会被保底覆盖，建议调小保底"
        )
    return config


class MemoryConfig(BaseModel):
    max_history: int = Field(default=100, description="临时对话历史的最大消息数")
    enable: bool = Field(default=True)
    data_dir: Path = Field(default=Path("./memory/data")) # 相对路径其实不安全，暂时如此硬编码
    save_interval_rounds: int = Field(default=10, description="每 N 轮对话保存一次快照")
    merge_batch_size: int = Field(default=15, description="合并时分批大小")
    async_save: bool = Field(default=True, description="是否异步保存快照（不阻塞主对话）")
    compact_history_after_save: int = Field(default=0, description="保存记忆快照后，用提取出的记忆替换 N 个保存周期前的历史片段；0 表示不替换，1 表示立即替换")
    memory_model: MemoryModelConfig = Field(default_factory=MemoryModelConfig, description="记忆提取与合并使用的单独模型配置")
    # 通用注入上限：对「常驻 / 检索 / 全量回退」三条路径都生效（方案 D12）
    injection_max_chars: int = Field(default=10000, description="注入模型的记忆文本字符上限；超出时按条目从最老丢")
    retrieval: "RetrievalConfig" = Field(default_factory=lambda: RetrievalConfig(), description="记忆检索（RAG）配置")