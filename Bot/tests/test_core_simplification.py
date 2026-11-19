"""Phase 3 核心层精简与懒加载改造的离线回归测试。"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))


def _probe_import(module_name: str, forbidden_modules: list[str]) -> dict[str, bool]:
    code = (
        "import json, sys; "
        f"sys.path.insert(0, {str(_BOT_DIR)!r}); "
        f"__import__({module_name!r}); "
        f"targets = {forbidden_modules!r}; "
        "print(json.dumps({m: (m in sys.modules) for m in targets}))"
    )
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    return json.loads(out.strip())


def test_lazy_imports_do_not_pull_heavy_modules_on_import():
    assert _probe_import("main", ["bot"]) == {"bot": False}
    assert _probe_import(
        "common.utils.audio_util",
        ["pydub", "soundfile", "scipy", "numpy"],
    ) == {"pydub": False, "soundfile": False, "scipy": False, "numpy": False}
    assert _probe_import(
        "agent.api",
        ["langchain_core", "agent.adaptor"],
    ) == {"langchain_core": False, "agent.adaptor": False}
    assert _probe_import(
        "devices.speaker",
        ["soundfile", "sounddevice", "pygame"],
    ) == {"soundfile": False, "sounddevice": False, "pygame": False}
    assert _probe_import(
        "framework.base_bot",
        ["aiohttp", "pipeline.base.base_async", "pipeline.llm.llm_async"],
    ) == {"aiohttp": False, "pipeline.base.base_async": False, "pipeline.llm.llm_async": False}


def test_pseudo_markers_precompiled_close_tags_and_streaming():
    from common.pseudo_markers import iter_visible_segments, strip_pseudo_blocks

    assert strip_pseudo_blocks("你好<SYSTEM>secret</system>世界") == "你好世界"
    assert strip_pseudo_blocks("开头<plan>未闭合") == "开头"

    segs1 = iter_visible_segments("前置<thin", flush=False)
    assert segs1 == [("前置", 2)]
    segs2 = iter_visible_segments("<thinking>内部", flush=False)
    assert segs2 == []
    segs3 = iter_visible_segments("<thinking>内部</thinking>尾声", flush=False)
    assert segs3 == [("尾声", len("<thinking>内部</thinking>尾声"))]


def test_audio_util_roundtrip_and_format_detection():
    from common.io.file_type import AudioFileType
    from common.utils.audio_util import (
        from_bytes_to_np_ndarray,
        from_ndarray_to_bytes,
        get_audio_real_format,
    )

    samples = np.array([0, 16384, -16384, 0], dtype=np.int16)
    wav_bytes = from_ndarray_to_bytes(samples, 16000)
    assert get_audio_real_format(wav_bytes) == AudioFileType.WAV
    decoded, sr = from_bytes_to_np_ndarray(wav_bytes, dtype="float32")
    assert sr == 16000
    assert decoded.shape == (4,)


def test_agent_api_invoke_template_preserves_behavior(monkeypatch):
    import agent.api as agent_api
    from common.enumerator import Language

    calls = []

    class _FakeModel:
        def invoke(self, prompt_value):
            messages = prompt_value.to_messages()
            calls.append([(m.type, m.content) for m in messages])
            return SimpleNamespace(content="happy")

    monkeypatch.setattr(agent_api, "_get_model", lambda: _FakeModel())

    assert agent_api.answer_question("文章", "问题") == "happy"
    assert agent_api.sentiment_analyse(["Default", "happy"], "真棒") == "happy"
    assert agent_api.sentiment_analyse(["Default"], "跳过模型") == "Default"
    assert agent_api.translate("hello", Language.EN, Language.ZH) == "happy"
    summary_msg = agent_api.summary("长文本", max_len=20)
    assert summary_msg.content == "happy"
    assert len(calls) == 4


def test_speaker_volume_clamping(tmp_path):
    import devices.speaker as speaker_module

    volumes = []

    class _FakeSound:
        def __init__(self, _path):
            pass

        def set_volume(self, v):
            volumes.append(v)

        def play(self):
            pass

    class _FakeMusic:
        @staticmethod
        def load(_path):
            pass

        @staticmethod
        def set_volume(v):
            volumes.append(v)

        @staticmethod
        def play():
            pass

        @staticmethod
        def get_busy():
            return False

    fake_pygame = SimpleNamespace(
        mixer=SimpleNamespace(Sound=_FakeSound, music=_FakeMusic)
    )
    spk = speaker_module.Speaker.__new__(speaker_module.Speaker)
    spk._get_pygame = lambda: fake_pygame
    spk._interrupt_event = threading.Event()
    spk._stop_flag = False

    dummy = tmp_path / "a.wav"
    dummy.write_bytes(b"RIFF")
    spk.playsound(dummy, block=False, volume=2.5)
    spk.playsound(dummy, block=True, volume=-0.5)
    assert volumes == [1.0, 0.0]


def test_base_bot_reload_pipeline_attr(monkeypatch):
    from framework.base_bot import BaseBot
    from pipeline.llm.config import LLMPipelineConfig
    from pipeline.llm.llm_async import LLMAsyncPipeline
    from pipeline.llm.llm_sync import LLMSyncPipeline

    bot = BaseBot.__new__(BaseBot)
    bot.asr = None
    bot.img_cap = None
    bot.ocr = None
    bot.tts = None
    bot.llm = LLMSyncPipeline(LLMPipelineConfig(enable=True, predict_url="http://127.0.0.1:1/old"))

    new_cfg = SimpleNamespace(
        pipeline=SimpleNamespace(
            asr=SimpleNamespace(enable=False),
            img_cap=SimpleNamespace(enable=False),
            llm=LLMPipelineConfig(enable=True, predict_url="http://127.0.0.1:2/new"),
            mllm=SimpleNamespace(enable=False),
            ocr=SimpleNamespace(enable=False),
            tts=SimpleNamespace(enable=False),
        )
    )
    monkeypatch.setattr("framework.base_bot.get_config", lambda: new_cfg)
    bot.reload_pipeline()
    assert isinstance(bot.llm, LLMSyncPipeline)
    assert bot.llm.predict_url == "http://127.0.0.1:2/new"

    bot._reload_pipeline_attr(
        "llm",
        True,
        LLMPipelineConfig(enable=True, predict_url="http://127.0.0.1:3/async"),
        LLMSyncPipeline,
        LLMAsyncPipeline,
    )
    # llm was Sync so remains Sync
    assert isinstance(bot.llm, LLMSyncPipeline)
    bot._reload_pipeline_attr(
        "llm",
        False,
        LLMPipelineConfig(enable=True),
        LLMSyncPipeline,
        LLMAsyncPipeline,
    )
    assert bot.llm is None


def test_context_skips_vlm_when_mllm_enabled(monkeypatch):
    import framework.context as fw_context
    from config import BotConfig

    cfg = BotConfig()
    cfg.pipeline.llm.enable = False
    cfg.pipeline.mllm.enable = True
    cfg.pipeline.vlm.enable = True
    cfg.pipeline.asr.enable = False
    cfg.pipeline.tts.enable = False
    cfg.pipeline.ocr.enable = False
    cfg.pipeline.img_cap.enable = False
    cfg.service.microphone.enable = False

    monkeypatch.setattr(fw_context, "get_config", lambda: cfg)
    monkeypatch.setattr(fw_context.devices.headless, "is_headless", lambda: True)
    monkeypatch.setattr(fw_context, "is_headless", lambda: True)
    monkeypatch.setattr(fw_context, "SmartMicrophone", lambda **kwargs: object())
    monkeypatch.setattr(fw_context, "Speaker", lambda: object())

    ctx = fw_context.BotContext()
    assert ctx.vlm is None


def test_prompt_managers_dedup_and_regex():
    from character.config import ChatConfig
    from common.enumerator import Language
    from condata.pipeline.llm import Conversation, RoleEnum
    from manager.llm_prompt_manager import LLMPromptManager
    from manager.tts_prompt_manager import parse_tts_prompt_filename

    pm = LLMPromptManager(ChatConfig(system_prompt="初始{bot_name}"), bot_name="粽子")
    pm.replace_system_prompt("新系统提示")
    assert pm.injected_history[0].content == "新系统提示"
    assert pm.current_history[0].content == "新系统提示"

    pm.current_history = [Conversation(role=RoleEnum.user, content="hi")]
    pm.replace_system_prompt("补插提示")
    assert pm.current_history[0].role == RoleEnum.system
    assert pm.current_history[0].content == "补插提示"

    lang, sentiment, text = parse_tts_prompt_filename("[zh][happy]你好世界.wav")
    assert lang == Language.ZH
    assert sentiment == "happy"
    assert "你好世界" in text


def test_tts_scheduler_clean_split_and_enqueue():
    from bot_tts_scheduler import BotTTSScheduler, clean_text_for_tts
    from common.enumerator import Language

    assert clean_text_for_tts("*摇尾巴* 你好（轻声）[shy]<tag>世界  ") == "你好世界"
    segs, rest = BotTTSScheduler.pop_stream_segments("第一句。第二句！未完", flush=False)
    assert segs == ["第一句。", "第二句！"]
    assert rest == "未完"

    adaptive = SimpleNamespace(
        enable=True,
        max_speed=1.35,
        long_text_threshold=10,
        idle_decay_seconds=0.0,
        interrupt_speed_step=1,
    )
    assert BotTTSScheduler._calc_max_speed_step(adaptive) == 7

    submitted = []
    cfg = SimpleNamespace(
        pipeline=SimpleNamespace(
            tts=SimpleNamespace(filter=True, adaptive_speed=adaptive, warmup_on_startup=False)
        ),
        system=SimpleNamespace(enable_clause_split=True, enable_sentiment_analysis=False),
    )
    sched = BotTTSScheduler(
        config_provider=lambda: cfg,
        tts_provider=lambda: None,
        tts_prompt_manager_provider=lambda: SimpleNamespace(
            default_tts_prompt=SimpleNamespace(sentiment="Default")
        ),
        speaker_provider=lambda: None,
        mic_provider=lambda: None,
        lang_provider=lambda: Language.ZH,
    )
    sched.submit_segment = lambda prompt, text, speed, emotion: submitted.append(text)
    try:
        assert sched.enqueue("甲，乙。", emotion="Default") is True
        assert submitted == ["甲", "乙"]
        submitted.clear()
        cfg.system.enable_clause_split = False
        assert sched.enqueue("甲，乙。", emotion="Default") is True
        assert submitted == ["甲，乙。"]
    finally:
        sched.shutdown()


def _make_wav_bytes(samples: np.ndarray, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()


def test_asr_controller_denoise_decode_and_streaming_query():
    from bot_asr_controller import BotASRController
    from condata.pipeline.asr import ASRPrediction, ASRStreamQuery
    from event.event_data import DeviceMicrophoneVADEvent

    ctrl = BotASRController.__new__(BotASRController)
    ctrl._mic_processing = False
    ctrl._mic_processing_lock = threading.Lock()
    ctrl._mic_utterance_active = False
    ctrl._mic_utterance_last_chunk = 0.0
    ctrl._asr_accumulated_text = ""
    ctrl._asr_accumulated_lock = threading.Lock()
    ctrl._pending_asr_count = 0
    ctrl._final_pending = False
    ctrl._on_reset_auto_chat = lambda: None
    ctrl._on_interrupt_tts = lambda: None
    ctrl._emit_event = lambda ev: None
    ctrl.turnsense_detector = None
    ctrl.desktop_asr_buffer = []
    ctrl.max_desktop_sentences = 2
    ctrl._desktop_asr_lock = None  # 覆盖 nullcontext 无锁分支

    ctrl.append_desktop_asr("一")
    ctrl.append_desktop_asr("二")
    ctrl.append_desktop_asr("三")
    assert ctrl.take_desktop_asr_text() == "二。三"

    seen_audio = []
    fake_asr = SimpleNamespace(
        stream_predict=lambda q: [
            ASRPrediction(transcript="嗯嗯"),
            ASRPrediction(transcript="你好世界"),
        ]
    )
    ctrl._asr_provider = lambda: fake_asr
    ctrl._config_provider = lambda: SimpleNamespace(
        pipeline=SimpleNamespace(
            asr=SimpleNamespace(
                denoise=SimpleNamespace(mic_enable=True, desktop_enable=False),
                turnsense=SimpleNamespace(enable=False),
            )
        )
    )
    ctrl.denoiser = SimpleNamespace(process=lambda audio, sr: audio * 0.5)
    ctrl._finish_asr_request = lambda transcript, is_final: transcript
    ctrl._emit_final_asr_text = lambda text: seen_audio.append(text)

    wav = _make_wav_bytes(np.array([1000, -1000, 2000, -2000], dtype=np.int16))
    ctrl.handle_vad_chunk(
        DeviceMicrophoneVADEvent(
            speech=wav, audio_type="wav", channels=1, sample_rate=16000, is_final=True
        )
    )
    assert seen_audio == ["你好世界"]

    # 非 WAV 字节在 fallback_raw=False 时降级回原始音频，不抛异常
    ctrl.handle_vad_chunk(
        DeviceMicrophoneVADEvent(
            speech=b"not-a-wav", audio_type="wav", channels=1, sample_rate=16000, is_final=True
        )
    )
    assert len(seen_audio) == 2

    q = ASRStreamQuery(is_final=True, audio_data=b"", channels=1, sample_rate=16000, media_type="wav")
    assert ctrl._run_streaming_asr_query(q) == "你好世界"


def test_event_binder_and_turn_orchestrator_unified_tails():
    from bot_event_binder import BotEventBinder
    from bot_tts_scheduler import BotTTSScheduler
    from bot_turn_orchestrator import BotTurnOrchestrator
    from condata.pipeline.asr import ASRPrediction
    from condata.pipeline.llm import LLMPrediction, LLMQuery
    from event.event_data import PipelineASREvent

    emitted_turns = []
    fake_bot = SimpleNamespace(
        get_runtime_config=lambda: SimpleNamespace(
            pipeline=SimpleNamespace(
                mllm=SimpleNamespace(enable=True),
                vlm=SimpleNamespace(mode="keyword"),
            )
        ),
        _next_turn_id=lambda: 7,
        skill_service=SimpleNamespace(parse_user_invocation=lambda t: None),
        intent_router=SimpleNamespace(enabled=False, available=False, allow_keyword_fallback=True),
        mllm_cfg=SimpleNamespace(enable=True),
        _mllm_screenshot_manager=SimpleNamespace(pop_all=lambda: ["shot1.webp"]),
        emit_llm_prediction=lambda text, **kw: emitted_turns.append((text, kw)),
    )
    binder = BotEventBinder(fake_bot)
    binder._on_pipeline_asr(
        PipelineASREvent(prediction=ASRPrediction(transcript="看看今天天气"), skip_tts=True)
    )
    assert emitted_turns == [
        (
            "看看今天天气",
            {
                "image_paths": ["shot1.webp"],
                "skip_tts": True,
                "_allow_keyword_match": True,
                "_turn_id": 7,
            },
        )
    ]

    enqueued = []
    orch = BotTurnOrchestrator.__new__(BotTurnOrchestrator)
    orch._config_provider = lambda: SimpleNamespace(
        character=SimpleNamespace(emotions=["Default"])
    )
    orch._tts_scheduler = SimpleNamespace(
        cancel_pending=lambda: None,
        current_task_id=lambda: 1,
        get_prompt=lambda e: None,
        pop_stream_segments=BotTTSScheduler.pop_stream_segments,
        enqueue=lambda seg, p, e: bool(enqueued.append(seg) or True),
        watch_round_end=lambda tid: None,
        select_prompt=lambda text: SimpleNamespace(sentiment="Default"),
    )
    orch._filter_provider = lambda: SimpleNamespace(filter=lambda text: False)
    orch._web_search_service_provider = lambda: SimpleNamespace(
        config=SimpleNamespace(model_marker="")
    )
    orch._skill_service_provider = lambda: SimpleNamespace(
        config=SimpleNamespace(model_marker="", model_create_marker="")
    )
    orch._model_tool_gateway_provider = lambda: SimpleNamespace(
        marker="tool_call",
        extract_stream_visible_text=lambda buf, flush=False, markers=(): (buf, ""),
    )
    orch._commit_history_and_memory = lambda *a, **kw: None
    orch._emit_event = lambda ev: None
    orch._vision_controller = SimpleNamespace(reset_auto_chat_timer=lambda: None)

    ok, pred = orch.emit_streaming_prediction(
        label="LLM",
        stream_fn=lambda q: [
            LLMPrediction(response="第一句。尾巴", history=[]),
        ],
        prediction_cls=LLMPrediction,
        query=LLMQuery(text="hi", history=[]),
        original_text="hi",
        skip_tts=False,
        stream_tts_by_sentence=True,
        cumulative=False,
        fallback_on_unstarted_error=False,
    )
    assert ok is True
    assert pred is not None and pred.response == "第一句。尾巴"
    assert enqueued == ["第一句。", "尾巴"]
