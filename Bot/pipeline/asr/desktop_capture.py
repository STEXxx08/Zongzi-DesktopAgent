import threading
import numpy as np
import sounddevice as sd
from loguru import logger
from event.event_data import DeviceDesktopAudioEvent
from event.event_emitter import emitter

class DesktopAudioCapture:
    def __init__(self, sample_rate=16000, channels=1,
                 read_chunk_duration=0.6, send_interval=3.0,
                 input_device=None):
        """
        使用 sounddevice 从指定音频设备捕获桌面音频。
        采用环形缓冲区，始终保留最新 send_interval 秒的音频。
        :param input_device: 设备名称或索引；None 或空字符串都表示使用系统默认输入设备（不推荐）
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.read_chunk_duration = read_chunk_duration
        self.send_interval = send_interval
        # 配置留空（None 或 ""）统一按"系统默认设备"处理，避免把空串当设备名传给 PortAudio
        if isinstance(input_device, str):
            input_device = input_device.strip() or None
        self.input_device = input_device

        self._stop_flag = False
        self._thread = None
        self._read_chunk_frames = int(sample_rate * read_chunk_duration)
        self._send_interval_frames = int(sample_rate * send_interval)

        # 环形缓冲区：始终存储最新的 send_interval_frames 个样本（强制单声道）
        self._buffer = np.zeros(self._send_interval_frames, dtype=np.float32)
        self._write_pos = 0
        self._total_samples = 0
        self._next_upload_threshold = self._send_interval_frames

        # 锁，保护缓冲区写操作（虽然只有一个写入线程，但为了安全）
        self._buffer_lock = threading.Lock()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag = False
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(f"DesktopAudioCapture started on device: {self.input_device}")

    def stop(self):
        self._stop_flag = True
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.info("DesktopAudioCapture stopped.")

    def _capture_loop(self):
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                device=self.input_device,
                dtype='float32',
                blocksize=self._read_chunk_frames,
            ) as stream:
                logger.info(f"DesktopAudioCapture stream opened. send_interval_frames={self._send_interval_frames}")
                while not self._stop_flag:
                    data, overflowed = stream.read(self._read_chunk_frames)
                    if overflowed:
                        logger.warning("Desktop audio capture overflow")
                    if data is not None and len(data) > 0:
                        # 转为单声道
                        if self.channels == 1:
                            audio_data = data.flatten()
                        else:
                            audio_data = np.mean(data, axis=1)

                        # 写入环形缓冲区
                        self._append_audio(audio_data)

                        # 检查是否达到发送阈值
                        with self._buffer_lock:
                            if self._total_samples >= self._next_upload_threshold:
                                # 复制当前缓冲区作为发送数据
                                send_data = self._buffer.copy()  # 拷贝一份
                                # 立即更新阈值，避免重复发送
                                self._next_upload_threshold += self._send_interval_frames
                                # 发射事件（数据拷贝已经完成，所以后续写入不影响）
                                emitter.emit(DeviceDesktopAudioEvent(
                                    audio_data=send_data.tobytes(),
                                    sample_rate=self.sample_rate,
                                    channels=1,
                                    media_type='raw'
                                ))
                                logger.debug(f"Desktop audio chunk sent, total_samples={self._total_samples}")
        except Exception as e:
            logger.exception(f"DesktopAudioCapture error: {e}")
        finally:
            self._stop_flag = True
            logger.info("DesktopAudioCapture loop ended.")

    def _append_audio(self, audio_data: np.ndarray):
        """将音频数据写入环形缓冲区"""
        with self._buffer_lock:
            n = len(audio_data)
            if n == 0:
                return
            # 更新总样本计数
            self._total_samples += n
            # 将数据写入循环缓冲区
            pos = self._write_pos
            buf = self._buffer
            # 如果数据长度超过缓冲区大小，只保留最后 send_interval_frames 部分
            if n > self._send_interval_frames:
                # 只取最后一部分
                audio_data = audio_data[-self._send_interval_frames:]
                n = self._send_interval_frames
                pos = 0  # 直接覆盖整个缓冲区

            # 写入
            end = pos + n
            if end <= self._send_interval_frames:
                buf[pos:end] = audio_data
            else:
                # 分两段写入
                first = self._send_interval_frames - pos
                buf[pos:] = audio_data[:first]
                buf[:end - self._send_interval_frames] = audio_data[first:]
            self._write_pos = end % self._send_interval_frames
    
    def clear_buffer(self):
        """清空缓冲区，重置写入位置和计数器"""
        with self._buffer_lock:
            self._buffer.fill(0.0)
            self._write_pos = 0
            self._total_samples = 0
            self._next_upload_threshold = self._send_interval_frames
            logger.debug("Desktop audio buffer cleared")