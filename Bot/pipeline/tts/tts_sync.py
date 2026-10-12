import os.path
import uuid
from http import HTTPStatus

import requests
from loguru import logger
from condata.pipeline.tts import TTSQuery, TTSPrediction, TTSStreamPrediction

from pipeline.base.base_sync import CommonModelPipeline
from pipeline.tts.baidu_tts import BaiduTTSPipeline
from pipeline.tts.config import TTSPipelineConfig, MODEL_ID_BAIDU


# 没有超时的请求会让退出流程永久卡死（KillableThread 无法中断阻塞中的 HTTP 调用），
# 因此这里给出显式上限；该上限需小于退出宽限期（service.desktop_shell.exit_grace_seconds），
# 才能保证超时由请求本身报错，而不是靠强制结束进程。超时只影响本次合成，由上层按失败处理。
TTS_REQUEST_TIMEOUT_SECONDS = 30
TTS_STREAM_TIMEOUT_SECONDS = (10, 30)


class TTSSyncPipeline(CommonModelPipeline):

    def __init__(self, config: TTSPipelineConfig):
        super().__init__(config)
        # Support Baidu TTS API
        if config.model_id == MODEL_ID_BAIDU and config.baidu_tts_config is not None:
            self.baidu = BaiduTTSPipeline(api_key=config.baidu_tts_config.api_key,
                                          secret_key=config.baidu_tts_config.secret_key)
            self.predict = self.baidu.predict
            self.stream_predict = self.baidu.stream_predict

    def predict(self, query: TTSQuery) -> TTSPrediction | None:
        assert isinstance(query, TTSQuery)
        if os.path.exists(query.refer_wav_path):
            query.refer_wav_path = os.path.abspath(query.refer_wav_path).replace("\\", "/")
        query_dict = self.parse_query(query)
        response = requests.post(url=self.predict_url, stream=True, json=query_dict,
                                 timeout=TTS_REQUEST_TIMEOUT_SECONDS)
        if response.status_code == HTTPStatus.OK:
            prediction = TTSPrediction(wave_data=response.content, audio_type=query.audio_type)
            return prediction
        else:
            logger.error(response.content)
            response.raise_for_status()

    def stream_predict(self, query: TTSQuery, chunk_size: int | None = None):
        assert isinstance(query, TTSQuery)
        if os.path.exists(query.refer_wav_path):
            query.refer_wav_path = os.path.abspath(query.refer_wav_path).replace("\\", "/")
        query_dict = self.parse_query(query)
        response = requests.post(url=self.stream_predict_url, stream=True,
                                 json=query_dict, timeout=TTS_STREAM_TIMEOUT_SECONDS)
        response.raise_for_status()
        last = 0
        id = str(uuid.uuid4())
        for idx, chunk in enumerate(response.iter_content(chunk_size=1024)):
            last = idx
            yield TTSStreamPrediction(seq=idx,
                                      id=id,
                                      is_final=False,
                                      wave_data=chunk,
                                      audio_type=query.audio_type)
        yield TTSStreamPrediction(is_final=True, seq=last + 1, audio_type=query.audio_type, wave_data=b'')

    def parse_query(self, query: any) -> dict:
        return super().parse_query(query)
