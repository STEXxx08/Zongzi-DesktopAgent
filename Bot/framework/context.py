import devices.headless
from character.filter.strategy import FirstMatchedFilter
from devices.headless import is_headless
from devices.speaker import Speaker
from manager.config_manager import get_config
from manager.llm_prompt_manager import LLMPromptManager
from manager.tts_prompt_manager import TTSPromptManager
from pipeline.asr.asr_sync import ASRSyncPipeline
from pipeline.imgcap.imgcap_sync import ImgCapSyncPipeline
from pipeline.llm.llm_sync import LLMSyncPipeline
from pipeline.ocr.ocr_sync import OCRSyncPipeline
from pipeline.vlm.vlm_sync import VLMSyncPipeline
from pipeline.tts.tts_sync import TTSSyncPipeline
from services.microphone.microphone import SmartMicrophone

_config = get_config()


class BotContext:
    """
    BotContext 应只包括初始化上下文资源的工作，
    事件监听器注册和各类事件的发送逻辑不应该体现在这里。
    BotContext here should only include initializing context resources.
    The logic for registering listeners and sending events shouldn't be reflected here.
    """

    def __init__(self):
        self.llm: LLMSyncPipeline | None = None
        self.asr: ASRSyncPipeline | None = None
        self.ocr: OCRSyncPipeline | None = None
        self.vlm: VLMSyncPipeline | None = None
        self.tts: TTSSyncPipeline | None = None
        self.img_cap: ImgCapSyncPipeline | None = None

        self.filter: FirstMatchedFilter | None = None
        self.llm_prompt_manager: LLMPromptManager | None = None
        self.tts_prompt_manager: TTSPromptManager | None = None

        if not devices.headless.is_headless():
            from devices.screen.base_screen import Screen
            self.screen: Screen = Screen()
        else:
            self.screen = None

        self.speaker: Speaker | None = None

        self.bot_name: str | None = None
        # 主人称呼（仅备注用途，当前无代码读取；随项目改名一并更新）
        self.master_name: str = "STEXxx"

        assert _config.pipeline.llm.enable, f"At least LLMPipeline must be enabled in your config."
        self.llm = LLMSyncPipeline(_config.pipeline.llm)
        self.filter = FirstMatchedFilter(_config.character.chat.filter.bad_words)
        self.llm_prompt_manager = LLMPromptManager(_config.character.chat, bot_name=_config.character.bot_name)
        self.speaker = Speaker()
        self.bot_name = _config.character.bot_name

        if _config.pipeline.asr.enable:
            self.asr = ASRSyncPipeline(_config.pipeline.asr)
        if _config.pipeline.ocr.enable:
            self.ocr = OCRSyncPipeline(_config.pipeline.ocr)
        if _config.pipeline.tts.enable:
            self.tts_prompt_manager = TTSPromptManager(_config.character.speech)
            self.tts = TTSSyncPipeline(_config.pipeline.tts)
        if _config.pipeline.img_cap.enable:
            self.img_cap = ImgCapSyncPipeline(_config.pipeline.img_cap)
        if _config.pipeline.vlm.enable:
            self.vlm = VLMSyncPipeline(_config.pipeline.vlm)
        if _config.service.microphone.vad_interim.enable:
            self.mic = SmartMicrophone(
                enable_vad=_config.service.microphone.vad_enabled,
                vad_mode=_config.service.microphone.vad_mode,
                sample_rate=_config.service.microphone.sample_rate,
                silence_timeout=_config.service.microphone.silence_timeout,   # 兼容旧接口
                rms_threshold=_config.service.microphone.rms_threshold,
                interim_enable=True,
                interim_silence_timeout=_config.service.microphone.vad_interim.interim_silence_timeout,
                min_interim_audio_sec=_config.service.microphone.vad_interim.min_interim_audio_sec
            )
        else:
            self.mic = SmartMicrophone(enable_vad=_config.service.microphone.enable, vad_mode=_config.service.microphone.vad_mode
                                    , sample_rate=_config.service.microphone.sample_rate
                                    , silence_timeout=_config.service.microphone.silence_timeout, rms_threshold=_config.service.microphone.rms_threshold)

        # Headless systems cannot load pynput.
        self.keyboard = None
        if not is_headless():
            from devices.keyboard import SmartKeyboard
            self.keyboard = SmartKeyboard(hotkeys=[_config.service.microphone.hotkey])
