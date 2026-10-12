from typing import TypeVar

from pydantic import BaseModel, Field

T = TypeVar('T')


VERSION = "1.1"
PROTOCOL_NAME = "ZongziProtocol"


class ZongziProtocol(BaseModel):
    """Self-Agent 的 WebSocket + JSON 消息对象（各端按 PROTOCOL_NAME 协商）。

    注意：`protocol` 字段的默认值是**线上格式**的一部分，改动等于协议不兼容。
    """
    protocol: str = Field(PROTOCOL_NAME, description="The name of the protocol.")
    version: str = Field(VERSION, description="The version of the protocol.")
    message: str = Field(..., description="A descriptive message.")
    action: str = Field(..., description="The action associated with the message.")
    code: int = Field(..., description="A status code.")
    data: T = Field(..., description="The data payload, which can be of any type.")
