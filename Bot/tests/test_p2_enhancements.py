"""P1/P2 延后优化与重构回归测试（离线可跑，不依赖 Core 服务在线）。

覆盖：
- 1.2 Core TTS /tts/abort 接口、TTSSyncPipeline.abort 与 Bot 侧取消/预热中止联动
- 2.2 纯文本 LLMSyncPipeline 流式预测 + Bot 流式分句送 TTS（兼容累积/增量与回退）
- 2.3 HarnessRecallService.update_config 与 MemoryRetriever.update_config 热重载
- 2.4 Core/starter.py 注册表与动态 importlib 模型加载
- 2.5 zongzi_api.py 移除 .dict()、context.py 移除 master_name、流式生成器无 InstrumentationWarning
"""

import importlib
import importlib.util
import shutil
import sys
import tempfile
import threading
import types
import uuid
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

_BOT_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BOT_DIR.parent
_CORE_DIR = _REPO_DIR / "Core"
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

from condata.pipeline.llm import LLMQuery, LLMPrediction  # noqa: E402

from memory.config import RetrievalConfig  # noqa: E402
from memory.retrieval import MemoryRetriever  # noqa: E402
from pipeline.llm.config import LLMPipelineConfig  # noqa: E402
from pipeline.llm.llm_sync import LLMSyncPipeline  # noqa: E402
from pipeline.tts.config import MODEL_ID_BAIDU, TTSPipelineConfig  # noqa: E402
from pipeline.tts.tts_sync import TTSSyncPipeline  # noqa: E402
from services.harness_recall.config import HarnessRecallConfig  # noqa: E402
from services.harness_recall.service import HarnessRecallService  # noqa: E402
from services.tool_runtime import ToolRuntime  # noqa: E402


def _make_tmp_dir(prefix: str) -> Path:
    base = _REPO_DIR / ".temp"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path(tempfile.gettempdir())
    target = base / f"{prefix}-{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=True)
    return target


# ---------------------------------------------------------------------------
# 1.2 Core TTS /tts/abort & Bot abort hooks
# ---------------------------------------------------------------------------


def test_tts_sync_abort_derives_url_and_handles_errors(monkeypatch):
    cfg = TTSPipelineConfig(enable=True, predict_url="http://127.0.0.1:11002/tts/predict")
    pipeline = TTSSyncPipeline(cfg)
    assert pipeline.abort_url == "http://127.0.0.1:11002/tts/abort"

    called = []

    def _ok_post(url, timeout):
        called.append((url, timeout))
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("pipeline.tts.tts_sync.requests.post", _ok_post)
    assert pipeline.abort(timeout=0.5) is True
    assert called == [("http://127.0.0.1:11002/tts/abort", 0.5)]

    def _fail_post(url, timeout):
        raise requests.ConnectionError("core offline")

    monkeypatch.setattr("pipeline.tts.tts_sync.requests.post", _fail_post)
    assert pipeline.abort() is False


def test_baidu_tts_disables_core_abort():
    cfg = TTSPipelineConfig(enable=True, model_id=MODEL_ID_BAIDU)
    pipeline = TTSSyncPipeline(cfg)
    assert pipeline.abort_url == ""
    assert pipeline.abort() is False


def test_zongzi_api_has_abort_route_and_no_pydantic_dict():
    source = (_CORE_DIR / "GPT-SoVITS" / "zongzi_api.py").read_text(encoding="utf-8")
    assert '@APP.post("/tts/abort")' in source
    assert "tts_pipeline.stop()" in source
    assert ".dict()" not in source


def test_bot_abort_warmup_only_calls_core_when_warmup_active():
    from bot_tts_scheduler import BotTTSScheduler

    aborted = []
    scheduler = BotTTSScheduler(
        config_provider=lambda: SimpleNamespace(),
        tts_provider=lambda: None,
        tts_prompt_manager_provider=lambda: None,
        speaker_provider=lambda: None,
        mic_provider=lambda: None,
        lang_provider=lambda: None,
    )
    scheduler.abort_core_tts_async = lambda: aborted.append("abort")

    # 预热未在执行时，不应无故向 Core 发送 /tts/abort
    scheduler.abort_warmup()
    assert scheduler._tts_warmup_abort.is_set()
    assert aborted == []

    # 预热正在执行且首次被中止时，应触发一次 /tts/abort
    scheduler._tts_warmup_abort.clear()
    scheduler._tts_warmup_active = True
    scheduler.abort_warmup()
    scheduler.abort_warmup()
    assert aborted == ["abort"]


# ---------------------------------------------------------------------------
# 2.2 Plain LLMSyncPipeline streaming + sentence-by-sentence TTS
# ---------------------------------------------------------------------------


def test_llm_pipeline_config_exposes_streaming_flags():
    cfg = LLMPipelineConfig(enable=True)
    assert cfg.stream_output is True
    assert cfg.stream_tts_by_sentence is True


def test_llm_sync_http_stream_predict_reads_ndjson_lines(monkeypatch):
    cfg = LLMPipelineConfig(
        enable=True,
        openai_format=False,
        stream_predict_url="http://127.0.0.1:11000/llm/stream-predict",
    )
    pipeline = LLMSyncPipeline(cfg)

    lines = [
        LLMPrediction(response="你好", history=[]).model_dump_json(),
        "",
        LLMPrediction(response="，世界！", history=[]).model_dump_json(),
    ]

    class _FakeStreamResp:
        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=True):
            return iter(lines)

    monkeypatch.setattr("pipeline.llm.llm_sync.requests.post", lambda **kwargs: _FakeStreamResp())
    preds = list(pipeline.stream_predict(LLMQuery(text="hi", history=[])))
    assert [p.response for p in preds] == ["你好", "，世界！"]


def test_bot_streaming_llm_splits_sentences_from_cumulative_chunks():
    from bot_tts_scheduler import BotTTSScheduler
    from bot_turn_orchestrator import BotTurnOrchestrator
    from services.tool_model_gateway import ModelToolGateway

    enqueued = []
    emitted = []
    committed = []
    prompts_by_emotion = {
        "happy": SimpleNamespace(sentiment="happy"),
        "Default": SimpleNamespace(sentiment="Default"),
    }

    tts_scheduler = SimpleNamespace(
        current_task_id=lambda: 0,
        cancel_pending=lambda: None,
        watch_round_end=lambda task_id: None,
        pop_stream_segments=BotTTSScheduler.pop_stream_segments,
        enqueue=lambda seg, prompt, emo: enqueued.append((seg, emo)) or True,
        select_prompt=lambda text: prompts_by_emotion["Default"],
        get_prompt=lambda emo: prompts_by_emotion.get(emo, prompts_by_emotion["Default"]),
    )
    vision_controller = SimpleNamespace(reset_auto_chat_timer=lambda: None)

    cumulative_chunks = [
        LLMPrediction(response="<emotion>hap", history=[]),
        LLMPrediction(response="<emotion>happy</emotion>你好", history=[]),
        LLMPrediction(response="<emotion>happy</emotion>你好！今天天气", history=[]),
        LLMPrediction(response="<emotion>happy</emotion>你好！今天天气真不错。", history=[]),
        LLMPrediction(response="<emotion>happy</emotion>你好！今天天气真不错。", history=[]),
    ]
    llm = SimpleNamespace(
        _is_openai_format=True,
        stream_predict=lambda query: iter(cumulative_chunks),
    )
    cfg = SimpleNamespace(
        character=SimpleNamespace(emotions=["Default", "happy"]),
        pipeline=SimpleNamespace(llm=SimpleNamespace(stream_tts_by_sentence=True)),
    )
    prompt_manager = SimpleNamespace(
        commit_history_update=lambda updater: committed.append(updater([])) or True,
        history_snapshot=lambda: [],
        injected_len=0,
    )
    orchestrator = BotTurnOrchestrator(
        config_provider=lambda: cfg,
        conversation_id_provider=lambda: "conv_test",
        next_turn_id=lambda: 1,
        llm_provider=lambda: llm,
        mllm_provider=lambda: None,
        mllm_cfg_provider=lambda: SimpleNamespace(enable=False),
        llm_prompt_manager_provider=lambda: prompt_manager,
        context_manager_provider=lambda: None,
        filter_provider=lambda: SimpleNamespace(filter=lambda text: False),
        memory_manager_provider=lambda: None,
        memory_retriever_provider=lambda: None,
        model_tool_gateway_provider=lambda: ModelToolGateway(ToolRuntime()),
        web_search_service_provider=lambda: None,
        skill_service_provider=lambda: None,
        tool_coordinator_provider=lambda: None,
        browser_service_provider=lambda: None,
        tts_scheduler=tts_scheduler,
        asr_controller=None,
        vision_controller=vision_controller,
        emit_event=lambda event: emitted.append(event),
    )

    query = LLMQuery(text="打招呼", history=[])
    streamed, prediction = orchestrator.emit_streaming_llm_prediction(
        query, "打招呼", skip_tts=False
    )

    assert streamed is True
    assert prediction is not None
    assert prediction.response == "你好！今天天气真不错。"
    assert getattr(prediction, "_streamed_tts", False) is True
    assert enqueued == [("你好！", "happy"), ("今天天气真不错。", "happy")]
    assert len(committed) == 1
    assert [m.content for m in committed[0]] == ["打招呼", "你好！今天天气真不错。"]
    assert any(getattr(ev, "skip_tts", None) is True for ev in emitted)


def test_bot_streaming_llm_falls_back_when_unstarted_stream_unsupported():
    from bot_turn_orchestrator import BotTurnOrchestrator

    orchestrator = BotTurnOrchestrator.__new__(BotTurnOrchestrator)
    orchestrator._config_provider = lambda: SimpleNamespace(
        pipeline=SimpleNamespace(llm=SimpleNamespace(stream_tts_by_sentence=True))
    )
    orchestrator._llm_provider = lambda: SimpleNamespace(
        _is_openai_format=False,
        stream_predict=lambda query: (_ for _ in ()).throw(NotImplementedError("no stream")),
    )
    orchestrator._filter_provider = lambda: SimpleNamespace(filter=lambda text: False)
    orchestrator._tts_scheduler = SimpleNamespace(
        current_task_id=lambda: 0,
        cancel_pending=lambda: None,
    )

    streamed, prediction = orchestrator.emit_streaming_llm_prediction(
        LLMQuery(text="hi", history=[]), "hi", skip_tts=False
    )
    assert streamed is False
    assert prediction is None


# ---------------------------------------------------------------------------
# 2.3 Hot reload: HarnessRecallService & MemoryRetriever update_config
# ---------------------------------------------------------------------------


def test_harness_recall_update_config_toggles_registration():
    runtime = ToolRuntime()
    state = SimpleNamespace(
        service=SimpleNamespace(
            harness=SimpleNamespace(enable=True, path="logs/harness.jsonl"),
            harness_recall=HarnessRecallConfig(enable=False),
            tool=SimpleNamespace(max_result_chars=4000),
        )
    )
    service = HarnessRecallService(runtime, lambda: state, project_root=_BOT_DIR)
    assert runtime.get("harness_recall") is None

    state.service.harness_recall = HarnessRecallConfig(enable=True)
    service.update_config()
    assert runtime.get("harness_recall") is not None

    state.service.harness.enable = False
    service.update_config()
    assert runtime.get("harness_recall") is None


def test_memory_retriever_update_config_updates_mode_and_budget():
    tmp_dir = _make_tmp_dir("mem-reload")
    try:
        core_path = tmp_dir / "core_memory.md"
        core_path.write_text(
            "[长期]:\n"
            "  - 用户喜欢猫\n\n"
            "[2026-11]:\n"
            "  - [18]:\n"
            "      - 10:00 第一条记录\n"
            "      - 11:00 第二条记录较长一些用于测试预算截断\n",
            encoding="utf-8",
        )
        retriever = MemoryRetriever(
            RetrievalConfig(mode="off"),
            data_dir=tmp_dir,
            core_path=core_path,
            injection_max_chars=1000,
        )
        assert retriever.resident().mode == "off"

        retriever.update_config(RetrievalConfig(mode="observe"), injection_max_chars=35)
        view = retriever.resident()
        assert retriever.config.mode == "observe"
        assert retriever.injection_max_chars == 35
        assert view.truncated_entries >= 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2.4 Core/starter.py plugin registry & dynamic import
# ---------------------------------------------------------------------------


def test_core_starter_registry_and_dynamic_loader(monkeypatch):
    # Core 与 Bot 均有 common/ 包：临时隔离加载 Core/starter.py，避免污染同进程的 Bot/common 导入。
    for key in list(sys.modules):
        if key == "common" or key.startswith("common."):
            monkeypatch.delitem(sys.modules, key, raising=False)
    monkeypatch.syspath_prepend(str(_CORE_DIR))
    spec_obj = importlib.util.spec_from_file_location("core_starter_under_test", _CORE_DIR / "starter.py")
    assert spec_obj is not None and spec_obj.loader is not None
    core_starter = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(core_starter)

    # 构造一个临时虚拟插件模块，验证无需修改 starter.py 即可注册加载
    mod_name = "test_fake_core_model_plugin"
    fake_mod = types.ModuleType(mod_name)

    class _FakeCfg:
        def __init__(self, endpoint: str, threads: int = 2):
            self.endpoint = endpoint
            self.threads = threads

    class _FakeModel:
        def __init__(self, config):
            self.config = config

    fake_mod.FakeModel = _FakeModel
    fake_mod.FakeConfig = _FakeCfg
    monkeypatch.setitem(sys.modules, mod_name, fake_mod)

    # 1) 同模块双类写法："module_path:ModelClass,ConfigClass"
    service_cfg = {
        "id": "custom/model-a",
        "registry": {"custom/model-a": f"{mod_name}:FakeModel,FakeConfig"},
        "config": {
            "custom/model-a": {"endpoint": "http://localhost:8000", "threads": 4},
        },
    }
    spec = core_starter.resolve_model_spec("LLM", "custom/model-a", service_cfg)
    model = core_starter.load_model_from_spec(spec, service_cfg["config"]["custom/model-a"])
    assert isinstance(model, _FakeModel)
    assert model.config.endpoint == "http://localhost:8000"
    assert model.config.threads == 4

    # 2) 内置项内联 entry 覆盖 + 过滤 entry 键
    inline_cfg = {
        "id": "custom/inline",
        "config": {
            "custom/inline": {
                "entry": f"{mod_name}:FakeModel,{mod_name}:FakeConfig",
                "endpoint": "http://localhost:9000",
            }
        },
    }
    spec_inline = core_starter.resolve_model_spec("ASR", "custom/inline", inline_cfg)
    model_inline = core_starter.load_model_from_spec(spec_inline, inline_cfg["config"]["custom/inline"])
    assert model_inline.config.endpoint == "http://localhost:9000"

    # 3) 未知 id 保持抛出 NameError
    with pytest.raises(NameError, match="No such model name"):
        core_starter.resolve_model_spec("LLM", "nonexistent/model", {})

    # 4) 现有内置 id 均在注册表中
    assert "Sherpa-ONNX-Streaming-Zipformer-ZH-INT8" in core_starter.MODEL_REGISTRY["ASR"]
    assert "ollama" in core_starter.MODEL_REGISTRY["LLM"]
    assert "GPT-SoVITS" in core_starter.MODEL_REGISTRY["TTS"]


# ---------------------------------------------------------------------------
# 2.5 Hygiene: master_name removed & no InstrumentationWarning
# ---------------------------------------------------------------------------


def test_bot_context_has_no_hardcoded_master_name():
    source = (_BOT_DIR / "framework" / "context.py").read_text(encoding="utf-8")
    assert "master_name" not in source
    assert "STEXxx" not in source


def test_stream_pipelines_emit_no_typeguard_instrumentation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import importlib
        import pipeline.mllm.mllm_sync as mllm_sync
        import pipeline.llm.llm_async as llm_async

        importlib.reload(mllm_sync)
        importlib.reload(llm_async)

    inst_warnings = [w for w in caught if "InstrumentationWarning" in w.category.__name__]
    assert inst_warnings == []
