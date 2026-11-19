"""第 4 组（语音链路）P1 缺陷的回归测试。

覆盖 P1-14 轮次结束后重置远程流式 ASR、P1-15 降噪采样率与时长、
P1-16 桌面 ASR 让路（轮次级）、P1-18 TTS 回退遵守设备、P1-19 有界队列。

全部离线运行，不需要声卡、不需要 Core 服务：

    conda activate MyAI
    python -m pytest Bot/tests/test_p1_voice.py -v
"""

import asyncio
import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))


def _tmp(label: str) -> Path:
    """临时目录放在仓库根 .temp 下（Bot/.temp 在受限账户里无法建子目录）。"""
    import uuid

    root = _BOT_DIR.parent / ".temp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"p1voice-{label}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tmp_path():
    """覆盖 pytest 的 tmp_path：系统临时目录在部分账户下不可写。"""
    return _tmp("case")


# --------------------------------------------------------------------------- P1-15


class _StubClearVoice:
    """替身：原样返回输入，便于验证长度/采样率换算。"""

    def __init__(self, task=None, model_names=None):
        self.model_names = model_names

    def __call__(self, audio, *args, **kwargs):
        return np.asarray(audio, dtype=np.float32)


@pytest.fixture
def denoiser(monkeypatch):
    import pipeline.asr.denoiser as denoiser_module

    monkeypatch.setattr(denoiser_module, "ClearVoice", _StubClearVoice, raising=False)
    monkeypatch.setattr(denoiser_module, "CV_AVAILABLE", True, raising=False)
    config = types.SimpleNamespace(mic_enable=True, desktop_enable=True, mode="all", device="auto", sample_rate=16000)
    instance = denoiser_module.Denoiser(config)
    assert instance.enable and instance._clearvoice is not None
    return instance


@pytest.mark.parametrize("sample_rate", [16000, 48000])
def test_denoiser_preserves_length_and_rate(denoiser, sample_rate):
    tone = np.sin(2 * np.pi * 440 * np.arange(sample_rate) / sample_rate).astype(np.float32)
    output = denoiser.process(tone, sample_rate)

    # 时长必须保持：否则调用方按原采样率写 WAV 头就会变成快放/慢放
    assert output.shape[0] == tone.shape[0]
    assert output.dtype == np.float32


def test_denoiser_disabled_returns_input(denoiser):
    tone = np.zeros(1600, dtype=np.float32)
    output = denoiser.process(tone, 16000)
    assert output.shape == tone.shape


def test_denoiser_failure_returns_input(denoiser):
    class _Boom:
        def __call__(self, audio, *args, **kwargs):
            raise RuntimeError("模拟 ClearVoice 失败")

    denoiser._clearvoice = _Boom()
    tone = np.ones(4800, dtype=np.float32)
    output = denoiser.process(tone, 48000)
    assert output.shape == tone.shape


# --------------------------------------------------------------------------- P1-15（采集率配置化）


def test_microphone_uses_configured_sample_rate(monkeypatch):
    import services.microphone.microphone as mic_module

    created = {}

    class _FakeStream:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def start_stream(self):
            return None

        def stop_stream(self):
            return None

        def read(self, *args, **kwargs):
            return None

        def close(self):
            return None

    class _FakePyAudio:
        paInt16 = 8

        def open(self, **kwargs):
            created.update(kwargs)
            return _FakeStream()

        def terminate(self):
            return None

    monkeypatch.setattr(mic_module.pyaudio, "PyAudio", _FakePyAudio)

    from services.microphone.microphone import SmartMicrophone

    mic = SmartMicrophone(enable_vad=False, sample_rate=48000)
    assert mic._sample_rate == 48000
    assert mic._chunk_size == int(48000 * 30 / 1000)

    with pytest.raises(ValueError):
        SmartMicrophone(enable_vad=False, sample_rate=44100)


def test_microphone_config_exposes_sample_rate():
    from services.microphone.config import MicrophoneServiceConfig

    assert MicrophoneServiceConfig().sample_rate == 16000


def test_microphone_shutdown_releases_audio_device():
    """退出时要真正释放音频设备，并禁止再打开采集流。"""
    import pyaudio

    import services.microphone.microphone as mic_module

    class _FakePyAudio:
        paInt16 = 8

        def __init__(self):
            self.terminated = False

        def open(self, **kwargs):
            raise AssertionError("收尾之后不得再打开采集流")

        def terminate(self):
            self.terminated = True

    fake_audio = _FakePyAudio()
    mic = mic_module.SmartMicrophone.__new__(mic_module.SmartMicrophone)
    mic._state_lock = threading.RLock()
    mic._audio_lock = threading.RLock()
    mic._stop_flag = False
    mic._talk_enabled_event = threading.Event()
    mic._reopen_requested = threading.Event()
    mic._audio_frames = []
    mic._is_speaking = False
    mic._has_interim_result = False
    mic._silence_duration = 0.0
    mic._interim_silence_emitted = False
    mic._stream = object()
    mic._audio = fake_audio
    mic._format = pyaudio.paInt16
    mic._channels = 1
    mic._sample_rate = 16000

    mic.shutdown()

    assert mic._stop_flag is True
    assert mic._stream is None
    assert mic._audio is None
    assert fake_audio.terminated is True
    # 已置位停止标志：_open_stream 直接返回，不会重新占用麦克风
    mic._open_stream()
    assert mic._stream is None


# --------------------------------------------------------------------------- P1-14


def _policy_bot(reset_default: bool):
    """用未初始化的实例测策略函数，避免拉起整个 Bot。"""
    from bot_asr_controller import BotASRController

    stub = BotASRController.__new__(BotASRController)
    stub._reset_remote_after_turn = reset_default
    return stub


def test_turn_commit_resets_remote_by_default():
    stub = _policy_bot(True)
    # 不显式传参 -> 走默认策略（提交即重置，避免下一轮从同一条远端流取回旧文本）
    assert stub.should_reset_remote(None) is True
    # 显式传 False 仍然尊重调用方
    assert stub.should_reset_remote(False) is False
    assert stub.should_reset_remote(True) is True

    disabled = _policy_bot(False)
    assert disabled.should_reset_remote(None) is False


# --------------------------------------------------------------------------- P1-16


def _guard_stub(processing: bool, active: bool, age_seconds: float):
    from bot_asr_controller import BotASRController

    stub = BotASRController.__new__(BotASRController)
    stub._mic_processing = processing
    stub._mic_processing_lock = threading.Lock()
    stub._mic_utterance_active = active
    stub._mic_utterance_last_chunk = time.monotonic() - age_seconds
    stub._mic_utterance_stale_seconds = 30.0
    return stub


def test_desktop_asr_yields_while_mic_utterance_active():
    # 单块正在处理 -> 让路
    assert _guard_stub(processing=True, active=False, age_seconds=0).desktop_asr_allowed() is False
    # 块间空隙但本轮语音未结束 -> 仍然让路（旧实现这里会放行）
    assert _guard_stub(processing=False, active=True, age_seconds=0.2).desktop_asr_allowed() is False
    # 空闲 -> 放行
    assert _guard_stub(processing=False, active=False, age_seconds=0).desktop_asr_allowed() is True


def test_desktop_asr_recovers_after_stale_utterance():
    stub = _guard_stub(processing=False, active=True, age_seconds=120)
    assert stub.desktop_asr_allowed() is True
    assert stub._mic_utterance_active is False


# --------------------------------------------------------------------------- P1-18


class _FakeSpeaker:
    """只测回退决策，不接触真实音频设备。"""

    def __init__(self, allow_fallback: bool, monkeypatch):
        import devices.speaker as speaker_module

        monkeypatch.setattr(
            speaker_module.Speaker,
            "_default_device_fallback_allowed",
            lambda self: allow_fallback,
        )
        self.speaker = speaker_module.Speaker.__new__(speaker_module.Speaker)
        self.speaker._interrupt_event = threading.Event()
        self.speaker._stop_flag = False
        self.falls_back = 0
        self.device_calls = 0

        def failing_device(path, device, volume):
            self.device_calls += 1
            raise RuntimeError("模拟指定设备失败")

        def fake_pygame(path, volume=1.0):
            self.falls_back += 1

        self.speaker._playsound_with_device = failing_device
        self.speaker._sync_playsound = fake_pygame


def test_tts_fallback_respects_device_by_default(monkeypatch, tmp_path):
    holder = _FakeSpeaker(allow_fallback=False, monkeypatch=monkeypatch)
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")

    holder.speaker._play_sound(audio, "Voicemeeter Aux Input")
    assert holder.device_calls == 1
    assert holder.falls_back == 0, "默认不得悄悄改走系统默认设备"


def test_tts_fallback_allowed_when_enabled(monkeypatch, tmp_path):
    holder = _FakeSpeaker(allow_fallback=True, monkeypatch=monkeypatch)
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")

    holder.speaker._play_sound(audio, "Voicemeeter Aux Input")
    assert holder.device_calls == 1
    assert holder.falls_back == 1


# --------------------------------------------------------------------------- P1-19


def test_speaker_queue_is_bounded():
    import devices.speaker as speaker_module

    speaker = speaker_module.Speaker.__new__(speaker_module.Speaker)
    from queue import Queue

    speaker.max_queued_clips = 3
    speaker.audio_clips = Queue(maxsize=3)
    speaker._queue_lock = threading.Lock()
    speaker._semaphore = threading.Event()
    speaker._dropped_clips = 0

    for index in range(5):
        speaker.enqueue_sound(Path(f"clip-{index}.wav"))

    assert speaker.audio_clips.qsize() == 3
    assert speaker._dropped_clips == 2
    # 保留的是最新的三条（队列满时丢弃最旧的）
    remaining = []
    while not speaker.audio_clips.empty():
        remaining.append(speaker.audio_clips.get_nowait()[0].name)
    assert remaining == ["clip-2.wav", "clip-3.wav", "clip-4.wav"]


def test_event_queues_are_bounded():
    import event.event_emitter as emitter_module

    sync_executor = emitter_module.SyncTaskExecutor(max_workers=1, max_queue=2)
    try:
        for index in range(4):
            sync_executor.add_sync_task(emitter_module.SyncFunc(lambda index=index: None, name=f"task-{index}"))
        assert sync_executor._sync_tasks.qsize() == 2
        assert sync_executor._dropped_tasks == 2
    finally:
        sync_executor.stop()

    async_executor = emitter_module.AsyncTaskExecutor(max_queue=2)
    try:
        async def _noop():
            return None

        for index in range(5):
            async_executor.add_async_task(emitter_module.AsyncCoro(_noop, name=f"task-{index}"))
        assert async_executor._async_tasks.qsize() == 2
        assert async_executor._dropped_tasks == 3
    finally:
        while not async_executor._async_tasks.empty():
            async_executor._async_tasks.get_nowait()


def test_default_queue_limits_are_finite():
    from event.event_emitter import AsyncTaskExecutor, SyncTaskExecutor

    assert AsyncTaskExecutor().max_queue > 0
    assert SyncTaskExecutor().max_queue > 0


# --------------------------------------------------------------------------- TurnSense / Desktop ASR / VLM 异步回调回归


def test_turnsense_process_audio_logits_supports_unnormalized_2d_logits():
    from pipeline.asr.turnsense import TurnSenseDetector

    raw_logits = np.array([[2.0, 1.0, 0.1], [-1.0, 3.0, 0.5]], dtype=np.float32)
    probs_2d, preds = TurnSenseDetector._process_audio_logits(raw_logits)

    assert probs_2d.shape == (2, 3)
    assert np.allclose(probs_2d.sum(axis=1), 1.0, atol=1e-5)
    assert list(preds) == [0, 1]


def test_desktop_asr_buffer_thread_safe_append_and_take():
    from bot_asr_controller import BotASRController

    stub = BotASRController.__new__(BotASRController)
    stub.desktop_asr_buffer = []
    stub._desktop_asr_lock = threading.Lock()
    stub.max_desktop_sentences = 3

    for idx in range(5):
        stub.append_desktop_asr(f"句{idx}")

    assert stub.take_desktop_asr_text() == "句2。句3。句4"
    assert stub.desktop_asr_buffer == []
    assert stub.take_desktop_asr_text() == ""


def test_vlm_async_predict_invokes_callback_once_and_resets_running_on_error(tmp_path):
    from bot_vision_controller import BotVisionController

    img1 = tmp_path / "ok.png"
    img1.write_bytes(b"png")
    callbacks = []
    done = threading.Event()

    class _OkVLM:
        def predict(self, query):
            return "画面正常"

    vlm_ref = [_OkVLM()]
    controller = BotVisionController(
        config_provider=lambda: None,
        vlm_provider=lambda: vlm_ref[0],
        ocr_provider=lambda: None,
        img_cap_provider=lambda: None,
        screen_provider=lambda: None,
        lang_provider=lambda: None,
        on_trigger_turn=lambda *a, **kw: None,
    )
    assert controller.async_predict(str(img1), lambda cap: (callbacks.append(cap), done.set())) is True
    assert done.wait(timeout=2.0)
    assert callbacks == ["画面正常"]
    assert controller._vlm_running is False
    assert not img1.exists()

    img2 = tmp_path / "fail.png"
    img2.write_bytes(b"png")
    fail_callbacks = []
    fail_done = threading.Event()

    class _BoomVLM:
        def predict(self, query):
            raise RuntimeError("VLM error")

    vlm_ref[0] = _BoomVLM()
    assert controller.async_predict(str(img2), lambda cap: (fail_callbacks.append(cap), fail_done.set())) is True
    assert fail_done.wait(timeout=2.0)
    assert fail_callbacks == [None]
    assert controller._vlm_running is False
    assert not img2.exists()

