"""检索索引的落盘与校验（jsonl + npy + meta 三件套）。

磁盘布局（方案 §4）：

```
index/
  retrieval_index.jsonl   # 每行一个条目级 chunk，顺序与 npy 行序严格一致
  retrieval_index.npy     # (N, dim) float32，已 L2 归一化
  index_meta.json         # 模型/dim/chunk 数/core_memory sha256/构建时间/schema 版本
```

启动校验：`sha256(core_memory.md)` 与 `meta.core_memory_sha256` 一致 **且**
`meta.chunks` 与 jsonl 行数一致 → 可直接 `np.load`；否则重建。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np

INDEX_DIR_NAME = "index"
INDEX_JSONL_NAME = "retrieval_index.jsonl"
INDEX_NPY_NAME = "retrieval_index.npy"
INDEX_META_NAME = "index_meta.json"

INDEX_SCHEMA = 1
INDEX_BUILDER = "memory-index/1"

# 索引规模告警阈值（方案 §12.6：>5 万条 ≈ 100 MB 时再评估向量库）
LARGE_INDEX_WARN_CHUNKS = 50_000


class MemoryIndexError(RuntimeError):
    """索引不可用（缺失、损坏或与档案不一致）。"""


@dataclass(frozen=True)
class IndexMeta:
    schema: int
    model: str
    onnx: str
    dim: int
    chunks: int
    core_memory_sha256: str
    built_at: str
    builder: str = INDEX_BUILDER

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping) -> "IndexMeta":
        return cls(
            schema=int(data.get("schema", 0)),
            model=str(data.get("model", "")),
            onnx=str(data.get("onnx", "")),
            dim=int(data.get("dim", 0)),
            chunks=int(data.get("chunks", -1)),
            core_memory_sha256=str(data.get("core_memory_sha256", "")),
            built_at=str(data.get("built_at", "")),
            builder=str(data.get("builder", INDEX_BUILDER)),
        )


def core_memory_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def index_dir(data_dir: Path) -> Path:
    return Path(data_dir) / INDEX_DIR_NAME


def index_paths(data_dir: Path) -> tuple[Path, Path, Path]:
    directory = index_dir(data_dir)
    return (
        directory / INDEX_JSONL_NAME,
        directory / INDEX_NPY_NAME,
        directory / INDEX_META_NAME,
    )


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def save_index(
    data_dir: Path,
    chunks: Sequence[Mapping],
    vectors: np.ndarray,
    meta: IndexMeta,
) -> None:
    """原子写入三件套。行序必须与 vectors 行序一致。"""
    jsonl_path, npy_path, meta_path = index_paths(data_dir)
    lines = "".join(json.dumps(dict(chunk), ensure_ascii=False) + "\n" for chunk in chunks)
    payload = lines.encode("utf-8")

    # 目录必须先建好：下面的临时文件是直接建在该目录里的。
    npy_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = tempfile.NamedTemporaryFile(
        dir=str(npy_path.parent), prefix=f".{npy_path.name}.", suffix=".tmp", delete=False
    )
    buffer.close()
    try:
        # np.save(文件名) 会自动补 .npy 后缀，这里传文件对象避免被改名。
        with open(buffer.name, "wb") as file:
            np.save(file, np.asarray(vectors, dtype=np.float32), allow_pickle=False)
            file.flush()
            os.fsync(file.fileno())
        with open(buffer.name, "rb") as file:
            npy_bytes = file.read()
    finally:
        try:
            os.unlink(buffer.name)
        except OSError:
            pass

    _atomic_write_bytes(jsonl_path, payload)
    _atomic_write_bytes(npy_path, npy_bytes)
    _atomic_write_bytes(
        meta_path,
        json.dumps(meta.to_dict(), ensure_ascii=False, indent=2).encode("utf-8"),
    )


def load_index(data_dir: Path) -> tuple[list[dict], np.ndarray, IndexMeta]:
    """读取三件套；任何不一致都抛 `MemoryIndexError`。"""
    jsonl_path, npy_path, meta_path = index_paths(data_dir)
    if not (jsonl_path.is_file() and npy_path.is_file() and meta_path.is_file()):
        raise MemoryIndexError("索引文件缺失")

    try:
        meta = IndexMeta.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都视为索引损坏
        raise MemoryIndexError(f"index_meta.json 不可解析：{exc}") from exc

    if meta.schema != INDEX_SCHEMA:
        raise MemoryIndexError(f"索引 schema 版本不匹配：{meta.schema} != {INDEX_SCHEMA}")

    chunks: list[dict] = []
    try:
        with jsonl_path.open("r", encoding="utf-8") as file:
            for line_no, line in enumerate(file, start=1):
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("chunk 必须是 JSON 对象")
                chunks.append(payload)
    except Exception as exc:  # noqa: BLE001
        raise MemoryIndexError(f"retrieval_index.jsonl 解析失败：{exc}") from exc

    if meta.chunks != len(chunks):
        raise MemoryIndexError(
            f"jsonl 行数({len(chunks)})与 meta.chunks({meta.chunks})不一致"
        )

    try:
        vectors = np.load(npy_path, allow_pickle=False)
    except Exception as exc:  # noqa: BLE001
        raise MemoryIndexError(f"retrieval_index.npy 读取失败：{exc}") from exc

    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim != 2:
        raise MemoryIndexError(f"向量矩阵维度异常：{vectors.shape}")
    if vectors.shape[0] != len(chunks):
        raise MemoryIndexError(
            f"向量行数({vectors.shape[0]})与 chunk 数({len(chunks)})不一致"
        )
    if meta.dim and vectors.shape[1] != meta.dim:
        raise MemoryIndexError(f"向量维度({vectors.shape[1]})与 meta.dim({meta.dim})不一致")

    return chunks, vectors, meta


def index_is_valid(data_dir: Path, *, core_text: str) -> tuple[bool, str]:
    """校验磁盘索引是否与当前档案一致；返回 (是否可用, 原因)。"""
    jsonl_path, _, meta_path = index_paths(data_dir)
    if not (jsonl_path.is_file() and meta_path.is_file()):
        return False, "索引文件缺失"
    try:
        _, _, meta = load_index(data_dir)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    expected = core_memory_sha256(core_text)
    if meta.core_memory_sha256 != expected:
        return False, "core_memory 已变化（sha256 不匹配）"
    return True, "ok"


def build_meta(
    *,
    chunks: Iterable[Mapping],
    dim: int,
    core_text: str,
    model: str,
    onnx: str,
    built_at: Optional[str] = None,
) -> IndexMeta:
    chunk_list = list(chunks)
    return IndexMeta(
        schema=INDEX_SCHEMA,
        model=model,
        onnx=onnx,
        dim=int(dim),
        chunks=len(chunk_list),
        core_memory_sha256=core_memory_sha256(core_text),
        built_at=built_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        builder=INDEX_BUILDER,
    )
