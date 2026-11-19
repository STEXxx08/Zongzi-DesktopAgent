from pydantic import BaseModel, Field


class HarnessRecallConfig(BaseModel):
    """模型回读 harness 工具记录的边界参数。"""

    enable: bool = Field(
        default=True,
        description="是否把工具记录回读注册为模型可调用工具（harness_recall）；改动后需重启。",
    )
    max_scan_bytes: int = Field(
        default=4194304,
        ge=65536,
        description="单次回读从日志末尾向前扫描的字节上限，超出只保留最近一段并显式标注。",
    )
    max_scan_lines: int = Field(
        default=5000,
        ge=100,
        le=200000,
        description="单次回读最多检查的日志行数。",
    )
    max_records: int = Field(
        default=8,
        ge=1,
        le=50,
        description="单次索引最多返回的工具调用条数。",
    )
    max_summary_chars: int = Field(
        default=200,
        ge=40,
        le=2000,
        description="索引里参数/结果摘要的字符上限；全文请用 ordinal/call_id 取。",
    )
    default_turns_back: int = Field(
        default=2,
        ge=0,
        le=10,
        description="scope=recent 时默认往前看几个 turn（0 表示只看当前 turn）。",
    )
    max_turns_back: int = Field(
        default=5,
        ge=0,
        le=50,
        description="scope=recent 时 turns_back 的硬上限。",
    )
