from pydantic import Field

from pipeline.base.base_sync import AbstractPipelineConfig


##########
# VLM    #
##########

# model_id 不再用枚举限制（加新 VLM 直接填字符串即可）。
MODEL_ID_QWEN3_VL_2B = 'Qwen/Qwen3-VL-2B-Instruct-FP8'
MODEL_ID_QWEN3_VL_4B = 'Qwen/Qwen3-VL-4B-Instruct-FP8'

KNOWN_VLM_MODEL_IDS = (MODEL_ID_QWEN3_VL_2B, MODEL_ID_QWEN3_VL_4B)


class VLMPipelineConfig(AbstractPipelineConfig):
    model_id: str = Field(
        default=MODEL_ID_QWEN3_VL_2B,
        description="The ID of the model used for Vision Language Model. **Any string is accepted**. "
                    f"常见取值：{', '.join(KNOWN_VLM_MODEL_IDS)}"
    )
    predict_url: str = Field(
        default="http://127.0.0.1:10003",
        description="The full URL for VLM prediction requests (OpenAI compatible API endpoint)."
    )
    stream_predict_url: str = Field(
        default="http://127.0.0.1:10003",  # VLM 不支持流式，留空即可
        description="The URL for streaming VLM prediction requests (not supported)."
    )
    model_name: str = Field(
        default="/model",
        description="The model name used in the API request (usually matches the served model name)."
    )
    max_tokens: int = Field(
        default=512,
        description="Maximum number of tokens to generate in the response.",
        ge=1,
        le=1024
    )
    temperature: float = Field(
        default=0.0,
        description="Sampling temperature (0 for deterministic output).",
        ge=0.0,
        le=2.0
    )
    max_pixels: int = Field(
        default=200704,  # 448*448
        description="Maximum pixel count for input images (used to resize before sending).",
        ge=1024
    )
    mode: str = Field(
        default="keyword",
        description="VLM mode: 'keyword' (trigger by keyword) or 'continuous' (always on)."
    )
    continuous_interval: int = Field(
        default=5,
        description="Interval in seconds for background capture in continuous mode.",
        ge=1
    )