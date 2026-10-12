from pydantic import BaseModel, Field

from pipeline.asr.config import ASRPipelineConfig
from pipeline.imgcap.config import ImgCapPipelineConfig
from pipeline.llm.config import LLMPipelineConfig
from pipeline.mllm.config import MLLMPipelineConfig
from pipeline.ocr.config import OCRPipelineConfig
from pipeline.tts.config import TTSPipelineConfig
from pipeline.vlm.config import VLMPipelineConfig


class PipelineConfig(BaseModel):
    asr: ASRPipelineConfig = Field(default=ASRPipelineConfig(),
                                   description="Configuration for the Automatic Speech Recognition pipeline.")
    llm: LLMPipelineConfig = Field(default=LLMPipelineConfig(),
                                   description="Configuration for the Large Language Model pipeline.")
    mllm: MLLMPipelineConfig = Field(default=MLLMPipelineConfig(),
                                   description="Configuration for the Multi Large Language Model pipeline.")
    img_cap: ImgCapPipelineConfig = Field(default=ImgCapPipelineConfig(),
                                          description="Configuration for the Image Captioning pipeline.")
    ocr: OCRPipelineConfig = Field(default=OCRPipelineConfig(),
                                   description="Configuration for the Optical Character Recognition pipeline.")
    vlm: VLMPipelineConfig = Field(default=VLMPipelineConfig(),
                                   description="Configuration for the Visual Language Model pipeline.")
    tts: TTSPipelineConfig = Field(default=TTSPipelineConfig(),
                                   description="Configuration for the Text-to-Speech pipeline.")
