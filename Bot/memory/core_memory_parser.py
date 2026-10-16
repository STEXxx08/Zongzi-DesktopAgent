"""core_memory.md 的三级 Markdown 解析器。

档案结构（见《记忆检索实施方案.md》§4.5.1）：

```markdown
[长期]:
  - 用户名为赛艇 (自2026-06-18起, 最后确认于2026-08-04)

[标记]:
  - 今天在打《历史模拟器：崇祯》

[2026-10]:
  - [14]:
      - 21:30 用户测试我完成度
```

- `[长期]` / `[标记]` 是**无日期的顶层方括号段**；`[YYYY-MM]` 是月份段，
  其下的 `- [DD]:` 是日期块，再下一层的 `- HH:MM 内容` / `- 内容` 是条目。
- 解析器必须容忍：缩进空格数不一、缺 `[标记]` 段、日期块跳跃（不连续）、
  条目缺 `HH:MM` 前缀、`[长期]` 的元数据格式漂移。

本模块只做解析与**无损渲染**：每个条目都保留原样的 `raw` 文本，渲染时按
规范缩进（日期块 2 空格、条目 6 空格）重新拼接，因此解析→渲染不会丢失信息。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

LONG_TERM_TITLE = "长期"
PINNED_TITLE = "标记"

KIND_LONG_TERM = "long_term"
KIND_PINNED = "pinned"
KIND_ENTRY = "entry"

_DAY_INDENT = "  "
_ENTRY_INDENT = "      "

_SECTION_RE = re.compile(r"^\[(?P<title>[^\[\]]+)\]\s*:?\s*$")
_MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{1,2})$")
# 容错：模型偶尔会写成 `[2026-10-14]:` 这样的"日期段"，等价于月份段 + 一个日期块。
_FULL_DATE_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})$")
_DAY_RE = re.compile(r"^-\s*\[(?P<day>\d{1,2})\]\s*:?\s*$")
_ENTRY_RE = re.compile(r"^-\s*(?P<body>.*)$")
_TIME_RE = re.compile(
    r"^(?P<time>\d{1,2}:\d{2}(?:\s*-\s*\d{1,2}:\d{2})?)\s+(?P<rest>.*)$"
)
_LONG_TERM_META_RE = re.compile(
    r"^(?P<text>.*?)\s*\(\s*自\s*(?P<since>\d{4}-\d{2}-\d{2})\s*起"
    r"\s*(?:,\s*最后确认于\s*(?P<confirmed>\d{4}-\d{2}-\d{2})\s*)?\)\s*$"
)


def _parse_iso(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Entry:
    """一条记忆条目（`[长期]` / `[标记]` / 日期块条目共用）。"""

    kind: str
    raw: str
    date: Optional[date] = None
    time: Optional[str] = None
    since: Optional[date] = None
    last_confirmed: Optional[date] = None
    line: int = 0
    order: int = 0

    @property
    def body(self) -> str:
        """去掉时间前缀后的正文（时间前缀存在时）。"""
        if not self.time:
            return self.raw
        match = _TIME_RE.match(self.raw)
        return match.group("rest").strip() if match else self.raw

    @property
    def chunk_text(self) -> str:
        """索引用的文本：带日期与时间前缀，让向量与展示都自带时间线索。"""
        prefix = self.date.isoformat() if self.date else ""
        return f"{prefix} {self.raw}".strip()

    def render(self) -> str:
        if self.kind == KIND_ENTRY:
            return f"{_ENTRY_INDENT}- {self.raw}"
        return f"{_DAY_INDENT}- {self.raw}"


@dataclass(frozen=True)
class DayBlock:
    """一个日期块（`- [DD]:` 及其下所有条目）。"""

    date: Optional[date]
    entries: tuple[Entry, ...]
    line: int = 0

    @property
    def sort_key(self) -> date:
        return self.date or date.min

    @property
    def chars(self) -> int:
        return sum(len(entry.raw) for entry in self.entries)


@dataclass
class ParsedCoreMemory:
    """整份档案的解析结果。"""

    long_term: tuple[Entry, ...] = ()
    pinned: tuple[Entry, ...] = ()
    days: tuple[DayBlock, ...] = ()  # 按日期倒序（与档案一致）
    text: str = ""

    @property
    def entries(self) -> tuple[Entry, ...]:
        return tuple(entry for day in self.days for entry in day.entries)

    @property
    def day_count(self) -> int:
        return len(self.days)

    @property
    def entry_count(self) -> int:
        return sum(len(day.entries) for day in self.days)


def parse_core_memory(text: str) -> ParsedCoreMemory:
    """把 core_memory.md 解析为结构化档案；格式漂移不抛异常。"""
    text = text or ""
    long_term: list[Entry] = []
    pinned: list[Entry] = []
    day_blocks: list[DayBlock] = []
    day_entries: list[Entry] = []
    current_day: Optional[date] = None
    current_day_line = 0
    year: Optional[int] = None
    month: Optional[int] = None
    mode = ""
    order = 0

    def flush_day() -> None:
        nonlocal day_entries, current_day
        if current_day is not None or day_entries:
            day_blocks.append(
                DayBlock(date=current_day, entries=tuple(day_entries), line=current_day_line)
            )
        day_entries = []
        current_day = None

    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())

        if indent == 0:
            section = _SECTION_RE.match(stripped)
            if section:
                title = section.group("title").strip()
                if title == LONG_TERM_TITLE:
                    flush_day()
                    mode = KIND_LONG_TERM
                elif title == PINNED_TITLE:
                    flush_day()
                    mode = KIND_PINNED
                else:
                    month_match = _MONTH_RE.match(title)
                    full_date_match = _FULL_DATE_RE.match(title)
                    if month_match or full_date_match:
                        flush_day()
                        source = month_match or full_date_match
                        year = int(source.group("year"))
                        month = int(source.group("month"))
                        mode = KIND_ENTRY
                        if full_date_match:
                            current_day_line = index
                            try:
                                current_day = date(year, month, int(full_date_match.group("day")))
                            except ValueError:
                                current_day = None
                    else:
                        flush_day()
                        mode = ""
                continue

        if not stripped.startswith("-"):
            continue

        if mode == KIND_ENTRY:
            day_match = _DAY_RE.match(stripped)
            if day_match:
                flush_day()
                current_day_line = index
                day_number = int(day_match.group("day"))
                try:
                    current_day = date(year, month, day_number) if year and month else None
                except ValueError:
                    current_day = None
                continue

        entry_match = _ENTRY_RE.match(stripped)
        if not entry_match:
            continue
        raw = entry_match.group("body").strip()
        if not raw:
            continue

        if mode == KIND_LONG_TERM:
            meta = _LONG_TERM_META_RE.match(raw)
            order += 1
            long_term.append(Entry(
                kind=KIND_LONG_TERM,
                raw=raw,
                since=_parse_iso(meta.group("since")) if meta else None,
                last_confirmed=_parse_iso(meta.group("confirmed")) if meta else None,
                line=index,
                order=order,
            ))
            continue

        if mode == KIND_PINNED:
            order += 1
            pinned.append(Entry(kind=KIND_PINNED, raw=raw, line=index, order=order))
            continue

        if mode == KIND_ENTRY:
            time_match = _TIME_RE.match(raw)
            order += 1
            day_entries.append(Entry(
                kind=KIND_ENTRY,
                raw=raw,
                date=current_day,
                time=time_match.group("time") if time_match else None,
                line=index,
                order=order,
            ))

    flush_day()

    # 日期块按日期倒序（档案本身就是倒序；这里做一次稳定排序以防漂移）。
    day_blocks = [block for block in day_blocks if block.entries]
    day_blocks.sort(key=lambda block: (block.sort_key, block.line), reverse=True)

    return ParsedCoreMemory(
        long_term=tuple(long_term),
        pinned=tuple(pinned),
        days=tuple(day_blocks),
        text=text,
    )


def render_long_term(entries: Iterable[Entry]) -> str:
    entries = tuple(entries)
    if not entries:
        return ""
    lines = [f"[{LONG_TERM_TITLE}]:", *(entry.render() for entry in entries)]
    return "\n".join(lines)


def render_pinned(entries: Iterable[Entry]) -> str:
    entries = tuple(entries)
    if not entries:
        return ""
    lines = [f"[{PINNED_TITLE}]:", *(entry.render() for entry in entries)]
    return "\n".join(lines)


def render_days(blocks: Iterable[DayBlock]) -> str:
    """按月份分组渲染日期块（月份倒序，日期块倒序）。"""
    groups: dict[str, list[DayBlock]] = {}
    order: list[str] = []
    for block in blocks:
        key = block.date.strftime("%Y-%m") if block.date else "未知月份"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(block)
    sections: list[str] = []
    for key in order:
        lines = [f"[{key}]:"]
        for block in groups[key]:
            label = f"{block.date.day:02d}" if block.date else "??"
            lines.append(f"{_DAY_INDENT}- [{label}]:")
            lines.extend(entry.render() for entry in block.entries)
        sections.append("\n".join(lines))
    return "\n".join(sections)


def is_pinned_header(line: str) -> bool:
    if line[:1].isspace():
        return False
    section = _SECTION_RE.match(line.strip())
    return bool(section) and section.group("title").strip() == PINNED_TITLE


def _is_section_header(line: str) -> bool:
    if line[:1].isspace():
        return False
    return bool(_SECTION_RE.match(line.strip()))


def split_pinned_segment(text: str) -> tuple[str, str]:
    """剥离 `[标记]` 段。

    返回 `(去掉标记段的正文, 标记段原文)`：标记段原文**逐字节**保留（含段标题行），
    拼回去时必须原样还原，因此调用方不得对其做任何加工。
    """
    text = text or ""
    lines = text.split("\n")
    start = None
    for index, line in enumerate(lines):
        if is_pinned_header(line):
            start = index
            break
    if start is None:
        return text, ""

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _is_section_header(lines[index]):
            end = index
            break

    segment_lines = lines[start:end]
    while segment_lines and not segment_lines[-1].strip():
        segment_lines.pop()
    segment = "\n".join(segment_lines)
    remaining = "\n".join(lines[:start] + lines[end:])
    return remaining, segment


def splice_pinned_segment(text: str, segment: str) -> str:
    """把 `[标记]` 段原文插回 `[长期]` 之后、第一个月份之前。"""
    if not segment.strip():
        return text
    lines = (text or "").split("\n")
    insert_at = len(lines)
    for index, line in enumerate(lines):
        if not _is_section_header(line) or is_pinned_header(line):
            continue
        section = _SECTION_RE.match(line.strip())
        # 只插在第一个**月份段**之前：`[长期]` 必须留在最上面。
        if section and _MONTH_RE.match(section.group("title").strip()):
            insert_at = index
            break
    segment_lines = segment.split("\n")
    merged = lines[:insert_at] + segment_lines + lines[insert_at:]
    return "\n".join(merged)
