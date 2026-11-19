"""Phase 3 Pipeline / Services / Memory 精简与懒加载离线单测。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))


def test_memory_package_lazy_exports_memory_manager():
    code = (
        "import sys; "
        "import memory; "
        "from memory.config import MemoryConfig; "
        "assert 'memory.manager' not in sys.modules; "
        "assert 'openai' not in sys.modules; "
        "cls = memory.MemoryManager; "
        "assert cls.__name__ == 'MemoryManager'; "
        "assert 'memory.manager' in sys.modules"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_BOT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout

    import memory

    assert "MemoryManager" in dir(memory)
    with pytest.raises(AttributeError):
        getattr(memory, "DoesNotExist")


def test_config_loader_helpers_and_service_loaders(tmp_path):
    from services.config_loader import (
        dump_pydantic_model,
        load_service_subconfig,
        load_yaml_section,
    )
    from services.intent_router.config import IntentRouterConfig, load_intent_router_config
    from services.web_search.config import WebSearchMode, WebSearchServiceConfig, load_web_search_config

    assert dump_pydantic_model(None) is None
    assert dump_pydantic_model({"a": 1}) == {"a": 1}
    cfg = IntentRouterConfig(enable=True, confidence_threshold=0.8)
    dumped = dump_pydantic_model(cfg)
    assert dumped["enable"] is True and dumped["confidence_threshold"] == 0.8

    plain_obj = SimpleNamespace(x=42)
    assert dump_pydantic_model(plain_obj) is None
    assert dump_pydantic_model(plain_obj, allow_dict_attr=True) == {"x": 42}

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "service:\n  intent_router:\n    enable: true\n    confidence_threshold: 0.9\n",
        encoding="utf-8",
    )
    assert load_yaml_section(("service", "intent_router"), config_path=yaml_path)["enable"] is True
    loaded = load_service_subconfig("intent_router", IntentRouterConfig, config_path=yaml_path)
    assert loaded.enable is True and loaded.confidence_threshold == 0.9

    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(
        "service:\n  intent_router:\n    confidence_threshold: 999\n",
        encoding="utf-8",
    )
    fallback = load_service_subconfig("intent_router", IntentRouterConfig, config_path=bad_yaml)
    assert fallback == IntentRouterConfig()

    root_cfg = SimpleNamespace(
        service=SimpleNamespace(
            intent_router=IntentRouterConfig(enable=True),
            web_search=WebSearchServiceConfig(mode=WebSearchMode.LOCAL_ONLY),
        )
    )
    assert load_intent_router_config(root_cfg).enable is True
    assert load_web_search_config(root_cfg).mode == WebSearchMode.LOCAL_ONLY


def test_intent_router_normalize_and_extract_query():
    from services.embedding.bge_onnx import normalize
    from services.intent_router.config import IntentDefinition
    from services.intent_router.router import BGEIntentRouter

    arr = np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32)
    assert np.allclose(BGEIntentRouter._normalize(arr), normalize(arr))

    intent = IntentDefinition(
        name="weather",
        action="web_tool",
        tool="weather",
        examples=["查一下北京天气"],
        strip_patterns=[r"天气"],
        default_query="上海",
    )
    assert BGEIntentRouter._extract_query("请帮我查一下北京天气怎么样", intent) == "北京"
    assert BGEIntentRouter._extract_query("天气", intent) == "上海"


def test_turnsense_softmax_delegates_to_softmax_axis():
    from pipeline.asr.turnsense import TurnSenseDetector

    logits = np.array([1.2, -0.5, 0.8], dtype=np.float32)
    p1 = TurnSenseDetector._softmax(logits)
    p2 = TurnSenseDetector._softmax_axis(logits, axis=-1)
    assert np.allclose(p1, p2)
    assert np.isclose(np.sum(p1), 1.0)


def test_core_web_search_provider_search_and_weather_via_post_prediction(monkeypatch):
    from condata.pipeline.web_search import (
        WebSearchPrediction,
        WebSearchResultData,
        WeatherCurrentData,
        WeatherPrediction,
    )
    from services.web_search.config import WebSearchServiceConfig
    from services.web_search.service import CoreWebSearchProvider, WebSearchService

    provider = CoreWebSearchProvider(WebSearchServiceConfig(), max_workers=1)

    class _Resp:
        def __init__(self, payload: bytes):
            self.content = payload

        def raise_for_status(self):
            return None

    calls = []

    def _fake_post(endpoint, json=None, timeout=None):
        calls.append((endpoint, json, timeout))
        if endpoint.endswith("/weather"):
            return _Resp(
                WeatherPrediction(
                    query=json["location"],
                    location="北京",
                    timezone="Asia/Shanghai",
                    current=WeatherCurrentData(
                        time="2026-11-19T12:00",
                        temperature=12.5,
                        apparent_temperature=10.0,
                        relative_humidity=40,
                        precipitation=0.0,
                        wind_speed=8.0,
                        weather_code=0,
                    ),
                ).model_dump_json().encode("utf-8")
            )
        return _Resp(
            WebSearchPrediction(
                query=json["query"],
                results=[WebSearchResultData(title="测试", url="https://example.com", snippet="摘要")],
            ).model_dump_json().encode("utf-8")
        )

    monkeypatch.setattr(provider._session, "post", _fake_post)

    search_res = provider.search("Python")
    assert len(search_res) == 1 and search_res[0].title == "测试"

    weather_res = provider.weather("北京")
    assert len(weather_res) == 1 and "北京天气" in weather_res[0].title
    assert "晴" in weather_res[0].snippet

    assert WebSearchService._remove_keyword_and_politeness("请帮我查一下南京天气怎么样", "天气") == "南京"

    ws = WebSearchService(WebSearchServiceConfig(), provider=provider)
    visible, directive = ws.parse_model_response('先查一下。<web_tool>{"tool":"search","query":"Python"}</web_tool>')
    assert visible == "先查一下。"
    assert directive is not None and directive.tool == "search" and directive.query == "Python"


def test_tool_model_gateway_cached_regex_and_stream_filtering():
    from services.tool_model_gateway import ModelToolGateway
    from services.tool_runtime import ToolDefinition, ToolRuntime

    runtime = ToolRuntime(max_workers=1)
    runtime.register(
        ToolDefinition(
            name="ping",
            description="ping",
            input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
            model_invocable=True,
            handler=lambda args: args["msg"],
        )
    )
    gw = ModelToolGateway(runtime, marker="custom_tool")

    visible, call = gw.parse_model_response('好的<custom_tool>{"name":"ping","arguments":{"msg":"ok"}}</custom_tool>')
    assert visible == "好的"
    assert call is not None and call.name == "ping" and call.arguments == {"msg": "ok"}

    # 代码块包裹与字符串内换行容错
    visible2, call2 = gw.parse_model_response('```json\n<custom_tool>{"name":"ping","arguments":{"msg":"line1\nline2"}}</custom_tool>\n```')
    assert call2 is not None and call2.arguments["msg"] == "line1\nline2"

    # 半截标签流式暂存与 flush 丢弃
    vis, rem = gw.extract_stream_visible_text("你好<custom_to", flush=False, markers=("custom_tool",))
    assert vis == "你好" and rem == "<custom_to"

    vis_bad, call_bad = gw.parse_model_response("前面文本<custom_tool>broken")
    assert call_bad is None and vis_bad == "前面文本" and gw.last_error is not None


def test_sandbox_and_skill_precompiled_regexes(tmp_path):
    from services.sandbox.config import SandboxServiceConfig
    from services.sandbox.filesystem import RestrictedFilesystem
    from services.skill_framework.config import SkillsServiceConfig
    from services.skill_framework.service import SkillService
    from services.tool_runtime import ToolRuntime

    fs = RestrictedFilesystem(SandboxServiceConfig(root=str(tmp_path / "sb")), project_root=tmp_path)
    with pytest.raises(ValueError):
        fs._path("CON")
    with pytest.raises(ValueError):
        fs._path("nul.txt")

    skill_dir = tmp_path / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: A   demo\n  skill\n---\n\nBody\n",
        encoding="utf-8",
    )
    svc = SkillService(
        SkillsServiceConfig(roots=["skills"], create_root="skills"),
        ToolRuntime(max_workers=1),
        project_root=tmp_path,
    )
    assert SkillService._normalize_description("a \n b\t c") == "a b c"
    vis, directive = svc.parse_model_response('加载中<skill_tool>{"name":"demo-skill"}</skill_tool>')
    assert vis == "加载中" and directive is not None and directive.name == "demo-skill"
    rem, loaded = svc.parse_user_invocation("/demo-skill hello")
    assert rem == "hello" and loaded.name == "demo-skill"


def test_pipeline_multipart_closes_opened_file_handles(tmp_path, monkeypatch):
    from condata.pipeline.asr import ASRPrediction, ASRQuery
    from condata.pipeline.ocr import OCRPrediction, OCRQuery
    from pipeline.asr.asr_async import ASRAsyncPipeline
    from pipeline.asr.asr_sync import ASRSyncPipeline
    from pipeline.asr.config import ASRPipelineConfig
    from pipeline.ocr.config import OCRPipelineConfig
    from pipeline.ocr.ocr_async import OCRAsyncPipeline
    from pipeline.ocr.ocr_sync import OCRSyncPipeline

    img_file = tmp_path / "sample.png"
    img_file.write_bytes(b"fake-png")
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"RIFF")

    opened_files = []

    def _fake_post(url=None, files=None, data=None, json=None, timeout=None):
        if files:
            for val in files.values():
                if hasattr(val, "closed"):
                    assert not val.closed
                    opened_files.append(val)
        content = (
            OCRPrediction(region_results=[]).model_dump_json().encode("utf-8")
            if "ocr" in (url or "")
            else ASRPrediction(transcript="ok").model_dump_json().encode("utf-8")
        )
        return SimpleNamespace(content=content, raise_for_status=lambda: None)

    monkeypatch.setattr("pipeline.base.base_sync.requests.post", _fake_post)
    monkeypatch.setattr("pipeline.asr.asr_sync.requests.post", _fake_post)

    ocr = OCRSyncPipeline(OCRPipelineConfig(enable=True))
    ocr_res = ocr.predict(OCRQuery(img_path=str(img_file)))
    assert ocr_res is not None
    assert len(opened_files) == 1 and opened_files[0].closed

    asr = ASRSyncPipeline(ASRPipelineConfig(enable=True))
    asr_res = asr.predict(ASRQuery(audio_path=str(audio_file)))
    assert asr_res is not None and asr_res.transcript == "ok"
    assert len(opened_files) == 2 and opened_files[1].closed

    # Async multipart closes file handles too
    async_handles = []

    class _FakeAsyncResp:
        def __init__(self, payload: str):
            self._payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self, encoding="utf8", loads=None):
            return loads(self._payload)

    class _FakeSession:
        def post(self, endpoint, data=None):
            for val in (data or {}).values():
                if hasattr(val, "closed"):
                    assert not val.closed
                    async_handles.append(val)
            payload = (
                OCRPrediction(region_results=[]).model_dump_json()
                if "ocr" in endpoint
                else ASRPrediction(transcript="async-ok").model_dump_json()
            )
            return _FakeAsyncResp(payload)

    async def _run_async():
        ocr_async = OCRAsyncPipeline(OCRPipelineConfig(enable=True))
        ocr_async._session = _FakeSession()
        await ocr_async.predict(OCRQuery(img_path=str(img_file)))

        asr_async = ASRAsyncPipeline(ASRPipelineConfig(enable=True))
        asr_async._session = _FakeSession()
        res = await asr_async.predict(ASRQuery(audio_path=str(audio_file)))
        assert res.transcript == "async-ok"

    asyncio.run(_run_async())
    assert len(async_handles) == 2 and all(h.closed for h in async_handles)


def test_pipeline_modules_avoid_eager_heavy_imports():
    code = "\n".join([
        "import sys",
        "import pipeline.asr.asr_sync",
        "import pipeline.tts.tts_sync",
        "import pipeline.vlm.vlm_sync",
        "import pipeline.mllm.mllm_screenshot_manager",
        "for mod in ('librosa', 'soundfile', 'PIL.Image', 'pipeline.asr.baidu_asr', 'pipeline.tts.baidu_tts'):",
        "    assert mod not in sys.modules, f'unexpected eager import: {mod}'",
    ])
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_BOT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
