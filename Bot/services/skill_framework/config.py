from pydantic import BaseModel, Field


class SkillsServiceConfig(BaseModel):
    enable: bool = Field(default=True, description="是否启用通用 Skills 框架。")
    roots: list[str] = Field(
        default_factory=lambda: ["skills", ".dsh/skills", ".agents/skills"],
        description="按优先级扫描的 Skill 根目录，先发现的同名 Skill 生效。",
    )
    create_root: str = Field(
        default="skills",
        description="通过框架创建新 Skill 时使用的根目录。",
    )
    allow_create: bool = Field(
        default=True,
        description="是否允许通过 SkillService API 创建 Skill。",
    )
    allow_model_create: bool = Field(
        default=True,
        description="是否允许模型通过 skill_create 工具创建 Skill。",
    )
    catalog_description_max_length: int = Field(
        default=300,
        ge=20,
        le=2000,
        description="模型目录中单个 Skill 描述的最大字符数。",
    )
    max_skill_bytes: int = Field(
        default=262144,
        ge=1024,
        le=4194304,
        description="单个 SKILL.md 允许读取的最大字节数。",
    )
    model_marker: str = Field(
        default="skill_tool",
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
        description="模型请求加载 Skill 时使用的机器标记。",
    )
    model_create_marker: str = Field(
        default="skill_create",
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
        description="模型请求创建 Skill 时使用的机器标记。",
    )