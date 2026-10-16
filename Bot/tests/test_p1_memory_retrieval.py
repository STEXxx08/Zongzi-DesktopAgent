"""P1 记忆检索（RAG）单测。

覆盖《记忆检索实施方案.md》§10.2 的断言表：

- 解析器（含 `[长期]` 结构化、`[标记]` 定位）与两级削减单位（天 / 条目）
- 窗口选择器：30 天 / 100 条双边界、`min_recent_days` 保底、日期块不连续
- `injection_max_chars` 恒等式超限、三态注入矩阵、`observe` 与 `off` 逐字节一致
- 条目级分块、索引落盘与校验、降级阶梯（模型坏 / 索引坏 / 编码异常）
- 确定性 stub embedder 下的检索排序、`min_score`、同日扩展、随机触发与稀疏概率
- `[标记]` 剥离/拼回逐字节一致、合并 prompt 不含"允许删除"
- `ContextManager` 的 MEMORY 块与 `neutralize_markers`

全部离线可跑，不写真实记忆目录：

    python -m pytest Bot/tests/test_p1_memory_retrieval.py -v
"""

import hashlib
import json
import logging
import random
import sys
import threading
import time
import uuid
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
_BOT_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _BOT_DIR.parent
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

from memory import index_store  # noqa: E402
from memory.config import (  # noqa: E402
    MemoryConfig,
    RetrievalConfig,
    normalize_retrieval_config,
)
from memory.core_memory_parser import (  # noqa: E402
    parse_core_memory,
    splice_pinned_segment,
    split_pinned_segment,
)
from memory.retrieval import (  # noqa: E402
    MEMORY_BLOCK_HEADER,
    MODE_OBSERVE,
    MODE_OFF,
    MODE_ON,
    RANDOM_BLOCK_HEADER,
    RETRIEVAL_BLOCK_HEADER,
    TRUNCATED_NOTICE,
    IDENTITY_OVER_BUDGET_NOTICE,
    MemoryRetriever,
    RetrievalUnavailable,
    apply_char_limit,
    entry_chars,
    identity_chars,
    select_resident_days,
)

_LIVE_ARCHIVE = _BOT_DIR / "memory" / "data" / "core_memory.md"
# 2026-10-15 定稿快照的 sha256：档案是**只读 fixture**，一旦它长过（真实运行会），
# 与定稿数字对不上的用例就跳过而不是误报失败。
_LIVE_ARCHIVE_SHA256 = "0d8e547e2efb816340390ed98e7564f4e82263bd4219d2aa6184c4bba994e337"

NOW = datetime(2026, 10, 15, 21, 30, 0)
TODAY = NOW.date()


# --------------------------------------------------------------------------- 临时目录与 fixture


def _test_tmp_root() -> Path:
    for candidate in (Path(__file__).resolve().parents[2] / ".temp", _BOT_DIR / ".temp"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / f".probe-{uuid.uuid4().hex[:6]}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            continue
        return candidate
    raise RuntimeError("找不到可写的测试临时目录")


@pytest.fixture
def tmp_dir():
    # 不要用 pytest 的 tmp_path / tempfile.mkdtemp：它们按 0o700 建目录，
    # 在本机沙箱下新建目录里写文件会被拒绝。
    path = _test_tmp_root() / f"mem-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _live_archive_text() -> str:
    if not _LIVE_ARCHIVE.is_file():
        pytest.skip("本机没有 Bot/memory/data/core_memory.md，跳过真实档案 fixture")
    return _LIVE_ARCHIVE.read_text(encoding="utf-8").strip()


def _archive(
    days: list[tuple[str, list[str]]],
    *,
    long_term: list[str] | None = None,
    pinned: list[str] | None = None,
) -> str:
    """按 dd -> [(MM-DD, [条目...])] 构造一份 core_memory 文本。"""
    lines: list[str] = []
    if long_term:
        lines.append("[长期]:")
        lines.extend(f"  - {item}" for item in long_term)
    if pinned:
        lines.append("[标记]:")
        lines.extend(f"  - {item}" for item in pinned)
    months: dict[str, list[str]] = {}
    for month in sorted({day[:7] for day, _ in days}, reverse=True):
        block = months.setdefault(month, [f"[{month}]:"])
        for day, entries in days:
            if day[:7] != month:
                continue
            block.append(f"  - [{day[8:10]}]:")
            block.extend(f"      - {entry}" for entry in entries)
    for month in sorted(months, reverse=True):
        lines.extend(months[month])
    return "\n".join(lines)


def _retriever(
    tmp_dir: Path,
    archive_text: str,
    *,
    mode: str = MODE_ON,
    injection_max_chars: int = 10000,
    embedder=None,
    embedder_factory=None,
    rng=None,
    config_overrides: dict | None = None,
    name: str = "core_memory.md",
) -> MemoryRetriever:
    (tmp_dir / name).write_text(archive_text, encoding="utf-8")
    config = RetrievalConfig(mode=mode, **(config_overrides or {}))
    return MemoryRetriever(
        config,
        data_dir=tmp_dir,
        core_path=tmp_dir / name,
        injection_max_chars=injection_max_chars,
        embedder=embedder,
        embedder_factory=embedder_factory,
        rng=rng,
        clock=lambda: NOW,
    )


class _LexicalEmbedder:
    """确定性"词形"嵌入：共享字符 bigram 越多，余弦越高。

    用 crc32 而不是内置 hash（后者每个进程都加了随机种子，不能用于断言）。
    """

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.calls = 0

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        for index in range(max(len(text) - 1, 1)):
            bigram = text[index:index + 2]
            vector[zlib.crc32(bigram.encode("utf-8")) % self.dim] += 1.0
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else vector

    def __call__(self, texts, *, max_length=None):
        self.calls += 1
        if isinstance(texts, str):
            texts = [texts]
        return np.asarray([self._vector(text) for text in texts], dtype=np.float32)


class _FlakyEmbedder(_LexicalEmbedder):
    """构建索引正常，但查询编码时抛错（模拟单次编码异常）。"""

    def __init__(self, fail_after: int = 1):
        super().__init__()
        self.fail_after = fail_after

    def __call__(self, texts, *, max_length=None):
        if self.calls >= self.fail_after:
            self.calls += 1
            raise RuntimeError("simulated encode failure")
        return super().__call__(texts, max_length=max_length)


def _raising_factory(_config):
    raise FileNotFoundError("模拟：模型文件缺失")


def _permissive_config():
    """任意属性都有值，用来喂给真实 pipeline 构造函数（不使用它的 predict）。"""

    class _PermissiveConfig:
        enable = True
        model_id = "stub"
        predict_url = "http://127.0.0.1:1/llm/predict"
        stream_predict_url = "http://127.0.0.1:1/llm/stream-predict"

        def __bool__(self):
            return True

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return True

    return _PermissiveConfig()


def _identity_budget(archive_text: str) -> int:
    """`[长期]` + `[标记]` 在注入文本里占用的字符数（用它换算字数上限）。"""
    parsed = parse_core_memory(archive_text)
    return identity_chars(parsed.long_term, parsed.pinned)


# --------------------------------------------------------------------------- 解析器


def test_parser_matches_live_archive_fixture():
    text = _live_archive_text()
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != _LIVE_ARCHIVE_SHA256:
        pytest.skip("真实档案已增长，定稿数字不再适用（结构不变量由其它用例覆盖）")
    parsed = parse_core_memory(text)
    assert len(parsed.long_term) == 13
    assert parsed.day_count == 29
    assert parsed.entry_count == 109
    assert len(text) == 8874


def test_parser_tolerates_format_drift():
    text = _archive(
        [("2026-10-14", ["21:30 有时间的条目", "没有时间前缀的条目"])],
        long_term=[
            "用户名为赛艇 (自2026-06-18起, 最后确认于2026-08-04)",
            "只有起始时间的旧格式 (自2026-06-18起)",
            "完全没有元数据的条目",
        ],
        pinned=["今天在打游戏"],
    )
    parsed = parse_core_memory(text)
    assert [entry.time for entry in parsed.days[0].entries] == ["21:30", None]
    assert parsed.long_term[0].since == date(2026, 6, 18)
    assert parsed.long_term[0].last_confirmed == date(2026, 8, 4)
    assert parsed.long_term[1].since == date(2026, 6, 18)
    assert parsed.long_term[1].last_confirmed is None
    # 格式漂移不许抛异常，只降级成 None
    assert parsed.long_term[2].since is None
    assert parsed.long_term[2].last_confirmed is None
    assert len(parsed.pinned) == 1


def test_parser_tolerates_missing_pinned_and_odd_indent():
    text = "[2026-10]:\n    - [14]:\n          - 缩进乱了也能解析\n"
    parsed = parse_core_memory(text)
    assert parsed.pinned == ()
    assert parsed.entry_count == 1
    assert parsed.days[0].entries[0].date == date(2026, 10, 14)


def test_parser_accepts_pinned_without_colon_and_ignores_empty_lines():
    text = "[长期]:\n  - 用户名为赛艇 (自2026-06-18起, 最后确认于2026-08-04)\n\n[标记]\n  - 标记段可以没有冒号\n\n[2026-10]:\n  - [14]:\n      - 一条\n"
    parsed = parse_core_memory(text)
    assert len(parsed.pinned) == 1
    assert parsed.pinned[0].raw == "标记段可以没有冒号"


def test_pinned_segment_strip_and_splice_is_byte_identical():
    text = _archive(
        [("2026-10-14", ["21:30 一条"])],
        long_term=["用户名为赛艇 (自2026-06-18起, 最后确认于2026-08-04)"],
        pinned=["今天在打《历史模拟器：崇祯》，内阁缺人，别催我干活"],
    )
    body, segment = split_pinned_segment(text)
    assert segment.startswith("[标记]:")
    assert "别催我干活" in segment
    assert "标记" not in body
    merged_body = "[长期]:\n  - 用户名为赛艇 (自2026-06-18起, 最后确认于2026-08-04)\n[2026-10]:\n  - [14]:\n      - 模型重写过的条目"
    out = splice_pinned_segment(merged_body, segment)
    out_body, out_segment = split_pinned_segment(out)
    assert out_segment == segment  # 逐字节一致
    # 位置：[长期] 之后、第一个月份之前
    assert out.index("[长期]") < out.index("[标记]:") < out.index("[2026-10]")
    assert json.dumps(out_segment) == json.dumps(segment)


def test_splice_without_pinned_is_noop():
    body = "[长期]:\n  - 只有长期段"
    assert splice_pinned_segment(body, "") == body


# --------------------------------------------------------------------------- 窗口选择器


def _day_blocks(spec: list[tuple[str, int]]):
    days = [(day, [f"条目 {index}" for index in range(count)]) for day, count in spec]
    return parse_core_memory(_archive(days)).days


def test_window_uses_whole_days_and_respects_both_bounds():
    days = _day_blocks([
        ("2026-10-15", 2), ("2026-10-14", 3), ("2026-10-13", 4), ("2026-10-12", 5),
        ("2026-10-11", 40), ("2026-10-10", 60), ("2026-09-01", 7),
    ])
    cfg = RetrievalConfig(mode=MODE_ON, min_recent_days=0, recent_days=30, max_recent_entries=100)
    selected, reduced = select_resident_days(days, TODAY, cfg)
    # 整日累加：2+3+4+5+40 = 54 < 100 → 再加 60 = 114 >= 100 时停止（触界的那天要算进去）
    assert [block.date for block in selected] == [
        date(2026, 10, 15), date(2026, 10, 14), date(2026, 10, 13),
        date(2026, 10, 12), date(2026, 10, 11), date(2026, 10, 10),
    ]
    assert selected[-1].date == date(2026, 10, 10)
    assert reduced is True
    # 绝不能只拿半天的条目
    for block in selected:
        source = next(item for item in days if item.date == block.date)
        assert len(block.entries) == len(source.entries)


def test_window_date_boundary_alone():
    days = _day_blocks([("2026-10-15", 1), ("2026-09-20", 1), ("2026-09-10", 1)])
    cfg = RetrievalConfig(mode=MODE_ON, min_recent_days=0, recent_days=30, max_recent_entries=1000)
    selected, _ = select_resident_days(days, TODAY, cfg)
    assert [block.date for block in selected] == [date(2026, 10, 15), date(2026, 9, 20)]


def test_window_skips_gaps_without_wasting_budget():
    """日期块不连续（09-24 → 08-19 → 08-07 有跳跃）时按存在的块累加，不逐日扣预算。"""
    days = _day_blocks([("2026-10-05", 1), ("2026-09-24", 1), ("2026-08-19", 1), ("2026-08-07", 1)])
    cfg = RetrievalConfig(mode=MODE_ON, min_recent_days=0, recent_days=365, max_recent_entries=3)
    selected, _ = select_resident_days(days, TODAY, cfg)
    assert [block.date for block in selected] == [
        date(2026, 10, 5), date(2026, 9, 24), date(2026, 8, 19),
    ]


def test_min_recent_days_floor_beats_entry_cap():
    days = _day_blocks([
        ("2026-10-15", 200), ("2026-10-05", 10), ("2026-09-20", 10),
    ])
    cfg = RetrievalConfig(mode=MODE_ON, min_recent_days=3, recent_days=30, max_recent_entries=100)
    selected, _ = select_resident_days(days, TODAY, cfg)
    # 200 条 > 100 条上限，但"今天"在保底范围内 → 一条都不能被挤出去
    assert [block.date for block in selected] == [date(2026, 10, 15)]
    assert len(selected[0].entries) == 200


def test_min_recent_days_floor_does_not_revive_old_blocks():
    """用户 40 天没来时，超出保底天数的旧块不得复活。"""
    days = _day_blocks([("2026-10-13", 2), ("2026-09-05", 5), ("2026-06-01", 5)])
    cfg = RetrievalConfig(mode=MODE_ON, min_recent_days=3, recent_days=30, max_recent_entries=100)
    selected, _ = select_resident_days(days, TODAY, cfg)
    assert [block.date for block in selected] == [date(2026, 10, 13)]


def test_identity_segments_are_not_counted_in_window_stats():
    text = _archive(
        [("2026-10-15", ["一条", "两条", "三条"])],
        long_term=["长期一 (自2026-01-01起, 最后确认于2026-02-02)"],
        pinned=["标记一"],
    )
    parsed = parse_core_memory(text)
    cfg = RetrievalConfig(mode=MODE_ON, min_recent_days=0, recent_days=30, max_recent_entries=2)
    selected, _ = select_resident_days(parsed.days, TODAY, cfg)
    # 只有 1 天 / 3 条日期块条目参与统计；[长期] 与 [标记] 不计入
    assert sum(len(block.entries) for block in selected) == 3
    assert len(parsed.long_term) == 1 and len(parsed.pinned) == 1


# --------------------------------------------------------------------------- 两级削减


def test_char_limit_drops_whole_oldest_entries():
    days = _day_blocks([("2026-10-15", 2), ("2026-10-14", 2), ("2026-10-13", 2)])
    entries = [entry for block in days for entry in block.entries]
    budget = sum(entry_chars(entry) for entry in entries[:4])
    kept, dropped = apply_char_limit(entries, budget)
    assert dropped == 2
    assert [entry.raw for entry in kept] == [entry.raw for entry in entries[:4]]
    assert all(entry.date >= date(2026, 10, 14) for entry in kept)


def test_char_limit_never_touches_identity_segments(tmp_dir):
    archive_text = _archive(
        [("2026-10-15", ["条目一", "条目二"]), ("2026-10-14", ["条目三"])],
        long_term=["长期一 (自2026-01-01起, 最后确认于2026-02-01)"],
        pinned=["标记一"],
    )
    parsed = parse_core_memory(archive_text)
    entries = [entry for block in parsed.days for entry in block.entries]
    # 预算刚好只够放得下最新的一条
    budget = _identity_budget(archive_text) + entry_chars(entries[0])
    retriever = _retriever(tmp_dir, archive_text, mode=MODE_ON, injection_max_chars=budget)
    view = retriever.resident(now=NOW)
    assert len(view.long_term) == 1
    assert len(view.pinned) == 1
    assert view.identity_over_budget is False
    assert view.truncated_entries == len(entries) - len(view.entries)
    assert view.truncated_entries >= 1
    assert [entry.raw for entry in view.entries] == [entries[0].raw]
    assert TRUNCATED_NOTICE.format(count=view.truncated_entries) in view.text


def test_identity_over_budget_keeps_everything_and_warns(tmp_dir, caplog):
    long_term = [f"很长的长期事实{i} " + "x" * 40 for i in range(1, 4)]
    archive_text = _archive(
        [("2026-10-15", ["条目甲", "条目乙"])],
        long_term=[f"{item} (自2026-01-01起, 最后确认于2026-02-01)" for item in long_term],
        pinned=["标记一"],
    )
    retriever = _retriever(tmp_dir, archive_text, mode=MODE_ON, injection_max_chars=50)
    with caplog.at_level(logging.ERROR, logger="memory.retrieval"):
        view = retriever.resident(now=NOW)
    assert view.identity_over_budget is True
    assert len(view.long_term) == 3 and len(view.pinned) == 1
    assert view.truncated_entries == 0  # 宁可超限也不丢条目
    assert IDENTITY_OVER_BUDGET_NOTICE in view.text
    assert any("超出" in record.message for record in caplog.records)


def test_off_mode_applies_only_char_limit(tmp_dir):
    archive_text = _archive(
        [("2026-10-15", ["新条目"]), ("2026-01-01", ["很旧很旧的条目"])],
        long_term=["长期一 (自2026-01-01起, 最后确认于2026-02-01)"],
    )
    retriever = _retriever(tmp_dir, archive_text, mode=MODE_OFF, injection_max_chars=10000)
    view = retriever.resident(now=NOW)
    assert len(view.days) == 2  # 不做日期/条数削减
    assert view.window_reduced is False

    parsed = parse_core_memory(archive_text)
    newest = parsed.days[0].entries[0]
    tight_budget = _identity_budget(archive_text) + entry_chars(newest)
    tight = _retriever(tmp_dir, archive_text, mode=MODE_OFF,
                       injection_max_chars=tight_budget, name="core_memory_2.md")
    tight_view = tight.resident(now=NOW)
    assert tight_view.truncated_entries == 1  # 只受字数上限约束，丢最老的那条
    assert {entry.raw for entry in tight_view.entries} == {"新条目"}


def test_forgetting_semantics_when_archive_is_fully_aged(tmp_dir):
    archive_text = _archive(
        [("2026-01-01", ["很久以前的条目"])],
        long_term=["用户名为赛艇 (自2026-01-01起, 最后确认于2026-01-02)"],
        pinned=["标记一"],
    )
    retriever = _retriever(tmp_dir, archive_text, mode=MODE_ON)
    view = retriever.resident(now=NOW)
    assert view.days == ()
    assert len(view.long_term) == 1 and len(view.pinned) == 1
    assert "很久以前的条目" not in view.text


# --------------------------------------------------------------------------- 分块与索引


_INDEX_DAYS = [
    ("2026-10-14", ["21:30 用户测试我完成度", "22:05 用户让我删掉 test_file.txt"]),
    ("2026-10-12", ["20:29 用户表示快要完成我的最初版"]),
]


def test_index_contains_entry_level_chunks_only(tmp_dir):
    archive_text = _archive(
        _INDEX_DAYS,
        long_term=["用户名为赛艇 (自2026-06-18起, 最后确认于2026-08-04)"],
        pinned=["今天的标记"],
    )
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder())
    assert retriever.rebuild_index() is True
    chunks = [json.loads(line) for line in
              (index_store.index_paths(tmp_dir)[0]).read_text(encoding="utf-8").splitlines()]
    assert len(chunks) == 3
    assert {chunk["kind"] for chunk in chunks} == {"entry"}
    assert [chunk["date"] for chunk in chunks] == ["2026-10-14", "2026-10-14", "2026-10-12"]
    assert chunks[0]["time"] == "21:30"
    assert chunks[0]["text"] == "2026-10-14 21:30 用户测试我完成度"
    assert chunks[0]["chars"] == len(chunks[0]["text"])
    assert chunks[1]["time"] == "22:05"
    assert chunks[2]["time"] == "20:29"
    assert all("长期" not in chunk["text"] and "标记" not in chunk["text"] for chunk in chunks)


def test_index_rows_match_vectors_and_meta(tmp_dir):
    archive_text = _archive(_INDEX_DAYS)
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder())
    retriever.rebuild_index()
    chunks, vectors, meta = index_store.load_index(tmp_dir)
    assert meta.chunks == len(chunks) == vectors.shape[0] == 3
    assert meta.schema == index_store.INDEX_SCHEMA
    assert meta.core_memory_sha256 == index_store.core_memory_sha256(archive_text)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)


def test_index_validation_detects_mismatch_and_rebuilds(tmp_dir):
    archive_text = _archive(_INDEX_DAYS)
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder())
    retriever.rebuild_index()
    valid, reason = index_store.index_is_valid(tmp_dir, core_text=archive_text)
    assert valid, reason

    # 档案变了 → sha256 不匹配
    (tmp_dir / "core_memory.md").write_text(archive_text + "\n[2026-10-11]:\n  - [11]:\n      - 新条目",
                                            encoding="utf-8")
    valid, reason = index_store.index_is_valid(
        tmp_dir, core_text=(tmp_dir / "core_memory.md").read_text(encoding="utf-8")
    )
    assert not valid and "sha256" in reason

    # jsonl 行数与 meta.chunks 不一致 → 必须被发现
    jsonl_path, _, meta_path = index_store.index_paths(tmp_dir)
    payload = jsonl_path.read_text(encoding="utf-8").splitlines()
    jsonl_path.write_text("\n".join(payload[:-1]) + "\n", encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["core_memory_sha256"] = index_store.core_memory_sha256(
        (tmp_dir / "core_memory.md").read_text(encoding="utf-8")
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(index_store.MemoryIndexError):
        index_store.load_index(tmp_dir)

    # 校验失败后 ensure_index() 会重建
    retriever.reload()
    retriever.ensure_index()
    assert retriever.index_ready
    assert len(retriever._chunks) == 4


def test_corrupt_index_is_reported_not_fatal(tmp_dir):
    archive_text = _archive(_INDEX_DAYS)
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder(),
                           config_overrides={"min_score": 0.1})
    retriever.rebuild_index()
    jsonl_path, npy_path, _ = index_store.index_paths(tmp_dir)
    npy_path.write_bytes(b"not an npy")
    retriever.reload()
    retriever.ensure_index()
    assert retriever.index_ready
    assert retriever.index_error is None
    assert len(retriever.retrieve("用户测试我完成度")) >= 1


def test_hot_reload_swaps_resident_text_and_index_together(tmp_dir):
    archive_text = _archive([("2026-10-15", ["旧内容"]), ("2026-06-01", ["很久以前的事"])])
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder(),
                           config_overrides={"min_score": 0.1})
    retriever.rebuild_index()
    assert retriever.index_ready

    # merge_memory.py 可能在 Bot 运行期间改盘：内容与长度都变了
    (tmp_dir / "core_memory.md").write_text(
        archive_text.replace("旧内容", "全新的内容"), encoding="utf-8"
    )
    assert retriever.refresh() is True
    # 成对替换：常驻文本换了，索引同时被判为未就绪（不允许只换一个）
    assert "全新的内容" in retriever.resident(now=NOW).text
    assert retriever.index_ready is False

    thread = retriever._rebuild_thread
    if thread is not None:
        thread.join(timeout=30)
    assert retriever.index_ready is True
    valid, reason = index_store.index_is_valid(tmp_dir, core_text=retriever.archive_text)
    assert valid, reason
    # 没有变化时 refresh 不该反复读盘
    assert retriever.refresh() is False


def test_memory_service_recall_and_query_fallback(tmp_dir):
    from memory.config import MemoryConfig as _MemoryConfig
    from services.memory_recall.service import MemoryService
    from services.tool_runtime import ToolCall, ToolRuntime

    archive_text = _archive(_INDEX_DAYS)
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder(),
                           config_overrides={"min_score": 0.1})
    retriever.rebuild_index()
    runtime = ToolRuntime(max_workers=1)
    config = _MemoryConfig(
        enable=True, data_dir=tmp_dir,
        retrieval=RetrievalConfig(mode=MODE_ON, min_score=0.1),
    )
    service = MemoryService(config, runtime, retriever)
    assert runtime.get("memory_recall") is not None
    assert service.model_instruction()

    # query 被写空时退回最近一轮用户话
    service.set_fallback_provider(lambda: "用户测试我完成度")
    result = runtime.invoke(ToolCall(name="memory_recall", arguments={"query": "  "}))
    assert result.ok, result.error
    assert "用户测试我完成度" in str(result.output)

    # 本地路由的 strip_patterns 可能把整句剥成残字：残字也要退回用户原话
    result = runtime.invoke(ToolCall(name="memory_recall", arguments={"query": "我"}))
    assert result.ok, result.error
    assert "用户测试我完成度" in str(result.output)

    # 没有兜底来源且 query 为空 → 明确失败，不静默返回空
    bare = MemoryService(config, runtime, retriever)
    result = runtime.invoke(ToolCall(name="memory_recall", arguments={}))
    assert result.ok is False and "query" in (result.error or "")

    # mode != on 时不注册工具（observe 必须与 off 注入一致）
    off_service = MemoryService(
        _MemoryConfig(enable=True, data_dir=tmp_dir, retrieval=RetrievalConfig(mode=MODE_OFF)),
        runtime, retriever,
    )
    assert runtime.get("memory_recall") is None
    assert off_service.model_instruction() is None


# --------------------------------------------------------------------------- 检索


def test_retrieval_ranking_and_min_score(tmp_dir):
    archive_text = _archive(_INDEX_DAYS + [("2026-08-07", ["06:51 用户询问今日新闻"])])
    embedder = _LexicalEmbedder()
    retriever = _retriever(tmp_dir, archive_text, embedder=embedder)
    retriever.rebuild_index()
    hits = retriever.retrieve("用户测试我完成度", same_day_expand=0, min_score=0.1)
    assert hits and hits[0].entry.raw == "21:30 用户测试我完成度"
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)

    # min_score 过滤：极高阈值 → 什么都不返回
    assert retriever.retrieve("用户测试我完成度", same_day_expand=0, min_score=0.999) == []
    assert retriever.retrieve("   ") == []


def test_retrieval_same_day_expansion(tmp_dir):
    archive_text = _archive([
        ("2026-10-14", ["21:30 用户测试我完成度", "22:05 用户让我删掉文件", "22:20 用户又说了别的"]),
        ("2026-10-12", ["20:29 用户表示快要完成我的最初版"]),
    ])
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder())
    retriever.rebuild_index()
    hits = retriever.retrieve("用户测试我完成度", same_day_expand=1, top_k=1, min_score=0.1)
    assert hits[0].expanded is False
    expanded = [hit for hit in hits if hit.expanded]
    assert len(expanded) == 1
    assert expanded[0].entry.date == date(2026, 10, 14)
    assert expanded[0].entry.raw != hits[0].entry.raw

    hits = retriever.retrieve("用户测试我完成度", same_day_expand=5, top_k=1, min_score=0.1)
    assert len([hit for hit in hits if hit.expanded]) == 2  # 同日只剩两条其他条目


def test_retrieval_block_respects_max_chars(tmp_dir):
    archive_text = _archive([
        ("2026-10-14", [f"21:3{i} 用户测试我完成度第{i}条" for i in range(6)]),
    ])
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder(),
                           config_overrides={"max_chars": 120, "same_day_expand": 5,
                                             "min_score": 0.1})
    retriever.rebuild_index()
    text = retriever.retrieval_block("用户测试我完成度")
    assert text.startswith(RETRIEVAL_BLOCK_HEADER)
    body = text.split("\n", 1)[1]
    assert "截断" in body
    assert len(body) <= 120 + len("\n（检索结果已按上限截断）")


def test_retrieval_block_uses_full_dates(tmp_dir):
    """召回片段可能来自很久以前，必须写完整日期，不能只写 `[14]:`。"""
    archive_text = _archive([
        ("2026-08-07", ["06:51 用户询问今日新闻"]),
        ("2026-10-14", ["21:30 用户测试我完成度"]),
    ])
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder(),
                           config_overrides={"min_score": 0.1, "same_day_expand": 0})
    retriever.rebuild_index()
    text = retriever.retrieval_block("用户询问今日新闻")
    assert "[2026-08-07]:" in text
    assert "[07]:" not in text


def test_retrieval_block_without_hits_reports_empty(tmp_dir):
    archive_text = _archive(_INDEX_DAYS)
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder())
    retriever.rebuild_index()
    assert retriever.retrieval_block("完全不相关的词").startswith("记忆档案里没有找到")


# --------------------------------------------------------------------------- 随机触发


def _random_archive() -> str:
    days = [("2026-10-15", ["今天的事一", "今天的事二"])]
    for offset in range(1, 12):
        day = (TODAY - timedelta(days=90 + offset * 5)).isoformat()
        days.append((day, [f"{day} 的旧事"]))
    return _archive(days)


def test_random_trigger_probability_is_deterministic(tmp_dir):
    archive_text = _random_archive()
    # p=0 → 永不触发
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder(),
                           rng=random.Random(7),
                           config_overrides={"random_probability": 0.0,
                                             "random_probability_sparse": 0.0,
                                             "sparse_resident_entries": 0})
    assert retriever.sample_random() is None

    # p=1 → 必触发，且一定落在常驻窗口之外
    retriever = _retriever(tmp_dir, archive_text, embedder=_LexicalEmbedder(),
                           rng=random.Random(7),
                           config_overrides={"random_probability": 1.0,
                                             "sparse_resident_entries": 0})
    hits = retriever.sample_random()
    assert hits
    resident_dates = {entry.date for entry in retriever.resident(now=NOW).entries}
    assert hits[0].entry.date not in resident_dates


def test_random_trigger_respects_ttl(tmp_dir):
    retriever = _retriever(tmp_dir, _random_archive(), embedder=_LexicalEmbedder(),
                           rng=random.Random(11),
                           config_overrides={"random_probability": 1.0,
                                             "random_repeat_ttl_hours": 6,
                                             "sparse_resident_entries": 0})
    first = retriever.sample_random()
    assert first
    key = f"{first[0].entry.line}:{first[0].entry.order}:{first[0].entry.raw[:24]}"
    assert key in retriever._random_injected
    for _ in range(5):
        again = retriever.sample_random()
        assert again
        assert again[0].entry.raw != first[0].entry.raw  # TTL 内不重复


def test_sparse_resident_switches_probability(tmp_dir):
    retriever = _retriever(tmp_dir, _random_archive(), embedder=_LexicalEmbedder(),
                           config_overrides={"random_probability": 0.0,
                                             "random_probability_sparse": 0.30,
                                             "sparse_resident_entries": 5})
    assert retriever.random_probability(0) == pytest.approx(0.30)
    assert retriever.random_probability(5) == pytest.approx(0.30)
    assert retriever.random_probability(6) == pytest.approx(0.0)
    assert retriever.random_probability(100) == pytest.approx(0.0)

    custom = _retriever(tmp_dir, _random_archive(), embedder=_LexicalEmbedder(),
                        name="core_memory_2.md",
                        config_overrides={"random_probability": 0.1,
                                          "random_probability_sparse": 0.9,
                                          "sparse_resident_entries": 2})
    assert custom.random_probability(2) == pytest.approx(0.9)
    assert custom.random_probability(3) == pytest.approx(0.1)


# --------------------------------------------------------------------------- 三态矩阵与降级


def test_three_state_injection_matrix(tmp_dir):
    archive_text = _archive(
        [("2026-10-15", ["今天的事"]), ("2026-06-01", ["很久以前的事"])],
        long_term=["长期一 (自2026-01-01起, 最后确认于2026-02-01)"],
        pinned=["标记一"],
    )
    off = _retriever(tmp_dir, archive_text, mode=MODE_OFF, name="core_memory_off.md")
    off_turn = off.build_turn_memory(query="今天的事", allow_random=False, now=NOW)
    assert "很久以前的事" in off_turn.text and "今天的事" in off_turn.text
    assert off_turn.resident_entries == 2

    # on + BGE 坏：不回退全量，只注入常驻窗口，并给出明确警告
    broken = _retriever(tmp_dir, archive_text, mode=MODE_ON, embedder_factory=_raising_factory,
                        name="core_memory_broken.md")
    broken_turn = broken.build_turn_memory(query="今天的事", allow_random=False, now=NOW)
    assert "今天的事" in broken_turn.text
    assert "很久以前的事" not in broken_turn.text
    assert broken_turn.warnings and "模型" in broken_turn.warnings[0]
    assert broken.embedder_error is not None

    # on + BGE 可用：常驻窗口 + 检索
    healthy = _retriever(tmp_dir, archive_text, mode=MODE_ON, embedder=_LexicalEmbedder(),
                         name="core_memory_healthy.md")
    healthy_turn = healthy.build_turn_memory(query="今天的事", allow_random=False, now=NOW)
    assert "今天的事" in healthy_turn.text
    assert "很久以前的事" not in healthy_turn.text
    assert healthy_turn.warnings == ()
    # off 与 on+BGE坏 的差别只有"有没有日期条数削减"
    assert off_turn.text != broken_turn.text
    assert off_turn.text.count("今天的事") == broken_turn.text.count("今天的事")


def test_observe_is_byte_identical_to_off(tmp_dir, caplog):
    archive_text = _archive(
        [("2026-10-15", ["今天的事"]), ("2026-06-01", ["很久以前的事"])],
        long_term=["长期一 (自2026-01-01起, 最后确认于2026-02-01)"],
        pinned=["标记一"],
    )
    off = _retriever(tmp_dir, archive_text, mode=MODE_OFF, name="core_memory_off.md")
    observe = _retriever(tmp_dir, archive_text, mode=MODE_OBSERVE, embedder=_LexicalEmbedder(),
                         name="core_memory_obs.md")
    observe.rebuild_index()
    with caplog.at_level(logging.INFO, logger="memory.retrieval"):
        off_turn = off.build_turn_memory(query="很久以前的事", allow_random=False, now=NOW)
        observe_turn = observe.build_turn_memory(query="很久以前的事", allow_random=False, now=NOW)
    assert off_turn.text == observe_turn.text  # 关键回归断言
    assert "很久以前的事" in observe_turn.text
    assert any("observe" in record.message for record in caplog.records)
    assert observe_turn.random_used is False


def test_missing_index_does_not_fall_back_to_full_archive(tmp_dir):
    archive_text = _archive(
        [("2026-10-15", ["今天的事"]), ("2026-06-01", ["很久以前的事"])],
    )
    broken = _retriever(tmp_dir, archive_text, mode=MODE_ON,
                        embedder_factory=_raising_factory)
    turn = broken.build_turn_memory(query=None, allow_random=True, now=NOW)
    assert "很久以前的事" not in turn.text  # 不回退全量注入
    assert "今天的事" in turn.text
    assert turn.warnings
    # 模型不可用时不会写出索引文件
    assert not index_store.index_paths(tmp_dir)[0].is_file()


def test_encode_failure_drops_retrieval_but_keeps_resident(tmp_dir):
    archive_text = _archive([
        ("2026-10-15", ["今天的事一", "今天的事二"]),
        ("2026-06-01", ["很久以前的事"]),
    ])
    embedder = _FlakyEmbedder(fail_after=1)
    retriever = _retriever(tmp_dir, archive_text, mode=MODE_ON, embedder=embedder)
    retriever.rebuild_index()  # 第一次调用正常
    with pytest.raises(RetrievalUnavailable):
        retriever.retrieve("今天的事")
    turn = retriever.build_turn_memory(query="今天的事", allow_random=False, now=NOW)
    assert "今天的事一" in turn.text
    assert turn.retrieval_hits == 0


def test_shared_embedder_serializes_tokenizer_calls():
    """HF fast tokenizer 内部是 Rust 的 RefCell：并发 encode 会抛 "Already borrowed"。

    bot 启动时 `intent_router.initialize()`（线程）与记忆索引后台重建会同时用同一份
    共享 embedder，所以整段分词必须串行化。这里用假 tokenizer 记账验证。
    """
    from services.embedding.bge_onnx import BGEOnnxEmbedder

    state = {"active": 0, "max_active": 0}
    guard = threading.Lock()

    class _FakeTokenizer:
        def __call__(self, texts, **_kwargs):
            with guard:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            try:
                time.sleep(0.005)
                return {"input_ids": np.zeros((len(texts), 4), dtype=np.int64)}
            finally:
                with guard:
                    state["active"] -= 1

    class _FakeSession:
        def run(self, _outputs, inputs):
            return [np.ones((len(inputs["input_ids"]), 1, 8), dtype=np.float32)]

    embedder = BGEOnnxEmbedder.__new__(BGEOnnxEmbedder)
    embedder.tokenizer = _FakeTokenizer()
    embedder.session = _FakeSession()
    embedder.input_names = {"input_ids"}
    embedder.default_max_length = 64
    embedder._call_lock = threading.Lock()
    embedder.spec = None

    errors = []

    def worker(max_length):
        try:
            for _ in range(3):
                embedder(["文本"], max_length=max_length)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(64 if index % 2 else 128,))
        for index in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert state["max_active"] == 1  # 分词从未被并发进入


def test_embedder_failure_is_cached_with_backoff(tmp_dir):
    calls = {"count": 0}

    def factory(_config):
        calls["count"] += 1
        raise RuntimeError("boom")

    retriever = _retriever(tmp_dir, _archive(_INDEX_DAYS), mode=MODE_ON, embedder_factory=factory)
    assert retriever.embedder_available() is False
    assert retriever.embedder_available() is False
    assert calls["count"] == 1  # 失败被缓存，不是每轮都重试
    assert retriever.embedder_error


# --------------------------------------------------------------------------- 参数校验


def test_config_normalization_takes_safe_values(caplog):
    with caplog.at_level(logging.WARNING, logger="memory.config"):
        config = normalize_retrieval_config(RetrievalConfig(
            mode="ON", top_k=0, same_day_expand=-1, max_chars=0,
            random_probability=5.0, sparse_resident_entries=-3,
            min_recent_days=90, recent_days=30,
        ))
    assert config.mode == MODE_ON
    assert config.top_k == 4 and config.same_day_expand == 3 and config.max_chars == 1200
    assert config.random_probability == pytest.approx(0.05)
    assert config.sparse_resident_entries == 5
    assert any("min_recent_days" in record.message for record in caplog.records)


def test_yaml_on_off_booleans_are_coerced():
    assert RetrievalConfig(mode=True).normalized().mode == MODE_ON
    assert RetrievalConfig(mode=False).normalized().mode == MODE_OFF


# --------------------------------------------------------------------------- 抽取参考与合并 prompt


def test_recent_reference_is_long_term_plus_window_only(tmp_dir):
    archive_text = _archive(
        [("2026-10-15", ["今天的事"]), ("2026-06-01", ["很久以前的事"])],
        long_term=["长期一 (自2026-01-01起, 最后确认于2026-02-01)"],
        pinned=["标记一"],
    )
    retriever = _retriever(tmp_dir, archive_text, mode=MODE_OFF)
    reference = retriever.recent_reference_text()
    assert "[长期]:" in reference and "长期一" in reference
    assert "今天的事" in reference
    assert "标记一" not in reference and "[标记]" not in reference
    assert "很久以前的事" not in reference


def test_extract_prompt_uses_recent_memory_placeholder():
    from memory.manager import EXTRACT_SYSTEM_PROMPT

    assert "{recent_memory}" in EXTRACT_SYSTEM_PROMPT
    assert "{core_memory}" not in EXTRACT_SYSTEM_PROMPT


def test_merge_prompt_forbids_deletion():
    from memory.manager import MERGE_SYSTEM_PROMPT

    assert "禁止删除" in MERGE_SYSTEM_PROMPT
    for permissive in ("允许删除", "直接删除", "或将旧的删除", "删除过时"):
        assert permissive not in MERGE_SYSTEM_PROMPT


def test_merge_keeps_pinned_segment_and_no_deletion_wording(tmp_dir):
    from memory.manager import MemoryManager

    class _ScriptedLLM:
        def __init__(self, responses):
            self._responses = list(responses)
            self.queries = []

        def predict(self, query):
            from condata.pipeline.llm import LLMPrediction
            self.queries.append(query)
            response = self._responses.pop(0) if self._responses else ""
            return LLMPrediction(response=response, history=[])

    pinned_line = "  - 今天在打《历史模拟器：崇祯》，内阁缺人，别催我干活"
    original = (
        "[长期]:\n"
        "  - 用户名为赛艇 (自2026-06-18起, 最后确认于2026-08-04)\n"
        "[标记]:\n"
        f"{pinned_line}\n"
        "[2026-10]:\n"
        "  - [14]:\n"
        "      - 21:30 用户测试我完成度\n"
    )
    (tmp_dir / "core_memory.md").write_text(original, encoding="utf-8")
    (tmp_dir / "memory_20261014_000000_000000.md").write_text(
        "---\ntimestamp: t\nsource: conversation_snapshot\n---\n[2026-10]:\n  - [14]:\n      - 22:05 用户让我删掉文件\n",
        encoding="utf-8",
    )

    llm = _ScriptedLLM([
        "[2026-10]:\n  - [14]:\n      - 21:30 用户测试我完成度",
        "[长期]:\n  - 用户名为赛艇 (自2026-06-18起, 最后确认于2026-08-04)\n"
        "[2026-10]:\n  - [14]:\n      - 21:30 用户测试我完成度",
    ])
    manager = MemoryManager(
        config=MemoryConfig(enable=True, data_dir=tmp_dir, async_save=False),
        llm_config=_permissive_config(),
    )
    manager._llm = llm
    manager._merge_llm = llm
    manager.merge_use_mllm = False
    assert manager.merge_all() is True

    merged = (tmp_dir / "core_memory.md").read_text(encoding="utf-8")
    _, segment = split_pinned_segment(merged)
    assert segment == "[标记]:\n" + pinned_line  # 逐字节一致
    assert merged.index("[长期]") < merged.index("[标记]:") < merged.index("[2026-10]")
    # 合并输入里不能出现标记段，且系统提示必须禁止删除
    prompts = "\n".join(
        message.content for query in llm.queries for message in query.history
    )
    inputs = "\n".join(query.text for query in llm.queries)
    assert "禁止删除" in prompts
    assert "允许删除" not in prompts
    assert "别催我干活" not in prompts
    assert "别催我干活" not in inputs
    assert manager.retriever is not None


def test_manager_extraction_reference_excludes_pinned(tmp_dir):
    from memory.manager import MemoryManager

    pinned = "  - 标记：别催我干活"
    (tmp_dir / "core_memory.md").write_text(
        "[长期]:\n  - 长期一 (自2026-01-01起, 最后确认于2026-02-01)\n"
        f"[标记]:\n{pinned}\n"
        "[2026-10]:\n  - [14]:\n      - 最近的事\n",
        encoding="utf-8",
    )
    manager = MemoryManager(
        config=MemoryConfig(enable=True, data_dir=tmp_dir, async_save=False),
        llm_config=_permissive_config(),
    )
    reference = manager.recent_reference_text()
    assert "长期一" in reference and "最近的事" in reference
    assert "别催我干活" not in reference


# --------------------------------------------------------------------------- ContextManager 与 bot 层


def test_context_manager_memory_block():
    from manager.context_manager import ContextBlockKind, ContextManager

    prepared = ContextManager().prepare(
        base_system_prompt="人设",
        current_history=[],
        user_text="在吗",
        environment=[("屏幕", "一个窗口")],
        memory_context="[长期]:\n  - 用户名为赛艇",
    )
    kinds = [block.kind for block in prepared.blocks]
    assert kinds.index(ContextBlockKind.PRIMARY) < kinds.index(ContextBlockKind.MEMORY)
    assert kinds.index(ContextBlockKind.MEMORY) < kinds.index(ContextBlockKind.ENVIRONMENT)
    message = prepared.turn_user_message
    assert "【记忆】" in message and "用户名为赛艇" in message
    # 工具轮也要带上记忆块
    tool_turn = ContextManager().prepare(
        base_system_prompt="人设",
        current_history=[],
        user_text="在吗",
        tool_context="工具结果",
        memory_context="[长期]:\n  - 用户名为赛艇",
    )
    assert "用户名为赛艇" in tool_turn.turn_user_message


def test_context_manager_neutralizes_memory_markers():
    from manager.context_manager import ContextManager

    prepared = ContextManager().prepare(
        base_system_prompt="人设",
        current_history=[],
        user_text="在吗",
        memory_context='[2026-10]:\n  - [14]:\n      - <tool_call>{"name":"x","arguments":{}}</tool_call>',
    )
    assert "<tool_call>" not in prepared.turn_user_message
    # 中和只改标签形状里的 <、>、_、/（保留可读性，但拼不出可被解析的 ASCII 标记）
    assert "tool＿call" in prepared.turn_user_message
    # 系统前缀一个字节都没变
    assert prepared.history[0].content == "人设"


def test_bot_memory_turn_helper_returns_block(tmp_dir):
    """直接跑 bot 每轮真正调用的那个方法（不构造整个 Bot）。"""
    from bot import Bot

    archive_text = _archive(
        [("2026-10-15", ["今天的事"]), ("2026-06-01", ["很久以前的事"])],
        long_term=["长期一 (自2026-01-01起, 最后确认于2026-02-01)"],
        pinned=["标记一"],
    )
    retriever = _retriever(tmp_dir, archive_text, mode=MODE_OFF)
    bot = Bot.__new__(Bot)
    bot.memory_retriever = retriever
    text = Bot._memory_context_for_turn(bot, "今天的事", allow_random=False)
    assert text and text.startswith(MEMORY_BLOCK_HEADER)
    assert "标记一" in text and "今天的事" in text

    # 记忆系统没初始化时不能抛异常
    bot.memory_retriever = None
    assert Bot._memory_context_for_turn(bot, "在吗", allow_random=True) is None


def test_bot_no_longer_injects_memory_into_system_prompt():
    source = (_BOT_DIR / "bot.py").read_text(encoding="utf-8")
    assert "关于用户的长期记忆" not in source
    assert "memory_context=self._memory_context_for_turn(" in source
    # 记忆块必须进当轮用户消息，不许往 history 里插一条记忆消息
    assert "Conversation(role=RoleEnum.user, content=memory_context" not in source


def test_bot_uses_one_shared_retriever_instance():
    source = (_BOT_DIR / "bot.py").read_text(encoding="utf-8")
    assert "retriever=self.memory_retriever" in source
    assert "MemoryService(" in source
    assert "self.memory_retriever.startup_check()" in source
