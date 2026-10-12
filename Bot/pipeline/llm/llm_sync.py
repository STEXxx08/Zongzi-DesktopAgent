"""LLM 管线（同步）。

两种后端二选一，由 `config.openai_format` 决定：

1. **HTTP 服务**（`openai_format=false`，默认）
   把请求 POST 到 Core 侧的 `/llm/predict` 与 `/llm/stream-predict`，
   由 Core 决定加载哪个本地模型。换模型只改 `Core/config.yaml`。

2. **OpenAI 兼容接口**（`openai_format=true`）
   直接把 `predict_url` 当 base_url，走 `openai` SDK。
   DeepSeek / Kimi / 豆包 / vLLM / Ollama / 任何 OpenAI 兼容端点都走这条。

   ⚠️ 这里**不做模型名白名单**：早期版本用 `if model_id == "..."` 逐个匹配，
   导致名单外的兼容模型直接 `NotImplementedError`，与"可任意组合"的设计目标冲突。
   现在所有兼容模型走同一条路径，差异用配置项（`temperature` / `max_tokens`）表达。
"""

from typing import Any, Generator

from loguru import logger
from openai import OpenAI
from requests import Response
from typeguard import typechecked
from condata.pipeline.llm import LLMQuery, LLMPrediction, RoleEnum, Conversation
from condata.harness import trace_model_call, usage_dict

from pipeline.base.base_sync import CommonModelPipeline
from pipeline.llm.config import LLMPipelineConfig


def _to_openai_messages(query: LLMQuery) -> list[dict]:
    """把 LLMQuery 转成 OpenAI chat 消息列表。"""
    messages = []
    for chat in query.history:
        role = getattr(chat.role, "value", chat.role)
        if chat.content is None:
            continue
        messages.append({"role": role, "content": chat.content})
    messages.append({"role": RoleEnum.user.value, "content": query.text})
    return messages


def _sampling_kwargs(config: LLMPipelineConfig) -> dict:
    """只把真正配置过的采样参数发给服务端，None 表示"用服务端默认值"。

    不写死 temperature 很重要：各家在线服务的默认值不同，强行覆盖会改变模型行为。
    """
    kwargs = {}
    if config.temperature is not None:
        kwargs["temperature"] = config.temperature
    if config.max_tokens is not None:
        kwargs["max_tokens"] = config.max_tokens
    return kwargs


class _OpenAICompatBackend:
    """OpenAI 兼容后端：把一次请求封装成"可调用"与"可流式迭代"两件事。

    抽出来单独放，是为了让测试可以替换 `client` 而不发真实网络请求。
    """

    def __init__(self, config: LLMPipelineConfig):
        self.model_id = str(config.model_id)
        self._sampling = _sampling_kwargs(config)
        # 类型标注用 Any：`@typechecked` 会严格校验，而测试里会把它换成假客户端。
        self.client: Any = OpenAI(api_key=config.api_key or "EMPTY", base_url=config.predict_url)

    def completions(self, messages: list[dict]):
        """非流式：返回一次完整的 chat completion。"""
        return self.client.chat.completions.create(
            model=self.model_id, messages=messages, stream=False, **self._sampling
        )

    def completions_stream(self, messages: list[dict]):
        """流式：返回 chunk 迭代器。"""
        return self.client.chat.completions.create(
            model=self.model_id, messages=messages, stream=True, **self._sampling
        )


class LLMSyncPipeline(CommonModelPipeline):

    def __init__(self, config: LLMPipelineConfig):
        super().__init__(config)
        self._is_openai_format = bool(config.openai_format)
        self._remote: _OpenAICompatBackend | None = None
        if self._is_openai_format:
            if not config.predict_url:
                raise ValueError(
                    "openai_format=true 时必须提供 predict_url，它会被当作 API base_url"
                    "（例如 https://api.deepseek.com 或 http://localhost:11434/v1）"
                )
            self._remote = _OpenAICompatBackend(config)

    # ---------------------------------------------------------------- predict

    @typechecked
    def predict(self, query: LLMQuery) -> LLMPrediction | None:
        if not self._is_openai_format:
            return super().predict(query)

        assert self._remote is not None
        messages = _to_openai_messages(query)
        completion = self._remote.completions(messages)
        resp = completion.choices[0].message.content or ""
        query.history.append(Conversation(role=RoleEnum.user, content=query.text))
        query.history.append(Conversation(role=RoleEnum.assistant, content=resp))
        return LLMPrediction(
            response=resp,
            history=query.history,
            usage=usage_dict(getattr(completion, "usage", None)),
        )

    def parse_query(self, query: LLMQuery) -> dict:
        """HTTP 后端用：Core 侧的本地模型从 history 里取 system prompt，需要显式补一个字段。"""
        query_dict = super().parse_query(query)
        if "system_prompt" not in query_dict:
            for chat in query.history:
                role = getattr(chat.role, "value", chat.role)
                if role == RoleEnum.system.value:
                    query_dict["system_prompt"] = chat.content
                    break
        return query_dict

    # --------------------------------------------------------- stream_predict

    def stream_predict(self, query: LLMQuery, chunk_size: int | None = None) -> Generator[
        LLMPrediction, None, None
    ]:
        """流式预测。

        注意：本方法**刻意不加 `@typechecked`** —— 它是生成器函数，
        装饰器无法把生成器包成"可迭代"（参见 llm_sync / mllm_sync 的
        InstrumentationWarning，同一个成因）。
        """
        if not self._is_openai_format:
            yield from super().stream_predict(query, chunk_size)
            return

        assert self._remote is not None
        messages = _to_openai_messages(query)
        accumulated = ""
        last_usage = None

        for chunk in self._remote.completions_stream(messages):
            # 用量通常只在最后一个 chunk 里出现；缺失就保持 None，绝不估算。
            if getattr(chunk, "usage", None) is not None:
                last_usage = chunk.usage
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            if not piece:
                continue
            accumulated += piece
            yield LLMPrediction(response=accumulated, history=[])

        if not accumulated:
            logger.warning("OpenAI 兼容流式接口没有返回任何文本内容（model={}）。", self._remote.model_id)
            return

        query.history.append(Conversation(role=RoleEnum.user, content=query.text))
        query.history.append(Conversation(role=RoleEnum.assistant, content=accumulated))
        yield LLMPrediction(
            response=accumulated,
            history=query.history,
            usage=usage_dict(last_usage),
        )

    def parse_prediction(self, response: Response) -> LLMPrediction:
        return LLMPrediction.model_validate_json(response.content)
