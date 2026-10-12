from pydantic import Field, BaseModel

from common.enumerator import BaseEnum
from common.utils.enum_util import enum_to_markdown
from pipeline.base.base_sync import AbstractPipelineConfig


#######
# TTS #
#######

# model_id 不再用枚举限制（加新 TTS 直接填字符串即可）。常见取值：
MODEL_ID_GPT_SOVITS = "GPT-SoVITS"
MODEL_ID_BAIDU = "BaiduTTS"

KNOWN_TTS_MODEL_IDS = (MODEL_ID_GPT_SOVITS, MODEL_ID_BAIDU)


# Config for BaiduTTS and should
class BaiduTTSConfig(BaseModel):
    api_key: str = Field(default="", description="The API key for Baidu TTS service.")
    secret_key: str = Field(default="", description="The secret key for Baidu TTS service.")


class AdaptiveSpeedConfig(BaseModel):
    enable: bool = Field(default=True,
                         description="Whether to adapt TTS speed for long text and repeated interruptions.")
    long_text_threshold: int = Field(default=50, ge=1,
                                     description="Text length threshold used by adaptive TTS speed.")
    interrupt_speed_step: int = Field(default=2, ge=0,
                                      description="Speed steps added whenever TTS is interrupted.")
    idle_decay_seconds: float = Field(default=30.0, ge=0.0,
                                      description="Seconds without new TTS before reducing one accumulated speed step. Set 0 to disable.")
    max_speed: float = Field(default=1.5, ge=1.0,
                             description="Maximum adaptive TTS speed factor.")


# Config for Core 侧 TTS 服务
class TTSPipelineConfig(AbstractPipelineConfig):
    model_id: str = Field(default=MODEL_ID_GPT_SOVITS,
                          description="The ID of the model used for text-to-speech. **Any string is accepted** "
                                      "（真正决定加载哪个模型的是 Core 侧 `config.yaml` 的 `id`）。\n"
                                      f"常见取值：{', '.join(KNOWN_TTS_MODEL_IDS)}")
    predict_url: str = Field(default="http://127.0.0.1:11002/tts/predict",
                             description="The URL for TTS prediction requests.")
    stream_predict_url: str = Field(default="http://127.0.0.1:11002/tts/stream-predict",
                                    description="The URL for streaming TTS prediction requests.")
    baidu_tts_config: BaiduTTSConfig = Field(default=BaiduTTSConfig(),
                                             description=f"Baidu TTS config. \n"
                                                         f"Only edit it when you set `model_id` to `{MODEL_ID_BAIDU}`.\n"
                                                         f"For more details please see the [documents](https://cloud.baidu.com/doc/SPEECH/s/mlbxh7xie).")
    filter: bool = Field(default=True,
                         description="Whether to filter action descriptions and other inputs in TTS requests.")
    cancel_on_new_input: bool = Field(
        default=True,
        description="Whether to cancel pending TTS tasks when a new LLM response arrives."
    )
    adaptive_speed: AdaptiveSpeedConfig = Field(default=AdaptiveSpeedConfig(),
                                                description="Adaptive TTS speed config.")
    output_device: str | None = Field(default=None,
                               description="The audio output device for TTS playback. If not set, the default device will be used.")
    allow_default_device_fallback: bool = Field(
        default=False,
        description="指定 output_device 播放失败时，是否允许改走系统默认设备。"
                    "默认不允许：悄悄改走默认设备会破坏防自激前提（TTS 被麦克风/桌面采集再识别回来）。")
    playback_volume: float = Field(default=0.8, ge=0.0,
                                   description="The local playback volume for TTS audio. 1.0 keeps the original volume.")
