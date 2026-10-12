from pydantic import BaseModel, Field

from character.config import CharacterConfig
from memory.config import MemoryConfig
from pipeline.base.config import PipelineConfig
from services.config import ServiceConfig


class AutoChatConfig(BaseModel):
    enable: bool = Field(default=True, description="Whether to enable auto chat.")
    timeout: int = Field(default=60, description="Determine how long after no user input would LLM start auto-chat.")


class SystemConfig(BaseModel):
    enable_clause_split: bool = Field(
        default=True,
        description='If `True`, splits LLM responses into smaller clauses before sending to TTS service. '
                    'This enables faster audio generation and reduced latency for real-time applications. \n'
                    'Set to `False` to send full sentences as a single unit for more natural speech flow at the cost of longer wait times.',
    )
    enable_sentiment_analysis: bool = Field(
        default=False,
        description='Automatically analyzes sentiment to select appropriate TTS prompts. '
                    'This also increases token consumption and adds slight latency due to extra processing.',
    )
    auto_chat: AutoChatConfig = Field(
        default_factory=AutoChatConfig,
        description="Configuration for automatic chat after an idle timeout.",
    )


class BotConfig(BaseModel):
    pipeline: PipelineConfig = Field(
        default_factory=PipelineConfig,
        description="Configuration for the pipeline settings. \n"
                    "Pipeline 是连接 Core 侧模型服务的方式：通过 HTTP 请求调用模型并取回响应。 \n"
                    "> [!NOTE] \n"
                    "> 1. 至少要启用 LLMPipeline。 \n"
                    "> 2. Core 侧服务可以分布在不同主机上，只要分别设置对应的 url 即可； \n"
                    ">    也可以全部换成第三方在线 API（填 api_key、改 predict_url）。 \n"
                    "> 3. 如果所有服务只能共用一个端口，可用 Nginx 做请求转发。",
    )
    service: ServiceConfig = Field(
        default_factory=ServiceConfig,
        description="Configuration for the service settings. \n"
                    "The services are usually opened locally, \n"
                    "and instances of other projects establish connections with the service.",
    )
    character: CharacterConfig = Field(
        default_factory=CharacterConfig,
        description="Configuration for the character settings.",
    )
    system: SystemConfig = Field(
        default_factory=SystemConfig,
        description="Configuration for the system settings.",
    )
    memory: MemoryConfig = Field(
        default_factory=MemoryConfig,
        description="Configuration for the memory settings.",
    )