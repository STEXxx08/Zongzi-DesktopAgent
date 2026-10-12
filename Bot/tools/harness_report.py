"""Harness 日志聚合看板（只读，不参与业务）。

把 `logs/harness.jsonl` 里按 call_id 记录的 started/finished 事件汇总成：
- 按对话（conversation_id）的轮数、耗时分布、token 用量、缓存命中率、工具调用与错误；
- 按调用名的耗时与 token 排行；
- 全程概览。

输出默认写到日志目录下的 harness_report.md，方便直接看；同时可输出 JSON。

用法：
    conda activate MyAI
    python tools/harness_report.py                       # 全量，写 Bot/logs/harness_report.md
    python tools/harness_report.py --since 2026-10-01    # 只看某个时间之后
    python tools/harness_report.py --conversation conv_xxx
    python tools/harness_report.py --top 15 --out -      # 打到标准输出
    python tools/harness_report.py --json Bot/logs/harness_report.json

说明：不引入任何第三方依赖，也不 import 应用代码，可以随时跑。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_DEFAULT_LOG_NAME = "harness.jsonl"
_DEFAULT_REPORT_NAME = "harness_report.md"
_TOOL_KINDS = {"tool"}


@dataclass
class Span:
    """一个已完成（或失败）的调用记录。"""

    call_id: str
    kind: str
    name: str
    event: str
    timestamp: str
    duration_ms: float | None = None
    status: str | None = None
    conversation_id: str | None = None
    turn_id: int | None = None
    round: int | None = None
    source: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
    usage_status: str | None = None
    error_type: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return (self.status or "success") == "success"

    @property
    def cache_total(self) -> int | None:
        if self.cache_hit_tokens is None and self.cache_miss_tokens is None:
            return None
        return (self.cache_hit_tokens or 0) + (self.cache_miss_tokens or 0)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _usage_fields(usage: Any) -> dict[str, int | None]:
    """从 usage / provider_usage 里取出 token 与缓存命中（字段名兼容多家服务商）。"""
    empty = {"input_tokens": None, "output_tokens": None, "total_tokens": None,
             "cache_hit_tokens": None, "cache_miss_tokens": None, "usage_status": None}
    if not isinstance(usage, dict):
        return empty
    empty["usage_status"] = usage.get("status")
    empty["input_tokens"] = _int(usage.get("input_tokens"))
    empty["output_tokens"] = _int(usage.get("output_tokens"))
    empty["total_tokens"] = _int(usage.get("total_tokens"))
    provider = usage.get("provider_usage")
    if isinstance(provider, dict):
        for key in ("prompt_cache_hit_tokens", "cache_hit_tokens", "cached_tokens"):
            if _int(provider.get(key)) is not None:
                empty["cache_hit_tokens"] = provider[key]
                break
        for key in ("prompt_cache_miss_tokens", "cache_miss_tokens"):
            if _int(provider.get(key)) is not None:
                empty["cache_miss_tokens"] = provider[key]
                break
        details = provider.get("prompt_tokens_details") or provider.get("input_tokens_details")
        if isinstance(details, dict):
            if empty["cache_hit_tokens"] is None and _int(details.get("cached_tokens")) is not None:
                empty["cache_hit_tokens"] = details["cached_tokens"]
    return empty


def load_spans(path: Path) -> tuple[list[Span], int]:
    """读取 jsonl；返回 (记录列表, 无法解析的行数)。"""
    spans: list[Span] = []
    bad = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            bad += 1
            continue
        if row.get("event") == "started":
            # started 与 finished 成对出现，聚合只看完成/失败记录，避免双计
            continue
        error = row.get("error") if isinstance(row.get("error"), dict) else {}
        spans.append(Span(
            call_id=row.get("call_id") or "",
            kind=row.get("kind") or "",
            name=row.get("name") or "",
            event=row.get("event") or "",
            timestamp=row.get("timestamp") or "",
            duration_ms=row.get("duration_ms") if isinstance(row.get("duration_ms"), (int, float)) else None,
            status=row.get("status"),
            conversation_id=row.get("conversation_id"),
            turn_id=_int(row.get("turn_id")),
            round=_int(row.get("round")),
            source=row.get("source"),
            error_type=error.get("type"),
            error_message=error.get("message"),
            **_usage_fields(row.get("usage")),
        ))
    return spans, bad


@dataclass
class ConversationStats:
    conversation_id: str
    turns: set[int] = field(default_factory=set)
    spans: list[Span] = field(default_factory=list)

    @property
    def durations(self) -> list[float]:
        return [s.duration_ms for s in self.spans if s.duration_ms is not None]

    @property
    def tokens(self) -> dict[str, int]:
        return {
            "input": sum(s.input_tokens or 0 for s in self.spans),
            "output": sum(s.output_tokens or 0 for s in self.spans),
            "total": sum(s.total_tokens or 0 for s in self.spans),
            "cache_hit": sum(s.cache_hit_tokens or 0 for s in self.spans),
            "cache_miss": sum(s.cache_miss_tokens or 0 for s in self.spans),
        }

    @property
    def errors(self) -> list[Span]:
        return [s for s in self.spans if not s.ok]

    @property
    def tool_calls(self) -> list[Span]:
        return [s for s in self.spans if s.kind in _TOOL_KINDS]


def _percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(ratio * (len(ordered) - 1)))))
    return ordered[index]


def _fmt_ms(value: float | None) -> str:
    if value is None:
        return "-"
    if value >= 1000:
        return f"{value / 1000:.2f} s"
    return f"{value:.0f} ms"


def _fmt_int(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _cache_ratio(hit: int, miss: int) -> str:
    total = hit + miss
    if total <= 0:
        return "-"
    return f"{hit / total * 100:.1f}% ({hit:,}/{total:,})"


def build_report(spans: Iterable[Span], *, top: int, log_path: Path, bad_lines: int,
                 since: str | None = None, conversation: str | None = None) -> dict[str, Any]:
    selected = list(spans)
    if since:
        selected = [s for s in selected if (s.timestamp or "") >= since]
    if conversation:
        selected = [s for s in selected if s.conversation_id == conversation]

    grouped: dict[str, ConversationStats] = {}
    for span in selected:
        key = span.conversation_id or "(无 conversation_id)"
        stats = grouped.setdefault(key, ConversationStats(conversation_id=key))
        stats.spans.append(span)
        if span.turn_id is not None:
            stats.turns.add(span.turn_id)

    for stats in grouped.values():
        stats.spans.sort(key=lambda s: s.timestamp)

    total_durations = [s.duration_ms for s in selected if s.duration_ms is not None]
    total_tokens = Counter()
    cache_hit = cache_miss = 0
    for span in selected:
        total_tokens["input"] += span.input_tokens or 0
        total_tokens["output"] += span.output_tokens or 0
        total_tokens["total"] += span.total_tokens or 0
        cache_hit += span.cache_hit_tokens or 0
        cache_miss += span.cache_miss_tokens or 0

    errors = [s for s in selected if not s.ok]
    by_name: dict[str, list[Span]] = defaultdict(list)
    for span in selected:
        by_name[span.name or "(未命名)"].append(span)

    name_rows = []
    for name, items in by_name.items():
        durations = [s.duration_ms for s in items if s.duration_ms is not None]
        name_rows.append({
            "name": name,
            "kind": items[0].kind,
            "count": len(items),
            "failed": sum(1 for s in items if not s.ok),
            "p50_ms": _percentile(durations, 0.5),
            "p95_ms": _percentile(durations, 0.95),
            "max_ms": max(durations) if durations else None,
            "total_ms": sum(durations) if durations else None,
            "input_tokens": sum(s.input_tokens or 0 for s in items),
            "output_tokens": sum(s.output_tokens or 0 for s in items),
            "cache_hit": sum(s.cache_hit_tokens or 0 for s in items),
            "cache_miss": sum(s.cache_miss_tokens or 0 for s in items),
        })
    name_rows.sort(key=lambda row: row["total_ms"] or 0, reverse=True)

    conversation_rows = []
    for stats in grouped.values():
        durations = stats.durations
        tokens = stats.tokens
        conversation_rows.append({
            "conversation_id": stats.conversation_id,
            "calls": len(stats.spans),
            "turns": max(stats.turns) if stats.turns else 0,
            "first": stats.spans[0].timestamp if stats.spans else "",
            "last": stats.spans[-1].timestamp if stats.spans else "",
            "p50_ms": _percentile(durations, 0.5),
            "p95_ms": _percentile(durations, 0.95),
            "total_ms": sum(durations) if durations else 0.0,
            "errors": len(stats.errors),
            "tool_calls": len(stats.tool_calls),
            **tokens,
        })
    conversation_rows.sort(key=lambda row: row["first"])

    return {
        "log_path": str(log_path),
        "bad_lines": bad_lines,
        "summary": {
            "calls": len(selected),
            "conversations": len(grouped),
            "errors": len(errors),
            "p50_ms": _percentile(total_durations, 0.5),
            "p95_ms": _percentile(total_durations, 0.95),
            "total_ms": sum(total_durations) if total_durations else 0.0,
            "input_tokens": total_tokens["input"],
            "output_tokens": total_tokens["output"],
            "total_tokens": total_tokens["total"],
            "cache_hit": cache_hit,
            "cache_miss": cache_miss,
            "no_usage_calls": sum(1 for s in selected if s.usage_status not in {"reported", None}),
        },
        "conversations": conversation_rows,
        "by_name": name_rows[:top],
        "errors": [
            {"timestamp": s.timestamp, "kind": s.kind, "name": s.name,
             "conversation_id": s.conversation_id, "turn_id": s.turn_id,
             "type": s.error_type, "message": (s.error_message or "")[:300]}
            for s in errors[:top]
        ],
    }


def render_markdown(report: dict[str, Any], *, top: int) -> str:
    summary = report["summary"]
    lines: list[str] = []
    lines.append("# Harness 聚合看板")
    lines.append("")
    lines.append(f"- 数据源：`{report['log_path']}`")
    lines.append(f"- 生成时间：{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
    if report["bad_lines"]:
        lines.append(f"- ⚠️ 无法解析的行：{report['bad_lines']}（已跳过，不影响其余统计）")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 已完成调用 | {_fmt_int(summary['calls'])} |")
    lines.append(f"| 对话数 | {_fmt_int(summary['conversations'])} |")
    lines.append(f"| 失败调用 | {_fmt_int(summary['errors'])} |")
    lines.append(f"| 耗时合计 | {_fmt_ms(summary['total_ms'])} |")
    lines.append(f"| 耗时 P50 / P95 | {_fmt_ms(summary['p50_ms'])} / {_fmt_ms(summary['p95_ms'])} |")
    lines.append(f"| 输入 / 输出 token | {_fmt_int(summary['input_tokens'])} / {_fmt_int(summary['output_tokens'])} |")
    lines.append(f"| token 合计 | {_fmt_int(summary['total_tokens'])} |")
    lines.append(f"| 缓存命中 | {_cache_ratio(summary['cache_hit'], summary['cache_miss'])} |")
    lines.append("")

    lines.append("## 按对话")
    lines.append("")
    lines.append("| 对话 | 轮数 | 调用 | 失败 | 工具 | 耗时合计 | P95 | 输入 token | 输出 token | 缓存命中 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for row in report["conversations"]:
        lines.append(
            f"| `{row['conversation_id']}` | {row['turns']} | {row['calls']} | {row['errors']} | "
            f"{row['tool_calls']} | {_fmt_ms(row['total_ms'])} | {_fmt_ms(row['p95_ms'])} | "
            f"{_fmt_int(row['input'])} | {_fmt_int(row['output'])} | "
            f"{_cache_ratio(row['cache_hit'], row['cache_miss'])} |"
        )
    lines.append("")

    lines.append(f"## 耗时排行（前 {top}）")
    lines.append("")
    lines.append("| 调用 | 类型 | 次数 | 失败 | 合计 | P50 | P95 | 最长 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in report["by_name"]:
        lines.append(
            f"| `{row['name']}` | {row['kind']} | {row['count']} | {row['failed']} | "
            f"{_fmt_ms(row['total_ms'])} | {_fmt_ms(row['p50_ms'])} | {_fmt_ms(row['p95_ms'])} | "
            f"{_fmt_ms(row['max_ms'])} |"
        )
    lines.append("")

    if report["errors"]:
        lines.append(f"## 失败记录（前 {len(report['errors'])}）")
        lines.append("")
        lines.append("| 时间 | 类型 | 调用 | 对话 | 轮 | 错误 |")
        lines.append("|---|---|---|---|---|---|")
        for row in report["errors"]:
            message = (row["message"] or "").replace("|", "\\|").replace("\n", " ")[:160]
            lines.append(
                f"| {row['timestamp'][:19]} | {row['kind']} | `{row['name']}` | "
                f"`{row['conversation_id']}` | {row['turn_id']} | {row['type']}: {message} |"
            )
        lines.append("")
    else:
        lines.append("## 失败记录")
        lines.append("")
        lines.append("没有失败记录。")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> 成本换算需要单价：在 `tools/harness_report.py` 的 `PRICING` 里填上你实际使用的")
    lines.append("> 单价（元/百万 token）后，`--json` 输出会附上 `cost_estimate`。未填写时不做任何猜测。")
    return "\n".join(lines)


# 单价表：默认留空，避免用记忆里的价格冒充你实际结算价。
# 形如：{"模型名的子串": {"input": 2.0, "output": 8.0, "cache_hit": 0.5}}
PRICING: dict[str, dict[str, float]] = {}


def estimate_cost(report: dict[str, Any]) -> dict[str, Any] | None:
    if not PRICING:
        return None
    summary = report["summary"]
    # 只按最贵一档粗算：日志里的模型名不一定能唯一映射到价目表
    first = next(iter(PRICING.values()))
    hit = summary["cache_hit"]
    miss = max(summary["cache_miss"], summary["input_tokens"] - hit)
    cost = (
        miss / 1_000_000 * first.get("input", 0.0)
        + hit / 1_000_000 * first.get("cache_hit", first.get("input", 0.0))
        + summary["output_tokens"] / 1_000_000 * first.get("output", 0.0)
    )
    return {"currency": "CNY", "note": "按 PRICING 中第一档单价粗算", "amount": round(cost, 4)}


def main(argv: list[str] | None = None) -> int:
    default_log = Path(__file__).resolve().parent.parent / "logs" / _DEFAULT_LOG_NAME
    parser = argparse.ArgumentParser(description="聚合 harness.jsonl 生成看板")
    parser.add_argument("--log", default=str(default_log), help="harness.jsonl 路径")
    parser.add_argument("--out", default=None,
                        help=f"Markdown 输出路径；'-' 表示标准输出；默认写到日志目录的 {_DEFAULT_REPORT_NAME}")
    parser.add_argument("--json", dest="json_out", default=None, help="可选的 JSON 输出路径")
    parser.add_argument("--since", default=None, help="只看该 ISO 时间之后的记录，例如 2026-10-01")
    parser.add_argument("--conversation", default=None, help="只看某个 conversation_id")
    parser.add_argument("--top", type=int, default=12, help="排行与失败列表条数")
    args = parser.parse_args(argv)

    log_path = Path(args.log)
    if not log_path.is_file():
        print(f"找不到日志文件：{log_path}", file=sys.stderr)
        return 2

    spans, bad = load_spans(log_path)
    report = build_report(spans, top=args.top, log_path=log_path, bad_lines=bad,
                          since=args.since, conversation=args.conversation)
    markdown = render_markdown(report, top=args.top)

    if args.out == "-":
        print(markdown)
    else:
        out_path = Path(args.out) if args.out else log_path.parent / _DEFAULT_REPORT_NAME
        out_path.write_text(markdown, encoding="utf-8")
        print(f"看板已写入：{out_path}")

    if args.json_out:
        cost = estimate_cost(report)
        if cost:
            report["cost_estimate"] = cost
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON 已写入：{args.json_out}")

    summary = report["summary"]
    print(
        f"调用 {summary['calls']}｜对话 {summary['conversations']}｜失败 {summary['errors']}｜"
        f"P95 {_fmt_ms(summary['p95_ms'])}｜token {_fmt_int(summary['total_tokens'])}｜"
        f"缓存命中 {_cache_ratio(summary['cache_hit'], summary['cache_miss'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
