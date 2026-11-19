"""设置页 3：语音采集、ASR 识别与 TTS 语音合成。"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from pipeline.asr.config import KNOWN_ASR_MODEL_IDS
from pipeline.tts.config import KNOWN_TTS_MODEL_IDS
from ui.settings.widgets import (
    SettingsCard,
    SliderSpinRow,
    build_settings_page,
    combo_value,
    make_checkbox,
    make_combo,
    make_float_spin,
    make_int_spin,
    make_line_edit,
)


class AudioPage(QWidget):
    def __init__(self, mic_cfg, asr_cfg, tts_cfg, parent=None) -> None:
        super().__init__(parent)

        # 1. 麦克风设置
        mic_card = SettingsCard("麦克风与语音端点检测 (VAD)", "调整拾音热键、采样率及静音截断灵敏度。", self)
        self._mic_enable = make_checkbox("启用麦克风输入", mic_cfg.enable)
        self._mic_hotkey = make_line_edit(mic_cfg.hotkey)
        self._mic_sample_rate = make_combo(
            [("16000 Hz (推荐)", "16000"), ("48000 Hz", "48000"), ("8000 Hz", "8000"), ("32000 Hz", "32000")],
            str(mic_cfg.sample_rate),
        )
        self._vad_enabled = make_checkbox("启用 WebRTC VAD 语音检测", mic_cfg.vad_enabled)
        self._vad_mode = make_int_spin(mic_cfg.vad_mode, 0, 3)
        self._silence_timeout = make_float_spin(mic_cfg.silence_timeout, 0.1, 10.0, step=0.1, decimals=2)
        self._rms_threshold = make_float_spin(mic_cfg.rms_threshold, 0.0, 5000.0, step=20.0, decimals=1)
        self._vad_interim_enable = make_checkbox("启用分段提交以支持中途打断 TTS", mic_cfg.vad_interim.enable)

        mic_card.add_row("麦克风开关", self._mic_enable, restart_required=True)
        mic_card.add_row("切换热键", self._mic_hotkey, restart_required=True)
        mic_card.add_row("采样率", self._mic_sample_rate, restart_required=True)
        mic_card.add_row("启用 VAD", self._vad_enabled, restart_required=True)
        mic_card.add_row("VAD 激进度 (0~3)", self._vad_mode, restart_required=True)
        mic_card.add_row("句末静音超时 (秒)", self._silence_timeout, restart_required=True)
        mic_card.add_row("能量门限 (RMS)", self._rms_threshold, restart_required=True)
        mic_card.add_row("流式分段打断", self._vad_interim_enable, restart_required=True)

        # 2. ASR 管线
        asr_card = SettingsCard("语音识别 (ASR) 与 TurnSense 语义端点", "配置 ASR 服务地址、TurnSense 及桌面音频捕获。", self)
        self._asr_enable = make_checkbox("启用 ASR 语音识别管线", asr_cfg.enable)
        self._asr_model_id = make_combo(list(KNOWN_ASR_MODEL_IDS), asr_cfg.model_id, editable=True)
        self._asr_predict_url = make_line_edit(asr_cfg.predict_url)
        self._asr_stream_url = make_line_edit(asr_cfg.stream_predict_url)
        self._turnsense_enable = make_checkbox("启用 TurnSense 语义说完检测", asr_cfg.turnsense.enable)
        self._turnsense_timeout = make_float_spin(asr_cfg.turnsense.timeout, 0.1, 10.0, step=0.1, decimals=2)
        self._turnsense_final_timeout = make_float_spin(
            asr_cfg.turnsense.final_timeout, 0.1, 10.0, step=0.1, decimals=2
        )
        self._desktop_audio_enable = make_checkbox(
            "同时捕获系统桌面音频送入 ASR（需配虚拟声卡防自激）", asr_cfg.desktop_audio.enable
        )
        self._desktop_audio_device = make_line_edit(
            asr_cfg.desktop_audio.input_device or "", placeholder="留空使用系统默认输入设备"
        )

        asr_card.add_row("启用 ASR", self._asr_enable, restart_required=True)
        asr_card.add_row("ASR 模型 ID", self._asr_model_id)
        asr_card.add_row("预测 URL", self._asr_predict_url)
        asr_card.add_row("流式 URL", self._asr_stream_url)
        asr_card.add_row("TurnSense 开关", self._turnsense_enable)
        asr_card.add_row("候选停顿超时 (秒)", self._turnsense_timeout)
        asr_card.add_row("提交确认延迟 (秒)", self._turnsense_final_timeout)
        asr_card.add_row("桌面音频识别", self._desktop_audio_enable, restart_required=True)
        asr_card.add_row("桌面音频设备名", self._desktop_audio_device, restart_required=True)

        # 3. TTS 管线
        tts_card = SettingsCard("语音合成 (TTS) 与播放", "控制语音服务地址、本地播放音量、输出设备与自适应语速。", self)
        self._tts_enable = make_checkbox("启用 TTS 语音合成管线", tts_cfg.enable)
        self._tts_model_id = make_combo(list(KNOWN_TTS_MODEL_IDS), tts_cfg.model_id, editable=True)
        self._tts_predict_url = make_line_edit(tts_cfg.predict_url)
        self._tts_stream_url = make_line_edit(tts_cfg.stream_predict_url)
        self._playback_volume = SliderSpinRow(tts_cfg.playback_volume, 0.0, 2.0, step=0.05, decimals=2)
        self._output_device = make_line_edit(tts_cfg.output_device or "", placeholder="留空使用系统默认输出设备")
        self._allow_default_fallback = make_checkbox(
            "指定输出设备失效时允许回落到系统默认设备", tts_cfg.allow_default_device_fallback
        )
        self._warmup_on_startup = make_checkbox("启动时自动预热 TTS 模型以降低首句延迟", tts_cfg.warmup_on_startup)
        self._cancel_on_new_input = make_checkbox("新回复到来或新说话时自动打断旧语音", tts_cfg.cancel_on_new_input)
        self._adaptive_speed_enable = make_checkbox("长文本与连续打断时动态加快语速", tts_cfg.adaptive_speed.enable)
        self._adaptive_max_speed = make_float_spin(tts_cfg.adaptive_speed.max_speed, 1.0, 2.5, step=0.05, decimals=2)

        tts_card.add_row("启用 TTS", self._tts_enable, restart_required=True)
        tts_card.add_row("TTS 模型 ID", self._tts_model_id)
        tts_card.add_row("预测 URL", self._tts_predict_url)
        tts_card.add_row("流式 URL", self._tts_stream_url)
        tts_card.add_row("本地播放音量", self._playback_volume)
        tts_card.add_row("音频输出设备", self._output_device)
        tts_card.add_row("设备失效回落", self._allow_default_fallback)
        tts_card.add_row("启动预热", self._warmup_on_startup, restart_required=True)
        tts_card.add_row("支持语音打断", self._cancel_on_new_input)
        tts_card.add_row("自适应语速", self._adaptive_speed_enable)
        tts_card.add_row("最高语速倍率", self._adaptive_max_speed)

        build_settings_page(self, [mic_card, asr_card, tts_card])

    def values(self) -> dict:
        return {
            "service": {
                "microphone": {
                    "enable": self._mic_enable.isChecked(),
                    "hotkey": self._mic_hotkey.text().strip() or "f8",
                    "sample_rate": int(combo_value(self._mic_sample_rate) or 16000),
                    "vad_enabled": self._vad_enabled.isChecked(),
                    "vad_mode": self._vad_mode.value(),
                    "silence_timeout": float(self._silence_timeout.value()),
                    "rms_threshold": float(self._rms_threshold.value()),
                    "vad_interim": {
                        "enable": self._vad_interim_enable.isChecked(),
                    },
                }
            },
            "pipeline": {
                "asr": {
                    "enable": self._asr_enable.isChecked(),
                    "model_id": combo_value(self._asr_model_id),
                    "predict_url": self._asr_predict_url.text().strip(),
                    "stream_predict_url": self._asr_stream_url.text().strip(),
                    "turnsense": {
                        "enable": self._turnsense_enable.isChecked(),
                        "timeout": float(self._turnsense_timeout.value()),
                        "final_timeout": float(self._turnsense_final_timeout.value()),
                    },
                    "desktop_audio": {
                        "enable": self._desktop_audio_enable.isChecked(),
                        "input_device": self._desktop_audio_device.text().strip() or None,
                    },
                },
                "tts": {
                    "enable": self._tts_enable.isChecked(),
                    "model_id": combo_value(self._tts_model_id),
                    "predict_url": self._tts_predict_url.text().strip(),
                    "stream_predict_url": self._tts_stream_url.text().strip(),
                    "playback_volume": self._playback_volume.value(),
                    "output_device": self._output_device.text().strip() or None,
                    "allow_default_device_fallback": self._allow_default_fallback.isChecked(),
                    "warmup_on_startup": self._warmup_on_startup.isChecked(),
                    "cancel_on_new_input": self._cancel_on_new_input.isChecked(),
                    "adaptive_speed": {
                        "enable": self._adaptive_speed_enable.isChecked(),
                        "max_speed": float(self._adaptive_max_speed.value()),
                    },
                },
            },
        }
