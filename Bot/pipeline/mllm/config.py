from pydantic import Field
from typing import Optional, List
from pipeline.base.base_sync import AbstractPipelineConfig


# model_id 一直是自由字符串（未使用的旧枚举已删除）。
KNOWN_MLLM_MODEL_IDS = ("qwen-vl-plus", "qwen3.7-plus")


class MLLMPipelineConfig(AbstractPipelineConfig):
    """配置多模态大语言模型（MLLM）服务的参数。"""

    enable: bool = Field(
        default=False,
        description="是否启用 MLLM 服务。"
    )
    predict_url: str = Field(
        default="http://127.0.0.1:11000/mllm/predict",
        description="MLLM 预测请求的 URL。"
    )
    stream_predict_url: str = Field(
        default="http://127.0.0.1:11000/mllm/stream-predict",
        description="MLLM 流式预测请求的 URL。"
    )
    stream_output: bool = Field(
        default=True,
        description="是否启用 MLLM 流式输出。关闭时保持原有非流式逻辑。"
    )
    stream_tts_by_sentence: bool = Field(
        default=True,
        description="启用 MLLM 流式输出时，是否按分句将增量文本提交给 TTS。"
    )
    reasoning_keywords: List[str] = Field(
        default=["思考", "推理", "分析"],
        description="ASR 输入命中这些关键词时，为本次 MLLM 请求临时启用 thinking。"
    )
    reasoning_budget_on_keyword: int = Field(
        default=80,
        ge=1,
        description="关键词触发 thinking 时使用的 thinking_budget。"
    )
    screenshot_interval: float = Field(
        default=3.0,
        description="截屏间隔（秒），用于定时获取屏幕内容。",
        gt=0.0
    )
    screenshot_definition: int = Field(
        default=1024,
        description="用于控制上传屏幕内容图像的最大长边像素量。",
        gt=0.0
    )
    max_queue_size: int = Field(
        default=2,
        description="请求队列的最大容量，超过时拒绝新请求。",
        ge=1
    )
    api_key: Optional[str] = Field(
        default="",
        description="访问 MLLM 服务所需的 API 密钥（若服务端需要）。客户端可忽略。"
    )
    model_id: str = Field(
        default="qwen3.7-plus",
        description="使用的模型标识符，**任意字符串都接受**（由服务端决定是否认识）。\n"
                    f"常见取值：{', '.join(KNOWN_MLLM_MODEL_IDS)}"
    )
