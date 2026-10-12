import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MLLMPipelineConfig:
    """
    多模态模型（MLLM）配置类，用于服务端启动。
    与 DeepSeekOnlineConfig 风格一致，直接从 YAML 读取并处理环境变量。
    """
    # ---------- 基础连接配置 ----------
    api_url: Optional[str] = None           # 对应 YAML 中的 api_url（DashScope base URL）
    api_key: Optional[str] = None           # 从环境变量读取 DASHSCOPE_API_KEY
    model_name: Optional[str] = None        # 模型名称，如 'qwen3.7-plus'
    timeout: int = 60

    # ---------- 生成参数 ----------
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    top_p: float = 0.95
    enable_thinking: bool = True
    thinking_budget: int = 200
    repetition_penalty: float = 1.0
    presence_penalty: float = 2.0

    # ---------- 内部字段（由 api_url 和 model_name 转换而来）----------
    base_url: str = field(default="", init=False)        # 实际使用的 DashScope base URL
    model_version: str = field(default="", init=False)   # 实际使用的模型名称

    def __post_init__(self):
        # 如果提供了 api_url，则设置 base_url
        if self.api_url:
            self.base_url = self.api_url
        else:
            # 若未提供，可设默认值或报错（根据项目需求）
            self.base_url = "https://dashscope.aliyuncs.com/api/v1"   # 默认公网地址

        # 如果提供了 model_name，则设置 model_version
        if self.model_name:
            self.model_version = self.model_name
        else:
            # 若未提供，设默认值（与 YAML 中的 model_name 必填可能冲突，但这里防御）
            self.model_version = "qwen3.7-plus"

        # API Key：优先从环境变量 DASHSCOPE_API_KEY 读取
        if self.api_key is None:
            self.api_key = os.getenv('DASHSCOPE_API_KEY')
        if not self.api_key:
            raise ValueError(
                "DashScope API key is required. Set DASHSCOPE_API_KEY environment variable "
                "or provide api_key in config."
            )