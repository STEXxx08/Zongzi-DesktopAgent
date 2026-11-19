"""设置页 5：分层长期记忆与向量检索 (RAG) 配置。"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ui.settings.widgets import (
    PasswordLineEdit,
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


class MemoryPage(QWidget):
    def __init__(self, memory_cfg, parent=None) -> None:
        super().__init__(parent)
        retrieval_cfg = memory_cfg.retrieval
        mm_cfg = memory_cfg.memory_model

        # 1. 长期记忆基础参数
        base_card = SettingsCard("记忆快照、合并与历史压缩", "控制临时对话历史上限与定期抽提归档周期。", self)
        self._memory_enable = make_checkbox("启用分层长期记忆系统", memory_cfg.enable)
        self._max_history = make_int_spin(memory_cfg.max_history, 4, 1000, step=4)
        self._save_interval = make_int_spin(memory_cfg.save_interval_rounds, 1, 100)
        self._merge_batch_size = make_int_spin(memory_cfg.merge_batch_size, 1, 100)
        self._async_save = make_checkbox("异步后台抽取保存快照（不阻塞当前对话）", memory_cfg.async_save)
        self._compact_after = make_int_spin(memory_cfg.compact_history_after_save, 0, 50)
        self._injection_max_chars = make_int_spin(memory_cfg.injection_max_chars, 500, 100000, step=500)

        base_card.add_row("启用长期记忆", self._memory_enable, restart_required=True)
        base_card.add_row("短期历史条数上限", self._max_history)
        base_card.add_row("快照保存周期 (轮)", self._save_interval)
        base_card.add_row("合并批大小", self._merge_batch_size)
        base_card.add_row("异步保存快照", self._async_save)
        base_card.add_row(
            "历史压缩延迟周期",
            self._compact_after,
            tooltip="0 表示禁用历史压缩，1 表示保存后立即压缩，N 表示保留最近 N 轮压缩游标",
        )
        base_card.add_row("记忆注入字符上限", self._injection_max_chars)

        # 2. RAG 检索参数
        rag_card = SettingsCard("记忆检索 (RAG) 与常驻窗口", "支持关闭 (off)、观测 (observe) 与真检索 (on) 三态模式。", self)
        self._retrieval_mode = make_combo(
            [
                ("关闭检索 / 全量注入 (off)", "off"),
                ("仅日志观测命中 (observe)", "observe"),
                ("开启常驻窗口 + 向量检索 (on)", "on"),
            ],
            retrieval_cfg.mode,
        )
        self._min_recent_days = make_int_spin(retrieval_cfg.min_recent_days, 0, 365)
        self._recent_days = make_int_spin(retrieval_cfg.recent_days, 0, 3650)
        self._max_recent_entries = make_int_spin(retrieval_cfg.max_recent_entries, 1, 5000, step=10)
        self._top_k = make_int_spin(retrieval_cfg.top_k, 1, 30)
        self._same_day_expand = make_int_spin(retrieval_cfg.same_day_expand, 0, 20)
        self._min_score = SliderSpinRow(retrieval_cfg.min_score, -1.0, 1.0, step=0.05, decimals=2)
        self._rag_max_chars = make_int_spin(retrieval_cfg.max_chars, 100, 20000, step=100)
        self._random_prob = make_float_spin(retrieval_cfg.random_probability, 0.0, 1.0, step=0.05, decimals=2)
        self._random_prob_sparse = make_float_spin(
            retrieval_cfg.random_probability_sparse, 0.0, 1.0, step=0.05, decimals=2
        )

        rag_card.add_row("检索工作模式", self._retrieval_mode)
        rag_card.add_row("无条件保底天数", self._min_recent_days)
        rag_card.add_row("自然常驻窗口天数", self._recent_days)
        rag_card.add_row("常驻窗口条目上限", self._max_recent_entries)
        rag_card.add_row("单轮召回条数 (top_k)", self._top_k)
        rag_card.add_row("同日连带条数上限", self._same_day_expand)
        rag_card.add_row("余弦相似度下限", self._min_score)
        rag_card.add_row("单轮检索字符上限", self._rag_max_chars)
        rag_card.add_row("偶发回忆概率", self._random_prob)
        rag_card.add_row("常驻稀疏时回忆概率", self._random_prob_sparse)

        # 3. 独立记忆模型
        mm_card = SettingsCard("独立记忆抽取模型", "可选指定单独且更经济的模型完成后台摘要与归并。", self)
        self._mm_enable = make_checkbox("为记忆任务使用独立模型（关闭则复用主模型）", mm_cfg.enable)
        self._mm_type = make_combo([("纯文本 LLM", "llm"), ("多模态 MLLM", "mllm")], mm_cfg.type)
        self._mm_model_id = make_line_edit(str(getattr(mm_cfg, "model_id", "") or ""))
        self._mm_predict_url = make_line_edit(str(getattr(mm_cfg, "predict_url", "") or ""))
        self._mm_api_key = PasswordLineEdit(
            str(getattr(mm_cfg, "api_key", "") or ""),
            placeholder="复用 Core 时可留空",
        )
        self._had_mm_extra_keys = {
            k for k in ("model_id", "predict_url", "api_key") if getattr(mm_cfg, k, None) is not None
        }

        mm_card.add_row("启用独立记忆模型", self._mm_enable, restart_required=True)
        mm_card.add_row("模型协议类型", self._mm_type, restart_required=True)
        mm_card.add_row("模型标识 (model_id)", self._mm_model_id, restart_required=True)
        mm_card.add_row("预测地址 (predict_url)", self._mm_predict_url, restart_required=True)
        mm_card.add_row("API Key", self._mm_api_key, restart_required=True)

        build_settings_page(self, [base_card, rag_card, mm_card])

    def values(self) -> dict:
        mm_dict: dict = {
            "enable": self._mm_enable.isChecked(),
            "type": combo_value(self._mm_type),
        }
        for key, raw_val in (
            ("model_id", self._mm_model_id.text().strip()),
            ("predict_url", self._mm_predict_url.text().strip()),
            ("api_key", self._mm_api_key.text().strip()),
        ):
            if raw_val or key in self._had_mm_extra_keys:
                mm_dict[key] = raw_val

        return {
            "memory": {
                "enable": self._memory_enable.isChecked(),
                "max_history": self._max_history.value(),
                "save_interval_rounds": self._save_interval.value(),
                "merge_batch_size": self._merge_batch_size.value(),
                "async_save": self._async_save.isChecked(),
                "compact_history_after_save": self._compact_after.value(),
                "injection_max_chars": self._injection_max_chars.value(),
                "retrieval": {
                    "mode": combo_value(self._retrieval_mode),
                    "min_recent_days": self._min_recent_days.value(),
                    "recent_days": self._recent_days.value(),
                    "max_recent_entries": self._max_recent_entries.value(),
                    "top_k": self._top_k.value(),
                    "same_day_expand": self._same_day_expand.value(),
                    "min_score": self._min_score.value(),
                    "max_chars": self._rag_max_chars.value(),
                    "random_probability": float(self._random_prob.value()),
                    "random_probability_sparse": float(self._random_prob_sparse.value()),
                },
                "memory_model": mm_dict,
            }
        }
