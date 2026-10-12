"""Harness 聚合看板（tools/harness_report.py）的回归测试。

    conda activate MyAI
    python -m pytest Bot/tests/test_p1_harness.py -v
"""

import json
import sys
from pathlib import Path

import pytest

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

_TOOLS_DIR = _BOT_DIR / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import harness_report as hr  # noqa: E402


def _tmp(label: str) -> Path:
    """临时目录放在仓库根 .temp 下（系统临时目录在部分账户/沙箱下不可写）。"""
    import uuid

    root = _BOT_DIR.parent / ".temp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"p1harness-{label}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tmp_path():
    return _tmp("case")


def _write_log(path: Path) -> Path:
    rows = [
        # 未成对出现的 started：聚合时应忽略，避免双计
        {"timestamp": "2026-10-01T00:00:00+00:00", "kind": "model", "event": "started",
         "name": "MLLMSyncPipeline", "call_id": "c1", "conversation_id": "conv_a", "turn_id": 1},
        {"timestamp": "2026-10-01T00:00:01+00:00", "kind": "model", "event": "finished",
         "name": "MLLMSyncPipeline", "call_id": "c1", "conversation_id": "conv_a", "turn_id": 1,
         "round": 0, "source": "dialogue", "duration_ms": 1200.0, "status": "success",
         "usage": {"status": "reported", "input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
                   "provider_usage": {"prompt_tokens": 100, "completion_tokens": 10,
                                      "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20}}},
        {"timestamp": "2026-10-01T00:00:02+00:00", "kind": "tool", "event": "finished",
         "name": "browser_search", "call_id": "c2", "conversation_id": "conv_a", "turn_id": 1,
         "round": 1, "duration_ms": 300.0, "status": "success"},
        {"timestamp": "2026-10-01T00:00:03+00:00", "kind": "tool", "event": "finished",
         "name": "sandbox_write", "call_id": "c3", "conversation_id": "conv_a", "turn_id": 2,
         "round": 1, "duration_ms": 5.0, "status": "error",
         "error": {"type": "ValueError", "message": "坏的参数"}},
        {"timestamp": "2026-10-02T00:00:00+00:00", "kind": "model", "event": "finished",
         "name": "LLMSyncPipeline", "call_id": "c4", "conversation_id": "conv_b", "turn_id": 1,
         "round": 0, "duration_ms": 4000.0, "status": "success"},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def log_file(tmp_path):
    return _write_log(tmp_path / "harness.jsonl")


def test_load_spans_skips_started_and_bad_lines(log_file):
    log_file.write_text(log_file.read_text(encoding="utf-8") + "{坏行\n", encoding="utf-8")
    spans, bad = hr.load_spans(log_file)
    assert bad == 1
    # started 行被忽略：5 条原始记录 → 4 条完成记录
    assert len(spans) == 4


def test_usage_and_cache_fields_are_extracted(log_file):
    spans, _ = hr.load_spans(log_file)
    model = next(s for s in spans if s.name == "MLLMSyncPipeline")
    assert model.input_tokens == 100 and model.output_tokens == 10
    assert model.cache_hit_tokens == 80 and model.cache_miss_tokens == 20
    assert model.cache_total == 100


def test_report_aggregates_per_conversation(log_file):
    spans, bad = hr.load_spans(log_file)
    report = hr.build_report(spans, top=5, log_path=log_file, bad_lines=bad)

    summary = report["summary"]
    assert summary["calls"] == 4
    assert summary["conversations"] == 2
    assert summary["errors"] == 1
    assert summary["input_tokens"] == 100
    assert summary["cache_hit"] == 80

    conv_a = next(row for row in report["conversations"] if row["conversation_id"] == "conv_a")
    assert conv_a["calls"] == 3
    assert conv_a["turns"] == 2          # turn_id 1 与 2
    assert conv_a["tool_calls"] == 2     # 两个 kind=tool
    assert conv_a["errors"] == 1
    assert conv_a["total_ms"] == pytest.approx(1505.0)

    conv_b = next(row for row in report["conversations"] if row["conversation_id"] == "conv_b")
    assert conv_b["calls"] == 1 and conv_b["errors"] == 0


def test_report_since_and_conversation_filters(log_file):
    spans, bad = hr.load_spans(log_file)
    only_b = hr.build_report(spans, top=5, log_path=log_file, bad_lines=bad, conversation="conv_b")
    assert only_b["summary"]["calls"] == 1
    assert [row["conversation_id"] for row in only_b["conversations"]] == ["conv_b"]

    since = hr.build_report(spans, top=5, log_path=log_file, bad_lines=bad, since="2026-10-02")
    assert since["summary"]["calls"] == 1
    assert since["summary"]["errors"] == 0


def test_by_name_ranking_sorted_by_total_duration(log_file):
    spans, bad = hr.load_spans(log_file)
    report = hr.build_report(spans, top=5, log_path=log_file, bad_lines=bad)
    names = [row["name"] for row in report["by_name"]]
    assert names[0] == "LLMSyncPipeline"      # 4s 最慢
    assert report["by_name"][-1]["total_ms"] <= report["by_name"][0]["total_ms"]


def test_markdown_contains_key_sections(log_file):
    spans, bad = hr.load_spans(log_file)
    report = hr.build_report(spans, top=5, log_path=log_file, bad_lines=bad)
    markdown = hr.render_markdown(report, top=5)
    for token in ("# Harness 聚合看板", "## 总览", "## 按对话", "## 耗时排行", "## 失败记录", "conv_a"):
        assert token in markdown
    assert "ValueError" in markdown


def test_cost_estimate_is_absent_without_pricing(log_file):
    spans, bad = hr.load_spans(log_file)
    report = hr.build_report(spans, top=5, log_path=log_file, bad_lines=bad)
    assert hr.PRICING == {}, "默认不应内置猜测的单价"
    assert hr.estimate_cost(report) is None


def test_cli_writes_report_and_json(log_file, tmp_path, capsys):
    out_md = tmp_path / "report.md"
    out_json = tmp_path / "report.json"
    code = hr.main(["--log", str(log_file), "--out", str(out_md), "--json", str(out_json), "--top", "3"])
    assert code == 0
    assert out_md.is_file() and "Harness" in out_md.read_text(encoding="utf-8")
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["summary"]["calls"] == 4
    captured = capsys.readouterr().out
    assert "调用 4" in captured


def test_cli_reports_missing_log(tmp_path, capsys):
    code = hr.main(["--log", str(tmp_path / "nope.jsonl"), "--out", "-"])
    assert code == 2
    assert "找不到日志文件" in capsys.readouterr().err
