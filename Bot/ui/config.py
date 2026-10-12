"""桌宠界面（PySide6）相关的配置模型。

原先这套配置放在 `services/text_input/config.py`，名字叫 `TextInputServiceConfig`，
但它服务的是"桌宠壳里的文字聊天窗"，与 services/ 下那些真正的后台服务无关
（历史原因：这里曾经是一个 wxPython 写的独立输入窗，那个模块已删除）。
"""
from pydantic import BaseModel, Field


class ChatWindowConfig(BaseModel):
    """桌宠壳里的"文字聊天窗"（键盘输入）配置。

    ⚠️ 只控制聊天窗本身；是否启动桌宠壳看 `service.desktop_shell.enable`。
    旧配置里的字段名是 `service.text_input_gui`，仍可被识别（见 services/config.py 的兼容分支）。
    """

    enable: bool = Field(default=True, description="桌宠壳里是否显示文字聊天窗。")
    title: str = Field(default="🤖 文本交互", description="聊天窗标题。")
    width: int = Field(default=600, description="聊天窗宽度（像素）。")
    height: int = Field(default=400, description="聊天窗高度（像素）。")
    font_size: int = Field(default=14, ge=8, le=36, description="对话显示字号。")
    input_font_size: int = Field(default=14, ge=8, le=36, description="输入框字号。")
    skip_tts: bool = Field(default=False, description="从聊天窗发出消息时是否跳过 TTS。")
