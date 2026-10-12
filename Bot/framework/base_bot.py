from loguru import logger

from framework.context import BotContext
from manager.config_manager import get_config
from pipeline.asr.asr_async import ASRAsyncPipeline
from pipeline.asr.asr_sync import ASRSyncPipeline
from pipeline.imgcap.imgcap_async import ImgCapAsyncPipeline
from pipeline.imgcap.imgcap_sync import ImgCapSyncPipeline
from pipeline.llm.llm_async import LLMAsyncPipeline
from pipeline.llm.llm_sync import LLMSyncPipeline
from pipeline.ocr.ocr_async import OCRAsyncPipeline
from pipeline.ocr.ocr_sync import OCRSyncPipeline
from pipeline.tts.tts_async import TTSAsyncPipeline
from pipeline.tts.tts_sync import TTSSyncPipeline


class BaseBot(BotContext):
    def __init__(self):
        super().__init__()

    def reload_pipeline(self):
        config = get_config()

        # ASR Pipeline
        if self.asr is not None:
            if isinstance(self.asr, ASRSyncPipeline):
                self.asr = ASRSyncPipeline(config.pipeline.asr)
            elif isinstance(self.asr, ASRAsyncPipeline):
                self.asr = ASRAsyncPipeline(config.pipeline.asr)
            else:
                logger.error(f"Unsupported pipeline type: {type(self.asr)}")
        else:
            logger.warning("Pipeline asr will not reload because it has not been established.")

        # Image Captioning Pipeline
        if self.img_cap is not None:
            if isinstance(self.img_cap, ImgCapSyncPipeline):
                self.img_cap = ImgCapSyncPipeline(config.pipeline.img_cap)
            elif isinstance(self.img_cap, ImgCapAsyncPipeline):
                self.img_cap = ImgCapAsyncPipeline(config.pipeline.img_cap)
            else:
                logger.error(f"Unsupported pipeline type: {type(self.img_cap)}")
        else:
            logger.warning("Pipeline img_cap will not reload because it has not been established.")

        # LLM Pipeline
        if self.llm is not None:
            if isinstance(self.llm, LLMSyncPipeline):
                self.llm = LLMSyncPipeline(config.pipeline.llm)
            elif isinstance(self.llm, LLMAsyncPipeline):
                self.llm = LLMAsyncPipeline(config.pipeline.llm)
            else:
                logger.error(f"Unsupported pipeline type: {type(self.llm)}")
        else:
            logger.warning("Pipeline llm will not reload because it has not been established.")

        # OCR Pipeline
        if self.ocr is not None:
            if isinstance(self.ocr, OCRSyncPipeline):
                self.ocr = OCRSyncPipeline(config.pipeline.ocr)
            elif isinstance(self.ocr, OCRAsyncPipeline):
                self.ocr = OCRAsyncPipeline(config.pipeline.ocr)
            else:
                logger.error(f"Unsupported pipeline type: {type(self.ocr)}")
        else:
            logger.warning("Pipeline ocr will not reload because it has not been established.")

        # TTS Pipeline
        if self.tts is not None:
            if isinstance(self.tts, TTSSyncPipeline):
                self.tts = TTSSyncPipeline(config.pipeline.tts)
            elif isinstance(self.tts, TTSAsyncPipeline):
                self.tts = TTSAsyncPipeline(config.pipeline.tts)
            else:
                logger.error(f"Unsupported pipeline type: {type(self.tts)}")
        else:
            logger.warning("Pipeline tts will not reload because it has not been established.")

        logger.info("Reloaded pipelines.")

    def reload_device(self):
        config = get_config()

        # Microphone
        if self.mic is not None:
            if config.service.microphone.enable:
                self.mic.resume()
            else:
                self.mic.pause()
        else:
            logger.info("Microphone will not reload because there is no microphone found.")
