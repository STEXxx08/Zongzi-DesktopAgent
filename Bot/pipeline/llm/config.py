from pydantic import Field

from pipeline.base.base_sync import AbstractPipelineConfig

#######
# LLM #
#######

# 这里**不再用枚举限制 model_id**。原因：
#   1) `model_id` 只在少数分支里被读取（判断是否走 OpenAI 兼容接口等），
#      并不参与"加载哪个模型"的决策 —— 那是 Core 侧 `config.yaml` 的 `id` 决定的。
#   2) 原来的枚举把"模型名单"当成了校验，导致名单外的兼容模型直接被拒，
#      与"ASR/LLM/TTS 可任意组合"的设计目标冲突。
#
# 下面列出**常见取值**供参考，取值本身不受限制：
KNOWN_MODEL_IDS = (
    # —— OpenAI 兼容的在线服务（`openai_format: true`）——
    "deepseek-chat",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "moonshot-v1-8k",
    "doubao-seed-1-6-flash-250715",
    # —— 走 HTTP 服务、由 Core 加载的本地模型（`openai_format: false`）——
    "THUDM/chatglm3-6b",
    "THUDM/GLM-4",
    "THUDM/glm-4-9b-chat-hf",
    "Qwen/Qwen-7B-Chat",
    "augmxnt/shisa-7b-v1",
    "01-ai/Yi-6B-Chat",
    "deepseek-ai/DeepSeek-R1-Distill-Llama-8B",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
    "ollama",
)


class LLMPipelineConfig(AbstractPipelineConfig):
    api_key: str | None = Field(
        default=None,
        description="访问 LLM 服务所需的 API 密钥。\n"
                    "`openai_format: true` 时必填（本地 Ollama 之类可随便填，例如 EMPTY）；\n"
                    "走本地 HTTP 服务时不需要。",
    )
    openai_format: bool = Field(
        default=False,
        description="是否走 OpenAI 兼容接口。\n"
                    "`true` 时把 `predict_url` 当作 base_url 直接调用（DeepSeek / Kimi / 豆包 / "
                    "vLLM / Ollama / 任何兼容端点皆可）；\n"
                    "`false` 时 POST 到 Core 侧的 `/llm/predict`。",
    )
    model_id: str = Field(
        default="THUDM/GLM-4",
        description="模型标识。\n"
                    "走 HTTP 服务时，真正决定加载哪个模型的是 Core 侧 `config.yaml` 的 `id`，"
                    "这里主要用于日志与分支判断；\n"
                    "走 OpenAI 兼容接口时，这里就是请求里带的 model 名。\n"
                    f"常见取值：{', '.join(KNOWN_MODEL_IDS)}",
    )
    temperature: float | None = Field(
        default=None,
        description="采样温度。**留空表示不发送该参数**，由服务端使用自己的默认值"
                    "（各家默认值不同，覆盖会改变模型行为）。",
    )
    max_tokens: int | None = Field(
        default=None,
        description="单次回复的最大 token 数。留空表示不发送该参数。",
    )
    predict_url: str = Field(
        default="http://127.0.0.1:11000/llm/predict",
        description="LLM 预测请求地址。\n"
                    "`openai_format: false` 时指向 Core 服务；\n"
                    "`openai_format: true` 时填 base_url，例如 `https://api.deepseek.com`。",
    )
    stream_predict_url: str = Field(
        default="http://127.0.0.1:11000/llm/stream-predict",
        description="LLM 流式预测请求地址（仅 `openai_format: false` 时使用）。",
    )
