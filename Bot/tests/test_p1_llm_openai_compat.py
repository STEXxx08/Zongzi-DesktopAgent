"""P1 回归：LLM 的 OpenAI 兼容路径不做模型名白名单。

背景：`llm_sync.py` 早期用 `if model_id == "..."` 逐个匹配 4 个在线模型，
名单外的兼容模型一律 `NotImplementedError`；同时 `stream_predict` 对
OpenAI 格式会把 base_url 当成 HTTP 端点去 POST（那里其实是 api.deepseek.com）。

本文件覆盖：
- 任意 model_id 都能走通 OpenAI 兼容路径（不再有白名单）
- 采样参数留空时不发送（交给服务端默认值）
- 流式走 SDK，不落回 HTTP 端点；流式结果与历史写回正确
- model_id 配置项接受任意字符串（不再被枚举拒绝）

运行：
    conda activate MyAI
    python -m pytest Bot/tests/test_p1_llm_openai_compat.py -v
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

from condata.pipeline.llm import Conversation, LLMQuery, RoleEnum  # noqa: E402

from pipeline.llm.config import LLMPipelineConfig  # noqa: E402
from pipeline.llm.llm_sync import LLMSyncPipeline  # noqa: E402


def _config(model_id: str = "some-brand-new-model", **overrides) -> LLMPipelineConfig:
    base = dict(
        enable=True,
        openai_format=True,
        predict_url="https://example.invalid/v1",
        model_id=model_id,
        api_key="test-key",
    )
    base.update(overrides)
    return LLMPipelineConfig(**base)


class _FakeUsage:
    """替身：模仿 openai SDK 的 CompletionUsage（真实对象带 model_dump()）。"""

    def __init__(self, prompt_tokens=11, completion_tokens=7, total_tokens=18):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

    def model_dump(self):
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


class _FakeCompletions:
    """替身：记录请求参数，返回构造好的响应。"""

    def __init__(self, chunks=None, content="你好，我是粽子。"):
        self.calls = []
        self._chunks = chunks or []
        self._content = content

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return iter(self._chunks)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))],
            usage=_FakeUsage(),
        )


def _pipeline_with_fake(monkeypatch, config, completions):
    pipeline = LLMSyncPipeline(config)
    monkeypatch.setattr(pipeline._remote, "client", SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    return pipeline


def _chunk(text, usage=None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=usage)


# ---------------------------------------------------------------------------


def test_config_accepts_arbitrary_model_id():
    """model_id 不再被枚举限制 —— 任何标识都应被接受。"""
    cfg = LLMPipelineConfig(enable=True, model_id="totally-unknown/model-xyz:latest")
    assert cfg.model_id == "totally-unknown/model-xyz:latest"


@pytest.mark.parametrize("model_id", ["deepseek-chat", "brand-new-llm-v9", "qwen3.8-omni-flash"])
def test_unknown_model_id_is_not_rejected(monkeypatch, model_id):
    """名单外的模型也必须走通（回归：早期会抛 NotImplementedError）。"""
    completions = _FakeCompletions()
    pipeline = _pipeline_with_fake(monkeypatch, _config(model_id), completions)

    prediction = pipeline.predict(LLMQuery(text="在吗", history=[]))

    assert prediction is not None
    assert prediction.response == "你好，我是粽子。"
    assert completions.calls[0]["model"] == model_id


def test_sampling_params_omitted_when_unset(monkeypatch):
    """temperature / max_tokens 留空时不发该参数，避免覆盖服务端默认值。"""
    completions = _FakeCompletions()
    pipeline = _pipeline_with_fake(monkeypatch, _config(), completions)

    pipeline.predict(LLMQuery(text="hi", history=[]))

    call = completions.calls[0]
    assert "temperature" not in call
    assert "max_tokens" not in call
    assert call["stream"] is False


def test_sampling_params_sent_when_configured(monkeypatch):
    completions = _FakeCompletions()
    pipeline = _pipeline_with_fake(
        monkeypatch, _config(temperature=0.6, max_tokens=512), completions
    )

    pipeline.predict(LLMQuery(text="hi", history=[]))

    call = completions.calls[0]
    assert call["temperature"] == 0.6
    assert call["max_tokens"] == 512


def test_predict_appends_history(monkeypatch):
    completions = _FakeCompletions(content="在的喵。")
    pipeline = _pipeline_with_fake(monkeypatch, _config(), completions)
    query = LLMQuery(text="在吗", history=[])

    prediction = pipeline.predict(query)

    assert [c.role for c in prediction.history] == [RoleEnum.user, RoleEnum.assistant]
    assert prediction.history[-1].content == "在的喵。"
    assert prediction.usage is not None


def test_stream_uses_sdk_and_accumulates(monkeypatch):
    """流式必须走 SDK，而不是 POST 到 base_url（回归：旧实现会这么干）。"""
    completions = _FakeCompletions(chunks=[_chunk("你"), _chunk("好"), _chunk("喵")])
    pipeline = _pipeline_with_fake(monkeypatch, _config(), completions)
    query = LLMQuery(text="打个招呼", history=[])

    chunks = list(pipeline.stream_predict(query))

    # 请求参数：stream=True 且带上了 model
    assert completions.calls[0]["stream"] is True
    assert completions.calls[0]["model"] == "some-brand-new-model"
    # 增量文本按累积方式给出
    assert [c.response for c in chunks[:3]] == ["你", "你好", "你好喵"]
    # 最后一帧带完整历史
    assert chunks[-1].history[-1].content == "你好喵"
    assert query.history[-1].content == "你好喵"


def test_stream_reports_usage_when_provider_sends_it(monkeypatch):
    """用量通常只在最后一个 chunk；有就带上，没有就留 None（不估算）。"""
    completions = _FakeCompletions(chunks=[_chunk("喵"), _chunk("！", usage=_FakeUsage())])
    pipeline = _pipeline_with_fake(monkeypatch, _config(), completions)

    chunks = list(pipeline.stream_predict(LLMQuery(text="hi", history=[])))

    assert chunks[-1].usage is not None
    assert chunks[-1].usage.get("total_tokens") == 18
    assert chunks[0].usage is None


def test_stream_without_content_does_not_pollute_history(monkeypatch):
    """服务端没吐字时不应写入历史，只记一条警告。"""
    completions = _FakeCompletions(chunks=[])
    pipeline = _pipeline_with_fake(monkeypatch, _config(), completions)
    query = LLMQuery(text="喂", history=[])

    chunks = list(pipeline.stream_predict(query))

    assert chunks == []
    assert query.history == []


def test_missing_base_url_raises_readable_error():
    with pytest.raises(ValueError, match="base_url"):
        LLMSyncPipeline(_config(predict_url=""))


def test_http_backend_path_untouched(monkeypatch):
    """openai_format=false 时不应构造远端客户端，也不应调用 SDK。"""
    cfg = _config(openai_format=False)
    pipeline = LLMSyncPipeline(cfg)

    assert pipeline._remote is None

    called = {}

    def _fake_super_predict(query, chunk_size=None):
        called["hit"] = True
        return None

    monkeypatch.setattr(
        "pipeline.base.base_sync.CommonModelPipeline.predict", lambda self, q: _fake_super_predict(q)
    )
    pipeline.predict(LLMQuery(text="hi", history=[]))

    assert called.get("hit") is True
