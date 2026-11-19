from typing import Literal

from typeguard import typechecked
from condata.pipeline.img_cap import ImgCapQuery, ImgCapPrediction

from pipeline.base.base_async import BaseAsyncPipeline, get_base_url
from pipeline.imgcap.config import ImgCapPipelineConfig


class ImgCapAsyncPipeline(BaseAsyncPipeline):
    def __init__(self, config: ImgCapPipelineConfig):
        super().__init__(base_url=get_base_url(config.predict_url))
        self._model_id: str = str(config.model_id)
        self._predict_endpoint = "/img-cap/predict"
        self._stream_predict_endpoint = "/img-cap/stream-predict"

    @typechecked
    async def predict(self, query: ImgCapQuery) -> ImgCapPrediction:
        return await self._post_image_prediction(self._predict_endpoint, query, ImgCapPrediction)
