"""共享的 BGE 中文句向量（ONNX，CPU）与进程级 session 单例。

自 `services/intent_router/router.py` 抽取。语义意图路由与记忆检索**必须**共用
同一份模型与同一个 onnxruntime session：各加载一份会白白多占 23.7 MB 权重和
一份图优化开销（方案 §12.3）。

这里刻意**不把 `max_length` 放进单例键**：tokenizer 的截断长度是按调用传参的，
因此 `max_length` 不同的两个使用方（意图路由 64 / 记忆检索 128）也能复用同一 session。

中文路径坑：本仓库路径含中文时，onnxruntime 拼装错误信息会退化成
`UnicodeDecodeError`，真实原因被完全掩盖，所以加载前先自己检查外置权重，
加载时再翻译异常（方案 §12.1 / §12.2）。
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# ONNX 允许把权重放在模型文件外的 *.onnx_data 里（optimum 导出默认这么做）。
# 文件名字符串在 protobuf 里是裸存的，因此可以直接从字节里扫出来。
_EXTERNAL_DATA_PATTERN = re.compile(rb"[\w.\-]{1,128}\.onnx_data")


def resolve_model_dir(model_path: str | Path) -> Path:
    """解析模型目录：相对路径先按启动目录找，找不到再按 Bot 项目根找。"""
    directory = Path(model_path)
    if directory.is_absolute() or directory.exists():
        return directory
    candidate = Path(__file__).resolve().parents[2] / directory
    return candidate if candidate.exists() else directory


def missing_external_data(model_file: Path) -> list[str]:
    """列出模型引用、但同目录下并不存在的 *.onnx_data 外置权重文件。

    onnxruntime 找不到外置权重时抛出的并不是"文件不存在"，而是拼装错误信息
    时按 UTF-8 解码非 ASCII 路径失败产生的 UnicodeDecodeError，真实原因被完全
    掩盖。所以这里先自己检查一遍，把缺文件的情况翻译成人话。
    """
    try:
        payload = Path(model_file).read_bytes()
    except OSError:
        return []
    names = {
        match.decode("ascii", "ignore")
        for match in _EXTERNAL_DATA_PATTERN.findall(payload)
    }
    parent = Path(model_file).parent
    return sorted(name for name in names if not (parent / name).exists())


@dataclass(frozen=True)
class EmbedderSpec:
    """决定能否复用同一个 session 的键：模型文件、providers 与线程数。"""

    model_path: str = "models/bge-small-zh-v1.5-onnx"
    onnx_model: str = "onnx/model_quantized.onnx"
    providers: tuple[str, ...] = ("CPUExecutionProvider",)
    intra_op_threads: int = 1
    inter_op_threads: int = 1

    @classmethod
    def from_config(cls, config: Any) -> "EmbedderSpec":
        providers = getattr(config, "providers", None) or ["CPUExecutionProvider"]
        return cls(
            model_path=str(getattr(config, "model_path", cls.model_path)),
            onnx_model=str(getattr(config, "onnx_model", cls.onnx_model)),
            providers=tuple(str(item) for item in providers),
            intra_op_threads=int(getattr(config, "intra_op_threads", 1)),
            inter_op_threads=int(getattr(config, "inter_op_threads", 1)),
        )


class BGEOnnxEmbedder:
    """BGE ONNX 句向量。输出已 L2 归一化，余弦相似度即内积。"""

    def __init__(self, spec: EmbedderSpec, *, max_length: int = 128):
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "BGE embedder requires onnxruntime and transformers"
            ) from exc

        self.spec = spec
        self.default_max_length = int(max_length)
        model_dir = resolve_model_dir(spec.model_path)
        model_file = model_dir / spec.onnx_model
        if not model_file.exists():
            raise FileNotFoundError(f"BGE ONNX model not found: {model_file}")
        absent_weights = missing_external_data(model_file)
        if absent_weights:
            raise FileNotFoundError(
                f"BGE ONNX model {model_file.name} needs external weight file(s) "
                f"{', '.join(absent_weights)} next to it in {model_file.parent}; only "
                f"{model_file.name} was downloaded (see services/intent_router/README.md)"
            )

        options = ort.SessionOptions()
        options.intra_op_num_threads = spec.intra_op_threads
        options.inter_op_num_threads = spec.inter_op_threads
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            self.session = ort.InferenceSession(
                str(model_file),
                sess_options=options,
                providers=list(spec.providers),
            )
        except UnicodeDecodeError as exc:
            # onnxruntime 在 Windows 上拼装错误信息时按 UTF-8 解码 ANSI 路径，
            # 只要模型路径含中文，真实错误就会变成这个解码异常。
            raise RuntimeError(
                f"onnxruntime could not load {model_file}; the underlying error is masked by "
                f"a path-encoding bug in onnxruntime (non-ASCII path or unreadable model "
                f"directory): {exc}"
            ) from exc
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(model_dir), local_files_only=True
        )
        self.input_names = {item.name for item in self.session.get_inputs()}
        self._call_lock = threading.Lock()

    @property
    def dimension(self) -> int:
        for output in self.session.get_outputs():
            shape = getattr(output, "shape", None) or []
            if len(shape) == 3 and isinstance(shape[-1], int):
                return int(shape[-1])
        return 0

    def __call__(
        self,
        texts: Sequence[str] | str,
        *,
        max_length: int | None = None,
    ) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        texts = list(texts)
        if not texts:
            return np.zeros((0, 512), dtype=np.float32)
        # 整段串行化：分词器（HF fast tokenizer）内部是 Rust 的 RefCell，
        # 并发 encode 会直接抛 "Already borrowed"；而意图路由与记忆检索是两个线程
        # （bot 里 intent_router.initialize 与索引后台重建会同时跑）。
        # session.run 本身线程安全，但单次只有 3.6 ms，不值得为并发冒风险。
        with self._call_lock:
            encoded = self.tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=int(max_length or self.default_max_length),
                return_tensors="np",
            )
            inputs = {
                name: np.asarray(value, dtype=np.int64)
                for name, value in encoded.items()
                if name in self.input_names
            }
            output = self.session.run(None, inputs)[0]
            embeddings = output[:, 0, :] if output.ndim == 3 else output
            return normalize(np.asarray(embeddings, dtype=np.float32))


def normalize(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if values.ndim == 1:
        values = values[None, :]
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return values / np.maximum(norms, 1e-12)


_EMBEDDER_LOCK = threading.Lock()
_EMBEDDERS: dict[EmbedderSpec, BGEOnnxEmbedder] = {}


def get_shared_embedder(spec: EmbedderSpec, *, max_length: int = 128) -> BGEOnnxEmbedder:
    """按 spec 取进程级共享的 embedder；同一 spec 只加载一次。"""
    with _EMBEDDER_LOCK:
        embedder = _EMBEDDERS.get(spec)
        if embedder is None:
            embedder = BGEOnnxEmbedder(spec, max_length=max_length)
            _EMBEDDERS[spec] = embedder
            logger.info(
                "BGE embedder loaded: model=%s, providers=%s",
                spec.model_path,
                ",".join(spec.providers),
            )
        return embedder


def shared_embedder_count() -> int:
    """已加载的 session 数（用于自检"没有双份 session"）。"""
    with _EMBEDDER_LOCK:
        return len(_EMBEDDERS)


def reset_shared_embedders() -> None:
    """仅供测试：清空单例注册表（不会释放已加载的 session）。"""
    with _EMBEDDER_LOCK:
        _EMBEDDERS.clear()


def describe_load_error(spec: EmbedderSpec, error: BaseException) -> str:
    """把加载失败翻译成可操作的人话（供降级警告使用）。"""
    model_dir = resolve_model_dir(spec.model_path)
    model_file = model_dir / spec.onnx_model
    return (
        f"BGE 向量模型不可用（{model_file}）：{error}。"
        "请确认 onnxruntime/transformers 已安装，且 onnx/model_quantized.onnx 与其"
        "同目录的 .onnx_data 外置权重（约 23.7 MB）都存在——缺外置权重时 onnxruntime "
        "只会抛出难懂的路径解码错误。"
    )
