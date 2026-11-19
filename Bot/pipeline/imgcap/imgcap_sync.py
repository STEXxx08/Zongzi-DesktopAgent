from condata.pipeline.img_cap import ImgCapQuery, ImgCapPrediction

from pipeline.base.base_sync import AbstractImagePipeline
from pipeline.imgcap.config import ImgCapPipelineConfig


class ImgCapSyncPipeline(AbstractImagePipeline):
    _prediction_model = ImgCapPrediction

    def __init__(self, config: ImgCapPipelineConfig):
        super().__init__(config)

    def predict(self, query: ImgCapQuery) -> ImgCapPrediction | None:
        assert isinstance(query, ImgCapQuery)
        return super().predict(query)

    def stream_predict(self, query: ImgCapQuery, chunk_size: int | None = None):
        assert isinstance(query, ImgCapQuery)
        raise NotImplementedError()
