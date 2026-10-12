import io
import threading
import time
import wave
import numpy as np
import pyaudio
import webrtcvad
from loguru import logger

from common.concurrent.abs_runnable import ThreadRunnable
from common.concurrent.killable_thread import ThreadKilledError
from common.io.file_type import AudioFileType
from event.event_data import CharacterStateEvent, DeviceMicrophoneVADEvent
from event.event_emitter import emitter


class SmartMicrophone(ThreadRunnable):
    def __init__(self,
                enable_vad: bool = True,
                vad_mode: int = 1,
                frame_duration: int = 30,
                sample_rate: int = 16000,
                silence_timeout: float = 1.5,
                rms_threshold: float = 200.0,
                # 新增参数
                interim_enable: bool = True,
                interim_silence_timeout: float = 0.5,
                min_interim_audio_sec: float = 0.5,
                streaming_asr_enable: bool = False,
                streaming_chunk_audio_sec: float = 0.24
                ):
        super().__init__()
        self._enable_vad = enable_vad
        assert frame_duration in [10, 20, 30], "frame_duration must be 10, 20, or 30 ms"

        self._format = pyaudio.paInt16
        self._channels = 1
        # 采集率由配置决定（默认 16k，与 ASR 一致）；webrtcvad 只接受 8k/16k/32k/48k
        self._sample_rate = int(sample_rate)
        if self._sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError("麦克风采样率必须是 8000/16000/32000/48000 之一")
        self._chunk_size = int(self._sample_rate * frame_duration / 1000)

        # PortAudio caches the device list in the PyAudio instance.  Keeping
        # that instance alive after a USB/Bluetooth device disconnects can
        # therefore make a reconnected microphone unavailable until the whole
        # process is restarted.  Create it lazily and replace it together with
        # the stream whenever the microphone is reopened.
        self._audio = None
        self._audio_lock = threading.RLock()
        self._sample_width = 2  # paInt16
        self._vad = webrtcvad.Vad(vad_mode) if enable_vad else None
        self._stream = None

        self._audio_frames = []
        self._is_speaking = False
        self._has_interim_result = False
        self._stop_flag = False

        self._talk_enabled_event = threading.Event()
        self._talk_enabled_event.clear()
        self._reopen_requested = threading.Event()

        self._state_lock = threading.RLock()

        # 新增：帧时长（秒）和静音累加器
        self._frame_duration_sec = frame_duration / 1000.0
        self._silence_duration = 0.0
        self._interim_silence_emitted = False
        self._silence_timeout = silence_timeout
        self._rms_threshold = rms_threshold

        self.interim_enable = interim_enable
        self.interim_silence_timeout = interim_silence_timeout
        self.min_interim_frames = int(min_interim_audio_sec * self._sample_rate / self._chunk_size)
        self.streaming_asr_enable = streaming_asr_enable
        self.streaming_chunk_frames = max(1, int(streaming_chunk_audio_sec * self._sample_rate / self._chunk_size))
        self.semantic_turn_mode = False

    @property
    def is_recording(self):
        return self._talk_enabled_event.is_set() and (not self._stop_flag) and self._stream_is_active()

    def _open_stream(self):
        with self._audio_lock:
            if self._stream is not None:
                return
            if self._stop_flag:
                # 已经在收尾：不要再打开新的采集流，否则麦克风会被重新占用
                return

            audio = pyaudio.PyAudio()
            try:
                stream = audio.open(
                    format=self._format,
                    channels=self._channels,
                    rate=self._sample_rate,
                    input=True,
                    frames_per_buffer=self._chunk_size,
                )
                self._sample_width = audio.get_sample_size(self._format)
            except Exception:
                try:
                    audio.terminate()
                except Exception as exc:
                    logger.warning(f"Failed to terminate unusable PyAudio instance: {exc}")
                raise

            self._audio = audio
            self._stream = stream

    def _stream_is_active(self) -> bool:
        with self._audio_lock:
            try:
                return self._stream is not None and self._stream.is_active()
            except Exception:
                return False

    def _safe_stop_stream(self):
        with self._audio_lock:
            stream = self._stream
            if stream is None:
                return
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception as exc:
                logger.warning(f"Failed to stop microphone stream: {exc}")

    def _safe_close_stream(self):
        with self._audio_lock:
            stream = self._stream
            self._stream = None
            if stream is None:
                return
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception as exc:
                logger.warning(f"Failed to stop microphone stream before close: {exc}")
            try:
                stream.close()
            except Exception as exc:
                logger.warning(f"Failed to close microphone stream: {exc}")

    def _release_audio(self):
        """Release both the stream and PortAudio device cache."""
        with self._audio_lock:
            self._safe_close_stream()
            audio = self._audio
            self._audio = None
            if audio is None:
                return
            try:
                audio.terminate()
            except Exception as exc:
                logger.warning(f"Failed to terminate PyAudio: {exc}")

    def _recover_stream(self, exc: Exception):
        logger.warning(f"Microphone stream read failed, rebuilding audio input: {exc}")
        self._release_audio()
        if not self._talk_enabled_event.is_set() or self._stop_flag:
            return
        time.sleep(0.2)
        try:
            self._open_stream()
            if self._talk_enabled_event.is_set() and not self._stream_is_active():
                self._stream.start_stream()
            logger.info("Microphone audio interface and input stream reopened.")
        except Exception as reopen_exc:
            logger.error(f"Failed to reopen microphone input stream: {reopen_exc}", exc_info=True)
            time.sleep(1.0)

    @property
    def is_speaking(self):
        """返回当前是否正在说话（VAD激活）"""
        return self._is_speaking

    def start(self):
        super().start()
        self._stop_flag = False
        try:
            while not self._stop_flag:
                self._stream_update()
                self._talk_enabled_event.wait()
                self._stream_update()
                if self._stop_flag:
                    break
                if self._stream is None or not self._stream_is_active():
                    time.sleep(0.1)
                    continue

                try:
                    data = self._stream.read(self._chunk_size, exception_on_overflow=False)
                except Exception as exc:
                    if self._stop_flag:
                        break
                    self._recover_stream(exc)
                    continue

                with self._state_lock:
                    self._vad_record(data)
                    self._check_silence_timeout()

        except ThreadKilledError:
            logger.debug("Microphone worker thread stopped.")
        except Exception as e:
            logger.exception(e)
        finally:
            self._release_audio()

    def _vad_record(self, data: bytes):
        audio_array = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(audio_array ** 2))

        if self._enable_vad:
            is_speech = self._vad.is_speech(data, self._sample_rate)
            if is_speech:
                if self._rms_threshold <= 0 or rms >= self._rms_threshold:
                    self._silence_duration = 0.0
                    self._interim_silence_emitted = False
                    if not self._is_speaking:
                        logger.debug("Voice detected: Beginning.")
                        self._is_speaking = True
                        self._has_interim_result = False
                        if self.semantic_turn_mode:
                            self._emit_activity_marker()
                    self._audio_frames.append(data)
                    self._maybe_emit_streaming_chunk()
                elif self._is_speaking and self.semantic_turn_mode:
                    # Preserve quiet sentence tails for ASR, but count them as
                    # silence for endpoint timing. This semantic-streaming-only
                    # behavior avoids changing the legacy VAD path used by other
                    # ASR modes.
                    self._audio_frames.append(data)
                    self._silence_duration += self._frame_duration_sec
            else:
                if self._is_speaking:
                    if self.semantic_turn_mode:
                        # TurnSense keeps a long-lived streaming ASR session, so
                        # retain trailing context until its semantic endpoint is
                        # committed. The standard ASR path keeps its old behavior.
                        self._audio_frames.append(data)
                    self._silence_duration += self._frame_duration_sec
        else:
            if not self._is_speaking:
                self._is_speaking = True
                self._has_interim_result = False
                if self.semantic_turn_mode:
                    self._emit_activity_marker()
            self._audio_frames.append(data)
            self._maybe_emit_streaming_chunk()

    def _maybe_emit_streaming_chunk(self):
        if not self.streaming_asr_enable:
            return
        if len(self._audio_frames) >= self.streaming_chunk_frames:
            logger.debug("Streaming ASR chunk reached, committing interim chunk.")
            self._emit_event(is_final=False)
            self._has_interim_result = True
            self._audio_frames = []

    def _check_silence_timeout(self):
        if not self._is_speaking:
            return

        if self.semantic_turn_mode:
            if self._silence_duration >= self._silence_timeout:
                logger.debug("Semantic turn final silence reached, committing final marker.")
                self._is_speaking = False
                if self._audio_frames:
                    self._emit_event(is_final=False)
                    self._audio_frames = []
                self._emit_final_marker()
                self._silence_duration = 0.0
                self._interim_silence_emitted = False
                self._has_interim_result = False
                return

            if (
                self.interim_enable
                and not self._interim_silence_emitted
                and self._silence_duration >= self.interim_silence_timeout
            ):
                if self._audio_frames:
                    logger.debug("Semantic turn pause reached, emitting candidate chunk without VAD finalizing.")
                    self._emit_event(is_final=False)
                    self._audio_frames = []
                # Do not reset cumulative silence here: otherwise the longer
                # final-silence timeout can never be reached.
                self._interim_silence_emitted = True
            return
    
        # 1. 最终静音（长阈值）
        if self._silence_duration >= self._silence_timeout:
            logger.debug("Final silence timeout reached, committing final chunk.")
            self._is_speaking = False
            if self._audio_frames:
                self._emit_event(is_final=True)
            elif self._has_interim_result:
                self._emit_final_marker()
            self._audio_frames = []
            self._silence_duration = 0.0
            self._interim_silence_emitted = False
            self._has_interim_result = False
            return
    
        # 2. 中间静音（短阈值），且启用了分段
        if self.interim_enable and self._silence_duration >= self.interim_silence_timeout:
            if len(self._audio_frames) >= self.min_interim_frames:
                logger.debug("Interim silence timeout reached, committing interim chunk.")
                self._emit_event(is_final=False)
                self._has_interim_result = True
                # 清空已提交的帧，但保持说话状态
                self._audio_frames = []

    def _emit_event(self, is_final: bool = True):
        with self._state_lock:
            if not self._audio_frames:
                return
            frames = self._audio_frames.copy()

        if frames:
            emitter.emit(DeviceMicrophoneVADEvent(
                speech=self._encode_frames(frames),
                audio_type=AudioFileType.WAV,
                channels=self._channels,
                sample_rate=self._sample_rate,
                is_final=is_final
            ))

    def _encode_frames(self, frames) -> bytes:
        file = io.BytesIO()
        with wave.open(file, 'wb') as wav_file:
            wav_file.setnchannels(self._channels)
            wav_file.setsampwidth(self._sample_width)
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(b''.join(frames))
        return file.getvalue()

    def take_pending_candidate(self) -> bytes:
        with self._state_lock:
            if not self._audio_frames:
                return b''
            frames = self._audio_frames.copy()
            self._audio_frames = []
        return self._encode_frames(frames)

    def flush_pending_candidate(self) -> bool:
        speech = self.take_pending_candidate()
        if not speech:
            return False
        emitter.emit(DeviceMicrophoneVADEvent(
            speech=speech,
            audio_type=AudioFileType.WAV,
            channels=self._channels,
            sample_rate=self._sample_rate,
            is_final=False
        ))
        return True

    def _emit_activity_marker(self):
        emitter.emit(DeviceMicrophoneVADEvent(
            speech=b'',
            audio_type=AudioFileType.WAV,
            channels=self._channels,
            sample_rate=self._sample_rate,
            is_final=False
        ))

    def _emit_final_marker(self):
        emitter.emit(DeviceMicrophoneVADEvent(
            speech=b'',
            audio_type=AudioFileType.WAV,
            channels=self._channels,
            sample_rate=self._sample_rate,
            is_final=True
        ))

    def _stream_update(self):
        if self._reopen_requested.is_set():
            # Keep this request separate from the enabled flag so even a very
            # quick off/on toggle cannot skip releasing the stale device.
            self._release_audio()
            self._reopen_requested.clear()

        if not self._talk_enabled_event.is_set():
            # A real close/reopen is intentional here.  Besides releasing the
            # device for other applications, this ensures the next resume uses
            # a fresh PortAudio device enumeration after a disconnect.
            self._release_audio()
            return

        if self._stream is None:
            try:
                self._open_stream()
            except Exception as exc:
                logger.warning(f"Failed to open microphone stream: {exc}")
                time.sleep(1.0)
                return

        if not self._stream_is_active():
            try:
                self._stream.start_stream()
            except Exception as exc:
                self._recover_stream(exc)

    def pause(self):
        with self._state_lock:
            self._talk_enabled_event.clear()
            self._reopen_requested.set()
            self._audio_frames = []
            self._is_speaking = False
            self._has_interim_result = False
            self._silence_duration = 0.0
            self._interim_silence_emitted = False
        logger.info("Paused smart microphone.")
        emitter.emit(CharacterStateEvent(state="idle"))

    def resume(self):
        self._talk_enabled_event.set()
        logger.info("Resumed smart microphone.")
        emitter.emit(CharacterStateEvent(state="listening"))

    def stop(self):
        with self._state_lock:
            self._stop_flag = True
            self._talk_enabled_event.set()
            self._audio_frames = []
            self._is_speaking = False
            self._has_interim_result = False
            self._silence_duration = 0.0
            self._interim_silence_emitted = False
        logger.info("Stopped smart microphone.")
        emitter.emit(CharacterStateEvent(state="idle"))

    def shutdown(self):
        """退出流程：停止采集并释放音频设备，避免进程退出后麦克风仍被占用。

        与 pause() 不同，这里会真正关闭 PortAudio 流并释放 PyAudio 实例；
        置位 stop 后再关流，保证 _open_stream 不会在收尾过程中重新打开设备。
        """
        self.stop()
        with self._audio_lock:
            self._stream = None
            audio = self._audio
            self._audio = None
        if audio is not None:
            try:
                audio.terminate()
            except Exception as exc:
                logger.warning(f"释放 PyAudio 实例失败：{exc}")
        logger.info("🎙️ 麦克风采集已关闭，音频设备已释放。")

    def name(self):
        return "SmartMicrophone"

    def is_set_talk_enabled_event(self):
        return self._talk_enabled_event.is_set()

    def set_talk_enabled_event(self):
        self._talk_enabled_event.set()
        emitter.emit(CharacterStateEvent(state="listening"))

    def unset_talk_enabled_event(self):
        self._talk_enabled_event.clear()
        self._reopen_requested.set()
        emitter.emit(CharacterStateEvent(state="idle"))

    def force_commit(self, is_emit=False, is_final=True):
        with self._state_lock:
            if self._is_speaking and self._audio_frames and is_emit:
                self._is_speaking = False
                self._emit_event(is_final=is_final)
            self._audio_frames = []
            self._is_speaking = False
            self._has_interim_result = False
            self._silence_duration = 0.0
            self._interim_silence_emitted = False