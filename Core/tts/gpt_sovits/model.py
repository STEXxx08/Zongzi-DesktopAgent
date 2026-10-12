"""
More details about the model:
    https://github.com/RVC-Boss/GPT-SoVITS
Please download this forked version:
    https://github.com/GPT-SoVITS
"""
from typing import Any
from loguru import logger
from common.abs_model import AbstractModel


class GPT_SoVITS(AbstractModel):
    def __init__(self):
        super().__init__()
        self.model_id = "GPT-SoVITS"

    def load_model(self):
        logger.warning("GPT-SoVITS")

    def predict(self, *args, **kwargs) -> Any:
        raise NotImplementedError("You should not call this method!")

    def stream_predict(self, *args, **kwargs) -> Any:
        raise NotImplementedError("You should not call this method!")
