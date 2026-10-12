from pydantic import BaseModel, Field


class HarnessLogConfig(BaseModel):
    enable: bool = Field(default=True, description="是否启用独立的模型、工具和协议诊断日志。")
    path: str = Field(default="logs/harness.jsonl", description="相对于 Bot 应用根目录的日志路径，不依赖启动目录。")
    include_payloads: bool = Field(default=True, description="记录工具参数、结果及协议错误原文；模型仅记录调用元数据和用量，不记录提示词或对话正文。")
    max_payload_chars: int = Field(default=32000, ge=1000, le=1000000, description="每个文本字段的日志字符上限，截断会明确标记。")
    max_bytes: int = Field(default=10485760, ge=1024, description="单个日志文件轮转大小，默认 10 MiB。")
    backup_count: int = Field(default=5, ge=0, le=100, description="保留的历史日志文件数。")