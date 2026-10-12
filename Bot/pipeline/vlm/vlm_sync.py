# pipeline/vlm/vlm_sync.py
from loguru import logger
import requests
import base64
from pathlib import Path
from typing import Optional
from PIL import Image
import io

from requests import Response
from pipeline.base.base_sync import AbstractImagePipeline
from pipeline.vlm.config import VLMPipelineConfig


class VLMSyncPipeline(AbstractImagePipeline):
    """
    VLM Pipeline，调用本地 vLLM 服务（Docker 容器）
    用于单图理解，返回图片描述文本
    """

    def __init__(self, config: VLMPipelineConfig):
        super().__init__(config)
        self.predict_url = config.predict_url
        self.model_name = config.model_name or "/model"
        self.max_tokens = config.max_tokens or 50
        self.temperature = config.temperature or 0
        self.max_pixels = config.max_pixels or 200704  # 448x448
        self.mode = config.mode

    def predict(self, query: "VLMQuery") -> Optional[str]:
        """
        调用 vLLM 服务进行图片理解
        :param query: 包含 img_path 和 prompt
        :return: 描述文本
        """
        img_path = query.img_path
        prompt = query.prompt or "请描述这张图片"

        # 读取并压缩图片
        image = Image.open(img_path).convert("RGB")
        # 计算等比缩放后的尺寸，确保不超过 max_pixels
        width, height = image.size
        current_pixels = width * height
        if current_pixels > self.max_pixels:
            scale = (self.max_pixels / current_pixels) ** 0.5
            new_width = int(width * scale)
            new_height = int(height * scale)
            image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # 转 Base64
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()

        # 构造 OpenAI 兼容请求
        if self.mode == 'keyword':
            payload = {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}},
                            {"type": "text", "text": prompt}
                        ]
                    }
                ],
                "max_tokens": self.max_tokens,
                "temperature": self.temperature
            }
        else:
            payload = {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}},
                            {"type": "text", "text": prompt}
                        ]
                    }
                ],
                "max_tokens": self.max_tokens / 2,
                "temperature": self.temperature
            }
        try:
            response = requests.post(
                f"{self.predict_url}/v1/chat/completions",
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"VLM predict failed: {e}")
            return None

    def parse_query(self, query: any) -> dict:
        return {"img_path": query.img_path, "prompt": query.prompt}

    def parse_prediction(self, response: Response) -> str:
        # 这个方法是 AbstractImagePipeline 要求的，但我们用上面的 predict 直接返回字符串
        # 如果父类强制要求，可以留空或返回解析后的结果
        pass


class VLMQuery:
    def __init__(self, img_path: str, prompt: str = "请客观简短地描述这张图片的主要内容和信息，不要包含冗余信息和猜测，不需要前缀（如“好的，以下是图片的主要内容描述”）"):
        self.img_path = img_path
        self.prompt = prompt