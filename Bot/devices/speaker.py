import os
import threading
from enum import Enum
from pathlib import Path
from queue import Empty, Full, Queue
import time

from loguru import logger

from common.concurrent.abs_runnable import ThreadRunnable
from common.concurrent.killable_thread import KillableThread
from event.event_data import DeviceSpeakerPlayEvent
from event.event_emitter import emitter

import soundfile as sf
import sounddevice as sd


class SystemSoundEnum(str, Enum):
    warn: str = "warn.wav"
    error: str = "error.wav"
    start: str = "start.wav"
    exit: str = "exit.wav"
    enable_func: str = "microphone-recoding.wav"
    disable_func: str = "microphone-stopped.wav"
    filtered: str = "filtered.wav"


class Speaker(ThreadRunnable):

    def name(self):
        return 'Speaker'

    def __init__(self):
        super().__init__()
        self._stop_flag = False
        self._semaphore = threading.Event()
        self._speaker_thread = KillableThread(target=self._run)
        # 有界播放队列：上游 TTS 生产速度超过播放速度时不能再无限堆积（P1-19）
        self.max_queued_clips = 32
        self.audio_clips: Queue = Queue(maxsize=self.max_queued_clips)
        self._dropped_clips = 0
        self._queue_lock = threading.Lock() # 新增：用于保护队列的锁
        self._interrupt_event = threading.Event()
        self._pygame_lock = threading.Lock()
        self._pygame = None
        self.is_playing = False

    def start(self):
        super().start()
        self._stop_flag = False
        self._semaphore.set()
        self._speaker_thread.start()

    def stop(self):
        super().stop()
        self._stop_flag = True
        self.audio_clips = None
        self._speaker_thread.kill()

    def _run(self):
        while not self._stop_flag:
            try:
                with self._queue_lock:
                    if self.audio_clips.empty():
                        self._semaphore.clear()
                        queued_clip = None
                    else:
                        queued_clip = self.audio_clips.get()
                        self.is_playing = True
                if queued_clip is None:
                    self._semaphore.wait()
                    continue
                if isinstance(queued_clip, tuple) and len(queued_clip) == 2:
                    audio_clip, emotion = queued_clip
                else:
                    audio_clip, emotion = queued_clip, "Default"
                self._interrupt_event.clear()
                try:
                    emitter.emit(DeviceSpeakerPlayEvent(audio_path=audio_clip, emotion=emotion))
                    from manager.config_manager import get_config
                    tts_config = get_config().pipeline.tts
                    # 配置留空 = 使用系统默认输出设备
                    output_device = getattr(tts_config, "output_device", None)
                    playback_volume = max(float(getattr(tts_config, "playback_volume", 1.0)), 0.0)
                    self._play_sound(audio_clip, output_device, playback_volume)
                finally:
                    self.is_playing = False
            except Exception as e:
                logger.error(f"Speaker _run loop caught exception: {e}", exc_info=True)
                # 不退出，继续循环

    def enqueue_sound(self, path_or_data: Path, emotion: str = "Default"):
        with self._queue_lock:
            while True:
                try:
                    self.audio_clips.put_nowait((path_or_data, emotion))
                    break
                except Full:
                    # 队列已满：丢弃最旧的一条，保留最新的语音（新的话更重要）
                    try:
                        self.audio_clips.get_nowait()
                    except Empty:
                        break
                    self._dropped_clips += 1
                    if self._dropped_clips == 1 or self._dropped_clips % 20 == 0:
                        logger.warning(
                            f"播放队列已满（上限 {self.max_queued_clips}），已丢弃较旧的音频；"
                            f"累计丢弃 {self._dropped_clips} 条。"
                        )
        self._semaphore.set()

    def stop_now(self):
        self.force_clear_queue()

    def _get_pygame(self):
        """惰性初始化 pygame，避免导入时输出提示并占用音频设备。"""
        with self._pygame_lock:
            if self._pygame is None:
                os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
                import pygame

                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                self._pygame = pygame
            return self._pygame

    def playsound(self, path: Path, block: bool = True, volume: float = 1.0):
        """保留原有播放接口，供提示音等非队列音频使用。"""
        if block:
            self._sync_playsound(path, volume)
        else:
            pygame = self._get_pygame()
            sound = pygame.mixer.Sound(path)
            sound.set_volume(min(max(volume, 0.0), 1.0))
            sound.play()

    def _sync_playsound(self, path: Path, volume: float = 1.0):
        pygame = self._get_pygame()
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(min(max(volume, 0.0), 1.0))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if self._interrupt_event.is_set() or self._stop_flag:
                pygame.mixer.music.stop()
                break
            time.sleep(0.01)

    def _default_device_fallback_allowed(self) -> bool:
        """音频回退是否允许改走系统默认设备。

        默认不允许：配置了 Voicemeeter 等指定设备时，悄悄改走默认设备会破坏
        "防自激"前提（TTS 直接进扬声器 → 被麦克风/桌面采集再识别回来）。
        """
        try:
            from manager.config_manager import get_config
            return bool(getattr(get_config().pipeline.tts, "allow_default_device_fallback", False))
        except Exception:
            return False

    def _play_sound(self, path: Path, device: str | None, volume: float = 1.0):
        """播放队列音频；降级时保持设备约束。"""
        try:
            if device:
                try:
                    self._playsound_with_device(path, device, volume)
                except Exception as exc:
                    if self._interrupt_event.is_set() or self._stop_flag:
                        logger.info("sounddevice playback interrupted.")
                    elif self._default_device_fallback_allowed():
                        logger.exception(
                            f"Failed to play with device {device}: {exc}; "
                            "已按配置回退到系统默认设备。"
                        )
                        self._sync_playsound(path, volume)
                    else:
                        logger.error(
                            f"指定播放设备 {device} 播放失败：{exc}。"
                            "已放弃本次播放（不改走默认设备，以免破坏防自激前提）；"
                            "如需允许回退，请把 pipeline.tts.allow_default_device_fallback 设为 true。"
                        )
            else:
                try:
                    self._sync_playsound(path, volume)
                except Exception as exc:
                    logger.exception(
                        f"Default pygame playback failed: {exc}; falling back to sounddevice."
                    )
                    self._playsound_with_device(path, None, volume)
        except Exception as exc:
            if self._interrupt_event.is_set() or self._stop_flag:
                logger.info("Audio playback interrupted.")
            else:
                logger.exception(f"Audio playback failed: {exc}")
        finally:
            time.sleep(0.05)
            if path.exists():
                try:
                    path.unlink()
                    # logger.debug(f"Deleted temp file {path}")
                except PermissionError as exc:
                    logger.warning(f"Could not delete temp file {path} (may be locked): {exc}")

    def _playsound_with_device(self, path: Path, device: str | None, volume: float = 1.0):
        """使用 sounddevice 播放到指定设备；device 为 None 时使用默认设备。"""
        audio_data, sample_rate = sf.read(path, dtype='float32')
        audio_data = (audio_data * volume).clip(-1.0, 1.0)

        target_sample_rate = 48000
        if sample_rate != target_sample_rate:
            import scipy.signal
            audio_data = scipy.signal.resample(
                audio_data,
                int(len(audio_data) * target_sample_rate / sample_rate)
            )
            sample_rate = target_sample_rate

        if audio_data.ndim == 1:
            channels = 1
            audio_data = audio_data.reshape(-1, 1)
        else:
            channels = audio_data.shape[1]

        position = 0

        def callback(outdata, frames, _time, status):
            nonlocal position
            if status:
                logger.debug(f"sounddevice playback status: {status}")
            if self._interrupt_event.is_set() or self._stop_flag:
                outdata.fill(0)
                raise sd.CallbackStop

            chunk = audio_data[position:position + frames]
            outdata[:len(chunk)] = chunk
            if len(chunk) < frames:
                outdata[len(chunk):].fill(0)
                raise sd.CallbackStop
            position += frames

        with sd.OutputStream(
            samplerate=sample_rate,
            device=device,
            channels=channels,
            dtype='float32',
            callback=callback,
        ) as stream:
            while stream.active and not self._interrupt_event.is_set() and not self._stop_flag:
                time.sleep(0.02)

    def clear_queue(self):
        """清空播放队列中所有尚未播放的音频任务（不影响当前正在播放的音频）。"""
        with self._queue_lock:
            if self.audio_clips is None:
                return
            while not self.audio_clips.empty():
                try:
                    self.audio_clips.get_nowait()
                except:
                    break
    
    def force_clear_queue(self):
        """
        强制清空播放队列并请求播放线程立即停止当前播放的音频。
        不在调用线程直接关闭 sounddevice 流，避免 Windows/PortAudio 堆损坏。
        """
        self._interrupt_event.set()
        self.clear_queue()
        logger.info("Speaker queue forcibly cleared and playback interruption requested.")

        