from loguru import logger
import requests
from requests import Response
from typeguard import typechecked
from condata.pipeline.mllm import MLLMQuery, MLLMPrediction
from condata.harness import trace_model_call, record_protocol_error
from pipeline.base.base_sync import CommonModelPipeline
from pipeline.mllm.config import MLLMPipelineConfig

class MLLMSyncPipeline(CommonModelPipeline):
    def __init__(self, config: MLLMPipelineConfig):
        super().__init__(config)
        self._stream_predict_url = config.stream_predict_url

    @typechecked
    def predict(self, query: MLLMQuery) -> MLLMPrediction | None:
        return super().predict(query)

    def parse_prediction(self, response: Response) -> MLLMPrediction:
        return MLLMPrediction.model_validate_json(response.content)

    def parse_query(self, query: MLLMQuery) -> dict:
        query_dict = super().parse_query(query)
        for key in ("enable_thinking", "thinking_budget"):
            if hasattr(query, key):
                value = getattr(query, key)
                if value is not None:
                    query_dict[key] = value
        return query_dict

    @typechecked
    @trace_model_call
    def stream_predict(self, query: MLLMQuery):
        response = requests.post(
            self._stream_predict_url,
            json=self.parse_query(query),
            stream=True,
        )
        response.raise_for_status()
        first_line_seen = False
        first_text_seen = False
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            if not first_line_seen:
                first_line_seen = True
                logger.debug("MLLM 客户端已收到首条流式数据。")
            try:
                prediction = MLLMPrediction.model_validate_json(line)
                if prediction.response and not first_text_seen:
                    first_text_seen = True
                    logger.debug("MLLM 客户端已收到首段非空流式响应。")
                yield prediction
            except Exception as e:
                record_protocol_error("mllm_stream", line, e)
                logger.warning("MLLM 流式数据解析失败，详情已记录到 Harness 日志。")