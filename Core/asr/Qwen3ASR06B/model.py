"""
Qwen3-ASR model implementation using transformers backend.
Reference: https://github.com/QwenLM/Qwen3-ASR
"""

import numpy as np
from loguru import logger
from qwen_asr import Qwen3ASRModel
import torch
from transformers import BitsAndBytesConfig
from asr.Qwen3ASR06B.config import QwenAsrModelConfig
from utils import audio_util
from common.decorator import log_model_loading
from condata.pipeline.asr import ASRPrediction, ASRQuery, ASRStreamQuery


class QwenAsrModel:
    def __init__(self, config: QwenAsrModelConfig):
        self.config = config
        self._model = None
        # 固定采样率，Qwen3-ASR 内部可重采样，但建议输入 16kHz
        self._sample_rate = 16000
        self.dtype = np.float32

    @log_model_loading("Qwen3-ASR (transformers)")
    def load_model(self):
        """
        Load Qwen3-ASR model with transformers backend.
        """
        logger.info(f"Loading Qwen3-ASR from {self.config.model_path} with dtype={self.config.dtype}, device={self.config.device}")
        # dtype = getattr(torch, self.config.dtype, torch.bfloat16)
        # 1. 配置 4-bit 量化

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,          # 双重量化，进一步压缩
            bnb_4bit_quant_type="nf4",               # 最优 4-bit 类型
            bnb_4bit_compute_dtype=torch.bfloat16,   # 计算时仍用 bf16 保证精度
        )

        init_kwargs = {
            "quantization_config": bnb_config,
            "device_map": self.config.device,
            "max_new_tokens": self.config.max_new_tokens,
            "max_inference_batch_size": 1,
        }
        # 启用 Flash Attention 2（如果可用）
        if self.config.use_flash_attn:
            try:
                import flash_attn  # noqa: F401
                init_kwargs["attn_implementation"] = "flash_attention_2"
                logger.info("Flash Attention 2 enabled.")
            except ImportError:
                logger.warning("flash_attn not installed, falling back to default attention.")

        self._model = Qwen3ASRModel.from_pretrained(self.config.model_path, **init_kwargs)
        assert self._model is not None, "Failed to load Qwen3-ASR model."
        logger.info("Qwen3-ASR model loaded successfully.")

    def predict(self, query: ASRQuery) -> ASRPrediction | None:
        """
        Non-streaming prediction from audio file path.
        """
        # 读取音频为 numpy 数组（确保为 float32 和单声道）
        wave_nparray, sample_rate = audio_util.from_file_to_np_array(query.audio_path, self.dtype.__name__)
        # 检查采样率（Qwen 内部会重采样，但为了效率建议 16kHz）
        if sample_rate != self._sample_rate:
            logger.warning(f"Sample rate mismatch: {sample_rate} != {self._sample_rate}, will be resampled internally.")
        # 调用 transcribe，传入 (array, sr)
        return self._wrapper(wave_nparray, sample_rate)

    def stream_predict(self, query: ASRStreamQuery) -> ASRPrediction | None:
        if query.media_type.lower() != 'raw':
            wave_nparray, sample_rate = audio_util.from_bytes_to_np_ndarray(query.audio_data, self.dtype.__name__)
        else:
            wave_nparray = np.frombuffer(buffer=query.audio_data, dtype=self.dtype.__name__)
            sample_rate = query.sample_rate

        # 直接返回 _wrapper 的结果，不使用 yield
        return self._wrapper(wave_nparray, sample_rate)

    def _wrapper(self, wave_nparray: np.ndarray, sample_rate: int) -> ASRPrediction | None:
        """
        Internal method to perform transcription on numpy array and return ASRPrediction.
        """
        assert wave_nparray is not None and isinstance(wave_nparray, np.ndarray), "Wrong format."
        assert len(wave_nparray) > 0, "The audio tensor size must be greater than 0."
        assert len(wave_nparray.shape) == 1, "The audio must be mono."
        assert wave_nparray.dtype == np.float32, "The dtype must be numpy.float32."

        try:
            # 调用 Qwen3ASRModel.transcribe，传入 (array, sr)
            results = self._model.transcribe(
                audio=(wave_nparray, sample_rate),
                language=self.config.language,  # None for auto-detect
            )
            if not results:
                logger.warning("No transcription result returned.")
                return None

            transcript = results[0].text.strip()
            if not transcript:
                logger.debug("Empty transcript.")
                return None

            logger.info(f"ASR result: {transcript}")
            return ASRPrediction(transcript=transcript)

        except Exception as e:
            logger.exception(f"Prediction error: {e}")
            raise e