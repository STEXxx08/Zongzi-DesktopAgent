import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from services.skill_framework.config import SkillsServiceConfig


logger = logging.getLogger(__name__)


_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TRUE_VALUES = {"true", "yes", "on", "1"}
_FALSE_VALUES = {"false", "no", "off", "0"}


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    when_to_use: str | None
    model_invocable: bool
    user_invocable: bool
    path: Path
    resource_base: Path


@dataclass(frozen=True)
class SkillDefinition(SkillSummary):
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SkillRegistry:
    """发现并加载与 DeepSeek Harness 文件格式兼容的本地 Skill。"""

    def __init__(
        self,
        config: SkillsServiceConfig,
        *,
        project_root: Path | None = None,
    ):
        self.config = config
        self.project_root = (project_root or self._find_project_root()).resolve()

    @staticmethod
    def _find_project_root() -> Path:
        """从应用代码位置定位项目根目录，不依赖进程当前工作目录。"""
        module_path = Path(__file__).resolve()
        for candidate in module_path.parents:
            if (
                (candidate / "config.yaml").is_file()
                and (candidate / "services" / "skill_framework").is_dir()
            ):
                return candidate
        # 标准源码布局为 <project>/services/skill_framework/registry.py。
        # 即使配置文件暂时缺失，也仍以代码包所在位置作为稳定锚点。
        return module_path.parents[2]

    def update_config(self, config: SkillsServiceConfig) -> None:
        self.config = config

    def list(self, *, model_invocable_only: bool = False) -> tuple[SkillSummary, ...]:
        if not self.config.enable:
            return ()
        discovered: dict[str, SkillSummary] = {}
        for root in self._resolved_roots():
            for path, expected_name in self._instruction_files(root):
                try:
                    definition = self._read(path, expected_name, include_content=False)
                except Exception as exc:
                    logger.warning(f"Skill 文件 {path} 无效，已跳过：{exc}")
                    continue
                if definition.name in discovered:
                    logger.debug(
                        f"发现重复 Skill“{definition.name}”，保留优先级更高的 "
                        f"{discovered[definition.name].path}"
                    )
                    continue
                discovered[definition.name] = definition
        summaries = tuple(discovered[name] for name in sorted(discovered))
        if model_invocable_only:
            summaries = tuple(item for item in summaries if item.model_invocable)
        return summaries

    def get(self, name: str, *, require_model_invocable: bool = False) -> SkillDefinition:
        self._validate_name(name)
        summaries = {item.name: item for item in self.list()}
        summary = summaries.get(name)
        if summary is None:
            raise KeyError(f"Skill 不存在：{name}")
        if require_model_invocable and not summary.model_invocable:
            raise PermissionError(f"Skill 不允许由模型调用：{name}")
        return self._read(summary.path, name, include_content=True)

    def create(
        self,
        *,
        name: str,
        description: str,
        content: str,
        when_to_use: str | None = None,
        metadata: dict[str, Any] | None = None,
        disable_model_invocation: bool = False,
        user_invocable: bool = True,
        overwrite: bool = False,
    ) -> SkillDefinition:
        if not self.config.enable or not self.config.allow_create:
            raise PermissionError("当前配置不允许创建 Skill")
        self._validate_name(name)
        description = self._required_text(description, "description")
        content = self._required_text(content, "content")
        # 写盘前先按字节校验大小：否则会写进一个之后每次读取都报错的 Skill（P1-13）
        document_size = len(content.encode("utf-8")) + len(description.encode("utf-8"))
        if document_size > self.config.max_skill_bytes:
            raise ValueError(
                f"Skill 内容约 {document_size} 字节超过上限 {self.config.max_skill_bytes} 字节"
            )
        root = self._resolve_root(self.config.create_root)
        skill_dir = root / name
        path = skill_dir / "SKILL.md"
        if path.exists() and not overwrite:
            raise FileExistsError(f"Skill 已存在：{name}")
        skill_dir.mkdir(parents=True, exist_ok=True)
        frontmatter: dict[str, Any] = {
            "name": name,
            "description": description,
        }
        if when_to_use:
            frontmatter["whenToUse"] = when_to_use.strip()
        if metadata:
            frontmatter["metadata"] = metadata
        if disable_model_invocation:
            frontmatter["disable-model-invocation"] = True
        if not user_invocable:
            frontmatter["user-invocable"] = False
        serialized = yaml.safe_dump(
            frontmatter,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        document = f"---\n{serialized}\n---\n\n{content.strip()}\n"
        # 落盘前按最终文档再校验一次字节数（frontmatter 也会占体积）
        size = len(document.encode("utf-8"))
        if size > self.config.max_skill_bytes:
            raise ValueError(
                f"Skill 内容 {size} 字节超过上限 {self.config.max_skill_bytes} 字节"
            )
        path.write_text(document, encoding="utf-8")
        logger.info(f"已创建 Skill“{name}”：{path}")
        return self._read(path, name, include_content=True)

    def _resolved_roots(self) -> tuple[Path, ...]:
        roots = []
        for value in self.config.roots:
            root = self._resolve_root(value)
            if root not in roots:
                roots.append(root)
        return tuple(roots)

    def _resolve_root(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        resolved = path.resolve()
        # 写入位置必须落在项目根目录内，避免配置成绝对路径后写穿到项目外面（P1-13）
        if resolved != self.project_root and not resolved.is_relative_to(self.project_root):
            raise PermissionError(f"Skill 目录必须位于项目根目录内：{resolved}")
        return resolved

    @staticmethod
    def _instruction_files(root: Path):
        if not root.is_dir():
            return
        for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if child.is_dir():
                path = child / "SKILL.md"
                if path.is_file():
                    yield path, child.name
            elif child.is_file() and child.suffix.lower() == ".md":
                yield child, child.stem

    def _read(
        self,
        path: Path,
        expected_name: str,
        *,
        include_content: bool,
    ) -> SkillDefinition:
        size = path.stat().st_size
        if size > self.config.max_skill_bytes:
            raise ValueError(
                f"文件大小 {size} 字节超过上限 {self.config.max_skill_bytes} 字节"
            )
        raw = path.read_text(encoding="utf-8")
        frontmatter, content = self._parse_frontmatter(raw)
        name = self._required_text(frontmatter.get("name"), "name")
        self._validate_name(name)
        if name != expected_name:
            raise ValueError(f"文件名对应名称“{expected_name}”与 name“{name}”不一致")
        description = self._required_text(frontmatter.get("description"), "description")
        when_to_use = frontmatter.get("whenToUse")
        if when_to_use is not None:
            when_to_use = str(when_to_use).strip() or None
        metadata = frontmatter.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata 必须是 YAML 对象")
        model_invocable = not self._parse_bool(
            frontmatter.get("disable-model-invocation", False),
            "disable-model-invocation",
        )
        user_invocable = self._parse_bool(
            frontmatter.get("user-invocable", True),
            "user-invocable",
        )
        resource_base = path.parent
        return SkillDefinition(
            name=name,
            description=description,
            when_to_use=when_to_use,
            model_invocable=model_invocable,
            user_invocable=user_invocable,
            path=path,
            resource_base=resource_base,
            content=content.strip() if include_content else "",
            metadata=dict(metadata),
        )

    @staticmethod
    def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
        lines = raw.lstrip("\ufeff").splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("缺少 YAML frontmatter 起始标记")
        end = next(
            (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
            None,
        )
        if end is None:
            raise ValueError("缺少 YAML frontmatter 结束标记")
        parsed = yaml.safe_load("\n".join(lines[1:end])) or {}
        if not isinstance(parsed, dict):
            raise ValueError("YAML frontmatter 必须是对象")
        return parsed, "\n".join(lines[end + 1:])

    @staticmethod
    def _required_text(value: Any, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"缺少必填字段：{field_name}")
        return text

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _SKILL_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Skill 名称必须使用 kebab-case：{name}")

    @staticmethod
    def _parse_bool(value: Any, field_name: str) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
        raise ValueError(f"{field_name} 必须是布尔值")