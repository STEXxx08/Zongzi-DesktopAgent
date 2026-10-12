from pydantic import Field, BaseModel

from common.enumerator import BaseEnum
from common.utils.enum_util import enum_to_markdown
from pipeline.base.base_sync import AbstractPipelineConfig


#######
# ASR #
#######

class AudioFormatEnum(BaseEnum):
    Float32: str = "float32"


# model_id 不再用枚举限制：加新 ASR 只需在 Core 侧实现并在 starter.py 挂上，
# Bot 这里直接填对应的字符串即可。下面列出常见取值供参考。
MODEL_ID_PARA = "iic/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1"
MODEL_ID_KOTOBA_WHISPER = "kotoba-tech/kotoba-whisper-v2.0"
MODEL_ID_BAIDU = "BaiduASR"
MODEL_ID_QWEN3_ASR = "Qwen3-ASR-0.6B"
MODEL_ID_SHERPA_ONNX = "Sherpa-ONNX-Streaming-Zipformer-ZH-INT8"

KNOWN_ASR_MODEL_IDS = (
    MODEL_ID_PARA,
    MODEL_ID_SHERPA_ONNX,
    MODEL_ID_QWEN3_ASR,
    MODEL_ID_KOTOBA_WHISPER,
    MODEL_ID_BAIDU,
)


class BaiduASRConfig(BaseModel):
    api_key: str = Field(default="", description="The API key for Baidu ASR service.")
    secret_key: str = Field(default="", description="The secret key for Baidu ASR service.")

class DesktopAudioConfig(BaseModel):
    enable: bool = Field(default=True, description="Whether to enable desktop audio capture for ASR.")
    sample_rate: int = Field(default=16000, description="The sample rate for desktop audio capture.")
    channels: int = Field(default=1, description="The number of audio channels for desktop audio capture.")
    chunk_duration: float = Field(default=0.6, description="The duration (in seconds) of each audio chunk captured from desktop.")
    send_interval: float = Field(default=3.0, description="The interval (in seconds) to send captured desktop audio data to ASR service.")
    input_device: str | None = Field(default=None, description="The audio device to capture desktop audio from.")

class DenoiseConfig(BaseModel):
    mic_enable: bool = Field(default=False, description="Whether to enable denoising for microphone audio.")
    desktop_enable: bool = Field(default=False, description="Whether to enable denoising for desktop audio.")
    mode: str = Field(default='fast', description="Denoising mode: 'fast' or 'quality'.")
    device: str = Field(default='cpu', description="Device to run denoising on: 'cpu' or 'cuda'.")
    sample_rate: int = Field(default=16000, description="Internal processing sample rate (should match ASR).")

class TurnSenseConfig(BaseModel):
    enable: bool = Field(default=False, description="Enable semantic turn detection for microphone ASR.")
    model_path: str = Field(default="models/TurnSense", description="Local TurnSense model/repo path.")
    backend: str = Field(default="auto", description="auto, onnx, transformers, http, module, or heuristic.")
    onnx_model: str = Field(default="model_int8.onnx", description="ONNX model filename relative to model_path.")
    onnx_provider: str = Field(default="CPUExecutionProvider", description="ONNX Runtime execution provider.")
    max_length: int = Field(default=256, ge=8, description="Max tokenizer length for TurnSense text classification.")
    api_url: str = Field(default="", description="Optional HTTP endpoint returning complete/incomplete/invalid.")
    timeout: float = Field(default=0.8, ge=0.1, description="Pause timeout for flushing microphone candidate audio chunks.")
    final_timeout: float = Field(default=0.8, ge=0.1, description="Delay after TurnSense predicts complete before final LLM commit.")
    streaming_asr: bool = Field(default=True, description="Keep ASR stream open until semantic turn commit.")
    max_wait: float = Field(default=2.0, ge=0.1, description="Max seconds to hold incomplete text since first incomplete judgment.")
    max_buffer_chars: int = Field(default=160, ge=10, description="Force commit if accumulated text exceeds this length.")
    fail_open: bool = Field(default=True, description="Commit text if TurnSense fails.")
    invalid_action: str = Field(default="drop", description="drop or hold when TurnSense returns invalid.")
    invalid_confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description="Only drop invalid audio above this confidence.")
    complete_confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0, description="Minimum confidence for an early complete decision.")
    interrupt_min_audio_sec: float = Field(default=0.48, ge=0.0, description="Minimum accumulated speech before TTS barge-in.")
    interrupt_min_confidence: float = Field(default=0.75, ge=0.0, le=1.0, description="Minimum TurnSense confidence before TTS barge-in.")

class ASRPipelineConfig(AbstractPipelineConfig):
    sample_rate: int = Field(48000, description="The sample rate for audio input. "
                                               "注意：真正上报给 ASR 的采样率来自音频事件——麦克风看 "
                                               "`service.microphone.sample_rate`，桌面音频看 "
                                               "`pipeline.asr.desktop_audio.sample_rate`；这里只在拿不到事件采样率时兜底。")
    channels: int = Field(1, description="The number of audio channels. "
                                        "仅作为 TurnSense 语义 ASR 路径拼装音频时的兜底值。")
    format: AudioFormatEnum = Field(AudioFormatEnum.Float32,
                                    description=f"The format of the audio data. {enum_to_markdown(AudioFormatEnum)}\n"
                                                "注意：当前没有任何代码读取该字段，实际上报的音频格式由采集层决定"
                                                "（麦克风走 16-bit PCM WAV）。保留是为了后续扩展，改它不会改变识别结果。")
    model_id: str = Field(default=MODEL_ID_PARA,
                          description="The ID of the model used for ASR. **Any string is accepted** "
                                      "（真正决定加载哪个模型的是 Core 侧 `config.yaml` 的 `id`）。\n"
                                      f"常见取值：{', '.join(KNOWN_ASR_MODEL_IDS)}")
    predict_url: str = Field(default="http://127.0.0.1:11000/asr/predict",
                             description="The URL for ASR prediction requests. "
                                         "端口需与 Core 侧 `config.yaml` 的 `ASR.port` 一致（默认 11001）。")
    stream_predict_url: str = Field(default="http://127.0.0.1:11000/asr/stream-predict",
                                    description="The URL for streaming ASR prediction requests. "
                                                "端口需与 Core 侧 `config.yaml` 的 `ASR.port` 一致（默认 11001）。")
    baidu_asr_config: BaiduASRConfig = Field(default=BaiduASRConfig(), description="Baidu ASR config."
                                                                                   f"Only edit it when you set `model_id` to `{MODEL_ID_BAIDU}`.\n"
                                                                                   f"For more details please see the [documents](https://cloud.baidu.com/doc/SPEECH/s/qlcirqhz0).")
    turnsense: TurnSenseConfig = Field(default=TurnSenseConfig(), description="Semantic turn detector config.")
    desktop_audio: DesktopAudioConfig = Field(default=DesktopAudioConfig(), description="Desktop audio capture config.")
    denoise: DenoiseConfig = Field(default=DenoiseConfig(), description="Denoising config.")
