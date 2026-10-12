from typing import List, Optional, Any
from pydantic import Field
from condata.pipeline.abs_data import AbstractModelQuery, AbstractModelPrediction
from condata.pipeline.llm import Conversation

class MLLMQuery(AbstractModelQuery):
    """
    Query for Multimodal Large Language Models (MLLM), supporting text and images.
    """
    text: str = Field(default="", description="The user input text.")
    history: List[Conversation] = Field(default_factory=list, description="Conversation history.")
    images: List[str] = Field(default_factory=list, description="Base64 encoded images (JPEG/PNG) to be included in the prompt.")

class MLLMPrediction(AbstractModelPrediction):
    """
    Prediction for MLLM.
    """
    response: str = Field(default="", description="The generated response text.")
    history: List[Conversation] = Field(default_factory=list, description="Updated conversation history including this turn.")
    usage: dict[str, Any] | None = Field(default=None, description="服务商返回的真实 token 用量；缺失时为 None，不估算。")