from typing import Generator

from typeguard import typechecked
from condata.pipeline.llm import LLMQuery, LLMPrediction
from condata.harness import trace_model_call

from pipeline.base.base_async import BaseAsyncPipeline, get_base_url
from pipeline.llm.config import LLMPipelineConfig


class LLMAsyncPipeline(BaseAsyncPipeline):
    def __init__(self, config: LLMPipelineConfig):
        super().__init__(base_url=get_base_url(config.predict_url))
        self._model_id: str = str(config.model_id)
        self._predict_endpoint = "/llm/predict"
        self._stream_predict_endpoint = "/llm/stream-predict"

    @typechecked
    @trace_model_call
    async def predict(self, query: LLMQuery) -> LLMPrediction:
        async with self.session.post(self._predict_endpoint, json=query.model_dump()) as resp:
            return await resp.json(encoding='utf8', loads=LLMPrediction.model_validate_json)

    @typechecked
    @trace_model_call
    async def stream_predict(self, query: LLMQuery) -> Generator[LLMPrediction, None, None]:
        async with self.session.post(self._stream_predict_endpoint, json=query.model_dump()) as resp:
            return await resp.json(encoding='utf8', loads=LLMPrediction.model_validate_json)