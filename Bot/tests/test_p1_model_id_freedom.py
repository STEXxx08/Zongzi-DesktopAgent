"""P1 回归：所有 pipeline 的 model_id 都接受任意字符串（不再有硬枚举白名单）。

背景：ASR / TTS / OCR / ImgCap / VLM 过去把 model_id 定义为枚举，
名单外的模型会在**启动时**被 pydantic 拒绝，导致"换成别的模型"必须先改代码。
这与"ASR / LLM / TTS 可任意组合"的设计目标冲突，现已全部放宽为 `str`。

判据不是"配置里有什么"，而是：
- 任意字符串能通过校验；
- 配置默认值仍然是原来那个（升级不改变现有行为）；
- 真正做分支判断的那两处（百度云 ASR / TTS）仍按字符串正确识别。

运行：
    conda activate MyAI
    python -m pytest Bot/tests/test_p1_model_id_freedom.py -v
"""

import sys
from pathlib import Path

import pytest

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

from pipeline.asr.config import (  # noqa: E402
    ASRPipelineConfig,
    KNOWN_ASR_MODEL_IDS,
    MODEL_ID_BAIDU as ASR_BAIDU,
    MODEL_ID_PARA,
)
from pipeline.imgcap.config import (  # noqa: E402
    ImgCapPipelineConfig,
    MODEL_ID_BLIP,
)
from pipeline.llm.config import LLMPipelineConfig  # noqa: E402
from pipeline.mllm.config import MLLMPipelineConfig  # noqa: E402
from pipeline.ocr.config import OCRPipelineConfig, MODEL_ID_PADDLE_OCR  # noqa: E402
from pipeline.tts.config import (  # noqa: E402
    TTSPipelineConfig,
    MODEL_ID_BAIDU as TTS_BAIDU,
    MODEL_ID_GPT_SOVITS,
)
from pipeline.vlm.config import VLMPipelineConfig, MODEL_ID_QWEN3_VL_2B  # noqa: E402

_ANY = "some-vendor/very-new-model-v9:latest"


@pytest.mark.parametrize(
    "config_cls, default",
    [
        (ASRPipelineConfig, MODEL_ID_PARA),
        (TTSPipelineConfig, MODEL_ID_GPT_SOVITS),
        (OCRPipelineConfig, MODEL_ID_PADDLE_OCR),
        (ImgCapPipelineConfig, MODEL_ID_BLIP),
        (VLMPipelineConfig, MODEL_ID_QWEN3_VL_2B),
        (MLLMPipelineConfig, "qwen3.7-plus"),
        (LLMPipelineConfig, "THUDM/GLM-4"),
    ],
)
def test_model_id_accepts_arbitrary_string(config_cls, default):
    cfg = config_cls(enable=True, model_id=_ANY)
    assert cfg.model_id == _ANY
    # 默认值必须保持原样：放宽校验不应改变既有行为
    assert config_cls(enable=True).model_id == default


def test_model_id_type_is_plain_str():
    """确认字段类型就是 str（回归：以前是 enum，填任意值会被拒）。"""
    assert ASRPipelineConfig.model_fields["model_id"].annotation is str
    assert TTSPipelineConfig.model_fields["model_id"].annotation is str
    assert OCRPipelineConfig.model_fields["model_id"].annotation is str
    assert ImgCapPipelineConfig.model_fields["model_id"].annotation is str
    assert VLMPipelineConfig.model_fields["model_id"].annotation is str


def test_known_ids_are_advertised_in_description():
    """常见取值应出现在字段说明里，供生成的 config.yaml 提示使用者。

    只看可读的标识片段（如 "BaiduASR"、"GPT-SoVITS"、"PaddleOCR"），
    不对长路径做整串匹配。
    """
    cases = [
        (ASRPipelineConfig, "BaiduASR"),
        (ASRPipelineConfig, "Sherpa-ONNX-Streaming-Zipformer-ZH-INT8"),
        (TTSPipelineConfig, "GPT-SoVITS"),
        (TTSPipelineConfig, "BaiduTTS"),
        (OCRPipelineConfig, "PaddleOCR"),
        (ImgCapPipelineConfig, "blip-image-captioning"),
        (VLMPipelineConfig, "Qwen3-VL-2B"),
    ]
    for cls, sample in cases:
        desc = cls.model_fields["model_id"].description or ""
        assert sample in desc, f"{cls.__name__} 的 model_id 说明里缺少 {sample}"


def test_unknown_asr_id_does_not_enable_baidu_client(monkeypatch):
    """回归：只有 model_id 恰好是百度云时才接管 predict，别的值走 HTTP 路径。

    直接拦截 `requests.post`，保证不发真实网络请求（本机可能正跑着 Core 服务）。
    """
    import requests

    from condata.pipeline.asr import ASRQuery

    from pipeline.asr.asr_sync import ASRSyncPipeline

    calls = []

    class _Stop(Exception):
        pass

    def _fake_post(url, **kwargs):
        calls.append(url)
        raise _Stop("已证明走的是 HTTP 路径")

    monkeypatch.setattr(requests, "post", _fake_post)

    pipeline = ASRSyncPipeline(ASRPipelineConfig(enable=True, model_id=_ANY))
    assert not hasattr(pipeline, "baidu"), "非百度云模型不应构造百度客户端"

    with pytest.raises(_Stop):
        pipeline.predict(ASRQuery(audio_path="dummy.wav"))

    assert calls and "asr/predict" in calls[0], f"未走 HTTP 路径：{calls}"


def test_baidu_ids_are_stable():
    """这两条是真实的功能分支判断，值不能随手改。"""
    assert ASR_BAIDU == "BaiduASR"
    assert TTS_BAIDU == "BaiduTTS"
