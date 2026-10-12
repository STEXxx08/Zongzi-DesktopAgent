import json
import os
import re
import stat
import tempfile
from pathlib import Path

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


def _replace_section_values(
    text: str,
    section: str,
    values: dict,
    indent: int,
    legacy_sections: tuple[str, ...] = (),
):
    prefix = " " * indent
    # 旧配置可能还叫 text_input_gui —— 认它，并且就地改写它，避免"改一处漏一处"。
    names = "|".join(re.escape(name) for name in (section, *legacy_sections))
    header = re.compile(
        rf"^{re.escape(prefix)}(?:{names}):[ \t]*(?:#.*)?$",
        re.MULTILINE,
    )
    match = header.search(text)
    if match is None:
        if section != "desktop_shell":
            raise ValueError(f"配置文件中缺少 service.{section}。")
        insertion = re.search(r"^  (?:chat_window|text_input_gui):", text, re.MULTILINE)
        if insertion is None:
            raise ValueError("配置文件中缺少 service.chat_window。")
        new_section = (
            "  desktop_shell:\n"
            f"    enable: {str(values['enable']).lower()} # 是否启动桌宠界面\n"
            f"    always_on_top: {str(values['always_on_top']).lower()} # 桌宠窗口是否置顶\n"
        )
        text = text[:insertion.start()] + new_section + text[insertion.start():]
        match = header.search(text)

    next_section = re.search(
        rf"^ {{{indent}}}[A-Za-z_][A-Za-z0-9_-]*:",
        text[match.end():],
        re.MULTILINE,
    )
    section_end = match.end() + next_section.start() if next_section else len(text)
    block = text[match.end():section_end]
    field_indent = " " * (indent + 2)

    for key, value in values.items():
        if isinstance(value, str):
            encoded = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            encoded = str(value).lower()
        else:
            encoded = str(value)
        field = re.compile(
            rf"^({re.escape(field_indent)}{re.escape(key)}:[ \t]*)(.*?)([ \t]+#.*)?$",
            re.MULTILINE,
        )
        field_match = field.search(block)
        if field_match:
            comment = field_match.group(3) or ""
            block = (
                block[:field_match.start()]
                + field_match.group(1)
                + encoded
                + comment
                + block[field_match.end():]
            )
        else:
            if block and not block.endswith("\n"):
                block += "\n"
            block += f"{field_indent}{key}: {encoded}\n"

    return text[:match.end()] + block + text[section_end:]


def save_desktop_settings(desktop_shell: DesktopShellConfig, chat_window: ChatWindowConfig):
    global _config
    desktop_shell = DesktopShellConfig.model_validate(desktop_shell.model_dump())
    chat_window = ChatWindowConfig.model_validate(chat_window.model_dump())
    config_path = get_default_config_path()
    original_text = config_path.read_text(encoding="utf-8")
    updated_text = _replace_section_values(
        original_text, "desktop_shell", desktop_shell.model_dump(), indent=2
    )
    updated_text = _replace_section_values(
        updated_text,
        "chat_window",
        chat_window.model_dump(),
        indent=2,
        legacy_sections=("text_input_gui",),
    )
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

    if _config is not None:
        _config.service.desktop_shell = parsed_config.service.desktop_shell
        _config.service.chat_window = parsed_config.service.chat_window
    logger.info("桌宠设置已保存至配置文件：{}", config_path)
