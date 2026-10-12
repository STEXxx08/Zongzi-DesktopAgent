# Bot —— 机器人本体

本目录是 Zongzi 的 Bot 侧：对话编排、语音链路、记忆、工具运行时与桌面界面。
**完整的功能介绍、架构图、安装与配置步骤请看仓库根目录的 [`../README.md`](../README.md)。**
这里只写 Bot 模块自身的事。

> **来源**：本目录的骨架（事件驱动 `event/`、`pipeline/` 协议层、`framework/` 上下文）
> 派生自 [ZerolanLiveRobot](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot)
> （MIT License，Copyright (c) 2024 AkagawaTsurunaki），上游 MIT 全文见同目录 [`NOTICE`](NOTICE)。
> 对话编排、语音链路、记忆压缩、上下文工程、工具/技能/MCP/沙箱、桌宠界面均为本项目重写，
> 版权归 Copyright (c) 2026 STEXxx，许可见仓库根目录 [`../LICENSE`](../LICENSE)。

---

## 运行

```powershell
conda activate MyAI
cd Bot
python main.py
```

首次运行会生成 `config.yaml` 并退出（配置由代码里的 `BotConfig` 模型生成，字段自带注释），
编辑后再运行一次即可。

- 桌宠界面：`service.desktop_shell.enable`（需要 PySide6，见 `requirements-ui.txt`）
- 纯控制台：把上面那项设为 `false`
- 麦克风热键：`service.microphone.hotkey`（默认 `f8`）
- 不接麦克风、只用文字调试 prompt：`service.chat_window.enable`（旧名 `text_input_gui`，仍兼容）

> 💡 仓库另带一份脱敏样例 `config.example.yaml`（由作者的运行配置导出），可对照字段与
> 默认值；**首次运行生成的那份才是权威**。

## 依赖

```powershell
pip install -r Bot/requirements.txt     # 版本已按实际运行环境锁定
pip install -r Bot/requirements-ui.txt  # 可选的桌面界面依赖（PySide6）
pip install -e con-datapack-1.11        # 共享协议包 condata，不在 PyPI，必须手动装
```

## 目录

| 目录 | 职责 |
|---|---|
| `bot.py` | 对话编排主循环：打断处理、工具多轮调度、主动搭话、退出收尾 |
| `manager/` | 上下文管理（稳定 system 前缀）、提示词组装、配置加载 |
| `memory/` | 运行时历史、定期快照、合并压缩、`core_memory` |
| `pipeline/` | 各类管线：`asr` `llm` `mllm` `tts` `ocr` `imgcap` `vlm` |
| `services/` | `tool_runtime` `tool_coordinator` `skill_framework` `mcp` `sandbox` `browser` `web_search` `net_safety` `intent_router` `microphone` |
| `devices/` | 麦克风、扬声器、键盘热键、无头模式采集 |
| `ui/` | 桌宠窗口与文本交互界面 |
| `event/` | `TypedEventEmitter` 事件骨架与事件定义 |
| `framework/` | 运行时上下文与 Bot 基类 |
| `skills/` | 技能目录（`SKILL.md`，兼容 DeepSeek Harness 格式） |
| `MCP/` | MCP 服务配置样例与只读资料目录 |
| `tools/` | `harness_report.py`：调用观测看板（纯读日志）；`scan_api_surface.py`：第三方 API 面扫描 |
| `docs/` | 工具 / Skill 编写指南、harness 日志格式 |
| `tests/` | 离线回归测试（不需要 Core 在线） |

## 开发

回归测试（判定标准：**没有 failed / error**，用例数随开发变动，当前收集量见根 [`README.md`](../README.md) 的开发章节）：

```powershell
conda activate MyAI
cd ..
python -m pytest Bot/tests/test_p0_smoke.py Bot/tests/test_p1_security.py `
                 Bot/tests/test_p1_voice.py Bot/tests/test_p1_desktop.py `
                 Bot/tests/test_p1_harness.py Bot/tests/test_p1_prompt.py `
                 Bot/tests/test_p1_pseudo_markers.py Bot/tests/test_p1_llm_openai_compat.py `
                 Bot/tests/test_p1_model_id_freedom.py -q
```

> ⚠️ 干净克隆上会有 **4 个用例自动跳过**：它们断言的是作者本机 `config.yaml` 里的自定义
> `system_prompt` 与 `service.web_search.mode`，那份配置属隐私数据、不随仓库发布，
> 跳过原因直接写在用例里。缺少 BGE 模型时会再跳过 1 个（见 `services/intent_router/README.md`）。

调用观测看板：

```powershell
python Bot/tools/harness_report.py --out -
```

扩展文档：

- 新增工具 → [`docs/TOOLS_CREATION_GUIDE.md`](docs/TOOLS_CREATION_GUIDE.md)
- 新增 Skill → [`docs/SKILLS_CREATION_GUIDE.md`](docs/SKILLS_CREATION_GUIDE.md)
- 日志字段 → [`docs/HARNESS_LOGGING.md`](docs/HARNESS_LOGGING.md)

## 设计约束（改动时别破坏）

1. **system prompt 前缀恒定**：时间、环境状态、工具轮数提示、工具结果都进**用户轮**，
   否则提示缓存整条作废（`manager/context_manager.py`）。
2. **不估算 token**：只用服务商返回的真实用量，缺失就留空（`condata.harness.usage_dict`）。
3. **外部内容先中和**：网页正文、MCP 返回值、文件内容进上下文前中和工具调用标记形状。
4. **出站先校验地址**：拒绝内网/保留地址并固定已验证 IP（`services/net_safety.py`）。
