"""设置页 4：大语言模型 (LLM / MLLM) 与视觉理解管线。"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from pipeline.llm.config import KNOWN_MODEL_IDS
from pipeline.mllm.config import KNOWN_MLLM_MODEL_IDS
from ui.settings.widgets import (
    OptionalNumberEdit,
    PasswordLineEdit,
    SettingsCard,
    TagListEditor,
    build_settings_page,
    combo_value,
    make_checkbox,
    make_combo,
    make_float_spin,
    make_int_spin,
    make_line_edit,
)


class ModelPage(QWidget):
    def __init__(self, pipeline_cfg, parent=None) -> None:
        super().__init__(parent)
        llm_cfg = pipeline_cfg.llm
        mllm_cfg = pipeline_cfg.mllm
        vlm_cfg = pipeline_cfg.vlm
        ocr_cfg = pipeline_cfg.ocr
        imgcap_cfg = pipeline_cfg.img_cap

        # 1. 纯文本 LLM 管线
        llm_card = SettingsCard("主对话模型 (LLM)", "支持 Core 本地转发服务或任意 OpenAI 兼容端点。", self)
        self._llm_enable = make_checkbox("启用纯文本 LLM 管线（未启用 MLLM 时生效）", llm_cfg.enable)
        self._llm_openai_format = make_checkbox(
            "使用 OpenAI 兼容协议直连（predict_url 填 base_url）", llm_cfg.openai_format
        )
        self._llm_model_id = make_combo(list(KNOWN_MODEL_IDS), llm_cfg.model_id, editable=True)
        self._llm_predict_url = make_line_edit(llm_cfg.predict_url)
        self._llm_stream_url = make_line_edit(llm_cfg.stream_predict_url)
        self._llm_api_key = PasswordLineEdit(llm_cfg.api_key or "", placeholder="OpenAI 兼容接口必填；走 Core 时可留空")
        self._llm_temperature = OptionalNumberEdit(llm_cfg.temperature, is_float=True)
        self._llm_max_tokens = OptionalNumberEdit(llm_cfg.max_tokens, is_float=False)
        self._llm_stream_output = make_checkbox("启用流式输出", llm_cfg.stream_output)
        self._llm_stream_tts = make_checkbox("流式输出时按分句增量提交 TTS", llm_cfg.stream_tts_by_sentence)

        llm_card.add_row("启用 LLM", self._llm_enable, restart_required=True)
        llm_card.add_row("OpenAI 兼容格式", self._llm_openai_format)
        llm_card.add_row("模型标识 (model_id)", self._llm_model_id)
        llm_card.add_row("预测地址 / Base URL", self._llm_predict_url)
        llm_card.add_row("流式地址", self._llm_stream_url)
        llm_card.add_row("API Key", self._llm_api_key)
        llm_card.add_row("Temperature", self._llm_temperature)
        llm_card.add_row("Max Tokens", self._llm_max_tokens)
        llm_card.add_row("流式输出", self._llm_stream_output)
        llm_card.add_row("边生成边合成语音", self._llm_stream_tts)

        # 2. 多模态 MLLM 管线
        mllm_card = SettingsCard("多模态对话与屏幕理解 (MLLM)", "启用后将自动接管主对话并定时采集屏幕画面。", self)
        self._mllm_enable = make_checkbox("启用 MLLM 多模态对话管线", mllm_cfg.enable)
        self._mllm_model_id = make_combo(list(KNOWN_MLLM_MODEL_IDS), mllm_cfg.model_id, editable=True)
        self._mllm_predict_url = make_line_edit(mllm_cfg.predict_url)
        self._mllm_stream_url = make_line_edit(mllm_cfg.stream_predict_url)
        self._mllm_api_key = PasswordLineEdit(mllm_cfg.api_key or "", placeholder="由 Core 接管时可留空")
        self._mllm_stream_output = make_checkbox("启用 MLLM 流式输出", mllm_cfg.stream_output)
        self._mllm_stream_tts = make_checkbox("MLLM 流式按句送 TTS", mllm_cfg.stream_tts_by_sentence)
        self._mllm_interval = make_float_spin(mllm_cfg.screenshot_interval, 0.2, 60.0, step=0.5, decimals=1)
        self._mllm_definition = make_int_spin(mllm_cfg.screenshot_definition, 128, 4096, step=64)
        self._mllm_max_queue = make_int_spin(mllm_cfg.max_queue_size, 1, 20)
        self._mllm_reasoning_keywords = TagListEditor(
            list(mllm_cfg.reasoning_keywords), placeholder="触发深度思考的关键词，逗号分隔"
        )
        self._mllm_reasoning_budget = make_int_spin(mllm_cfg.reasoning_budget_on_keyword, 1, 4096, step=16)

        mllm_card.add_row("启用 MLLM", self._mllm_enable, restart_required=True)
        mllm_card.add_row("MLLM 模型标识", self._mllm_model_id, restart_required=True)
        mllm_card.add_row("预测 URL", self._mllm_predict_url, restart_required=True)
        mllm_card.add_row("流式 URL", self._mllm_stream_url, restart_required=True)
        mllm_card.add_row("API Key", self._mllm_api_key, restart_required=True)
        mllm_card.add_row("流式输出", self._mllm_stream_output)
        mllm_card.add_row("边生成边合成语音", self._mllm_stream_tts)
        mllm_card.add_row("截屏间隔 (秒)", self._mllm_interval, restart_required=True)
        mllm_card.add_row("长边最大像素", self._mllm_definition)
        mllm_card.add_row("图像队列上限", self._mllm_max_queue, restart_required=True)
        mllm_card.add_row("思考触发关键词", self._mllm_reasoning_keywords)
        mllm_card.add_row("思考 Token 预算", self._mllm_reasoning_budget)

        # 3. 辅助视觉 (VLM / OCR / ImgCap)
        vision_card = SettingsCard("辅助视觉服务 (VLM / OCR / ImgCap)", "未启用 MLLM 时的屏幕理解与降级识别管线。", self)
        self._vlm_enable = make_checkbox("启用 VLM 视觉模型", vlm_cfg.enable)
        self._vlm_mode = make_combo(
            [("关键词按需触发 (keyword)", "keyword"), ("常态后台轮询 (continuous)", "continuous")],
            vlm_cfg.mode,
        )
        self._vlm_interval = make_int_spin(vlm_cfg.continuous_interval, 1, 300)
        self._vlm_url = make_line_edit(vlm_cfg.predict_url)
        self._vlm_model_name = make_line_edit(vlm_cfg.model_name)
        self._vlm_max_pixels = make_int_spin(vlm_cfg.max_pixels, 1024, 4194304, step=1024)
        self._ocr_enable = make_checkbox("启用 OCR 文字识别降级服务", ocr_cfg.enable)
        self._ocr_url = make_line_edit(ocr_cfg.predict_url)
        self._imgcap_enable = make_checkbox("启用 ImgCap 图像描述降级服务", imgcap_cfg.enable)
        self._imgcap_url = make_line_edit(imgcap_cfg.predict_url)

        vision_card.add_row("启用 VLM", self._vlm_enable, restart_required=True)
        vision_card.add_row("VLM 模式", self._vlm_mode, restart_required=True)
        vision_card.add_row("VLM 轮询间隔 (秒)", self._vlm_interval, restart_required=True)
        vision_card.add_row("VLM 地址", self._vlm_url)
        vision_card.add_row("VLM 服务端模型名", self._vlm_model_name)
        vision_card.add_row("VLM 最大像素", self._vlm_max_pixels)
        vision_card.add_row("启用 OCR", self._ocr_enable, restart_required=True)
        vision_card.add_row("OCR 预测地址", self._ocr_url)
        vision_card.add_row("启用 ImgCap", self._imgcap_enable, restart_required=True)
        vision_card.add_row("ImgCap 预测地址", self._imgcap_url)

        build_settings_page(self, [llm_card, mllm_card, vision_card])

    def values(self) -> dict:
        return {
            "pipeline": {
                "llm": {
                    "enable": self._llm_enable.isChecked(),
                    "openai_format": self._llm_openai_format.isChecked(),
                    "model_id": combo_value(self._llm_model_id),
                    "predict_url": self._llm_predict_url.text().strip(),
                    "stream_predict_url": self._llm_stream_url.text().strip(),
                    "api_key": self._llm_api_key.text().strip() or None,
                    "temperature": self._llm_temperature.value(),
                    "max_tokens": self._llm_max_tokens.value(),
                    "stream_output": self._llm_stream_output.isChecked(),
                    "stream_tts_by_sentence": self._llm_stream_tts.isChecked(),
                },
                "mllm": {
                    "enable": self._mllm_enable.isChecked(),
                    "model_id": combo_value(self._mllm_model_id),
                    "predict_url": self._mllm_predict_url.text().strip(),
                    "stream_predict_url": self._mllm_stream_url.text().strip(),
                    "api_key": self._mllm_api_key.text().strip(),
                    "stream_output": self._mllm_stream_output.isChecked(),
                    "stream_tts_by_sentence": self._mllm_stream_tts.isChecked(),
                    "screenshot_interval": float(self._mllm_interval.value()),
                    "screenshot_definition": self._mllm_definition.value(),
                    "max_queue_size": self._mllm_max_queue.value(),
                    "reasoning_keywords": self._mllm_reasoning_keywords.values(),
                    "reasoning_budget_on_keyword": self._mllm_reasoning_budget.value(),
                },
                "vlm": {
                    "enable": self._vlm_enable.isChecked(),
                    "mode": combo_value(self._vlm_mode),
                    "continuous_interval": self._vlm_interval.value(),
                    "predict_url": self._vlm_url.text().strip(),
                    "model_name": self._vlm_model_name.text().strip(),
                    "max_pixels": self._vlm_max_pixels.value(),
                },
                "ocr": {
                    "enable": self._ocr_enable.isChecked(),
                    "predict_url": self._ocr_url.text().strip(),
                },
                "img_cap": {
                    "enable": self._imgcap_enable.isChecked(),
                    "predict_url": self._imgcap_url.text().strip(),
                },
            }
        }
