from typing import Literal

from typeguard import typechecked
from condata.pipeline.ocr import OCRQuery, OCRPrediction

from pipeline.base.base_async import BaseAsyncPipeline, get_base_url
from pipeline.ocr.config import OCRPipelineConfig


class OCRAsyncPipeline(BaseAsyncPipeline):
    def __init__(self, config: OCRPipelineConfig):
        super().__init__(base_url=get_base_url(config.predict_url))
        self._model_id: str = str(config.model_id)
        self._predict_endpoint = "/ocr/predict"
        self._stream_predict_endpoint = "/ocr/stream-predict"

    @typechecked
    async def predict(self, query: OCRQuery) -> OCRPrediction:
        return await self._post_image_prediction(self._predict_endpoint, query, OCRPrediction)
