import json
from condata.harness import record_protocol_error
import logging
import re
from html import escape
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.skill_framework.config import SkillsServiceConfig
from services.skill_framework.registry import (
    SkillDefinition,
    SkillRegistry,
    SkillSummary,
)
from services.tool_runtime import ToolCall, ToolDefinition, ToolRuntime
from services.tool_model_gateway import compose_tool_instructions


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSkillDirective:
    name: str
    acknowledgement: str
    inject_result_as_skill: bool = True

    def tool_call(self) -> ToolCall:
        return ToolCall(
            name="skill",
            arguments={"name": self.name},
            source="model_skill",
        )


@dataclass(frozen=True)
class ModelSkillCreateDirective:
    name: str
    description: str
    content: str
    acknowledgement: str
    when_to_use: str | None = None
    metadata: dict[str, Any] | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    inject_result_as_skill: bool = False

    def tool_call(self) -> ToolCall:
        return ToolCall(
            name="skill_create",
            arguments={
                "name": self.name,
                "description": self.description,
                "content": self.content,
                "when_to_use": self.when_to_use,
                "metadata": self.metadata or {},
                "disable_model_invocation": self.disable_model_invocation,
                "user_invocable": self.user_invocable,
            },
            source="model_skill_create",
        )


class SkillService:
    """连接 Skill 文件注册表、模型目录和通用 ToolRuntime。"""

    def __init__(
        self,
        config: SkillsServiceConfig,
        tool_runtime: ToolRuntime,
        *,
        project_root: Path | None = None,
    ):
        self.config = config
        self.tool_runtime = tool_runtime
        self.registry = SkillRegistry(config, project_root=project_root)
        self._register_tool()

    def update_config(self, config: SkillsServiceConfig) -> None:
        self.config = config
        self.registry.update_config(config)

    def list(self, *, model_invocable_only: bool = False) -> tuple[SkillSummary, ...]:
        return self.registry.list(model_invocable_only=model_invocable_only)

    def load(self, name: str, *, require_model_invocable: bool = False) -> SkillDefinition:
        return self.registry.get(
            name,
            require_model_invocable=require_model_invocable,
        )

    def create_skill(self, **kwargs) -> SkillDefinition:
        """为后续 AI 创建 Skill 的受控入口；是否开放由配置决定。"""
        return self.registry.create(**kwargs)

    def model_instruction(self, *, include_common: bool = True) -> str | None:
        skills = self.list(model_invocable_only=True)
        if not self.config.enable or not skills:
            return None
        max_length = self.config.catalog_description_max_length
        entries = "\n".join(
            f"- `{skill.name}`: "
            f"{escape(self._normalize_description(skill.description)[:max_length])}"
            for skill in skills
        )
        marker = self.config.model_marker
        section = f"""
【Skills】目录只含摘要，不能据此猜测完整指令：
<available_skills>
{entries}
</available_skills>
用户明确点名或任务匹配时，优先加载尚未加载的 Skill，再分轮调用工具；加载不扩大工具权限。
<{marker}>{{"name":"skill-name"}}</{marker}>
仅在收到 <skill_content> 后遵循完整指令；上下文已有同名内容时不重复加载。
""".strip()
        return compose_tool_instructions([section]) if include_common else section

    def parse_model_response(
        self,
        response: str,
    ) -> tuple[str, ModelSkillDirective | ModelSkillCreateDirective | None]:
        create_visible, create_directive, matched_create = self._parse_create_response(
            response
        )
        if matched_create:
            return create_visible, create_directive

        marker = re.escape(self.config.model_marker)
        pattern = re.compile(
            rf"<{marker}>\s*(\{{.*?\}})\s*</{marker}>",
            flags=re.DOTALL | re.IGNORECASE,
        )
        match = pattern.search(response or "")
        if match is None:
            return response, None
        visible = pattern.sub("", response).strip()
        try:
            payload = json.loads(match.group(1))
            name = str(payload.get("name", "")).strip()
            available = {
                skill.name for skill in self.list(model_invocable_only=True)
            }
            if name not in available:
                raise ValueError(f"Skill 不存在或不允许模型调用：{name}")
        except Exception as exc:
            record_protocol_error(self.config.model_marker, response, exc)
            logger.warning(f"模型 Skill 加载指令无效：{exc}")
            return visible, None
        acknowledgement = visible or f"稍等，我先加载 {name} Skill。"
        return acknowledgement, ModelSkillDirective(
            name=name,
            acknowledgement=acknowledgement,
        )

    def _parse_create_response(
        self,
        response: str,
    ) -> tuple[str, ModelSkillCreateDirective | None, bool]:
        marker = re.escape(self.config.model_create_marker)
        pattern = re.compile(
            rf"<{marker}>\s*(\{{.*?\}})\s*</{marker}>",
            flags=re.DOTALL | re.IGNORECASE,
        )
        match = pattern.search(response or "")
        if match is None:
            return response, None, False
        visible = pattern.sub("", response).strip()
        if not self.config.allow_model_create:
            logger.warning("模型请求创建 Skill，但配置未开放该能力。")
            return visible, None, True
        try:
            payload = json.loads(match.group(1))
            name = str(payload.get("name", "")).strip()
            description = str(payload.get("description", "")).strip()
            content = str(payload.get("content", "")).strip()
            if not name or not description or not content:
                raise ValueError("name、description 和 content 均为必填字段")
            metadata = payload.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise ValueError("metadata 必须是 JSON 对象")
            when_to_use = payload.get("when_to_use", payload.get("whenToUse"))
            when_to_use = str(when_to_use).strip() if when_to_use else None
            disable_model_invocation = self._json_bool(
                payload.get("disable_model_invocation", False),
                "disable_model_invocation",
            )
            user_invocable = self._json_bool(
                payload.get("user_invocable", True),
                "user_invocable",
            )
        except Exception as exc:
            record_protocol_error(self.config.model_create_marker, response, exc)
            logger.warning(f"模型 Skill 创建指令无效：{exc}")
            return visible, None, True
        acknowledgement = visible or f"稍等，我正在创建 {name} Skill。"
        return acknowledgement, ModelSkillCreateDirective(
            name=name,
            description=description,
            content=content,
            acknowledgement=acknowledgement,
            when_to_use=when_to_use,
            metadata=metadata,
            disable_model_invocation=disable_model_invocation,
            user_invocable=user_invocable,
        ), True

    def parse_user_invocation(
        self,
        text: str,
    ) -> tuple[str, SkillDefinition] | None:
        """解析空白边界内的 /name，并加载允许用户调用的 Skill。"""
        if not self.config.enable:
            return None
        match = re.search(r"(?<!\S)/([a-z0-9]+(?:-[a-z0-9]+)*)(?!\S)", text or "")
        if match is None:
            return None
        name = match.group(1)
        summaries = {item.name: item for item in self.list()}
        summary = summaries.get(name)
        if summary is None or not summary.user_invocable:
            return None
        remaining = (text[:match.start()] + text[match.end():]).strip()
        return remaining, self.load(name)

    def _register_tool(self) -> None:
        self.tool_runtime.register(
            ToolDefinition(
                name="skill",
                description="按名称加载一个 Skill 的完整任务指令。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "要加载的 kebab-case Skill 名称",
                        },
                    },
                    "required": ["name"],
                },
                tags=frozenset({"skill"}),
                model_invocable=False,  # 保留专用 Skill 协议，不额外开放通用入口。
                handler=lambda args: self.render_skill(
                    self.load(
                        str(args["name"]),
                        require_model_invocable=True,
                    )
                ),
            ),
            replace=True,
        )
        self.tool_runtime.register(
            ToolDefinition(
                name="skill_create",
                description="创建一个兼容 DeepSeek Harness 格式的新 Skill。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "content": {"type": "string"},
                        "when_to_use": {"type": ["string", "null"]},
                        "metadata": {"type": "object"},
                        "disable_model_invocation": {"type": "boolean"},
                        "user_invocable": {"type": "boolean"},
                    },
                    "required": ["name", "description", "content"],
                },
                tags=frozenset({"skill", "write"}),
                model_invocable=False,  # 模型创建仍由专用标记和创建权限控制。
                handler=self._create_from_tool,
            ),
            replace=True,
        )

    def _create_from_tool(self, args: dict[str, Any]) -> str:
        if not self.config.allow_model_create:
            raise PermissionError("当前配置不允许模型创建 Skill")
        created = self.create_skill(
            name=str(args["name"]),
            description=str(args["description"]),
            content=str(args["content"]),
            when_to_use=args.get("when_to_use"),
            metadata=args.get("metadata") or {},
            disable_model_invocation=bool(args.get("disable_model_invocation", False)),
            user_invocable=bool(args.get("user_invocable", True)),
            overwrite=False,
        )
        return f"Skill“{created.name}”创建成功：{created.path}"

    @staticmethod
    def render_skill(skill: SkillDefinition) -> str:
        return (
            f'<skill_content name="{skill.name}">\n'
            f"<skill_resources>相对资源路径以 "
            f"{escape(str(skill.resource_base))} 为基准。</skill_resources>\n"
            f"<skill_instructions>\n{skill.content}\n</skill_instructions>\n"
            f"</skill_content>"
        )

    @staticmethod
    def _normalize_description(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _json_bool(value: Any, field_name: str) -> bool:
        if isinstance(value, bool):
            return value
        raise ValueError(f"{field_name} 必须是 JSON 布尔值")