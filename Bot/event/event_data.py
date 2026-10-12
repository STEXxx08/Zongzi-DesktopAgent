from pathlib import Path
from typing import Any, Union

from pydantic import BaseModel
from condata.pipeline.asr import ASRPrediction
from condata.pipeline.img_cap import ImgCapPrediction
from condata.pipeline.llm import LLMPrediction
from condata.pipeline.mllm import MLLMPrediction
from condata.pipeline.ocr import OCRPrediction
from condata.pipeline.tts import TTSPrediction

from common.io.file_type import AudioFileType
from event.registry import EventKeyRegistry


class BaseEvent(BaseModel):
    type: str


### System ###
class ConfigFileModifiedEvent(BaseEvent):
    type: str = EventKeyRegistry.System.CONFIG_FILE_MODIFIED


class LanguageChangeEvent(BaseEvent):
    target_lang: str
    type: str = EventKeyRegistry.System.LANG_CHANGE


class SystemUnhandledErrorEvent(BaseEvent):
    msg: str
    ex: Any
    type: str = EventKeyRegistry.System.SYSTEM_UNHANDLED_ERROR


class SystemCrashedEvent(BaseEvent):
    type: str = EventKeyRegistry.System.SYSTEM_CRASHED


class CharacterStateEvent(BaseEvent):
    state: str
    emotion: str | None = None
    type: str = EventKeyRegistry.Character.STATE


class SecondEvent(BaseEvent):
    elapsed: int = 0
    type: str = EventKeyRegistry.System.SECOND


### Pipeline ###
class PipelineASREvent(BaseEvent):
    prediction: ASRPrediction
    type: str = EventKeyRegistry.Pipeline.ASR
    skip_tts: bool = False   # 新增，默认 False
    source: str = "mic"   # 新增字段，表示音频来源，可能的值包括 "mic"（麦克风）和 "desktop"（桌面音频）

class DeviceDesktopAudioEvent(BaseEvent):
    type: str = EventKeyRegistry.Device.DESKTOP_AUDIO
    audio_data: bytes
    sample_rate: int
    channels: int
    media_type: str = 'raw'

class PipelineOutputLLMEvent(BaseEvent):
    prediction: Union[LLMPrediction, MLLMPrediction]
    type: str = EventKeyRegistry.Pipeline.LLM
    skip_tts: bool = False   # 新增，默认 False
    emotion: str | None = None


class PipelineOutputTTSEvent(BaseEvent):
    prediction: TTSPrediction
    transcript: str
    emotion: str = "Default"
    type: str = EventKeyRegistry.Pipeline.TTS


class PipelineImgCapEvent(BaseEvent):
    prediction: ImgCapPrediction
    type: str = EventKeyRegistry.Pipeline.IMG_CAP


class PipelineOCREvent(BaseEvent):
    prediction: OCRPrediction
    type: str = EventKeyRegistry.Pipeline.OCR

### Device ###
class DeviceScreenCapturedEvent(BaseEvent):
    img_path: Path
    is_camera: bool
    type: str = EventKeyRegistry.Device.SCREEN_CAPTURED


class DeviceKeyboardPressEvent(BaseEvent):
    hotkey: str
    type: str = EventKeyRegistry.Device.KEYBOARD_HOTKEY_PRESS


class DeviceMicrophoneVADEvent(BaseEvent):
    speech: bytes
    audio_type: AudioFileType
    channels: int
    sample_rate: int
    type: str = EventKeyRegistry.Device.MICROPHONE_VAD
    is_final: bool = True   # 新增，默认为True以保证向后兼容


class DeviceMicrophoneSwitchEvent(BaseEvent):
    switch: bool
    type: str = EventKeyRegistry.Device.MICROPHONE_SWITCH


class DeviceSpeakerPlayEvent(BaseEvent):
    audio_path: Path
    emotion: str = "Default"
    type: str = EventKeyRegistry.Device.SPEAKER_PLAY
