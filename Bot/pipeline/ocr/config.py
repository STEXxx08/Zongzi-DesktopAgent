from pydantic import Field

from pipeline.base.base_sync import AbstractPipelineConfig


#######
# OCR #
#######

# model_id 不再用枚举限制（加新 OCR 直接填字符串即可）。
MODEL_ID_PADDLE_OCR = 'paddlepaddle/PaddleOCR'

KNOWN_OCR_MODEL_IDS = (MODEL_ID_PADDLE_OCR,)


class OCRPipelineConfig(AbstractPipelineConfig):
    model_id: str = Field(default=MODEL_ID_PADDLE_OCR,
                          description="The ID of the model used for OCR. **Any string is accepted** "
                                      "（真正决定加载哪个模型的是 Core 侧 `config.yaml` 的 `id`）。\n"
                                      f"常见取值：{', '.join(KNOWN_OCR_MODEL_IDS)}")
    predict_url: str = Field(default="http://127.0.0.1:11004/ocr/predict",
                             description="The URL for OCR prediction requests. "
                                         "端口需与 Core 侧 `config.yaml` 的 `OCR.port` 一致（默认 11004）。")
    stream_predict_url: str = Field(default="http://127.0.0.1:11004/ocr/stream-predict",
                                    description="The URL for streaming OCR prediction requests. "
                                                "端口需与 Core 侧 `config.yaml` 的 `OCR.port` 一致（默认 11004）。")
