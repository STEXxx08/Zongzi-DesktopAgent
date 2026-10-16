"""记忆召回工具：把长期档案检索暴露给模型（`memory_recall`）。

三种召回触发（方案 §9）：

1. **关键词**（本地语义路由 → intent `recall_memory`）走**现成**的
   `_submit_routed_tool` 链路；
2. **LLM 自主**：本服务把工具注册为 `model_invocable=True`，暴露进
   `<tool_call>` 文本目录，模型输出 `{"name":"memory_recall",...}` 即触发；
3. **随机偶发**在 `bot.py` 的 `emit_llm_prediction` 内直接算，落 `MEMORY` 块。

前两条的检索结果都经通用工具链路进入 `TOOL_RESULT` 块，**不另造注入路径**。

只有在 `retrieval.mode == "on"` 时才注册工具：`off` / `observe` 下
`observe` 要求"注入文本与 off 逐字节一致"，所以连工具也不能开放。
"""

from __future__ import annotations

from loguru import logger

from memory.config import MemoryConfig, RetrievalConfig
from memory.retrieval import MODE_ON, MemoryRetriever, RetrievalUnavailable
from services.tool_runtime import ToolDefinition, ToolRuntime

TOOL_NAME = "memory_recall"

TOOL_DESCRIPTION = (
    "在长期记忆档案里检索较早以前（不在当前记忆块里）的具体往事或用户以前说过的话。"
    "需要回忆旧事、核对时间线、确认以前是否提过某事时使用。"
)

# 低于这个长度的 query 视为"空泛"：本地路由的 strip_patterns 可能把整句剥成一个
# 残字（实测"你还记得我之前说过什么吗"会被剥成"我"），拿它去检索只会捞到噪声。
MIN_QUERY_CHARS = 2

MODEL_INSTRUCTION = (
    "【长期记忆检索】memory_recall 用来回忆**较早以前**的具体旧事"
    "（当前记忆块里没有的内容）。只在确实需要旧信息时调用一次，query 写简短的中文关键词或问句；"
    "不要为了找话而反复调用，也不要用它替代正常回答。"
)


class MemoryService:
    """把 `MemoryRetriever` 注册为共享工具运行时里的一个只读工具。"""

    def __init__(
        self,
        config: MemoryConfig,
        runtime: ToolRuntime,
        retriever: MemoryRetriever,
    ):
        self.runtime = runtime
        self.retriever = retriever
        # 查询兜底链：query 空泛时退回最近一轮用户话（方案 §9）
        self._fallback_provider = None
        self.update_config(config)

    def set_fallback_provider(self, provider) -> None:
        self._fallback_provider = provider

    @property
    def config(self) -> MemoryConfig:
        return self._config

    def update_config(self, config: MemoryConfig) -> None:
        self._config = config
        self.runtime.unregister(TOOL_NAME)
        if not self.enabled:
            logger.info("记忆召回工具未注册（memory.enable=false 或 retrieval.mode != on）。")
            return
        self.runtime.register(ToolDefinition(
            name=TOOL_NAME,
            description=TOOL_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要在长期记忆里检索的内容，例如“上次说的那个游戏”或“热可可”。",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            tags=frozenset({"memory", "read"}),
            model_invocable=True,
            handler=self.recall,
        ))
        logger.info("记忆召回工具已注册：memory_recall（model_invocable）。")

    @property
    def retrieval(self) -> RetrievalConfig:
        return self._config.retrieval

    @property
    def enabled(self) -> bool:
        return bool(self._config.enable) and self._config.retrieval.mode == MODE_ON

    def recall(self, arguments: dict) -> str:
        """工具入口：返回渲染好的检索块文本（进入 `TOOL_RESULT`）。"""
        query = str((arguments or {}).get("query", "")).strip()
        if len(query) < MIN_QUERY_CHARS and self._fallback_provider is not None:
            # 关键词触发时 strip_patterns 可能把"你还记得…吗"整句剥成一个残字，
            # 这里退回最近一轮用户话（等价于方案 §9 的"剥空则退回整句"，只是把
            # "剥空"放宽到"剥得只剩残字"——残字检索出来的都是噪声）。
            try:
                fallback = (self._fallback_provider() or "").strip()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"🧠 记忆检索查询兜底失败：{exc}")
                fallback = ""
            if fallback:
                logger.info(f"🧠 memory_recall 的 query 过短（{query!r}），改用最近一轮用户话。")
                query = fallback
        if not query:
            raise RuntimeError("memory_recall 需要一个非空的 query（没能从最近对话里兜底出查询）。")
        try:
            text = self.retriever.retrieval_block(query)
        except RetrievalUnavailable as exc:
            raise RuntimeError(f"长期记忆检索当前不可用：{exc}") from exc
        logger.info(f"🧠 memory_recall 命中（query={query[:40]}）：{len(text)} 字符。")
        return text

    def model_instruction(self) -> str | None:
        if not self.enabled:
            return None
        return MODEL_INSTRUCTION
