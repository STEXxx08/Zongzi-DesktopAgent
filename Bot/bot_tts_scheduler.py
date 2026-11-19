from __future__ import annotations

from concurrent.futures.thread import ThreadPoolExecutor
import re
import threading
import time
from typing import Any, Callable

from loguru import logger

from agent.api import sentiment_analyse
from common.enumerator import Language
from common.io.api import save_audio
from common.io.file_type import AudioFileType
from common.utils.math_util import clamp
from common.utils.str_util import split_by_punc
from condata.data.prompt import TTSPrompt
from condata.pipeline.tts import TTSQuery
from event.event_data import CharacterStateEvent, PipelineOutputTTSEvent
from event.event_emitter import emitter

_TTS_CLEAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\*[^*]*\*"),
    re.compile(r"（[^）]*）"),
    re.compile(r"\([^)]*\)"),
    re.compile(r"\[[^\]]*\]"),
    re.compile(r"【[^】]*】"),
    re.compile(r"<[^>]*>"),
)
_TTS_WHITESPACE_RE = re.compile(r"\s+")
_STREAM_SEGMENT_RE = re.compile(r"[。！？!?；;\n]+")


def clean_text_for_tts(text: str) -> str:
    """过滤不适合送入 TTS 的动作描写、括号注释与标签。"""
    for pattern in _TTS_CLEAN_PATTERNS:
        text = pattern.sub("", text)
    return _TTS_WHITESPACE_RE.sub(" ", text).strip()


class BotTTSScheduler:
    """直连 Bot 主控的 TTS 调度器：管理预热、分句入队、自适应语速、打断与播放回收。"""

    def __init__(
        self,
        *,
        config_provider: Callable[[], Any],
        tts_provider: Callable[[], Any],
        tts_prompt_manager_provider: Callable[[], Any],
        speaker_provider: Callable[[], Any],
        mic_provider: Callable[[], Any],
        lang_provider: Callable[[], Language],
        emit_event: Callable[[Any], None] = emitter.emit,
    ) -> None:
        self._config_provider = config_provider
        self._tts_provider = tts_provider
        self._tts_prompt_manager_provider = tts_prompt_manager_provider
        self._speaker_provider = speaker_provider
        self._mic_provider = mic_provider
        self._lang_provider = lang_provider
        self._emit_event = emit_event

        self.tts_thread_pool = ThreadPoolExecutor(max_workers=1)
        self._tts_task_counter = 0
        self._tts_pending_futures: list[Any] = []
        self._tts_lock = threading.Lock()
        self._current_tts_future = None
        self._interrupt_required = False
        self.speed_step = 0
        self._last_tts_enqueue_time = time.monotonic()

        # TTS 预热的中止闸门：只要真的有一句话要合成，预热立刻让路并永久放弃。
        self._tts_warmup_abort = threading.Event()
        self._tts_warmup_active = False

    def current_task_id(self) -> int:
        with self._tts_lock:
            return self._tts_task_counter

    def start_warmup_thread(self) -> threading.Thread:
        thread = threading.Thread(
            target=self.warmup, daemon=True, name="TTSWarmupThread"
        )
        thread.start()
        return thread

    def warmup(self, attempts: int = 3, retry_delay: float = 10.0) -> None:
        """启动时提前触发 TTS 冷启动推理，避免正式第一句话等待过久。"""
        cfg = self._config_provider()
        if not cfg.pipeline.tts.warmup_on_startup:
            logger.debug("TTS 预热已按配置关闭。")
            return
        tts = self._tts_provider()
        prompt_manager = self._tts_prompt_manager_provider()
        if tts is None or prompt_manager is None:
            return
        prompts = prompt_manager.tts_prompts
        prompt = prompt_manager.default_tts_prompt or (prompts[0] if prompts else None)
        if prompt is None:
            logger.warning("没有可用的 TTS 参考音频，跳过预热。")
            return

        abort = self._tts_warmup_abort
        query = TTSQuery(
            text="你好。",
            text_language="auto",
            refer_wav_path=prompt.audio_path,
            prompt_text=prompt.prompt_text,
            prompt_language=prompt.lang,
            audio_type="wav",
            speed_factor=1.0,
        )
        for attempt in range(1, attempts + 1):
            if abort.is_set():
                logger.info("有真实语音要合成，TTS 预热提前放弃。")
                return
            started = time.monotonic()
            self._tts_warmup_active = True
            try:
                tts.predict(query=query)
            except Exception as exc:
                self._tts_warmup_active = False
                logger.warning(f"TTS 预热第 {attempt}/{attempts} 次失败：{exc}")
                if attempt >= attempts:
                    break
                if abort.wait(retry_delay):
                    logger.info("有真实语音要合成，TTS 预热提前放弃。")
                    return
                continue
            finally:
                self._tts_warmup_active = False
            logger.info(
                f"🔥 TTS 预热完成，耗时 {time.monotonic() - started:.2f} 秒；"
                f"首次正式合成不再承担冷启动开销。"
            )
            return
        logger.warning("TTS 预热始终失败，首次正式合成仍会承担冷启动开销。")

    def abort_core_tts_async(self) -> None:
        """异步向 Core TTS 发送取消请求，避免阻塞当前控制线程。"""
        tts = self._tts_provider()
        if tts is None or not hasattr(tts, "abort") or not getattr(tts, "abort_url", ""):
            return
        threading.Thread(
            target=tts.abort,
            daemon=True,
            name="TTSAbortThread",
        ).start()

    def abort_warmup(self) -> None:
        """置位预热中止标志；若预热请求仍在服务端执行，通知 Core 尽快中止。"""
        already_aborted = self._tts_warmup_abort.is_set()
        self._tts_warmup_abort.set()
        if not already_aborted and self._tts_warmup_active:
            self.abort_core_tts_async()

    def select_prompt(self, text: str) -> TTSPrompt:
        cfg = self._config_provider()
        prompt_manager = self._tts_prompt_manager_provider()
        if cfg.system.enable_sentiment_analysis:
            sentiment = sentiment_analyse(
                sentiments=prompt_manager.sentiments, text=text
            )
            return prompt_manager.get_tts_prompt(sentiment)
        return prompt_manager.default_tts_prompt

    def get_prompt(self, emotion: str) -> TTSPrompt | None:
        prompt_manager = self._tts_prompt_manager_provider()
        if prompt_manager is None:
            return None
        return prompt_manager.get_tts_prompt(emotion)

    def prepare_text(self, text: str) -> str:
        cfg = self._config_provider()
        if cfg.pipeline.tts.filter:
            return clean_text_for_tts(text)
        return text

    @staticmethod
    def _calc_max_speed_step(adaptive_speed: Any) -> int:
        max_speed = max(float(adaptive_speed.max_speed), 1.0)
        return max(int(round((max_speed - 1.0) / 0.05)), 0)

    @staticmethod
    def pop_stream_segments(buffer: str, flush: bool = False) -> tuple[list[str], str]:
        if not buffer:
            return [], ""
        segments: list[str] = []
        start = 0
        for match in _STREAM_SEGMENT_RE.finditer(buffer):
            end = match.end()
            segment = buffer[start:end].strip()
            if segment:
                segments.append(segment)
            start = end
        rest = buffer[start:]
        if flush and rest.strip():
            segments.append(rest.strip())
            rest = ""
        return segments, rest

    def submit_segment(
        self,
        tts_prompt: TTSPrompt,
        text: str,
        speed_factor: float = 1.0,
        emotion: str | None = None,
    ) -> None:
        def wrapper():
            with self._tts_lock:
                task_id = self._tts_task_counter
            try:
                tts = self._tts_provider()
                query = TTSQuery(
                    text=text,
                    text_language="auto",
                    refer_wav_path=tts_prompt.audio_path,
                    prompt_text=tts_prompt.prompt_text,
                    prompt_language=tts_prompt.lang,
                    audio_type="wav",
                    speed_factor=speed_factor,
                )
                prediction = tts.predict(query=query)

                cfg = self._config_provider()
                if cfg.pipeline.tts.cancel_on_new_input:
                    with self._tts_lock:
                        if task_id != self._tts_task_counter:
                            logger.info(f"TTS 任务 {task_id} 已过期，跳过播放。")
                            return
                self.play(
                    PipelineOutputTTSEvent(
                        prediction=prediction,
                        transcript=text,
                        emotion=emotion or tts_prompt.sentiment,
                    )
                )
            except Exception as exc:
                logger.error(f"TTS 任务 {task_id} 失败：{exc}")

        future = self.tts_thread_pool.submit(wrapper)

        def done_callback(f):
            with self._tts_lock:
                if f in self._tts_pending_futures:
                    self._tts_pending_futures.remove(f)

        with self._tts_lock:
            self._tts_pending_futures.append(future)
            self._current_tts_future = future
        future.add_done_callback(done_callback)

    def watch_round_end(self, task_id: int) -> None:
        def wrapper():
            while True:
                with self._tts_lock:
                    if task_id != self._tts_task_counter:
                        logger.debug("TTS 轮次已过期，跳过角色状态恢复。")
                        return
                    pending_tts = bool(self._tts_pending_futures)
                speaker = self._speaker_provider()
                audio_queue = getattr(speaker, "audio_clips", None)
                speaker_idle = not bool(getattr(speaker, "is_playing", False)) and (
                    audio_queue is None or audio_queue.empty()
                )
                if not pending_tts and speaker_idle:
                    break
                time.sleep(0.05)

            cfg = self._config_provider()
            with self._tts_lock:
                if task_id != self._tts_task_counter:
                    logger.debug("TTS 轮次已过期，跳过角色状态恢复。")
                    return
                if cfg.pipeline.tts.adaptive_speed.enable and self.speed_step > 0:
                    self.speed_step = max(self.speed_step - 1, 0)
                    logger.debug(
                        f"TTS 轮次完成，speed_step 已递减为 {self.speed_step}。"
                    )
            mic = self._mic_provider()
            state = "listening" if (mic is not None and mic.is_recording) else "idle"
            self._emit_event(CharacterStateEvent(state=state))

        threading.Thread(target=wrapper, name="TTSRoundEnd", daemon=True).start()

    def enqueue(
        self,
        text: str,
        tts_prompt: TTSPrompt | None = None,
        emotion: str | None = None,
    ) -> bool:
        self.abort_warmup()
        tts_text = self.prepare_text(text)
        if not tts_text:
            return False
        prompt = tts_prompt or self.select_prompt(tts_text)
        logger.debug(f"TTS 文本：{tts_text}")

        cfg = self._config_provider()
        adaptive_speed = cfg.pipeline.tts.adaptive_speed
        speed_factor = 1.0
        if adaptive_speed.enable:
            long_text_threshold = max(int(adaptive_speed.long_text_threshold), 1)
            max_speed_step = self._calc_max_speed_step(adaptive_speed)
            idle_decay_seconds = max(
                float(getattr(adaptive_speed, "idle_decay_seconds", 0.0)), 0.0
            )
            long_text_speed_steps = 0
            if len(tts_text) > long_text_threshold:
                long_text_speed_steps = int(
                    (len(tts_text) - long_text_threshold) / (long_text_threshold * 0.05)
                )
            with self._tts_lock:
                now = time.monotonic()
                if idle_decay_seconds > 0 and self.speed_step > 0:
                    idle_steps = int(
                        (now - self._last_tts_enqueue_time) // idle_decay_seconds
                    )
                    if idle_steps > 0:
                        self.speed_step = max(self.speed_step - idle_steps, 0)
                        logger.debug(
                            f"TTS 空闲衰减：空闲步数={idle_steps}，"
                            f"speed_step={self.speed_step}。"
                        )
                self._last_tts_enqueue_time = now
                interrupt_speed_step = self.speed_step
                effective_speed_step = clamp(
                    0, max_speed_step, interrupt_speed_step + long_text_speed_steps
                )
            speed_factor = 1.0 + effective_speed_step * 0.05
            logger.debug(
                f"TTS 自适应语速：打断增量={interrupt_speed_step}，"
                f"长文本增量={long_text_speed_steps}，有效增量={effective_speed_step}，"
                f"语速倍率={speed_factor:.2f}"
            )
        else:
            with self._tts_lock:
                self._last_tts_enqueue_time = time.monotonic()

        segments = (
            split_by_punc(tts_text, self._lang_provider()) or [tts_text]
            if cfg.system.enable_clause_split
            else [tts_text]
        )
        enqueued = False
        for transcript in segments:
            self.submit_segment(prompt, transcript, speed_factor, emotion)
            enqueued = True
        return enqueued

    def play(self, event: PipelineOutputTTSEvent) -> None:
        logger.debug("开始播放 TTS。")
        prediction = event.prediction
        audio_path = save_audio(
            wave_data=prediction.wave_data,
            format=AudioFileType(prediction.audio_type),
            prefix="tts",
        )
        cfg = self._config_provider()
        logger.debug("TTS 输出设备：{}", cfg.pipeline.tts.output_device)
        speaker = self._speaker_provider()
        if speaker is not None:
            speaker.enqueue_sound(audio_path, event.emotion)

    def cancel_pending(self) -> None:
        """取消所有待处理的 TTS 任务，并清空 Speaker 队列。"""
        cfg = self._config_provider()
        if not cfg.pipeline.tts.cancel_on_new_input:
            return
        futures_to_cancel = []
        with self._tts_lock:
            self._tts_task_counter += 1
            futures_to_cancel = list(self._tts_pending_futures)
            self._tts_pending_futures.clear()
        for future in futures_to_cancel:
            future.cancel()
        speaker = self._speaker_provider()
        if speaker is not None and hasattr(speaker, "clear_queue"):
            speaker.clear_queue()
            logger.info("收到新输入，已清空 TTS 扬声器队列。")
        self.abort_core_tts_async()

    def interrupt(self) -> None:
        """打断当前 TTS 播放。"""
        try:
            future_to_cancel = None
            cfg = self._config_provider()
            speaker = self._speaker_provider()
            with self._tts_lock:
                self._tts_task_counter += 1
                future_running = (
                    self._current_tts_future is not None
                    and not self._current_tts_future.done()
                )
                speaker_playing = bool(getattr(speaker, "is_playing", False))
                speaker_queued = bool(
                    getattr(speaker, "audio_clips", None) is not None
                    and not speaker.audio_clips.empty()
                )
                should_accelerate = future_running or speaker_playing or speaker_queued
                adaptive_speed = cfg.pipeline.tts.adaptive_speed
                if adaptive_speed.enable and should_accelerate:
                    max_speed_step = self._calc_max_speed_step(adaptive_speed)
                    old_speed_step = self.speed_step
                    self.speed_step = min(
                        self.speed_step + int(adaptive_speed.interrupt_speed_step),
                        max_speed_step,
                    )
                    logger.debug(
                        f"TTS 已被打断，speed_step：{old_speed_step} -> {self.speed_step}。"
                    )
                elif adaptive_speed.enable:
                    logger.debug("收到 TTS 打断请求，但当前没有活动任务；speed_step 保持不变。")
                if future_running:
                    future_to_cancel = self._current_tts_future
                    self._current_tts_future = None
                self._interrupt_required = True

            if future_to_cancel:
                future_to_cancel.cancel()
                logger.info("TTS 异步任务已取消。")
            self.abort_core_tts_async()
            if speaker is not None:
                try:
                    speaker.force_clear_queue()
                except Exception as exc:
                    logger.error(f"强制清空播放队列失败：{exc}", exc_info=True)
        except Exception as exc:
            logger.exception(f"打断 TTS 失败：{exc}")

    def shutdown(self) -> None:
        # 不等 TTS 队列排空：合成请求已有超时，等待只会拖住退出流程。
        self.tts_thread_pool.shutdown(wait=False, cancel_futures=True)
