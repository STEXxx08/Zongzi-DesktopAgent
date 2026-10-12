from dataclasses import dataclass, field
from typing import Optional


@dataclass
class QwenAsrModelConfig:
    """
    Configuration class for Qwen3-ASR model (transformers backend).
    """
    model_path: str = "./asr/Qwen3ASR06B"          # 模型本地路径
    device: str = "cuda:0"                      # 设备，如 "cuda:0" 或 "cpu"
    dtype: str = "bfloat16"                     # 数据类型: "bfloat16" 或 "float16"
    use_flash_attn: bool = True                 # 是否启用 flash-attention-2
    max_new_tokens: int = 256                   # 生成最大 token 数
    language: Optional[str] = None         # 强制语言，如 "Chinese"，None 表示自动检测