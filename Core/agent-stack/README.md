# Core 服务栈注册表启动器

本启动器只管理 Core 侧模型服务，保持 Core 与 Bot 分离。Bot 不在注册表内，仍由自己的入口启动；SearXNG 是独立进程/容器，也不归启动器管。

启动器**不读取 Bot 的任何配置**——Core 与 Bot 可以部署在不同机器上、Core 先启动。要启动哪些 Core 服务由 `Core/config.yaml` 里独立的 `Startup` 段决定（见下文）。

## 目录结构

```text
Core/
├─ config.yaml                # 模型配置 + 顶部独立的 Startup 启动开关段
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
agent-stack.cmd plan        :: 只打印"会启动哪些服务"，不动任何进程
agent-stack.cmd start
```

默认检测到 Windows Terminal 时使用单窗口多标签页，否则每个服务打开独立窗口：

```bat
agent-stack.cmd start -TerminalMode tabs
agent-stack.cmd start -TerminalMode windows
agent-stack.cmd start -Hidden
```

## 选哪些服务启动（`Core/config.yaml` 的 `Startup` 段）

**开关在 `Core/config.yaml` 里单独一段，和模型配置完全分离**：

```yaml
Startup:
  asr: true
  llm: true           # Bot 用 LLM 时开这个（与 mllm 二选一）
  mllm: false         # Bot 用 MLLM 时改开这个
  tts: true
  llm-memory: true    # Bot 的 memory.memory_model.type = llm 时需要
  mllm-memory: false  # type = mllm 时改开这个
  web_search: true
  ocr: false
  imgcap: false
```

- **键名 = `registry.json` 里的服务名**，所以加新服务时只需要在这里补一行。
- **两个启动器（`agent-stack.cmd` 与 `python agent_stack.py`）都读这一段**，改一次两边都生效，不用再进 `registry.json` 改 `enabled`。
- **Core 不需要知道 Bot 用哪个模型、也不需要读 Bot 的配置**：Core 和 Bot 可以分别部署在不同机器上、Core 先启动。你只需要在 Core 这边把 `llm` / `mllm` 相应那一行打开。
- 没在 `Startup` 里出现的服务，回落到 `registry.json` 自己的 `"enabled"` 值（没写 `enabled` 视为 `true`）。
- 解析是**行扫描**，不是完整 YAML：`Startup:` 顶格写，条目用 2 空格缩进，值只接受 `true/false`（也认 `yes/no/on/off/1/0`），行尾可以带 `#` 注释。`agent_stack.py` 与 `launcher.txt` 实现同一套规则，`validate` / `plan` 会把每条开关的**来源**打印出来（`config.yaml Startup.llm` 还是 `registry enabled=true`），值写错会给出警告而不是静默忽略。

有些服务**共用同一个端口**，天生互斥：

- `llm` 与 `mllm` 在 `Core/config.yaml` 里都是 **11000**；
- `llm-memory` 与 `mllm-memory` 都是 **10010**（`starter.py <llm|mllm> -memory`）。

`validate` / `plan` / `start` 会检查**同端口冲突**并直接报错说明是哪两个服务，而不是让第二个服务在绑定端口时莫名其妙地失败。

三种决定"这次启动什么"的方式，优先级从高到低：

| 方式 | 作用 | 是否会持久化 |
|---|---|---|
| `--only asr,tts`（`.cmd` 没有此参数） | 只启动这几个服务，**绕过 `Startup` 段**（也能临时拉起默认关闭的服务） | 否，只影响本次 |
| `-Config <路径>` / `--config <路径>` | 换一份 config.yaml（例如给不同机器准备不同 profile） | 否，只影响本次 |
| `Startup` 段 | 默认集合，两种启动器都读 | 是 |

## 跨平台启动器（`python agent_stack.py`）

上面的 `.cmd` / `.ps1` 是 Windows 专用的。Linux / WSL / macOS（Windows 也可以）用同一份 `registry.json` + `config.yaml` 的 Python 版本：

```bash
cd Core
python agent_stack.py validate          # 校验注册表 + 打印 Startup 开关来源 + 同端口冲突检查
python agent_stack.py plan              # 只打印将要启动哪些服务，不启动
python agent_stack.py start             # 按 config.yaml 的 Startup 段启动
python agent_stack.py start --only asr,tts  # 只拉起指定的几个
python agent_stack.py start --config other.yaml  # 换一份启动开关
python agent_stack.py status
python agent_stack.py logs --service asr
python agent_stack.py restart
python agent_stack.py stop
```

- **只依赖标准库**，任何 conda 环境都能跑。可选参数：`--registry <路径>`、`--config <路径>`、`--startup-timeout <秒>`、`--terminal auto|tabs|windows`、`--hidden`、`--only <服务,服务>`。
- **conda 的定位方式与 `.ps1` 完全一致**：先看 `CONDA_BAT` / `CONDA_EXE`，再看 `~/miniconda3|anaconda3/condabin/conda.bat`，最后才回落到 `PATH`。**`PATH` 里排在前面的是 `Library/bin/conda.bat`，那是需要先 `conda init` 的桩**，直接用它会在启动时报 `CondaError: Run 'conda init' before 'conda activate'` —— 现在会把它换成同级的 `condabin/conda.bat`，不再踩这个坑。
- **语义与 PowerShell 版一致**：`Startup` 开关、`start_order` 相同的服务并行启动、`tcp` / `process` / `systemd` 三种健康检查、同一个 `state.json`、同样以 `[服务名]` 前缀写进 `combined.log`、启动失败事务式回滚、`optional_dependencies`（SearXNG）只尽力启动且失败不中止、`stop` 不停可选依赖、`Ctrl+C` 会连同子进程树一起收掉。
- **平台差异**：

| 项 | Windows | Linux / macOS |
|---|---|---|
| conda 激活 | `cmd.exe` + `call condabin\conda.bat activate`（与 `.ps1` 版相同） | `conda run --no-capture-output -n <环境名>`（不需要 shell 里先 `conda init`） |
| 终端 | 默认与 `.cmd` 一致：`--terminal auto` 在有 `wt.exe` 时每个服务一个 Windows Terminal 标签页，否则每个服务一个控制台窗口（标题为 `Agent Stack - <服务名>`，`Ctrl+C` 只停该服务）；`--hidden` 完全不弹窗 | 服务在后台进程组里跑，输出全部进 `combined.log`，用 `logs` 看 |
| 服务存活 | 服务宿主用 `CREATE_BREAKAWAY_FROM_JOB` 启动：启动器退出（甚至终端被关掉）后服务不受宿主 job 对象连带终止；job 不允许 breakaway 时自动回退 | 同上，`start_new_session` 脱离父进程组 |
| 停止 | `taskkill /T /F`（失败回退到内核 `TerminateProcess`） | 向进程组发 `SIGTERM`，超时后 `SIGKILL` |
| 端口清扫 | `netstat -ano` | `ss -ltnp`，没有就退回 `lsof` |
| `runner: wsl-systemd` | 通过 `wsl.exe` 驱动 WSL 里的 systemd 单元 | 不支持（Linux 上直接用 systemd） |

- **实测状态**（2026-11-17，Windows）：两个启动器都验证了 `Startup` 段生效（改配置文件里的开关 → `plan` / `validate` / `start` 的选择结果同步变化；`llm`+`mllm` 同时打开会被拦下）。`.cmd` 版实测 `plan / validate / start / status / stop`（只开 `web_search` 跑通 conda 激活到端口就绪）；`agent_stack.py` 另用假注册表验证了 `conda` 与 `process` 两种 runner、`--only`、`hidden` 与 `tabs` 两种终端模式，并用真注册表验证 `asr` / `llm` / `llm-memory` / `web_search` 四个服务从 conda 激活到端口就绪（`llm` 需要 `DEEPSEEK_API_KEY`，缺失时会在启动阶段报错并事务回滚，日志里能看到完整 traceback）。同一台机器上 `tts`（GPT-SoVITS）冷启动超过 300 秒仍未就绪，启动器会按 `startup_timeout_seconds` 超时并回滚 —— 这是服务本身的加载耗时，不是启动器的问题，把该值调大或用 Bot 的 TTS 预热即可。**Linux 专有路径（`conda run`、进程组信号、`ss`/`lsof` 端口清扫）尚未实测**，第一次在 Linux 上用时请留意 `status` 输出。


管理命令：

```bat
agent-stack.cmd status
agent-stack.cmd plan
agent-stack.cmd logs
agent-stack.cmd logs -Service asr
agent-stack.cmd restart
agent-stack.cmd stop
```

合并日志位于 `agent-stack/runtime/logs/combined.log`。每行包含时间与来源模块。停止时会根据状态记录关闭启动器创建的全部进程树。

启动入口、PowerShell、子 CMD、Python 标准输出及合并日志统一使用 UTF-8，可正常显示中文路径和中文日志。

## 当前注册服务

| 服务 | 默认 | Conda 环境 | 工作目录 | 端口 |
|---|---|---|---|---:|
| `asr` | ✅ | `qwen3-asr` | Core 根目录 | 11001 |
| `llm` | ✅ | `llm_deepseek_online` | Core 根目录 | 11000 |
| `mllm` | ⬜ | `llm_deepseek_online` | Core 根目录 | 11000（与 `llm` 互斥） |
| `tts` | ✅ | `GPTSoVits` | `GPT-SoVITS` | 11002 |
| `llm-memory` | ✅ | `llm_deepseek_online` | Core 根目录 | 10010 |
| `mllm-memory` | ⬜ | `llm_deepseek_online` | Core 根目录 | 10010（与 `llm-memory` 互斥） |
| `web_search` | ✅ | `llm_deepseek_online` | Core 根目录 | 11012 |
| `ocr` | ⬜ | `MyAI` | Core 根目录 | 11004 |
| `imgcap` | ⬜ | `MyAI` | Core 根目录 | 11003 |

共 **9 个**服务，`config.yaml` 的 `Startup` 段默认开 5 个（✅）。`ocr` / `imgcap` 的依赖见 `Core/requirements-optional.txt`，装到哪个环境由你自己决定 —— 注册表里先按 `MyAI` 填了，改成你实际的环境即可。`registry.json` 里同名服务的 `enabled` 只在 `Startup` 段没提到该服务时生效（作为兜底默认值）。

`start_order` 全部为 10（见 `registry.json`）：同组服务激活阶段串行、模型加载与就绪等待并行，因此 `web_search` 不会等到其它服务全部 ready 之后才被拉起。启动器会串行执行每个服务的 Conda 激活；前一个环境写入激活成功标记后才激活下一个环境。已经激活的服务会立即开始加载模型，因此模型加载仍然可以并行进行。

每个 Conda 服务都在自己的 `cmd.exe` 环境中执行：

```bat
call condabin\conda.bat activate 环境名
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
- `validate` 会把可选依赖一起数出来：`Registry is valid: 5 service(s) to start, 1 optional dependency(ies).`
- 只有 `runner: "wsl-systemd"` 支持这种依赖（`unit` 必填），因为可选依赖的前提就是"别人机器上的 systemd 单元"。

> ✋ **`.cmd` / `.ps1` 启动器本身只在 Windows 上可用**（`cmd.exe` / PowerShell / Windows Terminal / `conda.bat`）。其它平台用 `python agent_stack.py`（见上文），它读同一份注册表；WSL 里的 systemd 单元也可以通过 `wsl-systemd` runner 被 Windows 侧启动器驱动。

## 独立的 Bot

Bot 不属于 Core 服务栈，用 MyAI 环境单独启动：

```bat
conda activate MyAI
cd Bot
python main.py
```

**Core 不读 Bot 的任何配置**：两者可以部署在不同机器上、Core 先启动，Core 只按自己 `config.yaml` 的 `Startup` 段决定拉哪些服务。所以"Bot 这轮用 LLM 还是 MLLM"这件事由你在 Core 侧改一行开关来表达（`llm` / `mllm` 二选一，记忆服务同理），改完随便跑一次 `agent-stack.cmd start` 或 `python agent_stack.py start` 即可，不需要进 `registry.json`。

`web_search` 已经登记在注册表里（`Startup.web_search: true`，`start` 会一起拉起），需要单独调试时也可以手动起：

```bat
conda activate llm_deepseek_online
cd Core
python starter.py web_search
```

它依赖的 SearXNG 是独立进程/容器，不用启动器管也能跑：本机 WSL 里的 systemd 单元由 `settings.optional_dependencies` 尽力拉起，装在别的机器上则由那台机器自己管理（见上一节与根 README 3.5）。

## 注册新的 Core 模块

需要做两件事：向 `agent-stack/registry.json` 的 `services` 数组添加注册项，并在 `Core/config.yaml` 的 `Startup` 段补一行开关。例如：

```json
{
  "name": "ocr",
  "enabled": false,
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

```yaml
Startup:
  ocr: true
```

`enabled: false`（或 `Startup.ocr: false`）的服务保留在注册表里但不会被 `start` 拉起：`--only ocr` 可以临时启动它，`status` 也会把它列出来（`stopped`）。**多个服务监听同一端口时不能同时打开**，`validate` / `plan` / `start` 会直接报错，而不是让后一个服务在绑定端口时失败。

三种 `runner`：

| runner | 用途 | 必填字段 | 健康检查 |
|---|---|---|---|
| `conda` | 用指定 conda 环境跑 Core 服务（默认） | `environment`、`command`、`working_directory` | `tcp`（默认 `127.0.0.1`）/ `process` |
| `process` | 不激活环境，直接用 PATH 里的解释器跑（不用 conda 时） | `command`、`working_directory` | `tcp` / `process` |
| `wsl-systemd` | 驱动某个 WSL 发行版里的 systemd 单元（也可作为可选依赖） | `unit`（`distro` 留空=默认发行版，`sudo: false` 可去掉 sudo） | `systemd` |

`start_order` 相同的服务会同时启动（模型并行加载），不同的按数值升序分组执行。

除上表字段外，`conda` / `process` 两种 runner 还接受可选的 `process_priority`（见上一节）。

## 进程优先级（`process_priority`）

延迟敏感的服务可以声明一个 Windows 进程优先级。启动器会在拉起服务后对**服务进程本身和它的宿主**分别设置，并把结果写进 `combined.log`：

```json
{
  "name": "asr",
  "enabled": true,
  "runner": "conda",
  "environment": "qwen3-asr",
  "command": "python",
  "arguments": ["-u", "starter.py", "asr"],
  "process_priority": "above_normal"
}
```

- 取值：`idle` / `below_normal` / `normal` / `above_normal` / `high` / `realtime`，缺省不设置。`validate` 会拒绝拼错的取值。
- **建议只用到 `above_normal`，不要用 `high` / `realtime`**：base priority 落在 16–31 的线程**不再享受调度器的动态优先级提升**，对实时音频反而是反效果。
- 为什么 ASR 需要它：ASR 的 encoder 实际跑在 CPU 上（见 `Core/config.yaml` 里 `ASR.config` 的注释），前台有游戏把 CPU 打满时，普通优先级的进程会被直接饿死，表现就是识别延迟越拖越长。
- 非 Windows 平台会记录一行 `ignored on this platform` 并继续启动，不会让服务起不来；设置失败同样只是记一行日志、不中止。