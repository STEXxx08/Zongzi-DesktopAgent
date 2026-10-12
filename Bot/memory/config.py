from pathlib import Path

from pydantic import BaseModel, Field

try:
    from pydantic import ConfigDict
except ImportError:
    ConfigDict = None


if ConfigDict is not None:
    class MemoryModelConfig(BaseModel):
        model_config = ConfigDict(extra="allow")

        enable: bool = Field(default=False, description="是否启用该记忆任务的单独模型配置")
        type: str = Field(default="llm", description="模型类型：llm 或 mllm")
else:
    class MemoryModelConfig(BaseModel):
        enable: bool = Field(default=False, description="是否启用该记忆任务的单独模型配置")
        type: str = Field(default="llm", description="模型类型：llm 或 mllm")

        class Config:
            extra = "allow"


class MemoryConfig(BaseModel):
    max_history: int = Field(default=100, description="临时对话历史的最大消息数")
    enable: bool = Field(default=True)
    data_dir: Path = Field(default=Path("./memory/data")) # 相对路径其实不安全，暂时如此硬编码
    save_interval_rounds: int = Field(default=10, description="每 N 轮对话保存一次快照")
    merge_batch_size: int = Field(default=15, description="合并时分批大小")
    async_save: bool = Field(default=True, description="是否异步保存快照（不阻塞主对话）")
    compact_history_after_save: int = Field(default=0, description="保存记忆快照后，用提取出的记忆替换 N 个保存周期前的历史片段；0 表示不替换，1 表示立即替换")
    memory_model: MemoryModelConfig = Field(default_factory=MemoryModelConfig, description="记忆提取与合并使用的单独模型配置")