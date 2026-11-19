from loguru import logger

from framework.context import BotContext
from manager.config_manager import get_config


class BaseBot(BotContext):
    def __init__(self):
        super().__init__()

    def _reload_pipeline_attr(
        self,
        attr_name: str,
        enabled: bool,
        pipeline_cfg,
        sync_cls,
        async_cls,
    ) -> None:
        current = getattr(self, attr_name, None)
        if not enabled:
            setattr(self, attr_name, None)
            return
        if current is None:
            logger.warning(f"Pipeline {attr_name} will not reload because it has not been established.")
            return
        if isinstance(current, sync_cls):
            setattr(self, attr_name, sync_cls(pipeline_cfg))
        elif isinstance(current, async_cls):
            setattr(self, attr_name, async_cls(pipeline_cfg))
        else:
            logger.error(f"Unsupported pipeline type: {type(current)}")

    def reload_pipeline(self):
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

        config = get_config()
        specs = (
            ("asr", config.pipeline.asr.enable, config.pipeline.asr, ASRSyncPipeline, ASRAsyncPipeline),
            ("img_cap", config.pipeline.img_cap.enable, config.pipeline.img_cap, ImgCapSyncPipeline, ImgCapAsyncPipeline),
            ("llm", config.pipeline.llm.enable and not config.pipeline.mllm.enable, config.pipeline.llm, LLMSyncPipeline, LLMAsyncPipeline),
            ("ocr", config.pipeline.ocr.enable, config.pipeline.ocr, OCRSyncPipeline, OCRAsyncPipeline),
            ("tts", config.pipeline.tts.enable, config.pipeline.tts, TTSSyncPipeline, TTSAsyncPipeline),
        )
        for attr_name, enabled, pipeline_cfg, sync_cls, async_cls in specs:
            self._reload_pipeline_attr(attr_name, enabled, pipeline_cfg, sync_cls, async_cls)

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
