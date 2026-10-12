import traceback
from typing import List, Any, Generator
from openai import OpenAI
from loguru import logger

from common.abs_model import AbstractModel           # 假设你的基类在这里
from condata.pipeline.llm import LLMQuery, LLMPrediction, Conversation, RoleEnum
from condata.harness import usage_dict
from common.decorator import log_model_loading, issue_solver   # 假设有这些装饰器

from .config import DeepSeekOnlineConfig


class DeepSeekOnlineModel(AbstractModel):
    """DeepSeek 在线 API 模型（无状态，每次请求携带完整历史）"""

    def __init__(self, config: DeepSeekOnlineConfig):
        super().__init__()
        self.model_id = "deepseek_online/DeepSeek-v4-flash"
        self.config = config
        self._client: OpenAI | None = None

    @log_model_loading("DeepSeek Online API")
    @issue_solver()
    def load_model(self):
        """初始化 OpenAI 客户端（API 调用无需加载模型权重）"""
        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )

    def predict(self, llm_query: LLMQuery) -> LLMPrediction:
        """非流式预测"""
        logger.info(f"=== predict called with query: {llm_query.text[:50]}... ===")

        if self._client is None:
            logger.error("Client is None, model not loaded!")
            raise RuntimeError("Model not loaded. Call load_model() first.")
    
        try:
            messages = self._to_api_messages(llm_query)
            logger.info(f"Messages to send: {messages}")
    
            logger.info(f"Calling API with model={self.config.model_version}, timeout={self.config.timeout}")
            response = self._client.chat.completions.create(
                model=self.config.model_version,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                top_p=self.config.top_p,
                frequency_penalty=self.config.frequency_penalty,
                presence_penalty=self.config.presence_penalty,
                stream=False,
            )
            logger.info(f"API response received: {response}")
    
            response_text = response.choices[0].message.content
            logger.info(f"Response text: {response_text[:100]}...")
    
            updated_history = self._build_updated_history(llm_query, response_text)
            return self._to_pipeline_format(response_text, updated_history, usage_dict(getattr(response, "usage", None)))
    
        except Exception as e:
            logger.error(f"Exception in predict: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            # 重新抛出，让上层知道错误
            raise

    def stream_predict(self, llm_query: LLMQuery) -> Generator[LLMPrediction, None, None]:
        """流式预测，yield 每个分片"""
        if self._client is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        messages = self._to_api_messages(llm_query)
        logger.info(f"Sending messages to API: {messages}") # 添加日志

        try:
            stream = self._client.chat.completions.create(
                model=self.config.model_version,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                top_p=self.config.top_p,
                frequency_penalty=self.config.frequency_penalty,
                presence_penalty=self.config.presence_penalty,
                stream=True,
                stream_options={"include_usage": True},
            )

            for chunk in stream:
                usage = usage_dict(getattr(chunk, "usage", None))
                content = chunk.choices[0].delta.content if chunk.choices else None
                if content is not None or usage is not None:
                    # 用量可能单独出现在空 choices 的末尾数据块中。
                    partial_pred = LLMPrediction(response=content or "", history=llm_query.history, usage=usage)
                    yield partial_pred

            # 3. 流结束后，可以 yield 一个包含完整历史和空回复的结束信号
            # 或者直接结束，由客户端自行处理
            final_pred = LLMPrediction(response="", history=llm_query.history) # 可选
            yield final_pred

        except Exception as e:
            logger.error(f"Error during streaming: {e}")
            # 可以选择yield一个错误信息
            error_pred = LLMPrediction(response=f"Error: {str(e)}", history=llm_query.history)
            yield error_pred

    # ---------- 辅助方法 ----------
    def _to_api_messages(self, llm_query: LLMQuery) -> List[dict]:
        messages = []
        system_content = None
    
        # 遍历历史，分离 system、user、assistant
        for chat in llm_query.history:
            if chat.role == RoleEnum.system:
                # 只取第一个 system（或根据需求合并）
                if system_content is None:
                    system_content = chat.content
            elif chat.role == RoleEnum.user:
                messages.append({"role": "user", "content": chat.content})
            elif chat.role == RoleEnum.assistant:
                messages.append({"role": "assistant", "content": chat.content})
            # 其他角色忽略
    
        # 如果存在 system，插入到最前面（OpenAI 要求 system 在第一条）
        if system_content:
            messages.insert(0, {"role": "system", "content": system_content})
    
        # 添加当前用户输入（作为新的 user 消息）
        messages.append({"role": "user", "content": llm_query.text})
    
        return messages

    def _build_updated_history(self, llm_query: LLMQuery, assistant_response: str) -> List[Conversation]:
        """构建包含本轮问答的新历史记录"""
        new_history = list(llm_query.history)  # 复制原有历史
        # 添加用户本轮输入（如果历史中已经包含则不必重复，这里假设历史不包含当前 query）
        # 注意：原有的 llm_query.history 通常不包含当前这一轮的 user 消息，
        # 而 _to_api_messages 已经将当前 user 消息作为最后一条传入 API。
        # 为了返回给调用方一个完整的对话历史，我们手动添加本轮 user 和 assistant。
        new_history.append(Conversation(role=RoleEnum.user, content=llm_query.text))
        new_history.append(Conversation(role=RoleEnum.assistant, content=assistant_response))
        return new_history

    @staticmethod
    def _to_pipeline_format(response: str, history: List[Conversation], usage: dict | None = None) -> LLMPrediction:
        """包装为标准输出格式"""
        return LLMPrediction(response=response, history=history, usage=usage)