import numpy as np
from loguru import logger

try:
    from clearvoice import ClearVoice
    CV_AVAILABLE = True
except ImportError:
    CV_AVAILABLE = False
    logger.warning("ClearVoice not installed, denoise feature will be disabled")


class Denoiser:
    def __init__(self, config: dict):
        self.enable = (config.mic_enable or config.desktop_enable)
        self.mode = config.mode
        self.device = config.device
        self.target_sr = config.sample_rate
        self._clearvoice = None
        # if self.target_sr >= 48000:
        #     self._model_names = ["FRCRN_SE_48K"]
        #     self._model_sr = 48000
        # else:
        #     self._model_names = ["FRCRN_SE_16K"]
        #     self._model_sr = 16000
        self._model_names = ["FRCRN_SE_16K"]
        self._model_sr = 16000
        if self.enable:
            if not CV_AVAILABLE:
                logger.error("ClearVoice is required but not installed. Disable denoise.")
                self.enable = False
            else:
                try:
                    self._clearvoice = ClearVoice(
                        task="speech_enhancement",
                        model_names=self._model_names,
                    )
                    logger.info(
                        f"models={self._model_names}, requested_device={self.device}, "
                        f"clearvoice_device=auto"
                    )
                except Exception as e:
                    logger.error(f"Failed to initialize ClearVoice: {e}")
                    self.enable = False

    def process(self, audio_data: np.ndarray, sample_rate: int) -> np.ndarray:
        """降噪后返回人声（单声道 float32，采样率与输入一致）。

        关键点：模型只吃 16k，所以内部会重采样到 16k，**处理完必须再重采样回输入
        采样率**。否则调用方仍按原 sample_rate 写 WAV 头并上报 ASR，音频时长会被
        压缩（48k -> 16k 变成 1/3），ASR 实际收到的是快放音频。
        """
        if not self.enable or self._clearvoice is None:
            return audio_data.astype(np.float32)

        # 1. 确保 float32
        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)

        # 2. 转为单声道
        if audio_data.ndim > 1:
            audio_data = np.mean(audio_data, axis=1)

        if audio_data.size == 0:
            return audio_data

        original_length = int(audio_data.shape[0])

        # 3. 重采样到模型要求的采样率（如果需要）
        model_input = audio_data
        if sample_rate and sample_rate != self._model_sr:
            import scipy.signal
            model_input = scipy.signal.resample(
                audio_data, max(1, int(len(audio_data) * self._model_sr / sample_rate))
            )

        try:
            # ClearVoice 的 NumPy 接口要求输入形状为 [batch, length]
            output = self._clearvoice(model_input.reshape(1, -1), False)
            vocals = np.asarray(output[0, :], dtype=np.float32)
        except Exception as e:
            logger.error(f"Denoise processing failed: {e}, returning original")
            return audio_data

        # 4. 还原到输入采样率，保持时长与调用方的 WAV 头一致
        if sample_rate and sample_rate != self._model_sr and vocals.size:
            import scipy.signal
            vocals = scipy.signal.resample(vocals, original_length)
        return vocals.astype(np.float32)
