"""设置页 6：联网搜索、意图路由、沙箱、Skills、MCP 与 Harness 工具治理。"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ui.settings.widgets import (
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


class ToolsPage(QWidget):
    def __init__(self, service_cfg, parent=None) -> None:
        super().__init__(parent)
        ws_cfg = service_cfg.web_search
        ir_cfg = service_cfg.intent_router
        tool_cfg = service_cfg.tool
        sb_cfg = service_cfg.sandbox
        br_cfg = service_cfg.browser
        sk_cfg = service_cfg.skills
        mcp_cfg = service_cfg.mcp
        hn_cfg = service_cfg.harness
        hr_cfg = service_cfg.harness_recall

        # 1. 联网与语义意图路由
        web_card = SettingsCard("联网搜索与本地语义路由", "配置 Core WebSearch 网关与本地 BGE 意图直连。", self)
        self._ws_mode = make_combo(
            [
                ("禁用联网 (disable)", "disable"),
                ("仅本地关键词快路 (local_only)", "local_only"),
                ("允许模型自主联网 (enable)", "enable"),
            ],
            ws_cfg.mode,
        )
        self._ws_endpoint = make_line_edit(ws_cfg.endpoint)
        self._ws_timeout = make_float_spin(ws_cfg.timeout_seconds, 0.5, 60.0, step=0.5, decimals=1)
        self._ws_max_chars = make_int_spin(ws_cfg.max_context_chars, 500, 20000, step=500)
        self._ws_keywords = TagListEditor(list(ws_cfg.keywords), placeholder="联网触发关键词，逗号分隔")

        self._ir_enable = make_checkbox("启用本地 BGE 语义意图路由", ir_cfg.enable)
        self._ir_conf = make_float_spin(ir_cfg.confidence_threshold, -1.0, 1.0, step=0.02, decimals=2)
        self._ir_margin = make_float_spin(ir_cfg.margin_threshold, 0.0, 1.0, step=0.01, decimals=2)
        self._ir_fallback = make_checkbox("本地语义模型不可用时回退为关键词匹配", ir_cfg.fallback_to_keywords)

        web_card.add_row("联网模式", self._ws_mode)
        web_card.add_row("搜索网关地址", self._ws_endpoint)
        web_card.add_row("搜索超时 (秒)", self._ws_timeout)
        web_card.add_row("上下文最大字符数", self._ws_max_chars)
        web_card.add_row("快路触发词", self._ws_keywords)
        web_card.add_row("启用语义意图路由", self._ir_enable)
        web_card.add_row("路由置信度阈值", self._ir_conf)
        web_card.add_row("路由边际差值阈值", self._ir_margin)
        web_card.add_row("关键词兜底", self._ir_fallback)

        # 2. 工具循环与文件沙箱
        runtime_card = SettingsCard("通用工具循环与文件沙箱", "限制每轮工具并发/轮次以及 Sandbox 目录读写权限。", self)
        self._tool_max_rounds = make_int_spin(tool_cfg.max_rounds, 1, 50)
        self._tool_max_calls = make_int_spin(tool_cfg.max_calls_per_round, 1, 64)
        self._tool_max_chars = make_int_spin(tool_cfg.max_result_chars, 1000, 1000000, step=1000)
        self._tool_marker = make_line_edit(tool_cfg.model_marker)

        self._sb_enable = make_checkbox("开放 Sandbox 沙箱文件工具", sb_cfg.enable)
        self._sb_write = make_checkbox("允许新建文件与目录 (allow_write)", sb_cfg.allow_write)
        self._sb_edit = make_checkbox("允许局部编辑已有文件 (allow_edit)", sb_cfg.allow_edit)
        self._sb_overwrite = make_checkbox("允许覆盖已有文件 (allow_overwrite)", sb_cfg.allow_overwrite)

        runtime_card.add_row("单轮最大工具轮次", self._tool_max_rounds)
        runtime_card.add_row("单轮最大并发调用", self._tool_max_calls)
        runtime_card.add_row("结果注入字符上限", self._tool_max_chars)
        runtime_card.add_row("工具协议标记", self._tool_marker)
        runtime_card.add_row("启用文件沙箱", self._sb_enable)
        runtime_card.add_row("创建文件权限", self._sb_write)
        runtime_card.add_row("编辑文件权限", self._sb_edit)
        runtime_card.add_row("覆盖文件权限", self._sb_overwrite)

        # 3. 网页研究 / Skills / MCP / Harness
        ext_card = SettingsCard("网页研究、Skills、MCP 与 Harness 观测", "管理扩展工具链与诊断日志。", self)
        self._br_enable = make_checkbox("启用网页正文抓取与研究工具", br_cfg.enable)
        self._br_backend = make_combo(
            [("自动优先 Playwright MCP (auto)", "auto"), ("静态 HTTP 读取 (http)", "http"), ("强制 MCP (mcp)", "mcp")],
            br_cfg.backend,
        )
        self._br_save_raw = make_checkbox("默认持久保存抓取到的网页原文", br_cfg.save_raw_by_default)
        self._sk_enable = make_checkbox("启用 Skills 技能框架", sk_cfg.enable)
        self._sk_allow_create = make_checkbox("允许通过 API 创建 Skill", sk_cfg.allow_create)
        self._sk_model_create = make_checkbox("允许模型自主创建 Skill", sk_cfg.allow_model_create)
        self._mcp_enable = make_checkbox("启用 MCP 外部工具客户端", mcp_cfg.enable)
        self._mcp_timeout = make_float_spin(mcp_cfg.request_timeout, 1.0, 120.0, step=1.0, decimals=1)
        self._hn_enable = make_checkbox("启用 Harness 调用诊断日志", hn_cfg.enable)
        self._hn_payloads = make_checkbox("在日志中记录工具参数与结果原文", hn_cfg.include_payloads)
        self._hr_enable = make_checkbox("允许模型回读本次运行工具记录 (harness_recall)", hr_cfg.enable)
        self._hr_max_records = make_int_spin(hr_cfg.max_records, 1, 50)

        ext_card.add_row("启用网页研究", self._br_enable)
        ext_card.add_row("研究抓取后端", self._br_backend)
        ext_card.add_row("自动保存原文", self._br_save_raw)
        ext_card.add_row("启用 Skills", self._sk_enable)
        ext_card.add_row("允许创建 Skill", self._sk_allow_create)
        ext_card.add_row("模型自建 Skill", self._sk_model_create)
        ext_card.add_row("启用 MCP 服务", self._mcp_enable)
        ext_card.add_row("MCP 请求超时 (秒)", self._mcp_timeout)
        ext_card.add_row("启用 Harness 日志", self._hn_enable)
        ext_card.add_row("记录载荷原文", self._hn_payloads)
        ext_card.add_row("工具记录回读", self._hr_enable, restart_required=True)
        ext_card.add_row("单次回读条数上限", self._hr_max_records)

        build_settings_page(self, [web_card, runtime_card, ext_card])

    def values(self) -> dict:
        return {
            "service": {
                "web_search": {
                    "mode": combo_value(self._ws_mode),
                    "endpoint": self._ws_endpoint.text().strip(),
                    "timeout_seconds": float(self._ws_timeout.value()),
                    "max_context_chars": self._ws_max_chars.value(),
                    "keywords": self._ws_keywords.values(),
                },
                "intent_router": {
                    "enable": self._ir_enable.isChecked(),
                    "confidence_threshold": float(self._ir_conf.value()),
                    "margin_threshold": float(self._ir_margin.value()),
                    "fallback_to_keywords": self._ir_fallback.isChecked(),
                },
                "tool": {
                    "max_rounds": self._tool_max_rounds.value(),
                    "max_calls_per_round": self._tool_max_calls.value(),
                    "max_result_chars": self._tool_max_chars.value(),
                    "model_marker": self._tool_marker.text().strip() or "tool_call",
                },
                "sandbox": {
                    "enable": self._sb_enable.isChecked(),
                    "allow_write": self._sb_write.isChecked(),
                    "allow_edit": self._sb_edit.isChecked(),
                    "allow_overwrite": self._sb_overwrite.isChecked(),
                },
                "browser": {
                    "enable": self._br_enable.isChecked(),
                    "backend": combo_value(self._br_backend),
                    "save_raw_by_default": self._br_save_raw.isChecked(),
                },
                "skills": {
                    "enable": self._sk_enable.isChecked(),
                    "allow_create": self._sk_allow_create.isChecked(),
                    "allow_model_create": self._sk_model_create.isChecked(),
                },
                "mcp": {
                    "enable": self._mcp_enable.isChecked(),
                    "request_timeout": float(self._mcp_timeout.value()),
                },
                "harness": {
                    "enable": self._hn_enable.isChecked(),
                    "include_payloads": self._hn_payloads.isChecked(),
                },
                "harness_recall": {
                    "enable": self._hr_enable.isChecked(),
                    "max_records": self._hr_max_records.value(),
                },
            }
        }
