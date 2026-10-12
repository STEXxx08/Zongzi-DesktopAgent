# Zongzi

**一个跑在自己电脑上的 AI 桌面伙伴：听得见、会说话、记得住你、能动手用工具。**
**名字叫「粽子」——有猫耳和猫尾的猫娘？**

当然实际上不只是这样，本项目实质上是：

**一个高自由度的桌面 Agent 集成框架。**

**把 ASR、LLM / MLLM、TTS、记忆、工具、屏幕识别这些组件拼成一个长期驻留在你电脑上的智能体 ——
听得到你说话，看得出你屏幕上在发生什么，记得住你们聊过的内容，还能自己调工具去干活。**
可以陪你玩游戏、看视频、处理日常杂事。

- **组件全可替换**：每个环节都能独立换实现，本地模型与云端 API 随意混搭 —— 换一个 `id` / `model_id` 就换一家
- **桌面原生**：屏幕理解、桌面音频感知、桌宠界面与字幕、全局热键
- **Agent 能力**：工具调用（同轮多轮）、Skills、MCP、文件沙箱、联网检索
- **自研记忆系统**：分层长期记忆 + 定期快照 + 合并压缩去重，记忆可单独指定模型（详见[记忆系统](#记忆系统自研)）
- **本地优先**：整条链路都能跑在本机（含离线模型），也可以只跑一个客户端 + 云端 API

「粽子」是自带的人设示例，把角色配置换掉，它就是你自己的 Agent。

Zongzi 由 **Bot**（Agent 本体）与 **Core**（模型服务层）两部分组成。Bot 负责对话编排、语音链路、屏幕理解、记忆、工具与界面；Core 把 ASR / LLM / MLLM / TTS 等模型封装成统一的 HTTP 服务。

**核心设计目标是「可组合」**：ASR、LLM、TTS 各组件彼此解耦，可以任意混搭本地模型与云端 API —— 例如「本地 ASR + 云端 LLM + 本地 TTS」，或「全部走云端 API」。详见[模型组合](#模型组合怎么搭)。

> 本项目是独立项目。最初的架构和构想来自`Bot/` ： [ZerolanLiveRobot](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot)，
> `Core/` ： [ZerolanCore](https://github.com/AkagawaTsurunaki/zerolan-core)，
> `con-datapack-1.11/` ： [ZerolanData](https://github.com/AkagawaTsurunaki/zerolan-data)，
> `Core/GPT-SoVITS/` 是 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 的本地副本；
> 上游均为 MIT 许可，版权声明见各自目录。详见文末 [License 与来源](#license-与来源)。
> 感谢AkagawaTsurunaki提供的开源代码，目前本项目基本没有复用初始代码，仅保留了HTTP传输架构。

- 后续所有提到的权重等不随仓库上传的大文件，如有需要可以来找我拿
- 本release目前只是一个早期架构版本，不了解环境部署的用户可能无法部署该项目，我会持续更新，最终目标是在架构基础上做出一份可能即装即用的应用框架~
- 本项目的公开版提取和README内容依赖AI完成，可能有逻辑遗漏，也可能在不同环境下出现各类问题，欢迎在项目下提出issue，我看到会尽快回复或修复~
- 本项目纯凭兴趣，非常欢迎大佬提出改进建议。
- 如果使用了本项目，或者因为本项目收到了一点点启发，就给个star呗，这对我很重要~

---

## 目录

- [特性](#特性)
- [记忆系统](#记忆系统自研)
- [环境兼容性（装之前先看）](#环境兼容性装之前先看)
- [部署教程](#部署教程)
- [模型组合怎么搭](#模型组合怎么搭)
- [项目结构](#项目结构)
- [开发](#开发)
- [已知限制](#已知限制)
- [License 与来源](#license-与来源)

---

## 特性

- 🎙️ **流式语音对话**：麦克风实时采集 → 流式 ASR → LLM → 流式 TTS 播放，支持中途打断（新语音可打断正在播放的 TTS）
- ⏱️ **TurnSense 语义端点检测**：不靠"静音 1 秒"这种硬阈值，用本地 ONNX 模型判断"这句话到底说完了没"，长句、停顿、思考都能正确处理
- 🖥️ **桌面音频作为补充输入**：可以捕获系统声音一起送入 ASR，让它知道你正在看/听什么
- 👁️ **屏幕理解（多模态）**：按间隔截屏交给多模态模型理解——本地 VLM（任意 OpenAI 兼容端点）或云端 MLLM 都能接；VLM 支持「关键词触发一次」与「常态轮询」两种模式，多模态模型不可用时降级为 OCR + 图像描述。看视频、玩游戏时它知道屏幕里正在发生什么
- 🧠 **自研分层长期记忆**：见下方[记忆系统](#记忆系统自研)一节
- 🧩 **通用工具运行时**：模型可自主调用工具；同一轮内支持多轮工具调用，工具执行期间的用户输入会被缓冲合并，不丢消息
- 🔎 **联网工具**：通用搜索 / 新闻（Core 的 `web_search` 服务，底层 **SearXNG**）＋ 天气 / Steam / GitHub / 汇率（直接打各自官方 API，**不需要 SearXNG**）
- 📖 **网页研究（Bot 侧）**：给主题或 URL 抓正文、按段读取、筛选后存进沙箱资料库（HTTP 静态读取，或接到 Playwright MCP 做渲染）；搜索发现复用上面的 Core 搜索接口
- 📚 **Skills 框架**：兼容 `SKILL.md` 格式，支持 `/skill-name` 显式调用，也允许模型自行创建 Skill
- 🔌 **MCP 客户端**：通过 JSON 配置接入可信服务，支持工具发现/调用、资源列表与读取、分页与 JSON/SSE 响应（暂不支持 stdio）
- 🗂️ **沙箱文件操作**：限定目录内的列目录 / 读文本 / 建文件 / 建子目录，拒绝目录穿越与符号链接
- 🐱 **桌宠界面**：置顶、拖拽、托盘、对话气泡（字幕）、按情绪切换表情、退出时记住位置
- 💬 **纯文本交互窗**：不接麦克风也能用，用来快速试 prompt
- 🎚️ **自适应语速**：被打断多或输出长时自动加快，空闲后缓慢回落
- 📊 **Harness 观测体系**：每次调用带 `call_id`、耗时、轮数、token 用量（含缓存命中/未命中）结构化落盘，附带成本看板工具
- 🔐 **出站网络防护**：网页读取 / MCP 走同一套地址校验，逐跳重校验 + 地址 pinning + 拒绝内网与保留地址

---

## 记忆系统

让 Agent「记得住你」的部分是**完全自研**的（`Bot/memory/`），也是投入较多、想继续做深的一块：

> 为什么不直接使用现成记忆框架 / RAG？本项目作为个人桌面 Agent，长期记忆基本就是聊天记录。首先是数据量级小，不需要为了检索精度做过多的索引优化；其次如果简单地把对话历史全量记忆，会引入大量无效数据（大多是闲聊或临时话题的细节），"全量记忆 + 建索引"的逻辑在这种场景下并不好用。
> 所以基于本项目的具体场景，我设计了这套记忆系统：原始数据直接筛选压缩，提高数据池质量；做日历式分层记忆——核心记忆置顶，其余记忆按新-旧排序，久远记忆模拟遗忘、淡化细节；后续计划加入一定的向量检索因素，久远记忆不再全量注入 prompt，进一步提升记忆框架的可用性与质量；合并失败时不写库，靠"合并前先备份上一版核心记忆 + 失败即中止"避免压缩模型的偶然崩溃导致记忆丢失。

| 阶段 | 做什么 | 关键配置 |
|---|---|---|
| 运行时历史 | 保留最近若干条消息作为短期上下文 | `memory.max_history` |
| 定期快照 | 每 N 轮把新对话抽成一份简短快照（可异步、不阻塞对话），提取失败时保留原文并重试 | `memory.save_interval_rounds` / `async_save` |
| 合并压缩 | 按批次把快照合并、去重成 `core_memory` 长期档案，覆盖前把旧档案备份进 `archive/` | `memory.merge_batch_size` |
| 历史压缩 | 用"已经总结好的记忆"替换掉若干保存周期之前的历史片段，抑制长对话的 token 膨胀 | `memory.compact_history_after_save` |

几个设计上的取舍：

- **记忆可以单独用另一个模型**：`memory.memory_model` 能指定独立的（更便宜的、更强的，甚至多模态的）模型专门做提取与合并，不影响主对话模型。
- **合并是全或无**：任一批合并失败就整体中止 —— 不写 `core_memory`、不归档任何快照，避免半成品污染长期记忆。
- **和前后台并发共存**：历史被后台压缩时不会覆盖前台的历史（读-改-写有锁保护，压缩游标不会走进注入块）。
- **还没有做**：向量检索 / RAG —— 目前 `core_memory` 是**全量注入**，体量大时会吃掉上下文预算。检索方案已经设计过，**排在 todo 列表前列**，是下一步的主要方向（`记忆检索（向量召回）`）。

> 想直接看实现：`Bot/memory/manager.py`（会话快照、压缩游标、批量合并）与 `Bot/memory/config.py`（上面那些配置项）。

**离线合并脚本**：`Bot/merge_memory.py` 是一个**独立脚本**，它不参与主进程，自己读 `Bot/config.yaml` 后把待合并的快照整理进 `core_memory` 就退出。适合给长时间没开机的记忆积压做一次性归并，也方便挂到系统计划任务上定时跑；`memory.enable: false` 时它会直接退出。用法见[开发 → 记忆合并](#记忆合并独立脚本)。

---

## 环境兼容性（装之前先看）

> 这一节是整个部署过程中最容易踩坑的地方。**本项目目前只在 Windows 上开发与验证**，
> 而 Windows 环境差异极大 —— 下面把「什么能跑、什么不能、为什么」摊开讲。

### 最重要的三条

1. **依赖要分环境装，推荐用 conda**。各组件之间依赖互相冲突（`torch` 版本、`numpy` 大版本都不一致），**没有一个环境能装下全部**——**最少要 3 个**：Bot / ASR / TTS（LLM · MLLM · 记忆 · Web Search 那套全是纯 Python 包，可以并进其中任意一个，见 [2.5](#25-按功能选-python-版本一张表就够)）。本文档一律用 conda 写：这也是 `Core/agent-stack/registry.json` 默认的激活方式（`runner: conda`）；启动器同样支持 `runner: process`（直接用 PATH 里的解释器），所以 conda 不是硬性要求，只是最省事。各环境的 Python 版本与端口见 [3.0 环境总览](#30-环境总览)。
2. **Bot 侧不需要 GPU，也不要装 `torch`**。Bot 全程通过 HTTP 调用 Core 的模型服务，是纯 CPU 客户端。这条能帮你省掉一半的安装麻烦。
   （唯一例外：TurnSense 的 `transformers` 可选后端会 import `torch`，但默认走 `onnx` 后端，不触发；`onnxruntime` 已在 `Bot/requirements.txt` 里。）
3. **模型服务默认跑在本机 `127.0.0.1`，但没有强制要求**：把 Core 部署到另一台机器（甚至有显卡的服务器），改 `config.yaml` 里的 `predict_url` 即可。低配笔记本也能只跑 Bot + 云端 API。

### 2.1 本机实测环境（作者机器，作为基准）

| 项 | 值 |
|---|---|
| 操作系统 | Windows 11 |
| GPU | NVIDIA GeForce RTX 5060 Laptop（**Blackwell / sm_120**，8 GB 显存） |
| 显卡驱动 | 595.97（支持 CUDA 13.2） |
| Python | Bot 侧 3.11.15；各 Core 环境分别 3.10 ~ 3.12（**4 个环境 4 个版本，当前实测值**） |

> 📌 **要不要把 Python 版本统一？** 结论是**没必要强求**——每个功能的依赖都锁在各自的版本区间里，
> 只有 `llm_deepseek_online` 那个环境可以并掉。
> **按功能选版本直接看 [2.5](#25-按功能选-python-版本一张表就够)**；
> 逐条代码级核对证据（轮子清单 / 源码行号 / PyPI 链接）由作者在本机逐项核验，此处只保留结论。

### 2.2 GPU 兼容范围（重点）

本项目的 **ASR 与 TTS 都跑在 GPU 上**，两边各自锁定 `torch` 版本，**且都用 CUDA 12.8 的预编译包**：

| 环境 | torch | 为什么是这一版 |
|---|---|---|
| `qwen3-asr` | **2.8.0+cu128** | RTX 50 系（Blackwell / sm_120）**需要 cu128 轮子**才有内核；torch 2.8 是包含 sm_120 支持的分水岭 |
| `GPTSoVits` | **2.7.1+cu128** | GPT-SoVITS fork 的依赖要求，同样使用 cu128 运行时 |
| `MyAI`（Bot） | 不使用 GPU | Bot 不发本地推理请求 |

由此推出的兼容结论：

| 你的显卡 | 能否直接跑 | 说明 |
|---|---|---|
| **RTX 50 系**（5060/5070/5080/5090） | ✅ 可，但**必须用 cu128 轮子** | 这是作者机器的情况。cu118/cu126 轮子**没有 sm_120 内核**，装了会报 `no kernel image is available` |
| **RTX 40/30/20 系、GTX 16 系** | ✅ 可 | 这些架构在 cu128 轮子里同样有对应内核；用同一套 cu128 依赖即可 |
| **AMD / Intel 显卡** | ❌ 不行 | 项目依赖 CUDA 版 `torch`、`sherpa-onnx`、`flash-attn` |
| **Apple Silicon / macOS** | ❌ 不行 | 项目依赖 CUDA 版 `torch`、`sherpa-onnx`、`flash-attn`；另外 Bot 的麦克风 / 桌面音频采集与桌宠界面也只在 Windows 上验证过 |
| **无独显的机器** | ✅ 可以，但要走"云端 API"路线 | 见 [模型组合](#模型组合怎么搭)：ASR 用百度云、LLM/MLLM 用在线 API、TTS 用百度云；Core 侧不需要本地模型 |

**显卡驱动的下限**：CUDA 12.8 运行时要求驱动 **≥ 570**（Windows）。用 `nvidia-smi` 看右上角 `CUDA Version`，低于 12.8 就先升驱动，否则连 CUDA 初始化都会失败。

**显存参考**：作者机器 8 GB 显存同时跑 ASR（sherpa-onnx int8）+ TTS（GPT-SoVITS）+ MLLM，占用约 4.9 GB。全部本地模型时建议 **≥ 8 GB 显存**；只想跑 ASR+TTS 的话 6 GB 也比较紧张但可试。

### 2.3 `flash-attn` 是硬依赖，且必须与 torch 精确对齐

`qwen3-asr` 环境的 Qwen3-ASR 模型要求 `flash-attn`，而 **Windows 上 flash-attn 基本不可能用 pip 从源码编出来**（需要完整的 CUDA 工具链，编译动辄一小时且极易失败）。

本项目用的是**手工/预编译的 Windows 轮子**，并且它对宿主环境有严格咬合关系：

```
flash_attn 2.8.3 + cu128 + torch 2.8.0 + cxx11abiFALSE + cp312 (Python 3.12)
```

> ⚠️ **只要 Python 版本、torch 版本或 C++ ABI 任一项对不上，就会 `import flash_attn` 失败**，
> 而且报错往往是底层符号缺失、很难读懂。

作者本机用的就是这个轮子：`flash_attn-2.8.3+cu128torch2.8.0cxx11abiFALSE-cp312-cp312-win_amd64.whl`（约 122 MB）。
**它不随仓库发布**（`.gitignore` 排除 `*.whl`），需要你自己准备一份同版本的轮子，装的时候 `pip install <你放它的路径>\flash_attn-2.8.3+cu128torch2.8.0cxx11abiFALSE-cp312-cp312-win_amd64.whl`。

- 想省事：把 `qwen3-asr` 环境固定为 **Python 3.12 + torch 2.8.0+cu128**，再装对应轮子
- 不想碰 flash-attn：**改用 sherpa-onnx ASR 或百度云 ASR**，这两条路都不需要 flash-attn（推荐新手走这条）
- 非 Windows 用户：可尝试官方源码编译，本项目未验证

### 2.4 可复现性现状（诚实说明）

| 组件 | 锁定程度 | 说明 |
|---|---|---|
| Bot 依赖 | ✅ 已锁版本 | 按实际环境逐项写死，拆成 **必需 / 界面 / 可选** 三份（`Bot/requirements.txt`、`-ui`、`-optional`） |
| Core conda 包 | ✅ 已固化 | `环境快照/environment-*.yml`，由仓库根目录的 `generate_env_snapshots.py` 导出（快照文件在 `环境快照/`） |
| Core pip 包 | ⚠️ **未完全锁定** | `Core/requirements-<环境名>.txt` 是**按 conda 环境拆开**的清单，不是 lock 文件。每个文件分两段：**第 1 段**是 PyPI 上能直接装的（`pip install -r` 装的就是这批），**第 2 段**是 PyPI 上没有轮子的（CUDA 版 torch / sherpa-onnx、本地编译的 `flash-attn`），那些行是注释，必须按注释里的命令单独装 |
| 模型权重 | ❌ 不随仓库发布 | ASR / TTS / TURN 等权重需自行下载，见第三章 |

**所以最省事的复现方式是照抄版本号，而不是让 pip 自由解析。** 下面第三章给的就是具体版本。

> 📌 维护者的建议：以后修好环境后，用仓库根目录的 `generate_env_snapshots.py` 重新导出；
> ⚠️ 不要用 `conda env export -n <名字>` —— 在某些 conda 版本上它会**静默导出 base 环境**（输出看着完全正常，极具迷惑性）。

### 2.5 按功能选 Python 版本（一张表就够）

> 依赖版本都写在 `Bot/requirements*.txt`、`Core/requirements-<环境名>.txt`（+ 各实现目录下的 `requirements.txt`）、`环境快照/*.yml` 里了，
> 这里只回答一个问题：**这套依赖能在哪个 Python 上装出来**。

| 功能 | 当前环境 | 这套依赖能用的 Python | 卡在哪 |
|---|---|---|---|
| **Bot**（桌宠客户端） | `MyAI` 3.11.15 | **3.11 ~ 3.12** | 下限：`onnxruntime 1.27` / `scipy 1.17` 要求 ≥3.11；上限：`numpy 1.26.4` 没有 3.13 轮子 |
| **ASR**（Core） | `qwen3-asr` 3.12.13 | **3.11 ~ 3.12** | 下限：`numpy 2.4.6` / `onnxruntime-gpu 1.27` / `scipy` 要求 ≥3.11 |
| **LLM / MLLM / 记忆 / WebSearch**（Core，全云 API） | `llm_deepseek_online` 3.11.0 | **3.10 ~ 3.13** | 几乎无限制——全是纯 Python 包（openai / dashscope / flask / requests） |
| **TTS**（Core） | `GPTSoVits` 3.10.20 | **3.9 ~ 3.10** | `numba 0.56.4` 最高只到 3.10，`numpy 1.23.4` 也只到 3.11 —— 所以 3.10 是这套锁定组合的上限 |

**⚠️ 四个特殊情况**（不是"版本选低一点"能绕开的，是"要么本地编译、要么换个功能"）：

| # | 情况 | 影响 |
|---|---|---|
| 1 | **要用 Qwen3-ASR，就必须 Python 3.12** | 它依赖 `flash-attn`，而 Windows 上 flash-attn 基本编不出来，现成的只有 `cp312` 那个轮子（见 2.3）。→ **只用 sherpa-onnx / 百度云 ASR 的话，ASR 那格 3.11 也行** |
| 2 | `webrtcvad`（Bot 的语音端点检测）**在任何 Python 上都没有现成轮子** | 得本地编译一次。→ Bot 的 VAD 可以在配置里关掉，关了就不需要它 |
| 3 | 跑 **GPT-SoVITS（TTS）要本地编译 `jieba_fast` 和 `pyopenjtalk`** | 这两个包 PyPI 上只有源码包，没有 Windows 轮子 |
| 4 | **本地大模型（vLLM）在 Windows 上跑不了** | 所以 LLM 那套默认是纯云 API；想本地跑只能用 `ollama` |

**作者的参考方案**（本机实测，可直接照抄）：

| 环境 | Python | 用途 | 为什么是这个版本 |
|---|---|---|---|
| `MyAI` | **3.11** | Bot | 不装 torch；3.11 / 3.12 都行 |
| `qwen3-asr` | **3.12** | ASR | flash-attn 轮子只有 cp312，**3.12 是硬要求** |
| `llm_deepseek_online` | **3.11** | LLM / MLLM / 记忆 / WebSearch | 纯云 API，其实随便什么版本 |
| `GPTSoVits` | **3.10** | TTS | 被 GPT-SoVITS 自己的依赖锁死 |

**想少装几个环境？** 只有 `llm_deepseek_online` 是可以直接并掉的——它只需要 openai / dashscope / flask 这类纯 Python 包，装进 `qwen3-asr` 就能跑同一批服务（只改 `Core/agent-stack/registry.json` 里的环境名，不用改代码）。
Bot / ASR / TTS 三个不建议合并：`torch` + `numpy` 版本互相冲突，就是上面说的那个"依赖地狱"。

---

## 部署教程

### 3.0 环境总览

| conda 环境 | 用途 | Python | 端口 | 需要 GPU |
|---|---|---|---|---|
| `MyAI` | Bot 主程序 | 3.11 | —— | ❌ |
| `qwen3-asr` | ASR 服务 | 3.12 | 11001 | ✅ |
| `llm_deepseek_online` | LLM / MLLM / llm-memory / web_search | 3.11 | 11000 / 10010 / 11012 | ❌（走在线 API） |
| `GPTSoVits` | TTS 服务（工作目录 `Core/GPT-SoVITS`） | 3.10 | 11002 | ✅ |

> 上表是**当前实测的混合版本**，不是「必须统一」的目标值：4 个环境分别用不同 Python 是刻意的（`torch` / `numpy` / `funasr` 互相冲突）。
> 想换 Python 版本前先看 **[2.5 按功能选 Python 版本](#25-按功能选-python-版本一张表就够)**——**别在本章的安装步骤里随手改**。
>
> 上面 4 个环境就是全部默认服务。`img_cap` / `ocr` 是**可选服务**（`registry.json` 里没登记），依赖见 `Core/requirements-optional.txt`。
>
> 📁 **依赖清单怎么组织的**：统一按 **「公共部分 + 每套实现一份」** ——
> 每个环境一份公共清单，各套实现（ASR 三条路线、LLM 四个服务、TTS 两种跑法…）
> 的依赖就放在它自己的目录下。**pip 不会替你选**，你选哪套就多装那一份。
> 完整对照表在 [`Core/requirements.txt`](Core/requirements.txt) 头部；Bot 端同理（必需 / 界面 / 可选三份）。

前置要求：**Windows 10/11**（Bot 与桌宠界面按 Windows 验证；Core 可以单独部署到 Linux/WSL，用 `python agent_stack.py` 管服务）、**conda**（Miniconda/Anaconda 均可）、**Git**、以及一块 NVIDIA 显卡（或走云端 API 路线）。
> 另外 ASR 服务会调 `ffmpeg` 做音频转单声道（`Core/asr/app.py` → `Core/utils/audio_util.py`），请确保它在 PATH 里。

> 🐧 **Core 能跑在 Linux / WSL 上吗？—— 能。**
> `Core/starter.py` 只有 `argparse` + Flask 的 `app.run()`，ASR 调 `ffmpeg` 是普通子进程调用，Core 自己的代码里没有平台分支。
> 服务栈用**跨平台启动器** `Core/agent_stack.py`（只依赖标准库，根 README 3.7 与 `Core/agent-stack/README.md` 都写了）：
> `python agent_stack.py start|stop|restart|status|logs|validate`，读的是同一份 `registry.json`。
> Windows 专用的 `agent-stack.cmd` / `agent-stack.ps1` 仍然保留（标签页窗口那套体验只有 Windows 有）；
> 反过来，Windows 上的启动器**也能驱动 WSL 里的 systemd 单元**（`runner: "wsl-systemd"`）—— SearXNG 就是这么接进启动流程的，见 [3.5](#35-安装-llm--web-search-环境llm_deepseek_online)。

### 3.1 获取代码与共享协议包

```powershell
git clone <你的仓库地址> Zongzi-DesktopAgent
cd Zongzi-DesktopAgent

# 共享协议包 condata 不在 PyPI，必须从源码以可编辑模式安装
# （Bot 与 Core 的每个环境都要装一次，下面各步骤会重复这条命令）
pip install -e con-datapack-1.11
```

### 3.2 安装 Bot（`MyAI` 环境）

```powershell
conda create -n MyAI python=3.11
conda activate MyAI
pip install -r Bot/requirements.txt            # 公共（必需）
pip install -r Bot/requirements-ui.txt         # 可选：桌宠界面 PySide6，纯控制台模式可跳过
pip install -r Bot/requirements-optional.txt   # 可选：TurnSense 音频前端等（缺了会降级，不会崩）
pip install -e con-datapack-1.11
```

> Bot 是纯 CPU 客户端，**不要**在这里装 `torch`。界面依赖也可省，把 `service.desktop_shell.enable` 设成 `false` 即可纯控制台运行。
> 三份清单的分工：`requirements.txt` = 必需 / `requirements-ui.txt` = 界面 / `requirements-optional.txt` = 可选功能。

### 3.3 安装 ASR 服务（`qwen3-asr` 环境）

**先选路线，再装依赖** —— ASR 有三条路线，**pip 不会替你选**：依赖清单按路线拆开了，你选哪条就多装那一份（对照表在 `Core/requirements-qwen3-asr.txt` 头部）。

**路线 A — sherpa-onnx（推荐，不需要 flash-attn、也不需要 torch）**

```powershell
conda create -n qwen3-asr python=3.12
conda activate qwen3-asr
# CUDA 版 sherpa-onnx 的 Windows 轮子：PyPI 上只有 CPU 版，本仓库也不带（.gitignore 排除 *.whl）。
# 作者本机用的是 sherpa_onnx-1.13.3+cuda12.cudnn9-cp312-cp312-win_amd64.whl（约 100 MB），
# 需自行准备同版本轮子（预编译包或按官方文档自编译：https://k2-fsa.github.io/sherpa/onnx/install/windows/build-cuda.html）
pip install <你放轮子的路径>\sherpa_onnx-1.13.3+cuda12.cudnn9-cp312-cp312-win_amd64.whl
pip install -r Core/requirements-qwen3-asr.txt         # 公共部分
pip install -r Core/asr/sherpa_onnx/requirements.txt   # 路线 A 专属
pip install -e ../con-datapack-1.11
```

**路线 B — Qwen3-ASR-0.6B（效果更好，但需要 flash-attn，且必须 Python 3.12）**

```powershell
pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
pip install flash_attn-2.8.3+cu128torch2.8.0cxx11abiFALSE-cp312-cp312-win_amd64.whl
pip install -r Core/asr/Qwen3ASR06B/requirements.txt
```

轮子必须与 `torch==2.8.0+cu128` / Python 3.12 精确匹配（见 [2.3](#23-flash-attn-是硬依赖且必须与-torch-精确对齐)）。
**顺序别反**：`accelerate` / `bitsandbytes` 都依赖 torch，先装清单会把 PyPI 上的 CPU 版 torch 拉进来。
上面这条 `pip install flash_attn-…whl` 里的文件名是**举例**，仓库里没有这个文件（见 2.3），把它换成你自己那份轮子的路径。

**路线 C — Paraformer（funasr）**

```powershell
pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
pip install -r Core/asr/paraformer/requirements.txt
```

**ASR 模型权重**：放到 `Core/models/` 下（不入库）。
本机实测用的是 `sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30`，在 `Core/config.yaml` 的 `ASR.config` 里指路径。

### 3.4 安装 TTS 服务（`GPTSoVits` 环境）

```powershell
conda create -n GPTSoVits python=3.10
conda activate GPTSoVits
pip install torch==2.7.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128
pip install -r Core/requirements-gpt-sovits.txt   # 公共
pip install -r Core/GPT-SoVITS/requirements.txt   # tts 的实现①：GPT-SoVITS 自己（当前在用）
pip install -e ../con-datapack-1.11
# 想改用 Core 侧的转发服务（starter.py tts）就再装：
#   pip install -r Core/tts/gpt_sovits/requirements.txt
# ⚠️ jieba_fast / pyopenjtalk 没有 Windows 轮子，需要本地编译（见 2.5 特殊情况 3）
```

`Core/GPT-SoVITS/` 是 GPT-SoVITS 的本地副本（含本项目新增的 API 封装 `zongzi_api.py`），详见其 [`SOURCE.md`](Core/GPT-SoVITS/SOURCE.md)。**TTS 参考音频**放在 `Core/GPT-SoVITS/` 下的相关目录，并在 `Bot/config.yaml` 的 `character.speech` 里配置。

### 3.5 安装 LLM / Web Search 环境（`llm_deepseek_online`）

这个环境**不需要 GPU**，因为它默认只跑在线 API 客户端：

```powershell
conda create -n llm_deepseek_online python=3.11
conda activate llm_deepseek_online
pip install -r Core/requirements-llm-deepseek-online.txt   # 公共
pip install -r Core/llm/deepseek_online/requirements.txt   # llm / llm-memory
pip install -r Core/mllm/requirements.txt                  # mllm
pip install -r Core/web_search/requirements.txt            # web_search
pip install -e ../con-datapack-1.11
# 只想跑其中几个服务，就少装对应的那一份；三个都装的体积很小（都是纯 Python 包）
```

#### Web Search 到底依赖什么

Core 的 `web_search` 服务对外提供 6 个工具，**只有两个需要 SearXNG**：

| 工具 | 数据来源 | 需要 SearXNG |
|---|---|---|
| `search`（通用网页搜索） | SearXNG（`format=json`） | ✅ |
| `news`（新闻） | SearXNG 的 news 分类 | ✅ |
| `weather` | Open-Meteo 官方 API | ❌ |
| `steam` | Steam 官方 API | ❌ |
| `github` | GitHub 官方 API | ❌ |
| `exchange_rate` | Frankfurter v2 | ❌ |

也就是说：**不装 SearXNG 也能用天气 / Steam / GitHub / 汇率这 4 个工具**，只是 `search` 和 `news` 会失败。

**SearXNG 是一个独立进程/容器，不是 pip 包**，也不会随仓库发布。要点：

1. 仓库里带了一份可直接套用的配置：[`Core/web_search/searxng/settings.yml`](Core/web_search/searxng/settings.yml) —— **必须在 SearXNG 那边开启 JSON 输出**（否则 `format=json` 返回 403）。
2. Core 侧指向它的地址是 `Core/config.yaml` 的 `WebSearch.config.endpoint`（默认 `http://127.0.0.1:8080/search`）。SearXNG 可以在同一台机器、**本机 WSL**，或另一台 Linux 机器上 —— 不是 `127.0.0.1` 就改成实际地址。
3. 它跑在哪台机器上，就把 `WebSearch.config.engines` 调整成那台机器**实际能连上**的引擎（默认只留 `bing`，引擎越多长尾延迟越高）。
4. **不要**把 SearXNG 直接暴露到公网（除非你已经配好认证、限流和反向代理，见 `Core/web_search/README.md`）。

**如果 SearXNG 跑在本机 WSL 的 systemd 单元里**（作者的环境就是：conda 环境 `searxng` + systemd 单元 `searxng-local`，用 `sudo systemctl start|stop|restart searxng-local` 管理），`Core/agent-stack/registry.json` 里已经登记了一条**可选依赖**：

```json
"settings": {
  "optional_dependencies": [
    { "name": "searxng", "enabled": true, "runner": "wsl-systemd",
      "distro": "", "unit": "searxng-local", "sudo": true }
  ]
}
```

- `agent-stack.cmd start` 会**先尽力把它拉起来**（`wsl sudo -n systemctl start searxng-local`），**失败只打印一条警告，不会中止整个启动**，也不会触发回滚。
- `agent-stack.cmd stop` **不会**去停它（它不只服务于这个栈），`status` 里会单独列出来。
- 用 `sudo -n` 是为了**绝不卡在 sudo 密码提示上**；没有免密 sudo 时会直接跳过，你自己 `sudo systemctl start searxng-local` 即可。
- SearXNG 在**另一台机器**上、或你想手工管理时，把这条的 `enabled` 改成 `false`；`distro` 留空表示 WSL 默认发行版。

### 3.6 配置

**Core 侧**：复制 `Core/config.example.yaml` 为 `Core/config.yaml`，按需修改。关键是每个服务段的 `id` 字段 —— 它决定启哪个实现：

```yaml
ASR:
  id: "Sherpa-ONNX-Streaming-Zipformer-ZH-INT8"   # ← 换 ASR 就改这里
LLM:
  id: "deepseek_online/DeepSeek-v4-flash"          # ← 换 LLM 就改这里
TTS:
  id: "GPT-SoVITS"                                 # ← 换 TTS 就改这里
```

**Bot 侧**：首次运行 `python main.py` 会**自动生成 `Bot/config.yaml` 然后退出**（配置由代码里的 `BotConfig` 模型生成，每个字段自带注释）。编辑完再运行一次。

需要你填的主要是：

- `pipeline.llm` / `pipeline.mllm`：要连的服务地址；若用第三方在线模型，填 `api_key`
- `pipeline.asr` / `pipeline.tts`：本地服务地址，或换成百度云接口
- `character.chat.system_prompt`：角色设定（默认是"粽子"）
- `character.speech.prompts_dir`：TTS 参考音频目录，按情绪分文件
- `service.microphone.hotkey`：麦克风开关热键（默认 `f8`）
- `service.desktop_shell` / `service.chat_window`：界面开关与字号（`chat_window` 原名 `text_input_gui`，旧配置仍兼容）

> 💡 仓库另带一份脱敏样例 `Bot/config.example.yaml`（由作者的运行配置导出），可用于对照字段与默认值；**首次运行重新生成的那份才是权威**，两者不一致时以生成的为准。

### 3.7 启动

```powershell
# 1) 拉起 Core 的 5 个服务（asr / mllm / tts / llm-memory / web_search）
cd Core
agent-stack.cmd validate
agent-stack.cmd start

# 2) 启动 Bot
conda activate MyAI
cd ../Bot
python main.py
```

Core 服务栈的管理命令：`status` / `logs` / `logs -Service asr` / `restart` / `stop`，合并日志在 `Core/agent-stack/runtime/logs/combined.log`。

**不在 Windows 上跑？** 用同一份注册表的跨平台启动器（只依赖标准库，Windows / Linux / WSL / macOS 都能跑）：

```bash
cd Core
python agent_stack.py start            # start / stop / restart / status / logs / validate
python agent_stack.py logs --service asr
```

语义与 PowerShell 版一致（同样的 `start_order` 并行、健康检查、`state.json`、合并日志、失败回滚、SearXNG 尽力启动）。差异只在平台细节：Linux 上 conda 走 `conda run --no-capture-output`，服务在后台运行、输出进合并日志（没有"每个服务一个窗口"的概念），`--hidden` 仅对 Windows 有意义。详见 `Core/agent-stack/README.md`。

默认按一下 `f8` 开始说话、说完再按一下送进 ASR；也可以直接在界面里打字。

### 3.8 模型权重：从哪来、放哪去

**所有权重都不随仓库发布**（体积从几十 MB 到 1.8 GB，远超 GitHub 限制）。你需要自己下载并放到下表的位置 —— **路径必须一致，因为 `config.yaml` 里按相对路径引用**。

> 📌 下表的"大小"是作者机器上的实测值，供你估算下载量与磁盘占用。
> 🔵 = 可选（该功能不用就不装）　🟡 = 建议（不装会静默降级）　🔴 = 必需
>
> 🗂️ 表里的**权重文件本身不在仓库里**（`Core/models/`、`Core/asr/Qwen3ASR06B/model.safetensors`、`Core/GPT-SoVITS/GPT_SoVITS/pretrained_models/`、`Bot/checkpoints/`、`Bot/models/` 下的 onnx、`Resources/` 都被 `.gitignore` 排除）——
> 照表里的路径**新建目录**再把文件放进去即可。
> 少量体积很小的配套文件（文本前端字典、tokenizer、`Bot/models/bge-small-zh-v1.5-onnx/` 的 `tokenizer*.json` 等）随仓库附带，无需下载。

#### ASR（ASR 服务要用）

| 权重 | 大小 | 放到 | 从哪来 |
|---|---|---|---|
| 🟡 `sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/`（含 `encoder/decoder/joiner.onnx`、`tokens.txt`） | 736 MB | `Core/models/` | [sherpa-onnx 官方模型发布页](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models)（找 `streaming-zipformer-zh` 系列）。**作者当前用的就是这个** |
| 🔵 `sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30/`（同系列小模型） | 160 MB | `Core/models/` | 同上；小模型省显存、精度略低 |
| 🔵 `Core/asr/Qwen3ASR06B/model.safetensors`（tokenizer 已随仓库附带，只缺权重） | ~1.8 GB | `Core/asr/Qwen3ASR06B/` | [Qwen/Qwen3-ASR](https://huggingface.co/) 系列（HuggingFace / ModelScope 均可）。**选了它就必须有 flash-attn**，见 [2.3](#23-flash-attn-是硬依赖且必须与-torch-精确对齐) |
| 🔵 `iic/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1` | 数百 MB | `Core/`（按 `model_path` 解析） | 通过 `funasr` / ModelScope 自动下载 |

#### Bot 侧本地模型

| 权重 | 大小 | 放到 | 从哪来 |
|---|---|---|---|
| 🔴 `TurnSense/TurnSense1.1/`（`model_int8.onnx` + `am.mvn`） | 217 MB | `Bot/models/TurnSense/TurnSense1.1/` | [brgroup/TurnSense](https://huggingface.co/brgroup/TurnSense)（HuggingFace）或 [Bairong-Xdynamics/TurnSense](https://github.com/Bairong-Xdynamics/TurnSense)（GitHub）。**Apache-2.0，随附 README 与 LICENSE 请一并保留** |
| 🟡 `bge-small-zh-v1.5-onnx/`（`onnx/model_quantized.onnx` 等） | 23 MB | `Bot/models/bge-small-zh-v1.5-onnx/` | 本地意图路由（`service.intent_router`）。ONNX 版可从 [Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5) 取；缺了会退化为关键词匹配 |
| 🔵 `checkpoints/FRCRN_SE_16K/last_best_checkpoint.pt` | 154 MB | `Bot/checkpoints/FRCRN_SE_16K/` | FRCRN 语音降噪（`pipeline.asr.denoise`）。仅在开启降噪时需要，默认关闭 |

#### TTS（GPT-SoVITS）

| 权重 | 大小 | 放到 | 从哪来 |
|---|---|---|---|
| 🔴 `GPT_SoVITS/pretrained_models/` 全套（`s1*.ckpt`、`s2G*.pth`、`chinese-roberta-wwm-ext-large/`、`chinese-hubert-base/`、`v2Pro/` 等） | ~4.5 GB | `Core/GPT-SoVITS/GPT_SoVITS/pretrained_models/` | [GPT-SoVITS 官方整合包 / 模型发布](https://github.com/RVC-Boss/GPT-SoVITS)（建议直接取官方预训练模型包，逐个下载容易漏文件） |
| 🔴 `GPT_SoVITS/text/G2PWModel/`、`cmudict*.rep`、`ja_userdic/` 等 | ~30 MB | `Core/GPT-SoVITS/GPT_SoVITS/text/` | 同官方整合包；中文/日文文本前端要用 |
| 🔵 `tools/uvr5/uvr5_weights/`（人声分离） | ~420 MB | 同目录 | 同官方整合包。**只在训练/预处理参考音频时用，纯推理不需要**，可省 |
| 🔴 **你自己的参考音频**（3~10 秒干声） | 几 MB | `Bot/resources/prompts/tts/`，命名 `[语言][情绪]文本.wav` | 自己录。**这是"粽子的声音"，必须自己准备**；配置项 `character.speech.prompts_dir` |

> ⚠️ **参考音频的命名规则很关键**：`[zh][Default]你好呀.wav` 这种格式里，**中括号内的情绪名要和 `character.emotions` 白名单对得上**（默认 `Default / happy / sad / angry / shy / surprised`）。
> 没有对应情绪的音频时会回落到 `Default`，所以至少要有 `[zh][Default]` 一条。

#### 放置位置速查（config.yaml 里的引用）

```yaml
# Core/config.yaml
ASR:
  config:
    Sherpa-ONNX-Streaming-Zipformer-ZH-INT8:
      model_dir: "models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30"   # 相对 Core/
    Qwen3-ASR-0.6B:
      model_path: "./asr/Qwen3ASR06B"                                                # 相对 Core/

# Bot/config.yaml
pipeline:
  asr:
    turnsense:
      model_path: 'models/TurnSense/TurnSense1.1'    # 相对 Bot/
  tts:
    # 参考音频目录
character:
  speech:
    prompts_dir: 'resources/prompts/tts'             # 相对 Bot/
```

> 💡 这些路径都在 `.gitignore` 里（`Core/models/`、`/Bot/models/*`、`/Bot/checkpoints/`、`/Resources/`、`*.whl` 等），所以**你怎么放都不会误提交**。
> 但要注意：`Bot/models/bge-small-zh-v1.5-onnx/` 下**随仓库走的是 tokenizer 文件**（`tokenizer.json`、`tokenizer_config.json`），只有 `onnx/` 权重被忽略。

### 3.9 装不上时先查这几条

| 症状 | 原因与处理 |
|---|---|
| `CUDA error: no kernel image is available` | torch 轮子与显卡架构不匹配。RTX 50 系必须用 `cu128` 轮子（见 2.2） |
| `import flash_attn` 报符号错误 | Python / torch / ABI 三方没对齐。要么严格照 2.3 的版本组合，要么改用 sherpa-onnx / 百度云 ASR |
| 某个服务起不来、端口没监听 | `agent-stack.cmd logs -Service <名字>`（跨平台：`python agent_stack.py logs --service <名字>`）看该服务日志；启动器超时默认 300 秒（大模型加载可能更久，改 `registry.json` 的 `startup_timeout_seconds`） |
| `import condata` 失败 | 忘记 `pip install -e con-datapack-1.11`，或装到了别的 conda 环境里 |
| 麦克风没反应 | 检查 `service.microphone.enable` 与热键；`pyaudio` 在部分机器需要先装对应轮子 |
| TTS 与桌面音频互相自激 | 同时开启桌面音频捕获与 TTS 时，需要虚拟声卡（如 Voicemeeter）把两者分开 |
| 日志出现 `using heuristic fallback` | `onnxruntime` 没装上，TurnSense 退化成规则判定。装 `onnxruntime`（已在 `Bot/requirements.txt`） |
| TTS 报找不到参考音频 | 检查 `Bot/resources/prompts/tts/` 的命名（`[zh][Default]….wav`）与 `character.emotions` 白名单是否对得上 |
| 模型加载超时 | 首次加载大模型可能超过启动器的 300 秒默认超时，改 `Core/agent-stack/registry.json` 的 `startup_timeout_seconds` |

---

## 模型组合怎么搭

这是本项目的核心设计目标，也是**目前最需要改进的地方** —— 下面把现状与边界讲清楚。

### 4.1 组件矩阵

| 组件 | 本地模型（Core 内实现） | 云端 / 外部服务 |
|---|---|---|
| **ASR** | `iic/speech_paraformer…`（funasr）、`Qwen3-ASR-0.6B`、`Sherpa-ONNX-Streaming-Zipformer-ZH-INT8` | **百度云 ASR**（`BaiduASR`，需 api/secret key） |
| **LLM** | `THUDM/chatglm3-6b`、`THUDM/GLM-4`、`Qwen/Qwen-7B-Chat`、`augmxnt/shisa-7b-v1`、`01-ai/Yi-6B-Chat`、`deepseek-ai/DeepSeek-R1-Distill-*`、**Ollama**（本地任意模型） | **DeepSeek v4**、**Kimi**、**豆包**；以及**任意 OpenAI 兼容端点**（改 `api_url` + `model_name` 即可） |
| **MLLM**（多模态） | —— | 任意 OpenAI 兼容多模态端点（`model_id` 是**自由字符串**，改 config 就能换） |
| **TTS** | `GPT-SoVITS` | **百度云 TTS** |
| **OCR / ImgCap** | `paddlepaddle/PaddleOCR`、`Salesforce/blip-image-captioning-large` | —— |

**兼容性最好的三条路**（推荐组合）：

1. **全云端**：百度云 ASR + DeepSeek/Kimi 在线 LLM + 百度云 TTS → **不需要显卡**，装完就能跑
2. **本地 ASR/TTS + 云端 LLM**：sherpa-onnx + DeepSeek 在线 + GPT-SoVITS → 作者的实际用法，延迟与质量平衡最好
3. **全本地**：sherpa-onnx + Ollama + GPT-SoVITS → 完全离线，代价是显存与质量

### 4.2 ⚠️ 当前的扩展性边界（诚实说明）

架构上 Bot↔Core 是 HTTP 解耦的，**加一个模型服务不需要动 Bot 代码**。但有几处仍在收紧的地方，想贡献模型的话请注意：

| 位置 | 现状 | 影响 |
|---|---|---|
| `Bot/pipeline/*/config.py` 的 `model_id` 字段 | **已全部放宽为自由字符串**（LLM / ASR / TTS / OCR / ImgCap / VLM / MLLM 都接受任意值） | 填任何模型标识都不会再被启动校验拒绝；常见取值写在字段说明里只作提示 |
| `Core/starter.py` 的 `asr_app()` / `llm_app()` 等 | 用 `if/elif` 链把模型 id 映射到实现类 | **在 Core 侧加本地模型要改这里**（但纯 API 类模型不用，`deepseek_online` 与 `mllm` 都已是"配置驱动"） |
| Bot 与 Core 的模型 id 命名体系 | 两套不完全一致（Bot 里可能写 `deepseek-chat`，Core 要的是 `deepseek_online/DeepSeek-v4-flash` 等） | 配置时要**以 Core `config.yaml` 的 `id` 为准**，Bot 的 `model_id` 在多数组件里只用于少量分支判断 |

> 结论：**模型标识这一层已经完全放开** —— 任意 OpenAI 兼容模型可以直接填 `LLM` 的 `model_id`
> 配合 `openai_format: true` 使用；ASR / TTS / OCR / ImgCap / VLM 填任何字符串也不会再被校验拦下。
> 唯一需要在代码里登记的环节变成 **Core 侧 `starter.py` 的模型映射**（决定"本地要加载哪个实现"）。

### 4.3 换模型的正确姿势

**换 LLM / MLLM（最简单，只改 Core）**：

```yaml
# Core/config.yaml
LLM:
  id: "deepseek_online/DeepSeek-v4-flash"
  config:
    deepseek_online/DeepSeek-v4-flash:
      api_url: "https://api.deepseek.com"   # ← 换成任意 OpenAI 兼容端点
      model_name: "deepseek-v4-pro"          # ← 换成该端点的模型名
```

**让 Bot 直接连在线模型（不经 Core）**：把 `Bot/config.yaml` 的 `pipeline.llm` 设为

```yaml
pipeline:
  llm:
    openai_format: true                        # 走 OpenAI 兼容协议
    predict_url: "https://api.deepseek.com"    # 这里填 base_url，不是 /llm/predict
    api_key: "sk-..."                          # 必填（本地 Ollama 随便填个非空值）
    model_id: "任何该端点支持的模型名"           # 不再有白名单限制
    temperature: null                          # 留空＝用服务端默认值
```

**换 ASR / TTS 到云端**：在 Bot 侧改 `pipeline.asr.model_id` 为 `BaiduASR`（填 api/secret key），TTS 同理改 `BaiduTTS`，此时**不需要启动对应的 Core 服务**。

**加一个全新的本地模型**：需要（a）在 Core 写 model 实现并挂到 `starter.py` 的映射里；（b）Bot 侧**直接填该模型标识**即可，不再需要改枚举。参照 `Bot/docs/` 下的开发指南。

---

## 项目结构

```
.
├─ Bot/                     机器人本体（纯 CPU 客户端）
│  ├─ main.py               入口：按配置选择桌宠界面或控制台模式
│  ├─ bot.py                对话编排主循环
│  ├─ config.py             BotConfig：配置模型（首次运行据此生成 config.yaml）
│  ├─ merge_memory.py       离线记忆合并脚本
│  ├─ manager/              配置 / 上下文（稳定 system 前缀）/ 提示词
│  ├─ memory/               记忆快照、合并压缩、core_memory
│  ├─ pipeline/             asr llm mllm tts ocr imgcap vlm 各管线（HTTP 客户端）
│  ├─ services/             tool_runtime · tool_coordinator · skills · mcp
│  │                        sandbox · browser · web_search · net_safety
│  │                        intent_router · microphone
│  ├─ devices/              麦克风、扬声器、键盘热键、屏幕捕获
│  ├─ ui/                   桌宠壳与文字聊天窗（PySide6）
│  ├─ event/  framework/    事件骨架与运行时上下文
│  ├─ skills/               技能目录（SKILL.md，兼容 DeepSeek Harness 格式）
│  ├─ MCP/                  MCP 服务配置（servers.example.json）与只读资料目录
│  ├─ docs/                 开发指南与 harness 日志格式说明
│  ├─ tools/                harness_report.py（调用观测看板）
│  │                        scan_api_surface.py（第三方 API 面静态扫描）
│  ├─ tests/                离线回归测试（自建临时目录，不依赖 Core 在线）
│  ├─ resources/prompts/    TTS 参考音频目录（音频需自己录，见 3.8）
│  ├─ models/               本地模型（仅意图路由 tokenizer 入库，其余自备）
│  ├─ Sandbox/              沙箱工作区（运行期创建，不入库）
│  └─ config.example.yaml   脱敏样例；config.yaml 首次运行自动生成，不入库
├─ Core/                    模型服务层
│  ├─ starter.py            单个服务的启动入口（模型 id → 实现 的映射）
│  ├─ agent-stack.cmd/.ps1  多服务启动器（Windows，注册表驱动）
│  ├─ agent_stack.py        多服务启动器（跨平台，读同一份注册表）
│  ├─ agent-stack/          registry.json 与运行时目录
│  ├─ config.example.yaml   样例配置（复制成 config.yaml 再改）
│  ├─ asr/ llm/ mllm/ tts/ ocr/ img_cap/ web_search/   各模型服务实现
│  ├─ models/               本地 ASR 权重（不入库，需自行新建）
│  ├─ tests/                离线测试资源（图片 / 音频样例，仅本地留存）
│  └─ GPT-SoVITS/           TTS 模型副本 + API 封装 zongzi_api.py（见其 SOURCE.md）
├─ con-datapack-1.11/       共享数据协议包（导入名 condata，须 pip install -e）
├─ generate_env_snapshots.py conda 环境快照导出脚本
├─ Resources/               本地模型与参考音频（不入库）
└─ 环境快照/                conda 环境依赖快照（environment-*.yml）
```

---

## 开发

### 测试

纯离线用例，不需要 Core 服务在线，也不需要 GPU：

```powershell
conda activate MyAI
python -m pytest Bot/tests -q            # 全量用例：当前收集 144 个（数字随开发变动）

# 只跑核心套件（更快）：p0 冒烟 + 全部 p1，当前收集 135 个
python -m pytest Bot/tests/test_p0_smoke.py Bot/tests/test_p1_security.py `
                 Bot/tests/test_p1_voice.py Bot/tests/test_p1_desktop.py `
                 Bot/tests/test_p1_harness.py Bot/tests/test_p1_prompt.py `
                 Bot/tests/test_p1_pseudo_markers.py Bot/tests/test_p1_llm_openai_compat.py `
                 Bot/tests/test_p1_model_id_freedom.py -q
```

`Bot/tests/pipeline/` 下的脚本需要真实的 ASR / LLM / TTS 服务在线，属人工调试脚本，默认不收集（`Bot/tests/pytest.ini` 的 `norecursedirs`）。

> 测试数量随开发持续变动，上面写的是某一时刻的**收集**数量；判断标准是**"没有 failed / error"**，而不是数字本身。
> 想随时数一遍：`python -m pytest Bot/tests --collect-only -q`。

### 记忆合并（独立脚本）

`Bot/merge_memory.py` 与主程序**互相独立**：它自己加载 `Bot/config.yaml`、把待合并的历史快照归并进 `core_memory`（覆盖前同样会先把旧档案备份到 `archive/`），做完即退出，不会启动对话链路，也不需要 Core 的服务在线（记忆模型走 `memory.memory_model` 指向的 LLM / MLLM 接口）。

```powershell
conda activate MyAI
cd Bot
python merge_memory.py
```

合并是全或无：任一批失败就整体中止、不写库（日志会说明是哪一批）。想定时跑（例如每天凌晨）就把它挂到系统计划任务 / `crontab`，命令同上；`memory.enable: false` 时脚本直接退出，不做任何事。

### 调用观测看板

```powershell
python Bot/tools/harness_report.py --out -      # 纯读日志，生成 Markdown 报告
```

报告包含每次调用的耗时分布、token 用量与**提示缓存命中率**。`PRICING` 目前为空，填入单价后才会输出成本估算。

### 依赖 API 面扫描（升级 Python / 依赖前用）

```powershell
python Bot/tools/scan_api_surface.py            # 打印各第三方包实际用到的 API
python Bot/tools/scan_api_surface.py --out api_surface.md
```

用 AST 抽出代码里真正调用的第三方 API（`from X import a` 与 `mod.attr.attr` 链），
以及静态分析看不见的动态 import。**判断"能不能升级 Python / 依赖版本"时用它，而不是只看依赖元数据**
—— 老包的 `Requires-Python` 往往写得极松，真正决定兼容性的是代码调用了哪些 API。

### 扩展指南

- 新增工具 → `Bot/docs/TOOLS_CREATION_GUIDE.md`
- 新增 Skill → `Bot/docs/SKILLS_CREATION_GUIDE.md`
- 日志字段（Harness）→ `Bot/docs/HARNESS_LOGGING.md`
- Core 服务注册 → `Core/agent-stack/README.md`

### 四条别破坏的设计

1. **system prompt 前缀恒定**：提示缓存是**前缀字节匹配**，system 里换一个字整条缓存作废。时间、环境状态、工具轮数提示、工具结果一律进**用户轮**（`Bot/manager/context_manager.py`）。
2. **不估算 token**：只用服务商返回的真实用量，缺失就留空（`condata.harness.usage_dict`）。
3. **外部内容先中和**：网页正文、MCP 返回值、文件内容进上下文前先中和工具调用标记形状的文本。
4. **出站先校验地址**：拒绝内网 / 保留地址并固定已验证 IP（`Bot/services/net_safety.py`）。

---

## 已知限制

- **仅在 Windows 上验证过**；Core 的 Python 服务与 `Core/agent_stack.py` 启动器本身是跨平台的，但 Windows 专用的 `agent-stack.cmd` / `agent-stack.ps1` 依赖 `cmd.exe` / PowerShell / Windows Terminal，Linux 上用 Python 启动器或自己写 systemd
- **SearXNG 得自己部署**（既不是 pip 包，也不随仓库发布）：不装的话 `search` / `news` 两个联网工具不可用，天气 / Steam / GitHub / 汇率照常工作（见 3.5）
- **依赖必须分环境装**（最少 3 个：Bot / ASR / TTS，互相冲突不能合并；GPU 依赖需按第三章的具体版本手工对齐）。默认流程用 conda，启动器也支持非 conda 的 `process` runner
- **`flash-attn` 在 Windows 上难以自行编译**，Qwen3-ASR 路线对版本咬合很严（见 2.3）
- **加新本地模型需要在 Core 侧登记**（`starter.py` 的映射），尚未做到"纯配置扩展"（见 4.2）；Bot 侧的 `model_id` 已无枚举限制
- 记忆检索（向量召回）尚未实现（**排在 todo 前列**），长期记忆目前全量注入，体量大时会吃掉上下文预算（见[记忆系统](#记忆系统自研)）
- MCP 暂不支持 stdio 传输
- 桌面音频捕获与 TTS 同时开启时，需要虚拟声卡（如 Voicemeeter）避免自激循环
- 部分 conda 版本上 `conda env list` 会内部报错并返回空列表，进而让 `conda env export -n <名字>` **静默导出 base**——请改用仓库根目录的 `generate_env_snapshots.py`

---

## License 与来源

本项目以 **MIT License** 发布，见 [`LICENSE`](LICENSE)（Copyright © 2026 STEXxx）。

### 内嵌的第三方组件

| 组件 | 来源 | 许可 | 声明文件 |
|---|---|---|---|
| `Core/GPT-SoVITS/`（TTS 本地副本 + 本项目 API 封装） | [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | MIT © 2024 RVC-Boss | [`LICENSE`](Core/GPT-SoVITS/LICENSE)、[`SOURCE.md`](Core/GPT-SoVITS/SOURCE.md) |
| `Bot/napcat/` | [NapNeko/NapCatQQ](https://github.com/NapNeko/NapCatQQ) | ⚠️ 上游未提供许可证文件，因此**不随本仓库发布** | —— |

### 早期骨架的版权声明（MIT 要求保留）

以下三个 MIT 许可项目的早期骨架被本项目重写并大量扩展：

| 本项目目录 | 上游项目 | 许可 | 声明文件 |
|---|---|---|---|
| `Bot/`（事件骨架、管线协议） | [AkagawaTsurunaki/ZerolanLiveRobot](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot) | MIT © 2024 AkagawaTsurunaki | [`Bot/NOTICE`](Bot/NOTICE) |
| `Core/`（模型服务层） | [AkagawaTsurunaki/zerolan-core](https://github.com/AkagawaTsurunaki/zerolan-core) | MIT © 2024 AkagawaTsurunaki | [`Core/LICENSE`](Core/LICENSE) |
| `con-datapack-1.11/`（数据协议包，导入名 `condata`） | [AkagawaTsurunaki/zerolan-data](https://github.com/AkagawaTsurunaki/zerolan-data) | MIT © 2024 AkagawaTsurunaki | [`con-datapack-1.11/LICENSE`](con-datapack-1.11/LICENSE) |

每个声明文件都含上游 MIT 全文 + 本项目在其上的改动说明。此外，部分源码文件头部保留了原作者的 `Author:` 注释（如 `Bot/common/web/json_ws.py`、`Bot/common/web/zrl_ws.py` 的 `Author: AkagawaTsurunaki`），同属 MIT 署名要求。

### 线上协议标识

`condata.protocol.protocol` 的 `PROTOCOL_NAME = "ZongziProtocol"`（即消息 `protocol` 字段的默认值）
是**线上格式的一部分**：改名会导致按旧串通信的远端连不上。修改前请确认没有外部端在按旧值通信。

**使用须知**：本项目会采集麦克风与桌面音频、操作本地文件（限沙箱目录）、发起出站网络请求，请在了解这些行为的前提下使用，勿用于非法用途。
