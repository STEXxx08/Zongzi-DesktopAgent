from typing import Generator
from typeguard import typechecked
from condata.pipeline.mllm import MLLMQuery, MLLMPrediction
from condata.harness import trace_model_call
from pipeline.base.base_async import BaseAsyncPipeline, get_base_url
from pipeline.mllm.config import MLLMPipelineConfig

class MLLMAsyncPipeline(BaseAsyncPipeline):
    def __init__(self, config: MLLMPipelineConfig):
        super().__init__(base_url=get_base_url(config.predict_url))
        self._predict_endpoint = "/mllm/predict"
        self._stream_predict_endpoint = "/mllm/stream-predict"

    @typechecked
    @trace_model_call
    async def predict(self, query: MLLMQuery) -> MLLMPrediction:
        async with self.session.post(self._predict_endpoint, json=query.model_dump()) as resp:
            return await resp.json(encoding='utf8', loads=MLLMPrediction.model_validate_json)

    @typechecked
    @trace_model_call
    async def stream_predict(self, query: MLLMQuery) -> Generator[MLLMPrediction, None, None]:
        async with self.session.post(self._stream_predict_endpoint, json=query.model_dump()) as resp:
            async for line in resp.content:
                if line:
                    yield MLLMPrediction.model_validate_json(line)