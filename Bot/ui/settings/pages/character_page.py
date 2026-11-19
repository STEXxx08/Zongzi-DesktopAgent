"""设置页 2：角色人设、系统提示词与主动互动。"""

from __future__ import annotations

from PySide6.QtWidgets import QPlainTextEdit, QWidget

from ui.settings.widgets import (
    PathPickerEdit,
    SettingsCard,
    TagListEditor,
    build_settings_page,
    combo_value,
    make_checkbox,
    make_combo,
    make_int_spin,
    make_line_edit,
)
from ui.theme import _ui_font


class CharacterPage(QWidget):
    def __init__(self, character_cfg, system_cfg, parent=None) -> None:
        super().__init__(parent)

        # 身份与情绪白名单
        identity_card = SettingsCard("角色身份与表情白名单", "修改后立即生效于后续对话与桌宠表情驱动。", self)
        self._bot_name = make_line_edit(character_cfg.bot_name)

        self._emotions = TagListEditor(
            list(character_cfg.emotions),
            placeholder="例如：Default, happy, sad, angry, shy, surprised",
        )
        self._bad_words = TagListEditor(
            list(character_cfg.chat.filter.bad_words),
            placeholder="需要屏蔽的 AI 套话，使用逗号分隔",
        )

        identity_card.add_row("角色名称", self._bot_name)
        identity_card.add_row(
            "允许的情绪标签",
            self._emotions,
            tooltip="模型输出的 <emotion> 白名单；界面会自动保留 Default 兜底",
        )
        identity_card.add_row("违禁套话过滤词", self._bad_words)

        # 人设提示词
        prompt_card = SettingsCard("角色人设提示词 (System Prompt)", "支持多行编写及 {bot_name} 占位符。", self)
        self._system_prompt = QPlainTextEdit(character_cfg.chat.system_prompt)
        self._system_prompt.setFont(_ui_font(10))
        self._system_prompt.setMinimumHeight(150)
        prompt_card.add_widget_row(self._system_prompt)

        # 语音参考与交互习惯
        behavior_card = SettingsCard("表达、语音参考与主动搭话", "调整 TTS 参考音频目录及闲置主动搭话。", self)
        self._language = make_combo(
            [("中文 (zh)", "zh"), ("英语 (en)", "en"), ("日语 (ja)", "ja")],
            character_cfg.speech.language,
        )
        self._prompts_dir = PathPickerEdit(character_cfg.speech.prompts_dir, mode="dir")
        self._is_remote = make_checkbox("参考音频存放在远端 TTS 服务器上", character_cfg.speech.is_remote)
        self._clause_split = make_checkbox(
            "启用按标点分句发送 TTS（显著降低首句延迟）", system_cfg.enable_clause_split
        )
        self._sentiment_analysis = make_checkbox(
            "启用额外情感分析（未带标签时多一次模型判定）", system_cfg.enable_sentiment_analysis
        )
        self._auto_chat_enable = make_checkbox("长时间无输入时主动找主人搭话", system_cfg.auto_chat.enable)
        self._auto_chat_timeout = make_int_spin(system_cfg.auto_chat.timeout, 5, 86400, step=10)

        behavior_card.add_row("默认语音语言", self._language)
        behavior_card.add_row("TTS 参考音频目录", self._prompts_dir)
        behavior_card.add_row("远端参考音频", self._is_remote)
        behavior_card.add_row("流式分句合成", self._clause_split)
        behavior_card.add_row("后置情感分析", self._sentiment_analysis)
        behavior_card.add_row("启用主动搭话", self._auto_chat_enable)
        behavior_card.add_row("主动搭话超时 (秒)", self._auto_chat_timeout)

        build_settings_page(self, [identity_card, prompt_card, behavior_card])

    def values(self) -> dict:
        emotions = self._emotions.values()
        if "Default" not in emotions:
            emotions.insert(0, "Default")
        return {
            "character": {
                "bot_name": self._bot_name.text().strip() or "粽子",
                "emotions": emotions,
                "chat": {
                    "system_prompt": self._system_prompt.toPlainText(),
                    "filter": {
                        "bad_words": self._bad_words.values(),
                    },
                },
                "speech": {
                    "language": combo_value(self._language),
                    "prompts_dir": self._prompts_dir.text() or "resources/prompts/tts",
                    "is_remote": self._is_remote.isChecked(),
                },
            },
            "system": {
                "enable_clause_split": self._clause_split.isChecked(),
                "enable_sentiment_analysis": self._sentiment_analysis.isChecked(),
                "auto_chat": {
                    "enable": self._auto_chat_enable.isChecked(),
                    "timeout": self._auto_chat_timeout.value(),
                },
            },
        }
