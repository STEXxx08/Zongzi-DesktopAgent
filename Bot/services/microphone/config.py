from pydantic import BaseModel, Field

class VadInterimConfig(BaseModel):
    enable: bool = Field(default=True, description="Enable interim chunk submission for TTS interruption.")
    interim_silence_timeout: float = Field(default=0.5, ge=0.1, description="Silence timeout for interim submission (seconds).")
    min_interim_audio_sec: float = Field(default=0.5, ge=0.2, description="Minimum audio length to trigger interim submission (seconds).")

class StreamingASRConfig(BaseModel):
    enable: bool = Field(default=True, description="Emit small speech chunks continuously when semantic streaming ASR is active.")
    chunk_audio_sec: float = Field(default=0.24, ge=0.03, description="Audio seconds per streaming chunk.")

class MicrophoneServiceConfig(BaseModel):
    enable: bool = Field(default=True, description="Enable microphone.")
    vad_enabled: bool = Field(default=True, description="Enable Voice Activity Detection.")
    vad_mode: int = Field(default=3, ge=0, le=3, description="VAD aggressiveness (0-3).")
    hotkey: str = Field(default='f8', description="Hotkey to toggle microphone on/off.")
    # webrtcvad 只支持 8000/16000/32000/48000；采集率与上报给 ASR 的采样率必须一致，
    # 否则 ASR 会把音频当成别的采样率来解码（听感上就是快放或慢放）。
    sample_rate: int = Field(default=16000, description="麦克风采集采样率；必须与 ASR 服务的采样率一致。")
    silence_timeout: float = Field(default=1.4, ge=0.1, description="Seconds to wait after speech ends before commit.")
    rms_threshold: float = Field(default=200.0, ge=0, description="RMS energy threshold to filter low-energy frames (0 = disable).")
    vad_interim: VadInterimConfig = Field(default_factory=VadInterimConfig, description="Interim VAD configuration.")
    streaming_asr: StreamingASRConfig = Field(default_factory=StreamingASRConfig, description="Streaming ASR chunk emission configuration.")
