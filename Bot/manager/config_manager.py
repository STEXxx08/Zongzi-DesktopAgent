import json
import os
import re
import stat
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from typeguard import typechecked

from common.generator.config_gen import ConfigFileGenerator
from config import BotConfig
from services.config import DesktopShellConfig
from ui.config import ChatWindowConfig

_project_dir = Path(__file__).resolve().parent.parent
_config: BotConfig | None = None

# 旧键 → 新键。这些键由模型校验器负责迁移并提示，这里不算"无效键"，避免重复报警。
_LEGACY_SECTION_ALIASES: dict[str, str] = {
    "text_input_gui": "chat_window",
}

# 书写时需识别别名的子节名称映射：target_key -> legacy_keys
_SECTION_ALIAS_LOOKUP: dict[str, tuple[str, ...]] = {
    "chat_window": ("text_input_gui",),
}


@typechecked
def generate_config_file(config_path: Path):
    config_path.parent.mkdir(parents=True, exist_ok=True)
    generator = ConfigFileGenerator()
    config_text = generator.generate_yaml(BotConfig())
    config_path.write_text(config_text, encoding="utf-8")
    logger.warning("未找到 `config.yaml`，已自动生成。请编辑配置文件后重新运行程序。")
    raise SystemExit(0)


@typechecked
def get_project_dir() -> Path:
    return _project_dir


@typechecked
def get_default_config_path() -> Path:
    return get_project_dir() / "config.yaml"


@typechecked
def get_config(path: Path | None = None) -> BotConfig:
    global _config
    if _config is not None:
        return _config
    config_path = path or get_default_config_path()
    if not config_path.exists():
        generate_config_file(config_path)
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    warn_unknown_config_keys(config_data, config_path)
    _config = BotConfig.model_validate(config_data)
    return _config


@typechecked
def save_config(config: BotConfig, path: Path | None = None):
    if config is None:
        raise ValueError("None can not be saved to config file.")
    config_path = path or get_default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        logger.warning("Config file already exists. Overwriting...")
    generator = ConfigFileGenerator()
    config_path.write_text(generator.generate_yaml(config), encoding="utf-8")
    logger.info("Config file was saved: {}", config_path)


def _collect_unknown_keys(node, model, prefix: str, out: list, depth: int):
    if depth > 8 or not isinstance(node, dict) or not hasattr(model, "model_fields"):
        return
    # 显式声明 extra="allow" 的模型（如 memory.memory_model）是同一份配置
    # 复用给记忆模型用的，允许出现额外的模型参数，不能报成无效键。
    if (getattr(model, "model_config", None) or {}).get("extra") == "allow":
        return
    for key, value in node.items():
        if not isinstance(key, str):
            continue
        path = f"{prefix}.{key}" if prefix else key
        field = model.model_fields.get(key)
        if field is None:
            if key in _LEGACY_SECTION_ALIASES:
                continue
            out.append(path)
            continue
        annotation = field.annotation
        sub = getattr(annotation, "__args__", ())
        target = next((item for item in sub if hasattr(item, "model_fields")), None)
        if target is None:
            target = annotation if hasattr(annotation, "model_fields") else None
        if target is not None:
            _collect_unknown_keys(value, target, path, out, depth + 1)


def warn_unknown_config_keys(config_data: dict | None, config_path: Path | None = None):
    """打印配置里 BotConfig 不认识的键。

    pydantic 的默认 `extra="ignore"` 会让拼错或已废弃的键**静默失效**：
    用户改了配置却没有任何反应，也无从知道改了没用。这里只警告、不报错，
    保证旧配置仍能启动；已由模型校验器迁移的旧键不在此列。
    """
    if not isinstance(config_data, dict):
        return
    unknown: list[str] = []
    _collect_unknown_keys(config_data, BotConfig, "", unknown, 0)
    if not unknown:
        return
    path_text = str(config_path or get_default_config_path())
    logger.warning(
        "配置文件中存在 {} 个本程序不会读取的键（它们不会生效）：{}",
        len(unknown),
        "、".join(sorted(unknown)),
    )
    logger.warning(
        "请删除或修正这些键，否则改动不会有任何效果；"
        "要确认某个键是否有效，请对照程序自动生成的默认配置。配置文件：{}",
        path_text,
    )


def _split_inline_comment(rest: str) -> tuple[str, str]:
    """拆分 YAML 键冒号后的值部分与行尾注释（跳过单双引号内部的 #）。"""
    quote: str | None = None
    i = 0
    n = len(rest)
    while i < n:
        ch = rest[i]
        if quote == "'":
            if ch == "'":
                if i + 1 < n and rest[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        elif quote == '"':
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
            elif ch == "#" and (i == 0 or rest[i - 1].isspace()):
                # 保留注释前的分隔空白，确保原格式不乱
                j = i
                while j > 0 and rest[j - 1] in (" ", "\t"):
                    j -= 1
                return rest[:j], rest[j:]
        i += 1
    return rest, ""


def _has_unclosed_quote(text: str) -> str | None:
    """检查单行标量起始部分是否存在未闭合的单/双引号，返回引号字符或 None。"""
    stripped = text.strip()
    if not stripped or stripped[0] not in ("'", '"'):
        return None
    quote = stripped[0]
    i = 1
    n = len(stripped)
    while i < n:
        ch = stripped[i]
        if quote == "'":
            if ch == "'":
                if i + 1 < n and stripped[i + 1] == "'":
                    i += 2
                    continue
                return None
        else:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                return None
        i += 1
    return quote


def _closes_quote(line: str, quote: str) -> bool:
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote == "'":
            if ch == "'":
                if i + 1 < n and line[i + 1] == "'":
                    i += 2
                    continue
                return True
        else:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                return True
        i += 1
    return False


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _find_key_span(lines: list[str], start: int, base_indent: int, val_part: str) -> int:
    """返回第 start 行所在键的完整结束行索引（不含），覆盖子块、块列表、块标量与折行文本。"""
    n = len(lines)
    stripped_val = val_part.strip()

    # 1. 未闭合引号跨行文本
    open_quote = _has_unclosed_quote(stripped_val)
    if open_quote is not None:
        k = start + 1
        while k < n:
            if _closes_quote(lines[k], open_quote):
                return k + 1
            k += 1
        return n

    # 2. YAML 块标量 | / >
    if stripped_val.startswith(("|", ">")):
        last_child = start
        k = start + 1
        while k < n:
            line = lines[k]
            if not line.strip():
                k += 1
                continue
            if _line_indent(line) > base_indent:
                last_child = k
                k += 1
                continue
            break
        return last_child + 1

    # 3. 冒号后为空：子字典或块列表
    if not stripped_val:
        last_child = start
        k = start + 1
        while k < n:
            line = lines[k]
            stripped = line.strip()
            if not stripped:
                k += 1
                continue
            ind = _line_indent(line)
            if stripped.startswith("#"):
                if ind > base_indent:
                    k += 1
                    continue
                break
            if ind > base_indent or (ind == base_indent and (stripped.startswith("- ") or stripped == "-")):
                last_child = k
                k += 1
                continue
            break
        return last_child + 1

    # 4. 普通行内标量（包含可能存在的多行缩进折行）
    last_child = start
    k = start + 1
    while k < n:
        line = lines[k]
        stripped = line.strip()
        if not stripped:
            k += 1
            continue
        ind = _line_indent(line)
        if ind <= base_indent:
            break
        if stripped.startswith("#"):
            k += 1
            continue
        last_child = k
        k += 1
    return last_child + 1


def _format_yaml_leaf_lines(key: str, value: Any, indent: int, comment: str = "") -> list[str]:
    """将叶子值格式化为保缩进与注释的 YAML 行列表。"""
    prefix = " " * indent
    if isinstance(value, Enum):
        value = value.value

    if isinstance(value, str) and "\n" in value:
        normalized = value.replace("\r\n", "\n")
        if normalized.endswith("\n") and not normalized.endswith("\n\n"):
            indicator = "|"
            raw_lines = normalized[:-1].split("\n")
        else:
            indicator = "|-"
            raw_lines = normalized.split("\n")
        header = f"{prefix}{key}: {indicator}{comment}"
        body_indent = " " * (indent + 2)
        body_lines = [
            f"{body_indent}{part}" if part != "" else ""
            for part in raw_lines
        ]
        return [header, *body_lines]

    if value is None:
        encoded = "null"
    elif isinstance(value, bool):
        encoded = "true" if value else "false"
    elif isinstance(value, (int, float)):
        encoded = str(value)
    elif isinstance(value, str):
        encoded = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, (list, tuple)):
        encoded = json.dumps(list(value), ensure_ascii=False)
    elif isinstance(value, dict):
        encoded = json.dumps(value, ensure_ascii=False)
    else:
        encoded = json.dumps(str(value), ensure_ascii=False)

    return [f"{prefix}{key}: {encoded}{comment}"]


def _scan_block_keys(lines: list[str], indent: int) -> list[tuple[str, int, int, str]]:
    """扫描当前缩进层的所有键，返回 [(key_name, start_idx, end_idx, comment), ...]。"""
    prefix = " " * indent
    key_pattern = re.compile(rf"^{re.escape(prefix)}([A-Za-z_][A-Za-z0-9_-]*):(.*)$")
    found: list[tuple[str, int, int, str]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        ind = _line_indent(line)
        if ind == indent:
            match = key_pattern.match(line)
            if match:
                key_name = match.group(1)
                val_part, comment = _split_inline_comment(match.group(2))
                end_idx = _find_key_span(lines, i, indent, val_part)
                found.append((key_name, i, end_idx, comment))
                i = end_idx
                continue
        i += 1
    return found


def _trailing_blank_or_comment_offset(lines: list[str]) -> int:
    """找到段落末尾适合插入新键的位置（排在末尾独立空白/下一段注释之前）。"""
    idx = len(lines)
    while idx > 0 and (not lines[idx - 1].strip() or lines[idx - 1].lstrip(" ").startswith("#")):
        idx -= 1
    return idx


def _update_yaml_block(lines: list[str], updates: dict[str, Any], indent: int = 0) -> list[str]:
    """在保持原有注释、空行和未知键的前提下递归更新 YAML 文本块。"""
    result = list(lines)
    prefix = " " * indent

    for key, new_val in updates.items():
        scanned = _scan_block_keys(result, indent)
        aliases = _SECTION_ALIAS_LOOKUP.get(key, ())
        target_names = (key, *aliases)

        hit = None
        for item in scanned:
            if item[0] in target_names:
                hit = item
                break

        is_nonempty_dict = isinstance(new_val, dict) and len(new_val) > 0

        if hit is not None:
            matched_name, start_idx, end_idx, comment = hit
            if is_nonempty_dict:
                header_line = f"{prefix}{key}:{comment}"
                sub_lines = _update_yaml_block(result[start_idx + 1 : end_idx], new_val, indent + 2)
                result = result[:start_idx] + [header_line, *sub_lines] + result[end_idx:]
            else:
                leaf_lines = _format_yaml_leaf_lines(key, new_val, indent, comment)
                result = result[:start_idx] + leaf_lines + result[end_idx:]
        else:
            # 旧配置若缺少 service.desktop_shell，优先插入在 chat_window 之前以兼容历史迁移约定
            insert_idx = None
            if indent == 2 and key == "desktop_shell":
                for item in scanned:
                    if item[0] in ("chat_window", "text_input_gui"):
                        insert_idx = item[1]
                        break
            if insert_idx is None:
                insert_idx = _trailing_blank_or_comment_offset(result)

            if is_nonempty_dict:
                header_line = f"{prefix}{key}:"
                sub_lines = _update_yaml_block([], new_val, indent + 2)
                new_block = [header_line, *sub_lines]
            else:
                new_block = _format_yaml_leaf_lines(key, new_val, indent)

            result = result[:insert_idx] + new_block + result[insert_idx:]

    return result


def _replace_nested_values(text: str, updates: dict[str, Any], indent: int = 0) -> str:
    """对完整 YAML 文本执行多层级保注释更新。"""
    has_trailing_newline = text.endswith("\n")
    lines = text.splitlines()
    updated_lines = _update_yaml_block(lines, updates, indent=indent)
    joined = "\n".join(updated_lines)
    if has_trailing_newline or updated_lines:
        joined += "\n"
    return joined


def _replace_section_values(
    text: str,
    section: str,
    values: dict,
    indent: int,
    legacy_sections: tuple[str, ...] = (),
):
    """兼容单小节替换接口（保留供既有测试及扩展调用）。"""
    if indent == 2 and section in ("desktop_shell", "chat_window"):
        return _replace_nested_values(text, {"service": {section: values}}, indent=0)
    if legacy_sections and section not in _SECTION_ALIAS_LOOKUP:
        _SECTION_ALIAS_LOOKUP[section] = tuple(legacy_sections)
    return _replace_nested_values(text, {section: values}, indent=indent)


def save_bot_settings(updates: dict[str, Any], path: Path | None = None) -> BotConfig:
    """将嵌套配置更新保注释写回 config.yaml，执行全量 Pydantic 校验并刷新内存单例。"""
    global _config
    config_path = path or get_default_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"未找到配置文件：{config_path}")
    original_text = config_path.read_text(encoding="utf-8")
    updated_text = _replace_nested_values(original_text, updates, indent=0)
    parsed_config = BotConfig.model_validate(yaml.safe_load(updated_text))

    file_mode = stat.S_IMODE(config_path.stat().st_mode)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=config_path.parent, delete=False
        ) as temporary_file:
            temporary_file.write(updated_text)
            temporary_path = Path(temporary_file.name)
        os.chmod(temporary_path, file_mode)
        os.replace(temporary_path, config_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    if _config is not None and config_path == get_default_config_path():
        _config.pipeline = parsed_config.pipeline
        _config.service = parsed_config.service
        _config.character = parsed_config.character
        _config.system = parsed_config.system
        _config.memory = parsed_config.memory
    logger.info("配置已保注释保存至：{}", config_path)
    return parsed_config


def save_desktop_settings(desktop_shell: DesktopShellConfig, chat_window: ChatWindowConfig):
    desktop_shell = DesktopShellConfig.model_validate(desktop_shell.model_dump())
    chat_window = ChatWindowConfig.model_validate(chat_window.model_dump())
    save_bot_settings(
        {
            "service": {
                "desktop_shell": desktop_shell.model_dump(),
                "chat_window": chat_window.model_dump(),
            }
        }
    )
    logger.info("桌宠设置已保存至配置文件：{}", get_default_config_path())
