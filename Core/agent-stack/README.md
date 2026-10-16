# Core 服务栈注册表启动器

本启动器只管理 Core 侧模型服务，保持 Core 与 Bot 分离。Bot 不在注册表内，仍由自己的入口启动；SearXNG 是独立进程/容器，也不归启动器管。

## 目录结构

```text
Core/
├─ agent-stack.cmd
├─ agent-stack.ps1
├─ starter.py
└─ agent-stack/
   ├─ registry.json
   ├─ launcher.txt
   ├─ service-host.txt
   └─ runtime/                 # 自动生成的状态和日志
```

入口文件位于 Core 根目录，其余注册表、执行逻辑和运行数据位于 `Core/agent-stack`。

## 使用

进入 Core 目录：

```bat
agent-stack.cmd validate
agent-stack.cmd start
```

默认检测到 Windows Terminal 时使用单窗口多标签页，否则每个服务打开独立窗口：

```bat
agent-stack.cmd start -TerminalMode tabs
agent-stack.cmd start -TerminalMode windows
agent-stack.cmd start -Hidden
```

## 跨平台启动器（`python agent_stack.py`）

上面的 `.cmd` / `.ps1` 是 Windows 专用的。Linux / WSL / macOS（Windows 也可以）用同一份 `registry.json` 的 Python 版本：

```bash
cd Core
python agent_stack.py validate          # 校验注册表
python agent_stack.py start             # 拉起全部服务
python agent_stack.py status
python agent_stack.py logs --service asr
python agent_stack.py restart
python agent_stack.py stop
```

- **只依赖标准库**，任何 conda 环境都能跑。可选参数：`--registry <路径>`、`--startup-timeout <秒>`、`--hidden`（Windows 下不弹控制台）。
- **语义与 PowerShell 版一致**：`start_order` 相同的服务并行启动、`tcp` / `process` / `systemd` 三种健康检查、同一个 `state.json`、同样以 `[服务名]` 前缀写进 `combined.log`、启动失败事务式回滚、`optional_dependencies`（SearXNG）只尽力启动且失败不中止、`stop` 不停可选依赖。
- **平台差异**：

| 项 | Windows | Linux / macOS |
|---|---|---|
| conda 激活 | `cmd.exe` + `call conda.bat activate`（与 `.ps1` 版相同） | `conda run --no-capture-output -n <环境名>`（不需要 shell 里先 `conda init`） |
| 终端 | 默认每个服务一个控制台窗口，`--hidden` 关闭 | 服务在后台进程组里跑，输出全部进 `combined.log`，用 `logs` 看 |
| 停止 | `taskkill /T /F`（失败回退到内核 `TerminateProcess`） | 向进程组发 `SIGTERM`，超时后 `SIGKILL` |
| 端口清扫 | `netstat -ano` | `ss -ltnp`，没有就退回 `lsof` |
| `runner: wsl-systemd` | 通过 `wsl.exe` 驱动 WSL 里的 systemd 单元 | 不支持（Linux 上直接用 systemd） |

- **实测状态**：Windows 上已用假注册表验证 `validate / start / status / logs / restart / stop`、`conda` 与 `process` 两种 runner、以及"可选依赖失败不中止"；**Linux 专有路径（`conda run`、进程组信号、`ss`/`lsof` 端口清扫）尚未实测**，第一次在 Linux 上用时请留意 `status` 输出。

管理命令：

```bat
agent-stack.cmd status
agent-stack.cmd logs
agent-stack.cmd logs -Service asr
agent-stack.cmd restart
agent-stack.cmd stop
```

合并日志位于 `agent-stack/runtime/logs/combined.log`。每行包含时间与来源模块。停止时会根据状态记录关闭启动器创建的全部进程树。

启动入口、PowerShell、子 CMD、Python 标准输出及合并日志统一使用 UTF-8，可正常显示中文路径和中文日志。

## 当前注册服务

| 服务 | Conda 环境 | 工作目录 | 端口 |
|---|---|---|---:|
| ASR | `qwen3-asr` | Core 根目录 | 11001 |
| MLLM | `llm_deepseek_online` | Core 根目录 | 11000 |
| GPT-SoVITS | `GPTSoVits` | `GPT-SoVITS` | 11002 |
| LLM memory | `llm_deepseek_online` | Core 根目录 | 10010 |
| Web Search | `llm_deepseek_online` | Core 根目录 | 11012 |

共 **5 个**服务。`start_order` 全部为 10（见 `registry.json`）：5 个服务同属一组，
激活阶段串行、模型加载与就绪等待并行，因此 `web_search` 不会等到其它四个服务全部 ready
之后才被拉起。启动器会串行执行每个服务的 Conda 激活；前一个环境写入激活成功标记后，
才激活下一个环境。已经激活的服务会立即开始加载模型，因此模型加载仍然可以并行进行。

每个 Conda 服务都在自己的 `cmd.exe` 环境中执行：

```bat
call conda.bat activate 环境名
python ...
```

启动器不再并发执行 Conda 激活，也不使用 `conda run`，避免 Windows 下多个服务争用 `__conda_tmp_*.txt`。单个环境激活的默认超时为 60 秒，可通过注册表中的 `activation_timeout_seconds` 调整。TTS 的当前工作目录明确设置为 `Core/GPT-SoVITS`。

在服务标签页或独立窗口中按 `Ctrl+C` 时，宿主会终止该服务的整个 `cmd.exe → Python` 子进程树。若需一次关闭全部服务，仍建议执行 `agent-stack.cmd stop`。

启动失败时会执行事务式回滚：先按状态文件反向终止已登记的进程树，再扫描注册表声明的监听端口并清理尚未来得及登记的孤儿 Python 进程。如果再次启动时检测到上一次只启动了一部分服务，也会先完成清理再重试。

## 可选依赖（不在栈的生命周期内）

`settings.optional_dependencies` 用来声明**外部基础设施**——当前只有 SearXNG（它不只服务于这个栈，也可能装着别的用途）：

```json
"settings": {
  "optional_dependencies": [
    { "name": "searxng", "enabled": true, "runner": "wsl-systemd",
      "distro": "", "unit": "searxng-local", "sudo": true }
  ]
}
```

语义与栈内服务刻意不同：

- `start` / `restart` 时**先尽力拉起**（`wsl -d <distro> sudo -n systemctl start <unit>`）；**失败只打印警告，不中止、不回滚**，其它服务照常启动。
- 用 `sudo -n`：绝不卡在 sudo 密码提示上，没有免密 sudo 就直接跳过。
- `stop` **不会停它**（只打印一行 `[deps] left running`），也不会写进 `state.json`；`status` 会把它单列一行（`running` / `stopped` / `unreachable`）。
- `distro` 留空 = WSL 默认发行版；SearXNG 在另一台机器或你想手工管理时，把 `enabled` 改成 `false`。
- `validate` 会把可选依赖一起数出来：`Registry is valid: 5 enabled service(s), 1 optional dependency(ies).`
- 只有 `runner: "wsl-systemd"` 支持这种依赖（`unit` 必填），因为可选依赖的前提就是"别人机器上的 systemd 单元"。

> ✋ **启动器本身只在 Windows 上可用**（`cmd.exe` / PowerShell / Windows Terminal / `conda.bat`）。Core 的 Python 服务跨平台，原生 Linux 请自己逐个 `python starter.py <服务>` 或写 systemd 单元；WSL 里的单元则可以通过上面的 `wsl-systemd` runner 被 Windows 侧启动器驱动。

## 独立的 Bot

Bot 不属于 Core 服务栈，用 MyAI 环境单独启动：

```bat
conda activate MyAI
cd Bot
python main.py
```

`web_search` 已经登记在注册表里（`agent-stack.cmd start` 会一起拉起），需要单独调试时也可以用同样的方式手动起：

```bat
conda activate MyAI
cd Core
python starter.py web_search
```

它依赖的 SearXNG 是独立进程/容器，不用启动器管也能跑：本机 WSL 里的 systemd 单元由 `settings.optional_dependencies` 尽力拉起，装在别的机器上则由那台机器自己管理（见上一节与根 README 3.5）。

## 注册新的 Core 模块

只需向 `agent-stack/registry.json` 的 `services` 数组添加注册项。例如：

```json
{
  "name": "ocr",
  "enabled": true,
  "runner": "conda",
  "start_order": 10,
  "environment": "MyAI",
  "working_directory": ".",
  "command": "python",
  "arguments": ["-u", "starter.py", "ocr"],
  "health": {
    "type": "tcp",
    "host": "127.0.0.1",
    "port": 11004
  }
}
```

三种 `runner`：

| runner | 用途 | 必填字段 | 健康检查 |
|---|---|---|---|
| `conda` | 用指定 conda 环境跑 Core 服务（默认） | `environment`、`command`、`working_directory` | `tcp`（默认 `127.0.0.1`）/ `process` |
| `process` | 不激活环境，直接用 PATH 里的解释器跑（不用 conda 时） | `command`、`working_directory` | `tcp` / `process` |
| `wsl-systemd` | 驱动某个 WSL 发行版里的 systemd 单元（也可作为可选依赖） | `unit`（`distro` 留空=默认发行版，`sudo: false` 可去掉 sudo） | `systemd` |

`start_order` 相同的服务会同时启动（模型并行加载），不同的按数值升序分组执行。