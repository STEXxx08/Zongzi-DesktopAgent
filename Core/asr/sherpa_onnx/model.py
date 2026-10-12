import io
import wave
from pathlib import Path
from threading import Lock

import numpy as np
from loguru import logger

from asr.sherpa_onnx.config import SherpaONNXASRConfig
from common.decorator import log_model_loading
from condata.pipeline.asr import ASRPrediction, ASRQuery, ASRStreamQuery


class SherpaONNXASRModel:
    def __init__(self, config: SherpaONNXASRConfig):
        self.config = config
        self._sherpa_onnx = None
        self._recognizer = None
        self._stream = None
        self._lock = Lock()
        self._last_text = ""

    @log_model_loading("Sherpa-ONNX Streaming Zipformer ASR")
    def load_model(self):
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "sherpa_onnx is not installed. Install the CUDA wheel in the Core Python environment."
            ) from exc

        self._sherpa_onnx = sherpa_onnx
        model_dir = Path(self.config.model_dir)
        encoder = str(model_dir / self.config.encoder)
        decoder = str(model_dir / self.config.decoder)
        joiner = str(model_dir / self.config.joiner)
        tokens = str(model_dir / self.config.tokens)

        for file_path in [encoder, decoder, joiner, tokens]:
            if not Path(file_path).exists():
                raise FileNotFoundError(f"Sherpa-ONNX model file not found: {file_path}")

        logger.info(
            "Loading Sherpa-ONNX ASR: provider={}, encoder={}, model_size={:.1f} MiB",
            self.config.provider,
            encoder,
            sum(Path(path).stat().st_size for path in [encoder, decoder, joiner]) / (1024 * 1024),
        )
        self._recognizer = self._create_recognizer(encoder, decoder, joiner, tokens)
        self._reset_stream()
        logger.info("Sherpa-ONNX ASR loaded successfully.")

    def _create_recognizer(self, encoder: str, decoder: str, joiner: str, tokens: str):
        if hasattr(self._sherpa_onnx.OnlineRecognizer, "from_transducer"):
            kwargs = dict(
                tokens=tokens,
                encoder=encoder,
                decoder=decoder,
                joiner=joiner,
                num_threads=self.config.num_threads,
                sample_rate=self.config.sample_rate,
                feature_dim=self.config.feature_dim,
                decoding_method=self.config.decoding_method,
                provider=self.config.provider,
                enable_endpoint_detection=self.config.enable_endpoint_detection,
            )
            try:
                return self._sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs)
            except TypeError:
                kwargs.pop("enable_endpoint_detection", None)
                return self._sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs)

        recognizer_config = self._sherpa_onnx.OnlineRecognizerConfig(
            transducer=self._sherpa_onnx.OnlineTransducerModelConfig(
                encoder=encoder,
                decoder=decoder,
                joiner=joiner,
            ),
            tokens=tokens,
            num_threads=self.config.num_threads,
            sample_rate=self.config.sample_rate,
            feature_dim=self.config.feature_dim,
            decoding_method=self.config.decoding_method,
            provider=self.config.provider,
            enable_endpoint_detection=self.config.enable_endpoint_detection,
        )
        return self._sherpa_onnx.OnlineRecognizer(recognizer_config)

    def predict(self, query: ASRQuery) -> ASRPrediction:
        with open(query.audio_path, "rb") as file:
            audio_data = file.read()
        return self.stream_predict(ASRStreamQuery(
            is_final=True,
            audio_data=audio_data,
            media_type=query.media_type,
            sample_rate=query.sample_rate,
            channels=query.channels,
        )) or ASRPrediction(transcript="")

    def stream_predict(self, query: ASRStreamQuery) -> ASRPrediction | None:
        with self._lock:
            if query.audio_data:
                samples, sample_rate = self._decode_audio(query.audio_data, query.sample_rate, query.channels)
                if len(samples):
                    self._stream.accept_waveform(sample_rate, samples)
                    self._decode_ready()

            text = self._result_text()
            prediction = None
            if text and text != self._last_text:
                self._last_text = text
                prediction = ASRPrediction(transcript=text)

            if query.is_final:
                self._finish_stream()
                final_text = self._result_text()
                if final_text:
                    prediction = ASRPrediction(transcript=final_text)
                self._reset_stream()

            return prediction

    def _decode_ready(self):
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)

    def _finish_stream(self):
        # Streaming Zipformer needs right-context audio to emit the last tokens.
        # sherpa-onnx's official online examples append 0.66 s of silence before
        # input_finished(); without it, sentence-final characters are often lost.
        tail_padding_seconds = max(0.0, float(self.config.tail_padding_seconds))
        if tail_padding_seconds:
            tail_padding = np.zeros(
                int(tail_padding_seconds * self.config.sample_rate),
                dtype=np.float32,
            )
            self._stream.accept_waveform(self.config.sample_rate, tail_padding)
        if hasattr(self._stream, "input_finished"):
            self._stream.input_finished()
        elif hasattr(self._stream, "input_finished_"):
            self._stream.input_finished_()
        self._decode_ready()

    def _reset_stream(self):
        self._stream = self._recognizer.create_stream()
        self._last_text = ""

    def _result_text(self) -> str:
        result = self._recognizer.get_result(self._stream)
        if isinstance(result, str):
            return result.strip()
        return getattr(result, "text", "").strip()

    def _decode_audio(self, audio_data: bytes, sample_rate: int, channels: int) -> tuple[np.ndarray, int]:
        if self._looks_like_wav(audio_data):
            with wave.open(io.BytesIO(audio_data), "rb") as wav_file:
                sample_rate = wav_file.getframerate()
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                frames = wav_file.readframes(wav_file.getnframes())
            if sample_width != 2:
                raise ValueError(f"Only 16-bit PCM WAV is supported, got sample width {sample_width}")
            samples = np.frombuffer(frames, dtype=np.int16)
        else:
            samples = np.frombuffer(audio_data, dtype=np.int16)

        if channels > 1 and len(samples):
            samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)

        samples = samples.astype(np.float32) / 32768.0
        if sample_rate != self.config.sample_rate:
            samples = self._resample_linear(samples, sample_rate, self.config.sample_rate)
            sample_rate = self.config.sample_rate
        return samples, sample_rate

    @staticmethod
    def _looks_like_wav(audio_data: bytes) -> bool:
        return len(audio_data) >= 12 and audio_data[:4] == b"RIFF" and audio_data[8:12] == b"WAVE"

    @staticmethod
    def _resample_linear(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
        if source_rate == target_rate or len(samples) == 0:
            return samples
        duration = len(samples) / source_rate
        target_len = max(1, int(duration * target_rate))
        old_positions = np.linspace(0.0, duration, num=len(samples), endpoint=False)
        new_positions = np.linspace(0.0, duration, num=target_len, endpoint=False)
        return np.interp(new_positions, old_positions, samples).astype(np.float32)