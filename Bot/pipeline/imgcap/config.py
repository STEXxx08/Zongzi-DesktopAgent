from pydantic import Field

from pipeline.base.base_sync import AbstractPipelineConfig


##########
# ImgCap #
##########

# model_id 不再用枚举限制（加新图像描述模型直接填字符串即可）。
MODEL_ID_BLIP = 'Salesforce/blip-image-captioning-large'

KNOWN_IMGCAP_MODEL_IDS = (MODEL_ID_BLIP,)


class ImgCapPipelineConfig(AbstractPipelineConfig):
    model_id: str = Field(default=MODEL_ID_BLIP,
                          description="The ID of the model used for image captioning. **Any string is accepted** "
                                      "（真正决定加载哪个模型的是 Core 侧 `config.yaml` 的 `id`）。\n"
                                      f"常见取值：{', '.join(KNOWN_IMGCAP_MODEL_IDS)}")
    predict_url: str = Field(default="http://127.0.0.1:11000/img-cap/predict",
                             description="The URL for image captioning prediction requests.")
    stream_predict_url: str = Field(default="http://127.0.0.1:11000/img-cap/stream-predict",
                                    description="The URL for streaming image captioning prediction requests.")
