from __future__ import annotations

from concurrent.futures.thread import ThreadPoolExecutor
from contextlib import nullcontext
import io
import threading
import time
from typing import Any, Callable
import wave

from loguru import logger
import numpy as np

from common.utils.str_util import is_blank
from condata.pipeline.asr import ASRPrediction, ASRStreamQuery
from event.event_data import (
    DeviceDesktopAudioEvent,
    DeviceMicrophoneVADEvent,
    PipelineASREvent,
)
from event.event_emitter import emitter
from pipeline.asr.turnsense import TurnSenseDetector


def ASR_result_filter(transcript: str) -> bool:
    """过滤空白或纯口癖填充词的 ASR 识别结果。"""
    return not transcript or len(transcript) < 2 or transcript in [
        "utter",
        "我。",
        "“我”",
        "嗯。",
        "嗯嗯",
        "啊啊",
        "哦哦",
        "呃呃",
        "我们是。",
        "我感觉，我",
    ]


class BotASRController:
    """直连 Bot 主控的语音输入控制器：管理 TurnSense 语义 ASR 状态机、流式累加与桌面 ASR 缓冲。"""

    def __init__(
        self,
        *,
        config_provider: Callable[[], Any],
        asr_provider: Callable[[], Any],
        mic_provider: Callable[[], Any],
        on_interrupt_tts: Callable[[], None],
        on_reset_auto_chat: Callable[[], None],
        emit_event: Callable[[Any], None] = emitter.emit,
    ) -> None:
        self._config_provider = config_provider
        self._asr_provider = asr_provider
        self._mic_provider = mic_provider
        self._on_interrupt_tts = on_interrupt_tts
        self._on_reset_auto_chat = on_reset_auto_chat
        self._emit_event = emit_event

        cfg = self._config_provider()

        # 麦克风 ASR 累积文本缓冲区（用于拼接中间结果）
        self._asr_accumulated_text = ""
        self._asr_accumulated_lock = threading.Lock()
        self._pending_asr_count = 0
        self._final_pending = False
        self._asr_incomplete_since = 0.0
        self._asr_force_commit_timer: threading.Timer | None = None
        self._asr_timer_generation = 0
        self._asr_audio_chunks: list[np.ndarray] = []
        self._asr_audio_sample_rate = 0
        # 本轮累计说话时长（秒）。与 _asr_audio_chunks 分开：后者会被 TurnSense 的
        # window_audio_sec 裁剪，而 TTS 打断门槛需要的是完整时长。
        self._asr_audio_total_seconds = 0.0
        self._semantic_asr_interrupted = False
        self._semantic_asr_pending_complete = False
        self._semantic_asr_executor = ThreadPoolExecutor(max_workers=1)
        self.turnsense_detector = None
        self.refresh_runtime_config(reset_state=False)

        # 桌面 ASR 缓冲区
        self.desktop_asr_buffer: list[str] = []
        self._desktop_asr_lock = threading.Lock()
        self.max_desktop_sentences = max(
            1, int(60 // cfg.pipeline.asr.desktop_audio.send_interval)
        )
        self._mic_processing = False
        self._mic_processing_lock = threading.Lock()
        self._mic_utterance_active = False
        self._mic_utterance_last_chunk = 0.0
        self._mic_utterance_stale_seconds = 30.0
        self._reset_remote_after_turn = True

        # 桌面音频捕获初始化（受 ASR 启用控制）
        self.desktop_audio_capture = None
        if cfg.pipeline.asr.enable and cfg.pipeline.asr.desktop_audio.enable:
            from pipeline.asr.desktop_capture import DesktopAudioCapture

            desktop_cfg = cfg.pipeline.asr.desktop_audio
            input_device = desktop_cfg.input_device
            self.desktop_audio_capture = DesktopAudioCapture(
                sample_rate=desktop_cfg.sample_rate,
                channels=desktop_cfg.channels,
                read_chunk_duration=desktop_cfg.chunk_duration,
                send_interval=desktop_cfg.send_interval,
                input_device=input_device,
            )
            self.desktop_audio_capture.start()
            logger.info("已启用桌面音频捕获，设备：{}".format(input_device))

        # 去噪器初始化
        denoise_config = cfg.pipeline.asr.denoise
        if denoise_config.mic_enable or denoise_config.desktop_enable:
            from pipeline.asr.denoiser import Denoiser

            self.denoiser = Denoiser(denoise_config)
        else:
            self.denoiser = None

    @staticmethod
    def _as_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _is_turnsense_config_enabled(self) -> bool:
        cfg = self._config_provider()
        return self._as_bool(getattr(cfg.pipeline.asr.turnsense, "enable", False))

    def is_semantic_asr_enabled(self) -> bool:
        return self._is_turnsense_config_enabled() and self.turnsense_detector is not None

    def _submit_semantic_asr_task(self, callback, *args) -> None:
        try:
            future = self._semantic_asr_executor.submit(callback, *args)
            future.add_done_callback(self._log_semantic_asr_task_error)
        except RuntimeError as exc:
            logger.debug(f"关闭过程中已忽略语义 ASR 任务：{exc}")

    @staticmethod
    def _log_semantic_asr_task_error(future) -> None:
        try:
            exc = future.exception()
        except Exception as callback_exc:
            logger.error(f"检查语义 ASR 任务失败：{callback_exc}", exc_info=True)
            return
        if exc is not None:
            logger.opt(exception=exc).error(f"语义 ASR 任务失败：{exc}")

    def refresh_runtime_config(self, reset_state: bool = True) -> None:
        cfg = self._config_provider()
        turnsense_config_enabled = self._is_turnsense_config_enabled()

        if not turnsense_config_enabled:
            if reset_state:
                self.semantic_asr_reset(reset_remote=False, reset_microphone=False)
            self.turnsense_detector = None
            self.configure_microphone_semantic_mode()
            logger.info(
                f"TurnSense 语义 ASR 模式：配置启用={turnsense_config_enabled}，"
                f"当前激活={self.is_semantic_asr_enabled()}，"
                f"模型 ID={getattr(cfg.pipeline.asr.model_id, 'value', cfg.pipeline.asr.model_id)}"
            )
            return

        if self.turnsense_detector is None:
            try:
                self.turnsense_detector = TurnSenseDetector(cfg.pipeline.asr.turnsense)
                logger.info("🧠 TurnSense 检测器初始化完成。")
            except Exception as exc:
                logger.error(f"TurnSense 初始化失败：{exc}", exc_info=True)
                self.turnsense_detector = None
                if not cfg.pipeline.asr.turnsense.fail_open:
                    raise

        self.configure_microphone_semantic_mode()
        logger.info(
            f"TurnSense 语义 ASR 模式：配置启用={turnsense_config_enabled}，"
            f"当前激活={self.is_semantic_asr_enabled()}，"
            f"模型 ID={getattr(cfg.pipeline.asr.model_id, 'value', cfg.pipeline.asr.model_id)}"
        )

    def configure_microphone_semantic_mode(self) -> None:
        mic = self._mic_provider()
        if mic is None:
            return
        cfg = self._config_provider()

        if not self.is_semantic_asr_enabled():
            mic.semantic_turn_mode = False
            mic.streaming_asr_enable = False
            vad_interim_cfg = getattr(cfg.service.microphone, "vad_interim", None)
            if vad_interim_cfg is not None:
                mic.interim_enable = self._as_bool(
                    getattr(vad_interim_cfg, "enable", mic.interim_enable)
                )
                mic.interim_silence_timeout = float(
                    getattr(
                        vad_interim_cfg,
                        "interim_silence_timeout",
                        mic.interim_silence_timeout,
                    )
                )
                min_interim_audio_sec = float(
                    getattr(vad_interim_cfg, "min_interim_audio_sec", 0.5)
                )
                mic.min_interim_frames = max(
                    1, int(min_interim_audio_sec * mic._sample_rate / mic._chunk_size)
                )
            logger.info(
                f"🎙️ 标准麦克风 ASR 模式：语义轮次={mic.semantic_turn_mode}，"
                f"候选流式识别={mic.streaming_asr_enable}，"
                f"中间结果={getattr(mic, 'interim_enable', False)}，"
                f"中间结果静音超时={getattr(mic, 'interim_silence_timeout', 0.0):.2f} 秒"
            )
            return

        stream_cfg = getattr(cfg.service.microphone, "streaming_asr", None)
        turnsense_cfg = cfg.pipeline.asr.turnsense
        mic.semantic_turn_mode = True
        mic.streaming_asr_enable = True
        if stream_cfg is not None:
            chunk_audio_sec = float(getattr(stream_cfg, "chunk_audio_sec", 0.24))
            mic.streaming_chunk_frames = max(
                1, int(chunk_audio_sec * mic._sample_rate / mic._chunk_size)
            )
        mic.interim_silence_timeout = float(getattr(turnsense_cfg, "timeout", 0.8))
        logger.info(
            f"🎙️ 语义麦克风分块：语义轮次={mic.semantic_turn_mode}，"
            f"候选流式识别={mic.streaming_asr_enable}，"
            f"分块帧数={mic.streaming_chunk_frames}，"
            f"暂停刷新超时={mic.interim_silence_timeout:.2f} 秒"
        )

    def handle_vad_chunk(self, event: DeviceMicrophoneVADEvent) -> None:
        """处理麦克风 VAD 音频块（降噪、语义 ASR 路由或标准流式 ASR）。"""
        cfg = self._config_provider()
        with self._mic_processing_lock:
            self._mic_processing = True
            self._mic_utterance_active = True
            self._mic_utterance_last_chunk = time.monotonic()
        try:
            self._on_reset_auto_chat()
            speech, channels, sample_rate = (
                event.speech,
                event.channels,
                event.sample_rate,
            )

            if cfg.pipeline.asr.denoise.mic_enable and channels == 1 and self.denoiser is not None:
                logger.debug("正在对麦克风音频块应用降噪。")
                try:
                    audio_float = self._decode_turnsense_audio(
                        speech, channels, fallback_raw=False
                    )
                    processed = self.denoiser.process(audio_float, sample_rate)
                    encoded = self._encode_turnsense_audio(processed, sample_rate)
                    if encoded:
                        speech = encoded
                except Exception as exc:
                    logger.error(f"麦克风音频降噪失败，将使用原始音频：{exc}")

            if self.is_semantic_asr_enabled():
                logger.debug(
                    f"语义 ASR 路径已启用：is_final={event.is_final}，"
                    f"字节数={len(speech or b'')}"
                )
                self._submit_semantic_asr_task(
                    self._handle_semantic_streaming_asr,
                    event,
                    speech,
                    channels,
                    sample_rate,
                )
                return

            if event.is_final and not speech:
                full_text = self._mark_asr_final()
                if full_text:
                    self._emit_final_asr_text(full_text)
                return

            with self._asr_accumulated_lock:
                self._pending_asr_count += 1
                logger.debug(f"ASR 待处理计数增加，当前为 {self._pending_asr_count}")

            transcript = ""
            append_transcript = False
            try:
                asr = self._asr_provider()
                query = ASRStreamQuery(
                    is_final=event.is_final,
                    audio_data=speech,
                    channels=channels,
                    sample_rate=sample_rate,
                    media_type=event.audio_type.value,
                )
                for prediction in asr.stream_predict(query):
                    candidate = prediction.transcript
                    if not ASR_result_filter(candidate):
                        transcript = candidate
                        append_transcript = True
                        break

                if append_transcript:
                    self._on_interrupt_tts()
                else:
                    logger.info("ASR 结果已被过滤。")
            except Exception as exc:
                logger.error(f"ASR 流式识别失败：{exc}", exc_info=True)
            finally:
                full_text = self._finish_asr_request(
                    transcript=transcript if append_transcript else "",
                    is_final=event.is_final,
                )
                if full_text:
                    self._emit_final_asr_text(full_text)
        except Exception:
            logger.exception("处理 VAD 语音块时发生未处理异常。")
        finally:
            with self._mic_processing_lock:
                self._mic_processing = False
                if event.is_final or not self._asr_utterance_pending():
                    self._mic_utterance_active = False

    def handle_desktop_audio(self, event: DeviceDesktopAudioEvent) -> None:
        """处理桌面捕获音频；麦克风活跃期间自动让路。"""
        if not self.desktop_asr_allowed():
            return

        cfg = self._config_provider()
        audio_data = event.audio_data
        if cfg.pipeline.asr.denoise.desktop_enable and self.denoiser is not None:
            try:
                audio_array = np.frombuffer(audio_data, dtype=np.float32)
                processed = self.denoiser.process(audio_array, event.sample_rate)
                audio_data = processed.tobytes()
            except Exception as exc:
                logger.error(f"桌面音频降噪失败，将使用原始音频：{exc}")

        asr = self._asr_provider()
        query = ASRStreamQuery(
            is_final=True,
            audio_data=audio_data,
            channels=event.channels,
            sample_rate=event.sample_rate,
            media_type=event.media_type,
        )
        for prediction in asr.stream_predict(query):
            if prediction.transcript and not is_blank(prediction.transcript):
                self._emit_event(
                    PipelineASREvent(
                        prediction=prediction,
                        source="desktop",
                        skip_tts=True,
                    )
                )

    def _handle_semantic_streaming_asr(
        self,
        event: DeviceMicrophoneVADEvent,
        speech: bytes,
        channels: int,
        sample_rate: int,
    ) -> None:
        if not self.is_semantic_asr_enabled():
            return
        if not speech:
            if event.is_final:
                logger.debug("语义 ASR 收到最终标记，正在结束缓冲流。")
                self._semantic_asr_complete_timeout_commit(
                    reason="semantic-final-marker"
                )
            return

        self._semantic_asr_append_audio(speech, channels, sample_rate)
        result = self._semantic_asr_predict_audio()
        if result is None:
            logger.debug("TurnSense 音频结果暂不可用，继续保留候选音频。")
            if not self._asr_incomplete_since:
                self._asr_incomplete_since = time.monotonic()
            self._schedule_semantic_asr_timeout()
            return

        cfg = self._config_provider()
        raw_label = result.label
        label = raw_label
        logger.info(
            f"TurnSense 音频候选：标签={label}，置信度={result.confidence:.3f}，"
            f"延迟={result.latency:.3f} 秒，字节数={len(speech)}，采样率={sample_rate}"
        )

        invalid_confidence_threshold = float(
            getattr(cfg.pipeline.asr.turnsense, "invalid_confidence_threshold", 0.8)
        )
        if label == "invalid" and result.confidence >= invalid_confidence_threshold:
            with self._asr_accumulated_lock:
                has_valid_turn = (
                    bool(self._asr_accumulated_text.strip())
                    or self._semantic_asr_pending_complete
                )
            if has_valid_turn:
                logger.info(
                    "当前轮次已有有效文本，已忽略 TurnSense 无效尾音并保留待执行的最终提交。"
                )
                if not self._semantic_asr_pending_complete:
                    self._schedule_semantic_asr_complete_commit()
                return
            if getattr(cfg.pipeline.asr.turnsense, "invalid_action", "drop") == "drop":
                logger.info("TurnSense 候选结果无效，丢弃当前语义 ASR 轮次。")
                self.semantic_asr_reset(reset_remote=True, reset_microphone=True)
            return
        if label == "invalid":
            label = "incomplete"

        complete_confidence_threshold = float(
            getattr(cfg.pipeline.asr.turnsense, "complete_confidence_threshold", 0.75)
        )
        if label == "complete" and result.confidence < complete_confidence_threshold:
            label = "incomplete"

        if self._semantic_asr_pending_complete:
            logger.info("语义 ASR 在完成超时前收到新的有效音频，继续当前轮次。")
            self._semantic_asr_pending_complete = False
            with self._asr_accumulated_lock:
                self._asr_timer_generation += 1
                if self._asr_force_commit_timer:
                    self._asr_force_commit_timer.cancel()
                    self._asr_force_commit_timer = None

        transcript = self._semantic_asr_transcribe_candidate(
            speech=speech,
            channels=channels,
            sample_rate=sample_rate,
            is_final=False,
        )
        if transcript:
            self._semantic_asr_update_text(
                transcript, is_final=(label == "complete"), commit=False
            )
            with self._asr_accumulated_lock:
                audio_seconds = float(
                    getattr(self, "_asr_audio_total_seconds", 0.0) or 0.0
                )
            interrupt_min_audio_sec = float(
                getattr(cfg.pipeline.asr.turnsense, "interrupt_min_audio_sec", 0.48)
            )
            interrupt_min_confidence = float(
                getattr(cfg.pipeline.asr.turnsense, "interrupt_min_confidence", 0.75)
            )
            interrupt_confirmed = (
                raw_label != "invalid"
                and audio_seconds >= interrupt_min_audio_sec
                and result.confidence >= interrupt_min_confidence
            )
            if not self._semantic_asr_interrupted and interrupt_confirmed:
                self._on_interrupt_tts()
                self._semantic_asr_interrupted = True
            elif not self._semantic_asr_interrupted:
                logger.debug(
                    f"延后 TTS 打断：音频={audio_seconds:.2f}/{interrupt_min_audio_sec:.2f} 秒，"
                    f"置信度={result.confidence:.3f}/{interrupt_min_confidence:.3f}"
                )

        if label == "incomplete":
            if not self._asr_incomplete_since:
                self._asr_incomplete_since = time.monotonic()
            self._schedule_semantic_asr_timeout()
            return

        if label == "complete":
            self._asr_incomplete_since = 0.0
            self._schedule_semantic_asr_complete_commit()

    def _run_streaming_asr_query(self, query: ASRStreamQuery) -> str:
        asr = self._asr_provider()
        transcript = ""
        for prediction in asr.stream_predict(query):
            candidate = prediction.transcript or ""
            if candidate and not ASR_result_filter(candidate):
                transcript = candidate
        return transcript

    def _semantic_asr_transcribe_candidate(
        self, speech: bytes, channels: int, sample_rate: int, is_final: bool
    ) -> str:
        try:
            query = ASRStreamQuery(
                is_final=is_final,
                audio_data=speech,
                channels=channels,
                sample_rate=sample_rate,
                media_type="wav",
            )
            transcript = self._run_streaming_asr_query(query)
            if transcript:
                state = "最终" if is_final else "有效"
                logger.info(f"语义 ASR 转写（{state}）：{transcript}")
            return transcript
        except Exception as exc:
            logger.error(f"语义 ASR 候选音频转写失败：{exc}", exc_info=True)
            return ""

    def _semantic_asr_commit_buffered_text(
        self, reason: str, reset_remote: bool | None = None
    ) -> None:
        self._semantic_asr_pending_complete = False
        with self._asr_accumulated_lock:
            full_text = self._asr_accumulated_text.strip()
        if not full_text:
            logger.debug(
                f"语义 ASR 缓冲文本为空（{reason}），跳过提交并丢弃当前轮次。"
            )
            self.semantic_asr_reset(
                reset_remote=self.should_reset_remote(reset_remote),
                reset_microphone=False,
            )
            self._end_mic_utterance()
            return
        logger.info(f"语义 ASR 正在提交（{reason}）：{full_text}")
        self.semantic_asr_reset(
            reset_remote=self.should_reset_remote(reset_remote),
            reset_microphone=False,
        )
        self._emit_final_asr_text(full_text)

    def desktop_asr_allowed(self) -> bool:
        """桌面 ASR 是否可以让路给麦克风（P1-16）。"""
        with self._mic_processing_lock:
            if self._mic_processing:
                logger.debug("麦克风 ASR 正在运行，已跳过桌面 ASR。")
                return False
            if not self._mic_utterance_active:
                return True
            if (
                time.monotonic() - self._mic_utterance_last_chunk
                > self._mic_utterance_stale_seconds
            ):
                logger.warning(
                    "麦克风语音轮次已超过 "
                    f"{self._mic_utterance_stale_seconds:.0f} 秒未更新，视为已结束并恢复桌面 ASR。"
                )
                self._mic_utterance_active = False
                return True
            logger.debug(
                "麦克风本轮语音尚未结束，已跳过桌面 ASR（避免两个 ASR 请求并发）。"
            )
            return False

    def append_desktop_asr(self, transcript: str) -> None:
        """线程安全地追加桌面 ASR 句子，并按上限淘汰最旧句子。"""
        if not transcript:
            return
        lock = getattr(self, "_desktop_asr_lock", None)
        max_sentences = max(int(getattr(self, "max_desktop_sentences", 1) or 1), 1)
        with lock or nullcontext():
            self.desktop_asr_buffer.append(transcript)
            while len(self.desktop_asr_buffer) > max_sentences:
                self.desktop_asr_buffer.pop(0)

    def take_desktop_asr_text(self) -> str:
        """原子取出并清空当前累积的桌面 ASR 文本，避免读-清空之间丢句子。"""
        lock = getattr(self, "_desktop_asr_lock", None)
        max_sentences = max(int(getattr(self, "max_desktop_sentences", 1) or 1), 1)
        with lock or nullcontext():
            if not self.desktop_asr_buffer:
                return ""
            recent = self.desktop_asr_buffer[-max_sentences:]
            self.desktop_asr_buffer.clear()
        return "。".join(recent)

    def should_reset_remote(self, reset_remote: bool | None) -> bool:
        if reset_remote is not None:
            return bool(reset_remote)
        return bool(self._reset_remote_after_turn)

    def _semantic_asr_finalize_stream_and_commit(self, reason: str) -> None:
        transcript = self._semantic_asr_finalize_stream()
        if transcript:
            self._semantic_asr_update_text(transcript, is_final=True, commit=False)
        self._semantic_asr_commit_buffered_text(reason=reason)

    def _semantic_asr_complete_timeout_commit(self, reason: str) -> None:
        self._semantic_asr_transcribe_and_commit(reason=reason)

    def _semantic_asr_append_audio(
        self, speech: bytes, channels: int, sample_rate: int
    ) -> None:
        if not speech:
            return
        if self.turnsense_detector is None or not getattr(
            self.turnsense_detector, "supports_audio", False
        ):
            return
        try:
            audio = self._decode_turnsense_audio(speech, channels)
        except Exception as exc:
            logger.warning(f"TurnSense 音频解码失败，将降级使用文本：{exc}")
            return
        with self._asr_accumulated_lock:
            if self._asr_audio_sample_rate and self._asr_audio_sample_rate != sample_rate:
                logger.warning(
                    f"TurnSense 音频采样率从 {self._asr_audio_sample_rate} 变为 "
                    f"{sample_rate}，正在清空音频缓冲区。"
                )
                self._asr_audio_chunks = []
                self._asr_audio_total_seconds = 0.0
            self._asr_audio_sample_rate = sample_rate
            self._asr_audio_chunks.append(audio)
            self._asr_audio_total_seconds += len(audio) / float(sample_rate or 1)
            self._trim_turnsense_audio_window()

    def _trim_turnsense_audio_window(self) -> None:
        """把 TurnSense 输入裁到最近 `window_audio_sec` 秒（调用方需持有 `_asr_accumulated_lock`）。"""
        cfg = self._config_provider()
        window = float(
            getattr(cfg.pipeline.asr.turnsense, "window_audio_sec", 4.0) or 0.0
        )
        sample_rate = self._asr_audio_sample_rate or 0
        if window <= 0 or sample_rate <= 0 or not self._asr_audio_chunks:
            return
        max_samples = int(window * sample_rate)
        kept = []
        total = 0
        for chunk in reversed(self._asr_audio_chunks):
            size = len(chunk)
            if kept and total + size > max_samples:
                break
            kept.append(chunk)
            total += size
        kept.reverse()
        trimmed = len(self._asr_audio_chunks) - len(kept)
        if trimmed > 0:
            self._asr_audio_chunks = kept
            logger.debug(
                f"TurnSense 音频窗口裁剪：丢弃 {trimmed} 个旧块，"
                f"保留 {len(kept)} 个（{total / sample_rate:.2f} 秒，窗口 {window:.2f} 秒）。"
            )

    @staticmethod
    def _decode_turnsense_audio(
        speech: bytes, channels: int, *, fallback_raw: bool = True
    ) -> np.ndarray:
        try:
            with io.BytesIO(speech) as wav_io:
                with wave.open(wav_io, "rb") as wav_file:
                    n_frames = wav_file.getnframes()
                    sample_width = wav_file.getsampwidth()
                    wav_channels = wav_file.getnchannels()
                    raw = wav_file.readframes(n_frames)
            if sample_width != 2:
                raise ValueError(f"unsupported wav sample width: {sample_width}")
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if wav_channels > 1:
                audio = audio.reshape(-1, wav_channels).mean(axis=1)
            return audio.astype(np.float32)
        except wave.Error:
            if not fallback_raw:
                raise
            audio = np.frombuffer(speech, dtype=np.float32).astype(np.float32)
            if channels > 1:
                audio = audio.reshape(-1, channels).mean(axis=1)
            return audio

    def _semantic_asr_get_audio(self):
        with self._asr_accumulated_lock:
            if not self._asr_audio_chunks or not self._asr_audio_sample_rate:
                return None, 0
            return (
                np.concatenate(self._asr_audio_chunks).astype(np.float32),
                self._asr_audio_sample_rate,
            )

    def _semantic_asr_update_text(
        self, transcript: str, is_final: bool = False, commit: bool = True
    ) -> None:
        with self._asr_accumulated_lock:
            transcript = transcript.strip()
            full_text = transcript or self._asr_accumulated_text.strip()
            self._asr_accumulated_text = full_text
        logger.debug(f"语义 ASR 累计文本：{full_text}")
        if commit:
            self._semantic_asr_commit_if_needed(
                force=False, reason="final" if is_final else "turnsense"
            )

    def _semantic_asr_commit_if_needed(self, force: bool, reason: str) -> None:
        if not force:
            return
        self._semantic_asr_transcribe_and_commit(reason=reason)

    def _semantic_asr_predict_audio(self):
        if self.turnsense_detector is None or not getattr(
            self.turnsense_detector, "supports_audio", False
        ):
            return None
        audio, audio_sample_rate = self._semantic_asr_get_audio()
        if audio is None:
            return None
        return self.turnsense_detector.predict_audio(audio, audio_sample_rate)

    def _semantic_asr_transcribe_and_commit(self, reason: str) -> None:
        tail = self._semantic_asr_take_microphone_tail()
        if tail is not None:
            tail_speech, tail_channels, tail_sample_rate = tail
            self._semantic_asr_append_audio(
                tail_speech, tail_channels, tail_sample_rate
            )

        cfg = self._config_provider()
        if getattr(cfg.pipeline.asr.turnsense, "streaming_asr", True):
            if tail is not None:
                transcript = self._semantic_asr_transcribe_candidate(
                    speech=tail_speech,
                    channels=tail_channels,
                    sample_rate=tail_sample_rate,
                    is_final=True,
                )
                if transcript:
                    self._semantic_asr_update_text(
                        transcript, is_final=True, commit=False
                    )
                self._semantic_asr_commit_buffered_text(reason=reason)
                return
            self._semantic_asr_finalize_stream_and_commit(reason=reason)
            return

        audio, audio_sample_rate = self._semantic_asr_get_audio()
        if audio is None:
            logger.debug("没有累计音频，已跳过语义 ASR 转写。")
            return

        transcript = self._semantic_asr_transcribe_audio(audio, audio_sample_rate)
        if not transcript:
            logger.debug(f"语义 ASR 转写返回空文本（{reason}），丢弃当前轮次。")
            self.semantic_asr_reset(reset_remote=True, reset_microphone=False)
            self._end_mic_utterance()
            return

        self._semantic_asr_update_text(transcript, is_final=True, commit=False)
        self._semantic_asr_commit_buffered_text(reason=reason)

    def _semantic_asr_finalize_stream(self) -> str:
        cfg = self._config_provider()
        _, audio_sample_rate = self._semantic_asr_get_audio()
        sample_rate = audio_sample_rate or cfg.pipeline.asr.sample_rate
        try:
            query = ASRStreamQuery(
                is_final=True,
                audio_data=b"",
                channels=1,
                sample_rate=sample_rate,
                media_type="wav",
            )
            transcript = self._run_streaming_asr_query(query)
            if transcript:
                logger.info(f"语义 ASR 转写（流结束）：{transcript}")
            return transcript
        except Exception as exc:
            logger.error(f"语义 ASR 流结束处理失败：{exc}", exc_info=True)
            return ""

    def _semantic_asr_transcribe_audio(
        self, audio: np.ndarray, sample_rate: int
    ) -> str:
        try:
            wave_data = self._encode_turnsense_audio(audio, sample_rate)
            query = ASRStreamQuery(
                is_final=True,
                audio_data=wave_data,
                channels=1,
                sample_rate=sample_rate,
                media_type="wav",
            )
            transcript = self._run_streaming_asr_query(query)
            if transcript:
                logger.info(f"语义 ASR 转写（TurnSense 结束）：{transcript}")
            return transcript
        except Exception as exc:
            logger.error(f"语义 ASR 最终转写失败：{exc}", exc_info=True)
            return ""

    @staticmethod
    def _encode_turnsense_audio(audio: np.ndarray, sample_rate: int) -> bytes:
        values = np.asarray(audio, dtype=np.float32).reshape(-1)
        if values.size == 0:
            return b""
        int16_audio = (np.clip(values, -1.0, 1.0) * 32767.0).astype(np.int16)
        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(int16_audio.tobytes())
            return wav_io.getvalue()

    def _schedule_semantic_asr_complete_commit(self) -> None:
        cfg = self._config_provider()
        timeout = float(getattr(cfg.pipeline.asr.turnsense, "final_timeout", 0.8))
        with self._asr_accumulated_lock:
            self._asr_timer_generation += 1
            generation = self._asr_timer_generation
            if self._asr_force_commit_timer:
                self._asr_force_commit_timer.cancel()
            self._semantic_asr_pending_complete = True
            self._asr_force_commit_timer = threading.Timer(
                timeout,
                lambda: self._submit_semantic_asr_task(
                    self._run_semantic_asr_timer,
                    generation,
                    "complete",
                    lambda: self._semantic_asr_complete_timeout_commit(
                        reason="turnsense-complete-timeout"
                    ),
                ),
            )
            self._asr_force_commit_timer.daemon = True
            self._asr_force_commit_timer.start()
        logger.info(f"语义 ASR 检测到完成信号，延迟 {timeout:.2f} 秒进行最终提交。")

    def _semantic_asr_take_microphone_tail(self):
        mic = self._mic_provider()
        if mic is None or not hasattr(mic, "take_pending_candidate"):
            return None
        try:
            speech = mic.take_pending_candidate()
            if not speech:
                return None
            logger.info(f"语义 ASR 已同步获取麦克风尾部音频：字节数={len(speech)}")
            return speech, mic._channels, mic._sample_rate
        except Exception as exc:
            logger.warning(f"获取语义 ASR 麦克风尾部音频失败：{exc}")
            return None

    def _schedule_semantic_asr_timeout(self) -> None:
        cfg = self._config_provider()
        turnsense_cfg = cfg.pipeline.asr.turnsense
        with self._asr_accumulated_lock:
            self._asr_timer_generation += 1
            generation = self._asr_timer_generation
            if self._asr_force_commit_timer:
                self._asr_force_commit_timer.cancel()
            incomplete_since = (
                getattr(self, "_asr_incomplete_since", 0.0) or time.monotonic()
            )
            remaining = max(
                0.1, turnsense_cfg.max_wait - (time.monotonic() - incomplete_since)
            )
            logger.debug(f"语义 ASR 最大等待定时器将在 {remaining:.2f} 秒后触发。")
            self._asr_force_commit_timer = threading.Timer(
                remaining,
                lambda: self._submit_semantic_asr_task(
                    self._run_semantic_asr_timer,
                    generation,
                    "max-wait",
                    lambda: self._semantic_asr_commit_if_needed(
                        force=True, reason="max-wait-timer"
                    ),
                ),
            )
            self._asr_force_commit_timer.daemon = True
            self._asr_force_commit_timer.start()

    def _run_semantic_asr_timer(
        self, generation: int, timer_name: str, callback
    ) -> None:
        with self._asr_accumulated_lock:
            if generation != self._asr_timer_generation:
                logger.debug(
                    f"忽略过期的语义 ASR {timer_name} 定时器：generation={generation}，"
                    f"当前 generation={self._asr_timer_generation}"
                )
                return
            self._asr_force_commit_timer = None
        logger.debug(f"语义 ASR 的 {timer_name} 定时器已触发：generation={generation}")
        callback()

    def semantic_asr_reset(
        self, reset_remote: bool = False, reset_microphone: bool = False
    ) -> None:
        with self._asr_accumulated_lock:
            self._asr_accumulated_text = ""
            self._pending_asr_count = 0
            self._final_pending = False
            self._asr_incomplete_since = 0.0
            self._asr_audio_chunks = []
            self._asr_audio_sample_rate = 0
            self._asr_audio_total_seconds = 0.0
            self._semantic_asr_interrupted = False
            self._semantic_asr_pending_complete = False
            self._asr_timer_generation += 1
            if self._asr_force_commit_timer:
                self._asr_force_commit_timer.cancel()
                self._asr_force_commit_timer = None
        mic = self._mic_provider()
        if reset_microphone and mic is not None:
            mic.force_commit(is_emit=False)
        if reset_remote:
            self._reset_remote_streaming_asr()

    def _reset_remote_streaming_asr(self) -> None:
        cfg = self._config_provider()
        try:
            with self._asr_accumulated_lock:
                sample_rate = (
                    self._asr_audio_sample_rate or cfg.pipeline.asr.sample_rate
                )
            asr = self._asr_provider()
            query = ASRStreamQuery(
                is_final=True,
                audio_data=b"",
                channels=cfg.pipeline.asr.channels,
                sample_rate=sample_rate,
                media_type="wav",
            )
            for _ in asr.stream_predict(query):
                pass
        except Exception as exc:
            logger.warning(f"重置远程流式 ASR 失败：{exc}")

    def reset_accumulator(self) -> None:
        """重置 ASR 累积文本（在对话结束或强制提交时调用）。"""
        if self.is_semantic_asr_enabled():
            self.semantic_asr_reset(reset_remote=True)
            return
        with self._asr_accumulated_lock:
            self._asr_accumulated_text = ""
            self._pending_asr_count = 0
            self._final_pending = False

    def _mark_asr_final(self) -> str:
        with self._asr_accumulated_lock:
            if self._pending_asr_count > 0:
                self._final_pending = True
                logger.debug("收到 ASR 最终标记，但仍有待处理请求，继续等待。")
                return ""
            self._final_pending = False
            full_text = self._asr_accumulated_text.strip()
            self._asr_accumulated_text = ""
            return full_text

    def _finish_asr_request(self, transcript: str, is_final: bool) -> str:
        with self._asr_accumulated_lock:
            self._pending_asr_count = max(0, self._pending_asr_count - 1)
            logger.debug(f"ASR 待处理计数减少，当前为 {self._pending_asr_count}")

            if transcript:
                previous = self._asr_accumulated_text.strip()
                transcript = transcript.strip()
                if previous and transcript.startswith(previous):
                    self._asr_accumulated_text = transcript
                elif previous and previous.endswith(transcript):
                    self._asr_accumulated_text = previous
                elif previous and transcript in previous:
                    self._asr_accumulated_text = previous
                else:
                    self._asr_accumulated_text = (
                        transcript if not previous else f"{previous}{transcript}"
                    )
                logger.debug(f"ASR 累计文本：{self._asr_accumulated_text}")

            if is_final:
                self._final_pending = True

            if self._final_pending and self._pending_asr_count == 0:
                self._final_pending = False
                full_text = self._asr_accumulated_text.strip()
                self._asr_accumulated_text = ""
                return full_text

            return ""

    def _asr_utterance_pending(self) -> bool:
        with self._asr_accumulated_lock:
            return bool(self._asr_accumulated_text.strip()) or self._pending_asr_count > 0

    def _end_mic_utterance(self) -> None:
        with self._mic_processing_lock:
            self._mic_processing = False
            self._mic_utterance_active = False

    def _emit_final_asr_text(self, full_text: str) -> None:
        if full_text:
            logger.info(f"最终 ASR 完整文本：{full_text}")
            dummy_pred = ASRPrediction(transcript=full_text)
            self._emit_event(PipelineASREvent(prediction=dummy_pred))
        else:
            logger.debug("最终 ASR 文本为空，跳过处理。")
        self._end_mic_utterance()

    def shutdown(self) -> None:
        if self.desktop_audio_capture:
            self.desktop_audio_capture.stop()
        self.semantic_asr_reset(reset_remote=False, reset_microphone=False)
        self._semantic_asr_executor.shutdown(wait=False, cancel_futures=True)
