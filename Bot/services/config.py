from loguru import logger
from pydantic import BaseModel, Field, model_validator

from ui.config import ChatWindowConfig
from services.browser.config import BrowserServiceConfig
from services.harness_config import HarnessLogConfig

from services.intent_router.config import IntentRouterConfig
from services.microphone.config import MicrophoneServiceConfig
from services.mcp.config import MCPServiceConfig
from services.sandbox.config import SandboxServiceConfig
from services.skill_framework.config import SkillsServiceConfig
from services.web_search.config import WebSearchServiceConfig


class DesktopShellConfig(BaseModel):
    enable: bool = Field(default=True, description="Start the desktop mascot shell instead of the console runtime.")
    always_on_top: bool = Field(default=True, description="Keep the mascot window above other windows.")
    bubble_offset_x: int = Field(default=0, ge=-2400, le=2400, description="Horizontal adjustment in pixels from the default speech bubble position.")
    bubble_offset_y: int = Field(default=0, ge=-1600, le=1600, description="Vertical adjustment in pixels from the default speech bubble position.")
    pet_x: int = Field(default=-1, description="桌宠窗口上次保存的横坐标；-1 表示未设置（自动落到主屏右下角）。")
    pet_y: int = Field(default=-1, description="桌宠窗口上次保存的纵坐标；-1 表示未设置（自动落到主屏右下角）。")
    exit_grace_seconds: float = Field(
        default=60.0,
        ge=5.0,
        le=600.0,
        description="退出时等待 bot.stop() 收尾的最长秒数；超时才强制结束进程，正常收尾不受影响。",
    )


class ToolServiceConfig(BaseModel):
    model_marker: str = Field(default="tool_call", pattern=r"^[a-z][a-z0-9_]{0,63}$", description="项目独立文本协议的外层调用标记；内部固定使用 name/arguments JSON。")
    max_parse_retries: int = Field(default=1, ge=0, le=3, description="通用工具指令解析失败后允许模型纠正的次数，不消耗工具执行轮数。")
    max_output_retries: int = Field(default=1, ge=0, le=1, description="仅在模型报告输出上限且工具调用被截断时，允许一次分段恢复；普通回复不重试。")
    max_result_chars: int = Field(
        default=20000,
        ge=1000,
        le=1000000,
        description="通用工具结果注入模型上下文的最大字符数。",
    )
    max_rounds: int = Field(
        default=6,
        ge=1,
        le=50,
        description="同一对话轮次内允许执行的最大工具调用轮数。",
    )
    max_calls_per_round: int = Field(
        default=8,
        ge=1,
        le=64,
        description="单个工具轮次内允许并行执行的最大工具调用数。",
    )
    max_workers: int = Field(
        default=4,
        ge=1,
        le=32,
        description="工具运行时线程数，在运行时启动时生效。",
    )


class ServiceConfig(BaseModel):
    harness: HarnessLogConfig = Field(default_factory=HarnessLogConfig)
    browser: BrowserServiceConfig = Field(default_factory=BrowserServiceConfig)
    tool: ToolServiceConfig = Field(default_factory=ToolServiceConfig)
    mcp: MCPServiceConfig = Field(default_factory=MCPServiceConfig)
    sandbox: SandboxServiceConfig = Field(default_factory=SandboxServiceConfig)
    skills: SkillsServiceConfig = Field(default_factory=SkillsServiceConfig)
    intent_router: IntentRouterConfig = Field(default_factory=IntentRouterConfig)
    web_search: WebSearchServiceConfig = Field(default_factory=WebSearchServiceConfig)
    desktop_shell: DesktopShellConfig = Field(default_factory=DesktopShellConfig)
    chat_window: ChatWindowConfig = Field(default_factory=ChatWindowConfig)
    microphone: MicrophoneServiceConfig = Field(default_factory=MicrophoneServiceConfig)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_text_input_gui(cls, data):
        """兼容旧配置字段名：service.text_input_gui → service.chat_window。

        旧的 config.yaml 仍然能加载（值不会丢），只是打一条提示；
        想彻底迁移就把它改名成 chat_window。
        """
        if isinstance(data, dict) and "text_input_gui" in data and "chat_window" not in data:
            logger.warning(
                "配置项 service.text_input_gui 已更名为 service.chat_window，"
                "本次仍按旧值加载；建议把它改名。"
            )
            data = dict(data)
            data["chat_window"] = data.pop("text_input_gui")
        return data
    
