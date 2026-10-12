import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DeepSeekOnlineConfig:
    # API 基础配置（兼容 YAML 中的命名）
    api_url: Optional[str] = None           # 对应 YAML 中的 api_url
    model_name: Optional[str] = None        # 对应 YAML 中的 model_name
    api_key: Optional[str] = None           # 从环境变量读取
    timeout: int = 90

    # 生成参数
    temperature: float = 0.6
    max_tokens: Optional[int] = None
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0

    # 内部使用的字段（由 api_url 和 model_name 转换而来）
    base_url: str = field(default="https://api.deepseek.com", init=False)
    model_version: str = field(default="deepseek-v4-flash", init=False)

    def __post_init__(self):
        # 如果提供了 api_url，则覆盖 base_url
        if self.api_url:
            self.base_url = self.api_url
        # 如果提供了 model_name，则覆盖 model_version
        if self.model_name:
            self.model_version = self.model_name
        # api_key 从环境变量读取（如果没有直接传入）
        if self.api_key is None:
            self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DeepSeek API key is required. Set DEEPSEEK_API_KEY environment variable "
                "or provide api_key in config."
            )