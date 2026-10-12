import base64
import os
from typing import List, Generator, Optional

import dashscope
from dashscope import MultiModalConversation
from loguru import logger
from condata.pipeline.mllm import MLLMQuery, MLLMPrediction
from condata.harness import usage_dict
from condata.pipeline.llm import Conversation, RoleEnum
from common.abs_model import AbstractModel
from .config import MLLMPipelineConfig


class MLLMOnlineModel(AbstractModel):
    def _model_name(self) -> str:
        return getattr(self.config.model_name, "value", self.config.model_name)

    @staticmethod
    def _role_value(role) -> str:
        return getattr(role, "value", role)

    @staticmethod
    def _bool_value(value) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def _generation_kwargs(self, messages: List[dict], stream: bool, query: MLLMQuery | None = None) -> dict:
        enable_thinking_config = getattr(query, "enable_thinking", self.config.enable_thinking)
        enable_thinking = self._bool_value(enable_thinking_config)
        kwargs = {
            "api_key": self.api_key,
            "model": self._model_name(),
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "top_p": self.config.top_p,
            "enable_thinking": enable_thinking,
            "repetition_penalty": self.config.repetition_penalty,
            "presence_penalty": self.config.presence_penalty,
            "stream": stream,
        }
        if enable_thinking:
            kwargs["thinking_budget"] = getattr(query, "thinking_budget", self.config.thinking_budget)
        if stream:
            kwargs["incremental_output"] = True
        logger.debug(
            f"DashScope 参数：模型={kwargs['model']}，流式={stream}，"
            f"启用思考={enable_thinking}，思考预算={kwargs.get('thinking_budget')}"
        )
        return kwargs

    def __init__(self, config: MLLMPipelineConfig):
        super().__init__()
        self.config = config
        # 设置 DashScope 基础 URL
        if config.api_url:
            dashscope.base_http_api_url = config.api_url
        # 设置 API Key（从环境变量读取）
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError("缺少 DashScope API Key，请在配置或 DASHSCOPE_API_KEY 环境变量中设置")

    def load_model(self):
        """无需加载权重，仅验证配置"""
        logger.info("MLLM 在线模型已加载，DashScope 客户端就绪。")

    def predict(self, query: MLLMQuery) -> MLLMPrediction:
        logger.info(f"开始 MLLM 预测，查询内容：{query.text}...")
        messages = self._build_messages(query)
        img_count = sum(1 for msg in messages for part in msg.get("content", []) if "image" in part)
        logger.info(
            f"待发送内容：{len(messages)} 条消息，{img_count} 张图片，"
            f"系统提示词='{query.history[0].content if query.history and query.history[0].role==RoleEnum.system else '无'}'"
        )
        logger.debug(
            f"DashScope 非流式请求开始：消息数={len(messages)}，图片数={img_count}，"
            f"历史消息数={len(query.history)}，文本字符数={len(query.text or '')}"
        )
        try:
            response = MultiModalConversation.call(
                **self._generation_kwargs(messages, stream=False, query=query)
            )
            logger.debug("DashScope 非流式请求已返回。")
        except Exception as e:
            logger.error(f"DashScope API 调用失败：{e}")
            raise

        if response.status_code != 200:
            logger.error(f"DashScope API 返回错误：{response.message}")
            raise RuntimeError(f"DashScope API 返回错误：{response.message}")

        reasoning = getattr(response.output.choices[0].message, "reasoning_content", None)
        if reasoning:
            logger.info(f"思考内容：{reasoning}...")
        # 提取响应文本（多模态输出 content 为数组）
        content = response.output.choices[0].message.content
        if isinstance(content, list):
            # 取第一个 text 元素
            output_text = next((item["text"] for item in content if "text" in item), "")
        else:
            output_text = content

        updated_history = self._build_updated_history(query, output_text)
        return MLLMPrediction(response=output_text, history=updated_history, usage=usage_dict(getattr(response, "usage", None)))

    def stream_predict(self, query: MLLMQuery) -> Generator[MLLMPrediction, None, None]:
        logger.info(f"开始 MLLM 流式预测，查询内容：{query.text[:50]}...")
        messages = self._build_messages(query)
        img_count = sum(1 for msg in messages for part in msg.get("content", []) if "image" in part)
        logger.debug(
            f"DashScope 流式请求开始：消息数={len(messages)}，图片数={img_count}，"
            f"历史消息数={len(query.history)}，文本字符数={len(query.text or '')}"
        )
        first_response_seen = False
        first_reasoning_seen = False
        first_text_seen = False
        try:
            responses = MultiModalConversation.call(
                **self._generation_kwargs(messages, stream=True, query=query)
            )
            logger.debug("DashScope 流式迭代器已返回。")
            for resp in responses:
                if resp.status_code == 200:
                    if not first_response_seen:
                        first_response_seen = True
                        logger.debug("已收到 DashScope 首个原始流式响应。")
                    message = resp.output.choices[0].message
                    reasoning = getattr(message, "reasoning_content", None)
                    if reasoning and not first_reasoning_seen:
                        first_reasoning_seen = True
                        logger.debug("已收到 DashScope 首段流式思考内容。")
                    content = message.content
                    if isinstance(content, list):
                        text = next((item["text"] for item in content if "text" in item), "")
                    else:
                        text = content
                    if text and not first_text_seen:
                        first_text_seen = True
                        logger.debug("已收到 DashScope 首段流式文本。")
                    yield MLLMPrediction(response=text, history=query.history, usage=usage_dict(getattr(resp, "usage", None)))
                else:
                    logger.error(f"DashScope 流式响应错误：{resp.message}")
                    yield MLLMPrediction(response=f"Error: {resp.message}", history=query.history,
                                         usage=usage_dict(getattr(resp, "usage", None)))
                    break
        except Exception as e:
            logger.error(f"DashScope 流式调用错误：{e}")
            yield MLLMPrediction(response=f"Error: {e}", history=query.history)

    def _build_messages(self, query: MLLMQuery) -> List[dict]:
        """
        构建 DashScope 要求的 messages 格式。
        content 为数组，元素为 {"text": ...} 或 {"image": ...}。
        """
        messages = []
        # 处理系统消息
        system_content = None
        for chat in query.history:
            role = self._role_value(chat.role)
            if role == "system":
                system_content = chat.content
            elif role in ("user", "assistant"):
                # 历史对话（纯文本）
                messages.append({
                    "role": role,
                    "content": [{"text": chat.content}]
                })
        if system_content:
            messages.insert(0, {
                "role": "system",
                "content": [{
                    "text": system_content,
                    "cache_control": {"type": "ephemeral"},
                }],
            })

        # 当前用户输入（文本 + 图片）
        content_parts = []
        if query.text:
            content_parts.append({"text": query.text})
        for img_b64 in query.images:
            # 如果已经是 data URI 则直接使用，否则补充前缀
            if img_b64.startswith("data:image"):
                content_parts.append({"image": img_b64})
            else:
                content_parts.append({"image": f"data:image/jpeg;base64,{img_b64}"})

        messages.append({
            "role": "user",
            "content": content_parts
        })

        return messages

    def _build_updated_history(self, query: MLLMQuery, assistant_response: str) -> List[Conversation]:
        new_history = list(query.history)
        new_history.append(Conversation(role=RoleEnum.user, content=query.text))
        new_history.append(Conversation(role=RoleEnum.assistant, content=assistant_response))
        return new_history