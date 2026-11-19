"""六大分类全量设置中心弹窗。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import BotConfig
from ui.mascot import render_avatar_thumbnail
from ui.settings.pages import (
    AppearancePage,
    AudioPage,
    CharacterPage,
    MemoryPage,
    ModelPage,
    ToolsPage,
)
from ui.theme import SETTINGS_DIALOG_QSS, _ui_font

# 标注为“需重启”的字段与中文提示映射（用于保存后检测是否提醒自动重启）
_RESTART_WATCH_FIELDS: tuple[tuple[str, str], ...] = (
    ("service.desktop_shell.enable", "桌宠桌面外壳启停"),
    ("service.microphone.enable", "麦克风总开关"),
    ("service.microphone.hotkey", "麦克风切换热键"),
    ("service.microphone.sample_rate", "麦克风采样率"),
    ("service.microphone.vad_enabled", "WebRTC VAD 开关"),
    ("service.microphone.vad_mode", "VAD 激进度"),
    ("service.microphone.silence_timeout", "句末静音超时"),
    ("service.microphone.rms_threshold", "麦克风能量门限"),
    ("service.microphone.vad_interim.enable", "流式分段打断开关"),
    ("pipeline.asr.enable", "ASR 语音识别管线启停"),
    ("pipeline.asr.desktop_audio.enable", "桌面音频捕获开关"),
    ("pipeline.asr.desktop_audio.input_device", "桌面音频输入设备"),
    ("pipeline.tts.enable", "TTS 语音合成管线启停"),
    ("pipeline.tts.warmup_on_startup", "TTS 启动预热"),
    ("pipeline.llm.enable", "纯文本 LLM 管线启停"),
    ("pipeline.mllm.enable", "MLLM 多模态管线启停"),
    ("pipeline.mllm.model_id", "MLLM 模型标识"),
    ("pipeline.mllm.predict_url", "MLLM 预测地址"),
    ("pipeline.mllm.stream_predict_url", "MLLM 流式地址"),
    ("pipeline.mllm.api_key", "MLLM API Key"),
    ("pipeline.mllm.screenshot_interval", "MLLM 截屏间隔"),
    ("pipeline.mllm.max_queue_size", "MLLM 图像队列上限"),
    ("pipeline.vlm.enable", "VLM 视觉管线启停"),
    ("pipeline.vlm.mode", "VLM 工作模式"),
    ("pipeline.vlm.continuous_interval", "VLM 后台轮询间隔"),
    ("pipeline.ocr.enable", "OCR 管线启停"),
    ("pipeline.img_cap.enable", "ImgCap 管线启停"),
    ("memory.enable", "分层长期记忆系统启停"),
    ("memory.memory_model.enable", "独立记忆模型开关"),
    ("memory.memory_model.type", "独立记忆模型类型"),
    ("memory.memory_model.model_id", "独立记忆模型标识"),
    ("memory.memory_model.predict_url", "独立记忆模型地址"),
    ("memory.memory_model.api_key", "独立记忆模型密钥"),
    ("service.harness_recall.enable", "工具记录回读开关"),
)


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
    return target


def _get_nested(obj: Any, dotted_path: str, default: Any = None) -> tuple[bool, Any]:
    cur = obj
    for part in dotted_path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return False, default
            cur = cur[part]
        elif hasattr(cur, part):
            cur = getattr(cur, part)
        else:
            return False, default
    return True, cur


def _wrap_scroll_page(page: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName("SettingsScrollArea")
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(page)
    return scroll


class DesktopSettingsDialog(QDialog):
    """覆盖 BotConfig 六大板块的导航式设置中心。"""

    def __init__(self, first=None, second=None, parent=None) -> None:
        if isinstance(first, BotConfig):
            bot_config = first
            if parent is None and isinstance(second, QWidget):
                parent = second
        else:
            bot_config = BotConfig()
            if first is not None:
                bot_config.service.desktop_shell = first
            if second is not None:
                bot_config.service.chat_window = second

        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("Zongzi 设置中心")
        self.resize(880, 620)
        self.setMinimumSize(720, 500)

        # 左侧品牌导航栏
        sidebar = QFrame(self)
        sidebar.setObjectName("SettingsSidebar")
        sidebar.setFixedWidth(206)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 12, 10, 10)
        sidebar_layout.setSpacing(10)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(4, 2, 4, 2)
        brand_row.setSpacing(10)
        brand_avatar = QLabel(sidebar)
        brand_avatar.setFixedSize(38, 38)
        ds_cfg = bot_config.service.desktop_shell
        brand_avatar.setPixmap(
            render_avatar_thumbnail(
                getattr(ds_cfg, "avatar_mode", "vector"),
                getattr(ds_cfg, "avatar_path", ""),
                38,
                circular=True,
            )
        )
        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(1)
        brand_title = QLabel("Zongzi 设置", sidebar)
        brand_title.setObjectName("SettingsBrandTitle")
        brand_title.setFont(_ui_font(11, bold=True))
        brand_sub = QLabel("桌面陪伴配置中心", sidebar)
        brand_sub.setObjectName("SettingsBrandSub")
        brand_sub.setFont(_ui_font(8))
        brand_text.addWidget(brand_title)
        brand_text.addWidget(brand_sub)
        brand_row.addWidget(brand_avatar)
        brand_row.addLayout(brand_text, 1)

        side_divider = QFrame(sidebar)
        side_divider.setObjectName("SettingsDivider")

        self._nav = QListWidget(sidebar)
        self._nav.setObjectName("SettingsNavList")
        self._nav.setFont(_ui_font(10))
        self._nav.setSpacing(2)
        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addWidget(side_divider)
        sidebar_layout.addWidget(self._nav, 1)

        self._stack = QStackedWidget(self)

        self._appearance_page = AppearancePage(
            bot_config.service.desktop_shell,
            bot_config.service.chat_window,
            self,
        )
        self._character_page = CharacterPage(bot_config.character, bot_config.system, self)
        self._audio_page = AudioPage(
            bot_config.service.microphone,
            bot_config.pipeline.asr,
            bot_config.pipeline.tts,
            self,
        )
        self._model_page = ModelPage(bot_config.pipeline, self)
        self._memory_page = MemoryPage(bot_config.memory, self)
        self._tools_page = ToolsPage(bot_config.service, self)

        self._pages: list[QWidget] = [
            self._appearance_page,
            self._character_page,
            self._audio_page,
            self._model_page,
            self._memory_page,
            self._tools_page,
        ]
        nav_specs = (
            ("🐱  外观与桌宠", "外观与桌宠", "定制猫娘形象、桌宠窗口行为与聊天外观"),
            ("🎀  角色与人设", "角色与人设", "编辑猫娘名字、情绪白名单、人设提示词与主动搭话"),
            ("🎙️  语音与音频", "语音与音频", "配置麦克风端点检测、ASR 语音识别与 TTS 语音合成"),
            ("🧠  模型与视觉", "模型与视觉", "管理主对话 LLM、多模态 MLLM 与辅助视觉管线"),
            ("📚  长期记忆 (RAG)", "长期记忆 (RAG)", "调整历史归档周期、向量检索权重与记忆抽取模型"),
            ("🛠️  联网与工具服务", "联网与工具服务", "配置联网搜索、语义意图路由、沙箱权限与扩展工具"),
        )
        self._page_meta: list[tuple[str, str]] = []
        for (label, title, subtitle), page in zip(nav_specs, self._pages):
            item = QListWidgetItem(label)
            self._nav.addItem(item)
            self._stack.addWidget(_wrap_scroll_page(page))
            self._page_meta.append((title, subtitle))

        self._page_title = QLabel(self)
        self._page_title.setObjectName("SettingsPageTitle")
        self._page_title.setFont(_ui_font(14, bold=True))

        self._page_sub = QLabel(self)
        self._page_sub.setObjectName("SettingsPageSub")
        self._page_sub.setFont(_ui_font(9))

        header_box = QVBoxLayout()
        header_box.setContentsMargins(4, 2, 4, 4)
        header_box.setSpacing(2)
        header_box.addWidget(self._page_title)
        header_box.addWidget(self._page_sub)

        content_col = QVBoxLayout()
        content_col.setContentsMargins(0, 0, 0, 0)
        content_col.setSpacing(8)
        content_col.addLayout(header_box)
        content_col.addWidget(self._stack, 1)

        self._nav.currentRowChanged.connect(self._on_nav_changed)
        self._nav.setCurrentRow(0)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(sidebar)
        body.addLayout(content_col, 1)

        footer_divider = QFrame(self)
        footer_divider.setObjectName("SettingsDivider")

        hint_label = QLabel("提示：带橙色「需重启」标记的项需重启生效，其余项保存后即刻热更新。", self)
        hint_label.setObjectName("SettingsFooterHint")
        hint_label.setFont(_ui_font(9))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("保存并应用")
            save_btn.setObjectName("PrimaryBtn")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("取消")

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        reset_button = buttons.addButton("回到默认位置", QDialogButtonBox.ButtonRole.ResetRole)
        reset_button.setToolTip("把桌宠移回主屏右下角（保存设置后生效）")
        reset_button.clicked.connect(self._request_default_position)

        footer = QHBoxLayout()
        footer.addWidget(hint_label)
        footer.addStretch(1)
        footer.addWidget(buttons)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)
        root.addLayout(body, 1)
        root.addWidget(footer_divider)
        root.addLayout(footer)
        self.setStyleSheet(SETTINGS_DIALOG_QSS)

    def _on_nav_changed(self, row: int) -> None:
        if 0 <= row < len(self._page_meta):
            self._stack.setCurrentIndex(row)
            title, subtitle = self._page_meta[row]
            self._page_title.setText(title)
            self._page_sub.setText(subtitle)

    def done(self, result: int) -> None:
        self._appearance_page.cleanup()
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._appearance_page.cleanup()
        super().closeEvent(event)

    def _request_default_position(self) -> None:
        self._appearance_page.request_reset_position()

    @property
    def reset_position_requested(self) -> bool:
        return self._appearance_page.reset_position_requested

    def bot_config_updates(self) -> dict[str, Any]:
        """导出五段式完整配置更新字典，可直接传入 save_bot_settings()。"""
        merged: dict[str, Any] = {
            "pipeline": {},
            "service": {},
            "character": {},
            "system": {},
            "memory": {},
        }
        app_vals = self._appearance_page.values()
        merged["service"]["desktop_shell"] = app_vals["desktop_shell"]
        merged["service"]["chat_window"] = app_vals["chat_window"]

        for page in (
            self._character_page,
            self._audio_page,
            self._model_page,
            self._memory_page,
            self._tools_page,
        ):
            _deep_merge(merged, page.values())
        return merged

    def values(self) -> dict[str, Any]:
        """返回全量嵌套更新并在顶层附带 desktop_shell / chat_window 便捷视图。"""
        full = self.bot_config_updates()
        full["desktop_shell"] = full["service"]["desktop_shell"]
        full["chat_window"] = full["service"]["chat_window"]
        return full

    def detect_restart_reasons(self, old_config: BotConfig) -> list[str]:
        """对比旧配置与新表单值，仅在修改了“需重启”参数时返回说明列表。"""
        updates = self.bot_config_updates()
        reasons: list[str] = []
        for dotted_path, label in _RESTART_WATCH_FIELDS:
            has_new, new_val = _get_nested(updates, dotted_path)
            if not has_new:
                continue
            _, old_val = _get_nested(old_config, dotted_path, None)
            # 对可空文本统一把 None 与 "" 视作未配置，防止无实质修改时误触发重启提示
            norm_old = "" if old_val is None else old_val
            norm_new = "" if new_val is None else new_val
            if norm_old != norm_new:
                reasons.append(label)
        return reasons
