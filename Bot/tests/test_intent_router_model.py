"""BGE 意图路由的模型文件自检。

覆盖外置权重（*.onnx_data）缺失的检测：onnxruntime 在这种情况下抛出的是
路径解码失败产生的 UnicodeDecodeError，真实原因完全看不出来，所以加载前必须先
自己检查一遍。

    conda activate MyAI
    python -m pytest Bot/tests/test_intent_router_model.py -v
"""

import sys
from pathlib import Path

import pytest

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

_DEFAULT_MODEL_DIR = _BOT_DIR / "models" / "bge-small-zh-v1.5-onnx"


def _tmp(label: str) -> Path:
    """临时目录放在仓库根 .temp 下（pytest 的 tmp_path 建在系统临时目录，本机不可写）。"""
    import uuid

    root = _BOT_DIR.parent / ".temp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"intent-router-{label}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tmp_path():
    return _tmp("case")


def _fake_onnx(path: Path, external_name: str | None) -> Path:
    """写一个最小的假 ONNX：protobuf 里外置权重的文件名是裸存的，正则扫得到。"""
    payload = b"\x08\x07\x12\x04test"
    if external_name is not None:
        # 真实文件里 protobuf 的长度前缀（非 \w 字节）把字段名和取值隔开
        payload += b"\x00location\x00" + external_name.encode("ascii")
    path.write_bytes(payload)
    return path


def test_missing_external_data_is_detected(tmp_path):
    from services.intent_router.router import missing_external_data

    model = _fake_onnx(tmp_path / "model_quantized.onnx", "model_quantized.onnx_data")
    assert missing_external_data(model) == ["model_quantized.onnx_data"]

    (tmp_path / "model_quantized.onnx_data").write_bytes(b"weights")
    assert missing_external_data(model) == []


def test_self_contained_model_reports_nothing(tmp_path):
    from services.intent_router.router import missing_external_data

    assert missing_external_data(_fake_onnx(tmp_path / "model.onnx", None)) == []


def test_missing_file_is_not_reported_as_broken(tmp_path):
    from services.intent_router.router import missing_external_data

    assert missing_external_data(tmp_path / "nope.onnx") == []


def test_resolve_model_dir_prefers_working_directory(tmp_path, monkeypatch):
    from services.intent_router.router import resolve_model_dir

    (tmp_path / "models" / "demo").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert resolve_model_dir("models/demo") == Path("models/demo")


def test_resolve_model_dir_falls_back_to_bot_root(tmp_path, monkeypatch):
    from services.intent_router.router import resolve_model_dir

    if not _DEFAULT_MODEL_DIR.is_dir():
        pytest.skip("本机没有下载 BGE 模型目录，跳过（见 services/intent_router/README.md）")
    monkeypatch.chdir(tmp_path)
    assert resolve_model_dir("models/bge-small-zh-v1.5-onnx") == _DEFAULT_MODEL_DIR


def test_shipped_model_has_its_external_weights():
    """本机模型目录要么没下载，要么必须自带 onnx_data——半个模型比没有更坑。"""
    from services.intent_router.router import missing_external_data

    model = _DEFAULT_MODEL_DIR / "onnx" / "model_quantized.onnx"
    if not model.is_file():
        pytest.skip("本机没有下载 BGE 模型，跳过（见 services/intent_router/README.md）")
    assert missing_external_data(model) == []
