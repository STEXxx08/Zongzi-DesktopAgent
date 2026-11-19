# Zongzi-DesktopAgent

[![Bilibili](https://img.shields.io/badge/Bilibili-%E7%B2%BD%E5%AD%90-00A1D6?logo=bilibili&logoColor=white)](https://space.bilibili.com/35778513)
[![Offline Tests](https://img.shields.io/github/actions/workflow/status/STEXxx08/Personal-Agent-AI/bot-tests.yml?label=Bot%20Offline%20Tests)](.github/workflows/bot-tests.yml)

**一个跑在自己电脑上的 AI 桌面伙伴，也是一个组件全可替换的桌面 Agent 集成框架。**

---

## 项目简述

**她叫「粽子」**——一位能驻留在你桌面上的 AI 伙伴（默认人设是有猫耳和猫尾的猫娘）：**听得见你说话、看得见你屏幕上在发生什么、记得住你们聊过的内容，还能自己调工具帮你查资料干活**。无论是陪你打游戏、看视频，还是顺手查天气/新闻/GitHub、整理网页资料，她都能长期陪在桌面角落里。

把「粽子」的人设与立绘换掉，它本身是一套**高自由度、全组件解耦的桌面 Agent 框架**：

- **Bot（桌面客户端，纯 CPU）**：负责对话编排、语音与屏幕感知、自研长期记忆、工具/Skills/MCP 运行时、以及 PySide6 桌宠界面。
- **Core（模型服务层）**：把 ASR / LLM / MLLM / TTS / OCR / 图像描述 / 联网检索封装为统一 HTTP 服务，可跑在本机，也可丢到另一台带显卡的机器上。
- **任意混搭**：本地模型与云端 API 自由组合——零显卡可以直接「Bot + 云端 API」秒开打字聊天；有 NVIDIA 显卡则可以搭出「本地 ASR + 云端 LLM/MLLM + 本地 GPT-SoVITS」的低延迟语音体验。

> 💡 **快速导航**
> - **对电脑不太熟，只想先跑起来打字聊天？** → 直接看 **[零基础快速上手教程](#零基础快速上手教程)**（不需要显卡、不下载大模型、不用管 Core 服务）
> - **想折腾本地语音识别、语音合成、多模态看屏幕或二次开发？** → 跳过小白教程，直奔 **[详细技术文档](#详细技术文档)**

> - 权重等不随仓库上传的大文件，如有需要可以来找我拿（见文末 [Contact](#contact)）
> - 本项目的公开版提取与 README 由 AI 协助完成，可能有逻辑遗漏，也可能在不同环境下出现各类问题，欢迎在项目下提出 issue，我看到会尽快回复或修复~
> - 本项目纯凭兴趣，非常欢迎大佬提出改进建议。
> - 如果使用了本项目，或者因为本项目收到了一点点启发，就给个 star 呗，这对我很重要~

<details>
<summary>上游致谢说明（MIT 开源骨架来源）</summary>

本项目为独立项目。最初的架构与通信构想参考了 [ZerolanLiveRobot](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot)（`Bot/`）、[ZerolanCore](https://github.com/AkagawaTsurunaki/zerolan-core)（`Core/`）、[ZerolanData](https://github.com/AkagawaTsurunaki/zerolan-data)（`con-datapack-1.11/`），并内嵌了 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)（`Core/GPT-SoVITS/`）本地副本。目前除 HTTP 传输骨架外，主体逻辑已基本重写。感谢 AkagawaTsurunaki 与 RVC-Boss 提供的 MIT 开源基座，完整版权声明见文末 [License 与来源](#license-与来源)。

</details>

### 目录

- [项目简述](#项目简述)
- [项目亮点](#项目亮点)
- [零基础快速上手教程](#零基础快速上手教程)
- [详细技术文档](#详细技术文档)
  - [4.1 架构设计与模型组合](#41-架构设计与模型组合)
  - [4.2 全套本地部署与 Core 服务](#42-全套本地部署与-core-服务)
  - [4.3 核心技术子系统详解](#43-核心技术子系统详解)
  - [4.4 项目目录结构](#44-项目目录结构)
  - [4.5 开发、测试与扩展](#45-开发测试与扩展)
  - [4.6 已知限制](#46-已知限制)
- [License 与来源](#license-与来源)
- [Contact](#contact)

---

## 项目亮点

### 🐱 1. 三模桌宠界面与可视化设置中心
- **三种形象渲染模式**：内置零依赖**矢量猫娘（粽子）**、**图片/GIF/WebP 立绘包**（仓库自带 `Gemini 娘 · 星饰猫耳` 与 `DeepSeek 娘 · 冬装小鲸鱼` 两套 8 表情 Q 版立绘，支持自定义目录）、以及 **Live2D 模型导入**（`.model3.json`，需可选安装 `live2d-py`）；资源异常时平滑回退到内置矢量猫娘，不会崩溃。
- **桌面陪伴体验**：支持窗口置顶、自由拖拽、缩放（`0.5x ~ 2.0x`）与透明度调节、气泡字幕、随回复情绪切换表情、系统托盘控制、退出记住位置并做多屏可见性校验。
- **六大分类设置中心**：内置「外观与桌宠 / 角色与人设 / 语音与音频 / 模型与视觉 / 长期记忆 (RAG) / 联网与工具服务」图形配置界面，无需手改 YAML；保存时保留原注释原子写回，普通项即时热生效，需重启项自动弹窗提醒一键重启。

### 🎙️ 2. 低延迟流式语音与语义断点检测
- **全链路流式响应**：麦克风采集 → 流式 ASR → **LLM / MLLM 流式输出按标点分句** → 流式 TTS 合成播放，大幅降低首字发声延迟（TTFA）。
- **TurnSense 语义端点检测**：告别"停顿一秒就抢话"的机械阈值，使用本地 ONNX 模型（带 `window_audio_sec` 滑窗裁剪，固定长语音开销）判断"这句话到底说完没有"。
- **随时打断 & 自适应语速 & 冷启动预热**：说话随时打断正在播放的语音，并异步触发 Core `POST /tts/abort` 尽快终止当前合成；被打断频繁或回复较长时自动提高语速；启动后静默预热 TTS，让第一句回复告别十几秒冷启动等待。

### 👁️ 3. 屏幕多模态与桌面声音感知
- **看懂你的屏幕**：支持云端 MLLM 定时截图（支持关键词临时开启推理预算）与本地 VLM（关键词触发 / 常态轮询），无多模态时可降级为 OCR + 图像描述。
- **听得到电脑声音**：可选捕获桌面系统音频送入 ASR 作为环境辅助信息（麦克风说话时优先让路，不阻塞交互），看视频或打游戏时它能接上画面和语音里的梗，空闲超时还会主动找你搭话。

### 🧠 4. 自研四层长期记忆系统
- **专为陪伴场景设计**：不用"全量聊天记录塞进向量库"的重型方案，而是做筛选压缩与日历式分层记忆（`Bot/memory/`）：
  1. **聊天时自动抽取快照**：`main.py` 运行期间每 N 轮自动抽取简短记忆摘要条目（异步不阻塞对话，失败保留原文重试），并按周期压缩旧对话历史抑制 token 膨胀；
  2. **独立主入口 `merge_memory.py` 离线合并**：`Bot/merge_memory.py` 是与 `Bot/main.py` 并列的主要入口——平时自动生成的记忆条目，需要通过手动离线运行 `merge_memory.py`（全或无批量去重合并，写前先备份至 `archive/`）来更新进长期档案 `core_memory`；记忆提取/合并可单独指定另一个更便宜或更强的模型；
  3. **四层上下文注入与 RAG**：`[长期]` 与 `[标记]` 恒定置顶注入 + 最近 N 天常驻日历窗口 + 远期记忆通过 **BGE ONNX 向量检索**按需召回 / 低概率"随机想起"，支持 `off / observe / on` 三态开关。

### 🛠️ 5. 完备的 Agent 工具生态与工程底座
- **实用工具与扩展标准**：同一轮对话支持多轮工具调用，工具运行期间的输入自动缓冲合并；内置**联网检索**（搜索 / 新闻 / 天气 / Steam / GitHub / 汇率）、**网页正文研究与沙箱归档**、兼容 `SKILL.md` 的 **Skills 框架**（支持 `/skill-name` 与模型自创 Skill）、**HTTP MCP 客户端**、**受限目录文件沙箱**，以及让模型回读本次运行内工具调用记录的 **`harness_recall`**。
- **高自由度与高缓存命中**：Bot 侧所有 `model_id` 均无白名单限制，Core 侧 `starter.py` 支持注册表与配置 `entry` 动态按需加载；System Prompt 前缀保持字节级恒定（时间/环境/记忆/工具结果全部放进用户轮），实测提示缓存命中率可达 **97%+**；自带出站 SSRF 地址校验与外部内容标记中和。

---

## 零基础快速上手教程

> **这一节面向几乎没折腾过代码的同学**。目标是用最简单的方式、在 Windows 电脑上**10 分钟内让猫娘出现在桌面并打字聊天**：
> **不需要独立显卡、不需要下载几 GB 的语音权重、也不需要启动复杂的 Core 服务**。等跑通之后，再按需对照第 4 节加语音和屏幕识别。

### 准备工作：只需两样东西

1. **安装 Miniconda**（可以理解为"给 AI 准备一个独立的 Python 小房间"，不会弄乱你电脑原本的环境）：
   - 前往 [Miniconda 下载页面](https://docs.conda.io/en/latest/miniconda.html) 下载 Windows 64 位安装包，一路点 `Next` 默认安装即可。
   - 装好后，点击 Windows 开始菜单，搜索 **`Anaconda Prompt`** 并打开它（后面所有命令都在这个黑框框里输入）。
2. **准备一个云端大模型 API Key**（用来充当猫娘的"大脑"，推荐 DeepSeek，几块钱能用很久）：
   - 去 [DeepSeek 开放平台](https://platform.deepseek.com/) 注册登录 → 左侧找到「API keys」→ 点击创建并复制那串 `sk-` 开头的密钥妥善保存（别发给别人）。
   - *如果你有 Kimi / 豆包 / 硅基流动 / 本机 Ollama 等其它支持 OpenAI 格式的接口，也完全通用。*

---

### 第 1 步：下载项目代码

- **方式 A（纯小白推荐，不用装 Git）**：
  1. 回到本 GitHub 页面顶部，点击绿色的 **`<> Code`** 按钮 → 点击 **`Download ZIP`**。
  2. 把下载的压缩包解压到一个**纯英文路径**的文件夹里（推荐例如 `D:\Personal-Agent-AI`，注意打开这个文件夹后里面要能直接看到 `Bot`、`Core` 这几个子文件夹）。
- **方式 B（如果你电脑装了 Git）**：
  在 `Anaconda Prompt` 里运行：
  ```powershell
  git clone https://github.com/STEXxx08/Personal-Agent-AI.git
  ```

---

### 第 2 步：安装运行环境（复制粘贴即可，只需做一次）

打开 **`Anaconda Prompt`**，先进入你刚才解压/下载好的项目文件夹（假设在 `D:\Personal-Agent-AI`，如果放在别的地方请换成你的实际路径）：

```powershell
cd /d D:\Personal-Agent-AI
```

然后**按顺序逐行复制粘贴**下面这 6 行命令（每行粘贴后按回车，等它跑完再贴下一行）：

```powershell
conda create -n MyAI python=3.11 -y
conda activate MyAI
pip install -r Bot/requirements.txt
pip install -r Bot/requirements-ui.txt
pip install -r Bot/requirements-optional.txt
pip install -e con-datapack-1.11
```

> ⚠️ **遇到红色报错 `webrtcvad` 编译失败怎么办？**
> 很多没装过开发工具的 Windows 电脑会卡在 `webrtcvad` 这一步。不用管报错，直接先运行下面这行安装现成的安装包，然后再把刚才没跑完的命令行补跑一遍即可：
> ```powershell
> pip install webrtcvad-wheels
> ```

---

### 第 3 步：生成配置文件 & 填入你的 API Key

继续在同一个 `Anaconda Prompt` 窗口里运行：

```powershell
cd Bot
python main.py
```

> 📌 **看到程序"自己退出了"？别慌，这是正常的！**
> 第一次运行时，程序会在 `Bot` 文件夹里自动生成一份叫 **`config.yaml`** 的配置文件，然后自动退出让你填写密钥——这不是报错。

用记事本（或右键打开方式）打开 **`Bot\config.yaml`**，只需要修改下面 **2 个地方**并保存：

1. **填上你的云端大模型密钥**（找到 `pipeline:` 下面的 `llm:`，把对应几行改成下面这样；确认紧挨着的 `mllm:` 下方 `enable` 是 `False`）：
   ```yaml
   pipeline:
     llm:
       enable: true
       openai_format: true                        # ← 改成 true：直接走在线大模型接口
       predict_url: 'https://api.deepseek.com'    # ← 服务商接口地址（用别家就换别家地址）
       api_key: 'sk-把这里换成你刚才复制的密钥'      # ← 填入你的 API Key
       model_id: 'deepseek-chat'                  # ← 填该平台支持的模型名
   ```
2. **先关掉语音功能**（因为我们这一步还没装本地语音引擎，不关掉会提示连不上）：
   ```yaml
   pipeline:
     asr:
       enable: false                              # ← 改成 false（关闭语音识别）
     tts:
       enable: false                              # ← 改成 false（关闭语音合成）
   service:
     microphone:
       enable: false                              # ← 改成 false（关闭麦克风）
   ```

*(💡 小提示：长期记忆功能默认已经是开启状态，会自动复用上面填的大模型，不用额外折腾。)*

---

### 第 4 步：启动！召唤你的桌面猫娘

回到刚才那个 `Anaconda Prompt` 黑框框，再输入一次启动命令：

```powershell
python main.py
```

稍等几秒，屏幕右下角就会弹出可爱的猫娘桌宠和聊天窗口啦！直接在聊天窗口里打字发送，她就会回答你并做出对应表情~

---

### 第 5 步：日常使用与形象切换小贴士

- **想换猫娘形象 / 改名字性格 / 调字体大小？**
  右键点击桌面上的猫娘（或点击聊天窗口右上角设置按钮 / 右下角系统托盘小图标），打开 **「Zongzi 设置中心」**：
  - 在 **「外观与桌宠」** 里可以一键切换 **矢量粽子**、**Gemini 娘**、**DeepSeek 冬装娘**，也可以导入你自己的图片立绘包或 Live2D 模型；
  - 在 **「角色与人设」** 里可以修改角色名和人设 Prompt；
  - 改完点 **「保存并应用」**，大部分设置会直接热生效，需要重启的项也会贴心提示你。
- **日常启动与定期整理长期记忆（两个并列主入口）**：
  - **平时打开聊天（`main.py`）**：打开 `Anaconda Prompt`，依次输入下面三行即可启动（聊天期间程序会自动把对话要点总结成一条条记忆快照）：
    ```powershell
    conda activate MyAI
    cd /d D:\Personal-Agent-AI\Bot
    python main.py
    ```
  - **手动离线更新长期记忆（`merge_memory.py`）**：`merge_memory.py` 是和 `main.py` 并列的另一个主要入口。平时聊天自动总结出的记忆条目不会直接写进最终的 `core_memory`，隔一段时间（或聊完重要话题后）关掉聊天、在 `Bot` 目录下手动运行一次它，就会把积攒的记忆条目离线整理合并进长期档案 `core_memory`：
    ```powershell
    conda activate MyAI
    cd /d D:\Personal-Agent-AI\Bot
    python merge_memory.py
    ```

### 接下来还想加什么能力？

跑通文字聊天后，如果你想要更多能力，可以按需跳到下方第 4 节对应位置：

| 想要解锁的新能力 | 需要准备什么 | 跳到哪一节 |
|---|---|---|
| **能听会说（语音对话）** | 免显卡用百度云 ASR/TTS；追求低延迟与好音色用本机 NVIDIA 显卡 + 权重（约 6~10 GB） | [4.1 模型组合](#41-架构设计与模型组合) · [4.2 本地部署](#42-全套本地部署与-core-服务) |
| **看懂屏幕（陪玩游戏/看视频）** | 在设置中心或配置里启用 **MLLM**（填阿里云百炼等支持图片的在线多模态 API，或接本地 VLM） | [4.1 模型组合](#41-架构设计与模型组合) |
| **联网搜网页 / 查天气 / 新闻** | 启动 Core 的 `web_search` 服务（天气/Steam/GitHub/汇率开箱可用，搜网页/新闻需额外配 SearXNG） | [4.2 Web Search 与 SearXNG](#web-search-与-searxng-说明) |

---

## 详细技术文档

### 4.1 架构设计与模型组合

Zongzi 采用 **Bot（前端编排客户端）↔ Core（后端模型服务网关）** 的 HTTP/JSON 解耦架构。只要协议一致，Bot 侧与 Core 侧可以部署在不同主机上，各管线也可绕过 Core 直接对接外部在线 API。

#### Core 服务注册表与默认端口

| 服务名 | 默认启停（`Startup`） | Conda 环境 | 工作目录 | 监听端口 | 需要 GPU | 说明 |
|---|---|---|---|---:|---|---|
| `asr` | ✅ | `qwen3-asr` | `Core/` | `11001` | ✅（可用 CPU） | 语音识别（`sherpa-onnx` / `Qwen3-ASR` / `Paraformer`） |
| `llm` | ✅ | `llm_deepseek_online` | `Core/` | `11000` | ❌（在线/Ollama） | 纯文本对话大模型（与 `mllm` 互斥） |
| `mllm` | ⬜ | `llm_deepseek_online` | `Core/` | `11000` | ❌（在线 API） | 多模态大模型（与 `llm` 互斥） |
| `tts` | ✅ | `GPTSoVits` | `Core/GPT-SoVITS/` | `11002` | ✅ | 语音合成（`zongzi_api.py`） |
| `llm-memory` | ✅ | `llm_deepseek_online` | `Core/` | `10010` | ❌ | 记忆抽取/合并纯文本服务（与 `mllm-memory` 互斥） |
| `mllm-memory` | ⬜ | `llm_deepseek_online` | `Core/` | `10010` | ❌ | 记忆抽取/合并多模态服务（与 `llm-memory` 互斥） |
| `web_search` | ✅ | `llm_deepseek_online` | `Core/` | `11012` | ❌ | 联网/天气/新闻/Steam/GitHub/汇率 6 合 1 网关 |
| `ocr` | ⬜ | `MyAI` | `Core/` | `11004` | 可选 | 可选视觉降级：PaddleOCR 文字识别 |
| `imgcap` | ⬜ | `MyAI` | `Core/` | `11003` | ✅ | 可选视觉降级：BLIP 图像描述 |

#### 组件兼容矩阵与推荐搭配

| 组件 | 本地模型（Core 内置实现） | 云端 / 外部服务 |
|---|---|---|
| **ASR** | `Sherpa-ONNX-Streaming-Zipformer-ZH-INT8`（推荐）、`Qwen3-ASR-0.6B`、`iic/speech_paraformer…`（funasr） | **百度云 ASR**（`BaiduASR`，无需显卡） |
| **LLM** | **Ollama**（任意本地模型）、`THUDM/chatglm3-6b`、`THUDM/glm-4-9b-chat-hf`、`Qwen/Qwen-7B-Chat`、`augmxnt/shisa-7b-v1`、`01-ai/Yi-6B-Chat`、`deepseek-ai/DeepSeek-R1-Distill-*` | **DeepSeek**、**Kimi**、**豆包**及**任意 OpenAI 兼容端点** |
| **MLLM** | —— | 任意 OpenAI / DashScope 兼容多模态端点（如 `qwen3.8-omni-flash`、`qwen3.7-plus`） |
| **VLM**（视觉辅助） | 本地 vLLM 等任意 OpenAI 兼容视觉端点（Bot 侧直连，端口默认 `10003`） | 同左 |
| **TTS** | `GPT-SoVITS`（`Core/GPT-SoVITS/zongzi_api.py`） | **百度云 TTS**（`BaiduTTS`，无需显卡） |
| **OCR / ImgCap** | `paddlepaddle/PaddleOCR`、`Salesforce/blip-image-captioning-large` | —— |

**三条兼容性最佳的搭配方案**：
1. **全云端（零显卡）**：百度云 ASR + OpenAI 兼容在线 LLM/MLLM + 百度云 TTS。
2. **本地语音 + 云端大脑（作者主力方案，延迟与质量最均衡）**：`sherpa-onnx` ASR + DeepSeek / 通义多模态 + `GPT-SoVITS` TTS。
3. **全本地离线**：`sherpa-onnx` ASR + Ollama + `GPT-SoVITS` TTS。

#### 换模型与纯配置扩展机制

- **Bot 侧 `model_id` 无白名单限制**：`Bot/pipeline/*/config.py` 中所有组件的 `model_id` 均为自由字符串（启动校验不拦截任何模型名）。
  - **Bot 直连在线 LLM**（跳过 Core）：在 `Bot/config.yaml` 中将 `pipeline.llm.openai_format` 设为 `true`，`predict_url` 填服务商 `base_url`（不要带 `/llm/predict`），填好 `api_key` 和 `model_id`；`temperature` 和 `max_tokens` 留空即沿用服务端默认值。
  - **切换百度云语音**：将 `Bot/config.yaml` 的 `pipeline.asr.model_id` 设为 `BaiduASR`（并填 `baidu_asr_config`），TTS 同理设为 `BaiduTTS`（若使用云端参考音色库需设 `character.speech.is_remote: true` 并填写 `character.speech.prompts`），即可关闭对应的 Core 本地服务。
- **Core 侧更换或新增模型**：
  - 更换已登记模型：在 `Core/config.yaml` 修改对应服务段的 `id` 字段（如 `LLM.id`、`ASR.id`）。
  - **新增本地模型已支持纯配置扩展**：`Core/starter.py` 已从早期的 `if/elif` 硬编码重构为 **`MODEL_REGISTRY` + `importlib` 按需动态加载**（仅导入选中的分支，避免跨 Conda 环境导入报错）。现在直接在 `Core/config.yaml` 的模型配置下声明 `entry: "module_path:ModelClass,ConfigClass"`（或在服务段 `registry` 映射中登记），即可无需修改 `Core/starter.py` 代码接入新实现。

---

### 4.2 全套本地部署与 Core 服务

#### 环境兼容性与硬件要求

> ⚠️ **装 Core 之前必须知道的三条原则**：
> 1. **依赖必须按服务拆分 Conda 环境**：`Bot`、`ASR`、`TTS` 之间的 `torch` 和 `numpy` 大版本互不兼容，**至少需要 3 个独立环境**（纯 Python 的 `llm_deepseek_online` 可独立也可合并进 `qwen3-asr`）。`Core/agent-stack/registry.json` 默认使用 `runner: "conda"`，也支持不激活 conda 的 `runner: "process"`。
> 2. **Bot 侧绝不装 `torch`**：Bot 全程纯 CPU 运行（TurnSense 与 BGE 意图路由/记忆检索默认均走 `onnxruntime` CPU 推理）。
> 3. **Core 可跨机部署**：低配电脑可以只跑 Bot，把 Core 放在另一台带 N 卡的机器上，然后修改 `Bot/config.yaml` 中的各 `predict_url` 与 `stream_predict_url`。

- **作者实测基准环境**：Windows 11 + NVIDIA GeForce RTX 5060 Laptop（8 GB 显存，Blackwell / `sm_120`）+ 显卡驱动 `595.97`（CUDA 13.2）。8 GB 显存同时运行 `sherpa-onnx` ASR + `GPT-SoVITS` TTS + 在线 MLLM 时，实测显存占用约 **4.9 GB**；全套本地推荐 **≥ 8 GB 显存**（仅跑 ASR+TTS 时 6 GB 可尝试）。
- **GPU 与系统兼容矩阵**：

| 硬件 / 系统 | 能否本地跑 Core | 说明 |
|---|---|---|
| **RTX 50 系**（sm_120） | ✅ **必须用 `cu128` 轮子** | `cu118`/`cu126` 无 sm_120 内核，会报 `no kernel image is available`；Windows 驱动需 **≥ 570** |
| **RTX 40 / 30 / 20 系、GTX 16 系** | ✅ 可直接用同一套 `cu128` 依赖 | CUDA 12.8 运行时要求驱动版本 **≥ 570**（用 `nvidia-smi` 检查右上角 CUDA Version） |
| **AMD / Intel 显卡 / macOS** | ❌ 不支持本地 GPU 链路 | 强依赖 CUDA 版 `torch` / `sherpa-onnx` / `flash-attn`，建议走全云端 API 路线 |
| **Linux / WSL** | ⚠️ Core 可跑、Bot 未验证 | Core 与 `Core/agent_stack.py` 跨平台，但 Bot 音频采集与桌宠壳层仅在 Windows 实测 |

- **各环境 Python 版本与 `torch` 锁定表**：

| Conda 环境 | 用途 | 推荐 Python（可用范围） | `torch` 版本 | 版本卡点与特殊依赖说明 |
|---|---|---|---|---|
| `MyAI` | Bot 主程序 | **3.11**（3.11 ~ 3.12） | 不装 | `webrtcvad` 缺编译环境改用 `pip install webrtcvad-wheels`（麦克风顶层依赖，关麦也需存在） |
| `qwen3-asr` | Core `asr`（11001） | **3.12**（3.11 ~ 3.12） | `2.8.0+cu128` | 选 **Qwen3-ASR** 路线必须 Python 3.12（`flash-attn` 轮子锁死）；走 `sherpa-onnx` 可 3.11 且不装 `torch` |
| `llm_deepseek_online` | Core `llm`/`mllm`/`llm-memory`/`web_search` | **3.11**（3.10 ~ 3.13） | 不装 | 全是纯 Python 包（`openai`/`dashscope`/`flask`），想少建环境可直接装进 `qwen3-asr` |
| `GPTSoVits` | Core `tts`（11002） | **3.10**（3.9 ~ 3.10） | `2.7.1+cu128` | 被 `numba 0.56.4` + `numpy 1.23.4` 锁在 3.10；`jieba_fast`/`pyopenjtalk` 需 Windows MSVC 编译 |

<details>
<summary>展开查看：<code>flash-attn</code> 精确对齐要求与环境快照（<code>环境快照/*.yml</code>）重建说明</summary>

1. **`flash-attn`（仅 Qwen3-ASR 路线需要）**：Windows 上极难从源码编译，必须使用与宿主机完全咬合的预编译轮子（不随仓库发布）：
   `flash_attn-2.8.3+cu128torch2.8.0cxx11abiFALSE-cp312-cp312-win_amd64.whl`
   Python 版本、torch 版本、C++ ABI 任一项不一致都会在 `import flash_attn` 时报符号缺失错误。不想折腾请优先选择 **路线 A `sherpa-onnx`**。
2. **环境快照与导出避坑**：
   - `Bot/requirements.txt` 已按实际环境严格锁死；`Core/requirements-<环境名>.txt` 分两段——能从 PyPI 直装的列在正文中，CUDA 版 `torch` / `sherpa-onnx` / `flash-attn` 等写在注释中需手动安装（`Core/requirements.txt` 头部有完整目录索引）。
   - `环境快照/` 目录下保存了 4 个环境的 Conda 基底快照，可通过仓库根目录 `python generate_env_snapshots.py` 重新导出。**注意**：部分 conda 版本（实测 25.7.0）执行 `conda env export -n <名字>` 会因内部 bug 静默导出 `base` 环境，请务必使用脚本而非手敲 `-n`。

</details>

#### 分步安装 Core 环境

> 以下默认你已经在项目根目录 `Personal-Agent-AI` 下，且已完成 [快速上手第 2 步](#第-2-步安装运行环境复制粘贴即可只需做一次) 中的 `MyAI` 环境。另请确保 **`ffmpeg`** 已加入系统 `PATH`（`Core/asr/app.py` 音频转单声道会调用它）。

##### 1. 安装 ASR 服务（`qwen3-asr` 环境，3 条路线任选其一）

- **路线 A — `sherpa-onnx`（强烈推荐：无需 `flash-attn`、无需 `torch`、低延迟流式）**：
  ```powershell
  conda create -n qwen3-asr python=3.12 -y
  conda activate qwen3-asr
  # PyPI 上的 sherpa-onnx 只有 CPU 版，如需 GPU 推理请自行准备对应 CUDA 轮子（如 sherpa_onnx-1.13.3+cuda12.cudnn9-cp312-cp312-win_amd64.whl）
  pip install <你的轮子路径>\sherpa_onnx-1.13.3+cuda12.cudnn9-cp312-cp312-win_amd64.whl
  pip install -r Core/requirements-qwen3-asr.txt
  pip install -r Core/asr/sherpa_onnx/requirements.txt
  pip install -e con-datapack-1.11
  ```
- **路线 B — `Qwen3-ASR-0.6B`（必须先装 cu128 torch 与匹配 `flash-attn` 轮子，顺序不能反）**：
  ```powershell
  conda create -n qwen3-asr python=3.12 -y
  conda activate qwen3-asr
  pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
  pip install <你的轮子路径>\flash_attn-2.8.3+cu128torch2.8.0cxx11abiFALSE-cp312-cp312-win_amd64.whl
  pip install -r Core/requirements-qwen3-asr.txt
  pip install -r Core/asr/Qwen3ASR06B/requirements.txt
  pip install -e con-datapack-1.11
  ```
- **路线 C — `Paraformer`（funasr）**：
  ```powershell
  conda create -n qwen3-asr python=3.12 -y
  conda activate qwen3-asr
  pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
  pip install -r Core/requirements-qwen3-asr.txt
  pip install -r Core/asr/paraformer/requirements.txt
  pip install -e con-datapack-1.11
  ```

##### 2. 安装 TTS 服务（`GPTSoVits` 环境）

```powershell
conda create -n GPTSoVits python=3.10 -y
conda activate GPTSoVits
pip install torch==2.7.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128
pip install -r Core/requirements-gpt-sovits.txt
pip install -r Core/GPT-SoVITS/requirements.txt
pip install -e con-datapack-1.11
```
*(注意：`jieba_fast` 和 `pyopenjtalk` 无 Windows 预编译轮子，需要电脑具备 MSVC 生成工具进行本地编译。如改用 `starter.py tts` 转发模式，再补装 `Core/tts/gpt_sovits/requirements.txt`。)*

##### 3. 安装 LLM / MLLM / Web Search 环境（`llm_deepseek_online`）

```powershell
conda create -n llm_deepseek_online python=3.11 -y
conda activate llm_deepseek_online
pip install -r Core/requirements-llm-deepseek-online.txt
pip install -r Core/llm/deepseek_online/requirements.txt
pip install -r Core/mllm/requirements.txt
pip install -r Core/web_search/requirements.txt
pip install -e con-datapack-1.11
```

#### Web Search 与 SearXNG 说明

Core 的 `web_search` 服务（端口 `11012`）对外提供 6 个工具，**只有 `search` 和 `news` 依赖独立部署的 SearXNG**；`weather`（Open-Meteo）、`steam`（Steam 官方 API）、`github`（GitHub Releases API）、`exchange_rate`（Frankfurter v2）均直连官方接口，**不装 SearXNG 也能正常使用**。

如果需要使用 `search` / `news`，需自行通过 Docker 或 WSL 部署 SearXNG，要点如下：
1. 参考仓库内置模板 [`Core/web_search/searxng/settings.yml`](Core/web_search/searxng/settings.yml)，**必须开启 JSON 格式输出**，否则 `format=json` 请求会返回 HTTP 403。
2. 在 `Core/config.yaml` 中将 `WebSearch.config.endpoint` 指向你的 SearXNG 地址（默认 `http://127.0.0.1:8080/search`），并按实际网络可达情况精简 `engines`（默认仅留 `bing` 以控制长尾延迟）。切勿在未配认证限流的情况下将 SearXNG 暴露到公网（详见 [`Core/web_search/README.md`](Core/web_search/README.md)）。

<details>
<summary>展开查看：若 SearXNG 运行在本机 WSL systemd 单元（<code>searxng-local</code>）的自动拉起配置</summary>

`Core/agent-stack/registry.json` 的 `settings.optional_dependencies` 默认配置了 `searxng`（`runner: "wsl-systemd"`、`unit: "searxng-local"`、`sudo: true`）：
- 执行 `agent-stack.cmd start` 时会用 `wsl sudo -n systemctl start searxng-local` 尽力拉起（不阻塞密码输入，失败仅输出警告，不会中止主服务启动）。
- 执行 `agent-stack.cmd stop` 时**不会**关闭 SearXNG；`status` 会单独汇报其状态。
- 如果你的 SearXNG 跑在远程机器或使用 Docker 手动管理，请将 `Core/agent-stack/registry.json` 中该条的 `enabled` 改为 `false`。

</details>

---

#### 模型权重与参考音频

**大模型与权重文件均不随仓库上传**，请按下方路径新建目录放入（相关路径均已配置在 `.gitignore` 中，不会误提交；如需完整打包好的权重文件可联系作者获取）：

- 标记说明：🔴 **启用对应功能时必需**　🟡 **建议配置（缺失会静默降级）**　🔵 **按路线可选**

| 所属端 | 权重 / 资源 | 参考体积 | 放置路径（必须一致） | 获取来源 / 说明 |
|---|---|---:|---|---|
| **Core ASR** | 🟡 `sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30/` | 736 MB | `Core/models/` | [sherpa-onnx asr-models 发布页](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models)（含 `encoder/decoder/joiner.onnx` + `tokens.txt`，作者主力方案） |
| **Core ASR** | 🔵 `sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30/` | 160 MB | `Core/models/` | 同上（轻量小模型，省显存但精度略低） |
| **Core ASR** | 🔵 `model.safetensors`（Qwen3-ASR-0.6B） | ~1.8 GB | `Core/asr/Qwen3ASR06B/` | HuggingFace / ModelScope 下载 `Qwen/Qwen3-ASR-0.6B`（tokenizer 已随仓库附带） |
| **Core ASR** | 🔵 Paraformer 模型 | 数百 MB | `Core/` | 首次运行通过 `funasr` / ModelScope 自动下载 |
| **Bot 本地** | 🔴 `TurnSense1.1/`（`model_int8.onnx` + `am.mvn`） | 217 MB | `Bot/models/TurnSense/TurnSense1.1/` | [brgroup/TurnSense](https://huggingface.co/brgroup/TurnSense) 或 [GitHub](https://github.com/Bairong-Xdynamics/TurnSense)（Apache-2.0，请保留随附 LICENSE） |
| **Bot 本地** | 🟡 `bge-small-zh-v1.5-onnx/`（`onnx/model_quantized.onnx` 等） | 23 MB | `Bot/models/bge-small-zh-v1.5-onnx/` | [Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5)（仓库已附带 `tokenizer*.json`；缺失时意图路由退化为关键词匹配，RAG 检索不可用） |
| **Bot 本地** | 🔵 `FRCRN_SE_16K/last_best_checkpoint.pt` | 154 MB | `Bot/checkpoints/FRCRN_SE_16K/` | 仅开启 `pipeline.asr.denoise` 语音降噪时需要，默认关闭 |
| **Core TTS** | 🔴 `GPT_SoVITS/pretrained_models/` 全套 | ~4.5 GB | `Core/GPT-SoVITS/GPT_SoVITS/pretrained_models/` | [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 官方预训练模型整合包（含 `s1*.ckpt`、`s2G*.pth`、`chinese-roberta-wwm-ext-large`、`chinese-hubert-base`、`v2Pro` 等） |
| **Core TTS** | 🔴 `GPT_SoVITS/text/` 文本前端（`G2PWModel/` 等） | ~30 MB | `Core/GPT-SoVITS/GPT_SoVITS/text/` | 同上官方整合包，中文/日文文本前端必需 |
| **Bot TTS** | 🔴 **TTS 参考音频**（3~10 秒干声） | 几 MB | `Bot/resources/prompts/tts/` | 自行录制，命名格式 **`[语言][情绪]音频文本.wav`**（如 `[zh][Default]你好呀.wav`） |

> ⚠️ **TTS 参考音频关键规则**：
> 1. 启用 `pipeline.tts.enable: true` 且 `is_remote: false` 时，`Bot/resources/prompts/tts/` **不能为空**，否则启动会报错 `There are no eligible TTS prompts...`。
> 2. 文件名中括号里的情绪标签需与 `Bot/config.yaml` 中 `character.emotions` 白名单（默认 `Default / happy / sad / angry / shy / surprised`）对应；缺少某情绪音频时会自动回落到 `Default`，因此**至少必须有一条 `[zh][Default]...wav`**。

<details>
<summary>展开查看：<code>Core/config.yaml</code> 与 <code>Bot/config.yaml</code> 中权重路径对照片段</summary>

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
service:
  intent_router:
    model_path: 'models/bge-small-zh-v1.5-onnx'      # 相对 Bot/（与 memory.retrieval 共用）
character:
  speech:
    prompts_dir: 'resources/prompts/tts'             # 相对 Bot/
```

</details>

---

#### 启动与管理 Core 服务栈

1. **配置 Core**：复制 [`Core/config.example.yaml`](Core/config.example.yaml) 为 `Core/config.yaml`。
   - **选择启停哪些服务**：由 `Core/config.yaml` 顶部的独立 `Startup` 段决定（`agent-stack.cmd` 与 `agent_stack.py` 均读取此处，不读 Bot 配置）。同端口服务（`llm`/`mllm` 共用 `11000`、`llm-memory`/`mllm-memory` 共用 `10010`）**每组只能开一个**，同时设 `true` 时启动器会主动报错拦下。
   - **设置 API Key 环境变量**（如果走在线 LLM / MLLM）：按各自服务要求配置对应环境变量（如 DeepSeek / 阿里云百炼 `DASHSCOPE_API_KEY` 等）。

2. **一键启停命令**：
   - **Windows 推荐（支持 Windows Terminal 多标签页）**：
     ```powershell
     cd Core
     agent-stack.cmd validate      # 校验注册表、Startup 开关来源与端口冲突
     agent-stack.cmd plan          # 预览将启动的服务，不拉起进程
     agent-stack.cmd start         # 按 Startup 段并行拉起所有已启用服务
     agent-stack.cmd status        # 查看运行状态
     agent-stack.cmd logs -Service asr   # 查看指定服务日志（全部合并日志位于 Core/agent-stack/runtime/logs/combined.log）
     agent-stack.cmd stop          # 停止全部栈内服务（含进程树清理）
     ```
   - **跨平台启动器（Windows / Linux / WSL / macOS，仅标准库）**：
     ```bash
     cd Core
     python agent_stack.py start
     python agent_stack.py start --only asr,tts        # 临时覆盖 Startup 段，只起指定服务
     python agent_stack.py start --config other.yaml   # 指定自定义配置
     python agent_stack.py logs --service asr
     python agent_stack.py stop
     ```
     *(Windows 上默认行为与 `.cmd` 一致，可通过 `--terminal windows|tabs` 或 `--hidden` 控制窗口；详见 [`Core/agent-stack/README.md`](Core/agent-stack/README.md)。)*

3. **启动机制与优化细节**：
   - 注册表中默认服务 `start_order: 10`，**同组服务串行激活 Conda 环境（防并发抢占临时文件），但模型加载与健康检查并行等待**，无额外串行延迟。
   - `asr` 服务在 `registry.json` 中默认配置了 `process_priority: "above_normal"`，避免前台运行大型游戏打满 CPU 时语音识别线程被饿死。
   - 启动 Bot 后，日志出现 `🔥 TTS 预热完成，耗时 X 秒；首次正式合成不再承担冷启动开销。` 即表示冷启动预热生效（可用 `pipeline.tts.warmup_on_startup: false` 关闭）。

---

#### 常见故障排查

| 症状 | 原因与处理 |
|---|---|
| `CUDA error: no kernel image is available` | `torch` 版本与显卡架构不匹配。RTX 50 系必须用 `cu128` 轮子且驱动 ≥ 570 |
| `import flash_attn` 报底层符号缺失 | Python / `torch` / C++ ABI 未对齐。严格按 4.2 的 `cp312 + torch2.8.0+cu128` 组合安装，或改用 `sherpa-onnx` |
| `pip install webrtcvad` 编译报错 | 缺 MSVC 生成工具。改用 `pip install webrtcvad-wheels`（提供同名模块） |
| `import condata` 报错 | 忘记在当前激活的 Conda 环境内执行 `pip install -e con-datapack-1.11` |
| 某个 Core 服务起不来或端口未监听 | 执行 `agent-stack.cmd logs -Service <服务名>` 看错误堆栈；大模型首次加载超过 300 秒可在 `Core/agent-stack/registry.json` 调大 `startup_timeout_seconds` |
| 日志提示 `sherpa-onnx` 回退到 CPU | `onnxruntime_providers_cuda.dll` 未能加载，模型自动回落到 CPU 并以 `num_threads` 控制速度 |
| 日志出现 `using heuristic fallback` | 未安装 `onnxruntime`，TurnSense 退化为规则判定。检查 `Bot/requirements.txt` 依赖是否装全 |
| TTS 启动报错 `There are no eligible TTS prompts…` | `pipeline.tts.enable: true` 且 `is_remote: false` 时，`Bot/resources/prompts/tts/` 不能为空且至少需有一条 `[zh][Default]...wav`；不用语音请关闭 `pipeline.tts.enable` |
| 麦克风没声音或热键无反应 | 检查 `service.microphone.enable` 与 `hotkey`（默认 `f8`）；若用外部自定义音频设备名，`allow_default_device_fallback` 默认 `false`，请核对设备名 |
| 桌面音频与 TTS 互相循环自激 | 同时启用 `desktop_audio` 与 TTS 时，需用虚拟声卡（如 Voicemeeter）将 TTS 播放通道与桌面捕获通道隔离 |

---

### 4.3 核心技术子系统详解

#### 自研四层长期记忆系统（`Bot/memory/`）

相比直接把全量聊天记录塞入重型 RAG 框架，个人桌面陪伴的记忆总量有限但闲聊噪声极大。本项目针对该场景设计了**筛选压缩 + 日历分层 + 按需召回**的自研记忆系统（核心实现见 `Bot/memory/manager.py`、`retrieval.py`、`core_memory_parser.py`、`index_store.py`、`config.py`）：

| 阶段 | 核心机制 | 关键配置 / 入口 |
|---|---|---|
| **1. 运行时历史** | 保留近期消息作为短期工作上下文 | `memory.max_history` |
| **2. 自动摘要生成快照** | `main.py` 运行期间每 N 轮异步抽取记忆条目，不阻塞前台对话；失败保留原文重试 | `memory.save_interval_rounds`、`async_save` |
| **3. 离线合并更新 `core_memory`** | 通过与 `main.py` 并列的主入口 `Bot/merge_memory.py` 手动离线执行，将自动生成的快照条目按批去重归并进 `core_memory.md`（覆盖前先备份旧版至 `archive/`；任一批失败则全或无中止） | `python merge_memory.py`、`memory.merge_batch_size`、`memory.memory_model` |
| **4. 对话历史压缩** | 用已提取的记忆摘要替换若干保存周期前的历史片段，抑制长对话 Token 膨胀（`RLock` 原子提交，不覆盖并发对话） | `memory.compact_history_after_save` |

**四层注入与 RAG 向量检索**：
- **注入位置**：长期记忆不再拼接到 `system_prompt`（否则日期变化会导致整条 Prompt Cache 作废），而是注入**当轮用户消息**的记忆块中。
- **裁剪与恒等式**：`[长期]` 与 `[标记]` 段恒定置顶、永不丢弃；其余日常记忆按天为单位构建常驻窗口——现实日期最近 `min_recent_days` 天无条件保底，再按 `recent_days` 与 `max_recent_entries` 整日累加；超出 `injection_max_chars` 时按条目从最老起剔除。
- **三种旧事召回**：① 本地 BGE 语义路由命中 `recall_memory` 意图；② 模型自主调用 `memory_recall` 工具；③ 本轮未触发主动检索时按概率随机"想起"一条窗外旧记忆（常驻稀疏时改用更高的 `random_probability_sparse`）。
- **三态模式 `memory.retrieval.mode`**：`'off'`（仅受总字数约束的全量注入）、`'observe'`（注入结果与 `off` 逐字节一致，但后台记录检索命中用于调参对比）、`'on'`（正式启用常驻窗口 + 向量检索）。检索开启但 BGE 模型不可用时**不静默回退全量**，仅注入常驻窗口并打明确告警。
- **共享嵌入器与索引**：与语义意图路由共用同一个 CPU `BGEOnnxEmbedder` 实例（`Bot/services/embedding/bge_onnx.py`）；索引文件存于 `Bot/memory/data/index/`，在执行离线合并更新 `core_memory` 时重建，启动时做 sha256 校验。`Bot/merge_memory.py` 读取 `Bot/config.yaml` 后独立完成合并并退出（无需 Core 服务在线，`memory.enable: false` 时直接退出），也可直接挂到系统计划任务定时运行：
  ```powershell
  conda activate MyAI
  cd Bot
  python merge_memory.py
  ```

#### 语音链路、TurnSense 与 TTS 调度

- **TurnSense 滑窗优化**（`Bot/bot_asr_controller.py` & `Bot/pipeline/asr/turnsense.py`）：每次仅将最近 `window_audio_sec`（默认 `4.0` 秒）的音频送入 TurnSense 做端点判定，将长语音分片判断延迟压到常数级（而用于判断是否满足 TTS 打断门槛的真实说话时长仍单独累计，不受滑窗截断影响）。
- **流式分句与实时打断**（`Bot/bot_tts_scheduler.py`）：纯文本 `LLM` 与多模态 `MLLM` 共享统一的流式状态机，边接收流式文本、边剥离句首 `<emotion>` 标签与伪思考标签、边按标点切句送入 TTS；支持自适应语速、以及打断/中止预热时向 Core `POST /tts/abort` 发送非阻塞中止指令。

#### 工具运行时、生态能力与 Harness 观测

- **通用工具调度**：`ToolRuntime`（线程安全注册、Schema 校验、超时控制）+ `ToolCoordinator`（支持同一 Turn 内多轮工具调用，执行期新消息通过 `buffered_merge` 缓冲合并）。
- **联网与研究**：Core `web_search` 网关 + Bot 侧 `BrowserService`（受 SSRF 防护保护的静态正文抓取或对接 Playwright MCP，支持分段阅读与筛选后归档进 `Sandbox/research/`）。
- **Skills、MCP 与文件沙箱**：
  - **Skills**（`Bot/services/skill_framework/`）：兼容 `SKILL.md` 格式，支持用户 `/skill-name` 显式调用及模型自主创建（需 `allow_model_create: true`）。
  - **MCP**（`Bot/services/mcp/`）：通过 `Bot/MCP/servers.json`（模板见 `servers.example.json`）接入远程 Streamable HTTP MCP 服务，支持工具白名单、资源读取、JSON/SSE 响应（暂不支持 `stdio`）。
  - **文件沙箱**（`Bot/services/sandbox/`）：限定在 `Bot/Sandbox/` 内进行列目录、读、写、编辑、复制、重命名与建目录，拒绝目录穿越、符号链接/硬链接与保留设备名，不开放删除或 Shell 执行。
- **Harness 诊断与工具回读**（`Bot/docs/HARNESS_LOGGING.md`）：
  - 每次模型调用、工具执行、协议错误均以 JSONL 落盘至 `Bot/logs/harness.jsonl`（含真实厂商 Token 用量与缓存命中明细），可通过 `Bot/tools/harness_report.py` 生成分析看板。
  - **`harness_recall` 回读工具**（`Bot/services/harness_recall/`）：解决多轮工具调用结果不写历史导致的"后轮忘前轮"问题——严格限定只扫描**本次进程启动（`boot_offset`）之后且属于当前 `conversation_id`** 的记录，从文件尾按 256 KiB 分块倒扫预筛，先返回带序号索引再按 `ordinal` 取全文。

#### 四条别破坏的设计约束

1. **System Prompt 前缀恒定**：提示缓存依赖前缀字节完全匹配，任何动态时间、桌面/屏幕环境、记忆块、工具轮数提示、工具结果一律放进**当轮用户消息**（`Bot/manager/context_manager.py`）。
2. **不估算 Token**：只记录服务商返回的真实 `usage`，缺失则标 `unknown` / `null`（`condata.harness.usage_dict`）。
3. **外部内容先中和**：网页正文、MCP 返回值、沙箱文件内容进上下文前先经 `neutralize_markers()` 中和伪工具标记形状，防止间接提示注入。
4. **出站网络防护**：`BrowserService` 与 `MCPService` 共用 `Bot/services/net_safety.py`，逐跳校验重定向、拒绝内网/环回/保留地址并固定已解析 IP（DNS Pinning）。

---

### 4.4 项目目录结构

```text
.
├─ .github/workflows/
│  └─ bot-tests.yml                  # GitHub Actions CI：自动执行 Bot 离线回归测试
├─ Bot/                              # 机器人本体（纯 CPU 客户端）
│  ├─ main.py                        # 主入口 1：按配置启动桌宠界面或纯控制台对话
│  ├─ merge_memory.py                # 主入口 2（与 main.py 并列）：手动离线合并快照条目、更新 core_memory
│  ├─ bot.py                         # 组合根门面与配置热重载（reload_config）
│  ├─ bot_turn_orchestrator.py       # 对话轮次编排（上下文组装、推理重试、工具调度）
│  ├─ bot_asr_controller.py          # 语音输入主控（TurnSense 滑窗、流式 ASR、桌面音频让路）
│  ├─ bot_tts_scheduler.py           # TTS 调度主控（启动预热、流式分句、自适应语速、打断）
│  ├─ bot_vision_controller.py       # 视觉与主动搭话主控（VLM 轮询、OCR/ImgCap 降级）
│  ├─ bot_event_binder.py            # 事件总线监听绑定与热重载分发
│  ├─ config.py                      # BotConfig 配置模型（首次运行据此生成 config.yaml）
│  ├─ config.example.yaml            # 脱敏样例配置（首次自动生成的才是最新权威）
│  ├─ manager/                       # 配置读写 / 上下文（保 Cache 前缀）/ 提示词管理
│  ├─ memory/                        # 四层长期记忆（快照、全或无合并、core_memory、RAG 检索）
│  ├─ pipeline/                      # asr / llm / mllm / tts / ocr / imgcap / vlm HTTP 客户端
│  ├─ services/                      # tool_runtime · tool_coordinator · tool_model_gateway
│  │                                 # skill_framework · mcp · sandbox · browser · web_search
│  │                                 # intent_router · microphone · net_safety
│  │                                 # embedding · memory_recall · harness_recall
│  ├─ ui/                            # PySide6 桌宠壳与设置中心
│  │  ├─ desktop_shell.py / pet_window.py / chat_window.py / bridge.py / theme.py
│  │  ├─ mascot/                     # 三模渲染：矢量猫娘 / 立绘包 / Live2D + 内置预设
│  │  └─ settings/                   # 六大分类全量图形设置中心
│  ├─ devices/                       # 麦克风、扬声器、全局热键、屏幕捕获、无头探测
│  ├─ resources/mascot/              # 内置 Q 版立绘预设（gemini_q / deepseek_winter）
│  ├─ resources/prompts/             # TTS 参考音频目录（音频需自备，见 4.2）
│  ├─ models/                        # 本地 ONNX 模型目录（仅 BGE tokenizer 入库，权重自备）
│  ├─ skills/                        # Skill 指令目录（SKILL.md）
│  ├─ MCP/                           # MCP 服务配置样例（servers.example.json）与只读资料
│  ├─ Sandbox/                       # 沙箱工作区
│  ├─ tools/                         # harness_report.py（观测看板）、scan_api_surface.py
│  ├─ docs/                          # 工具/Skill 开发指南与 Harness 日志规范
│  └─ tests/                         # 离线回归测试（不依赖 Core 与 GPU）
├─ Core/                             # 模型服务层
│  ├─ starter.py                     # 单服务启动入口（MODEL_REGISTRY + 配置 entry 动态加载）
│  ├─ agent-stack.cmd / .ps1         # Windows 多服务启动器
│  ├─ agent_stack.py                 # 跨平台多服务启动器（标准库）
│  ├─ agent-stack/                   # registry.json 服务注册表与运行时日志
│  ├─ config.example.yaml            # Core 样例配置（含顶部 Startup 开关段）
│  ├─ asr/ llm/ mllm/ tts/ ocr/ img_cap/ web_search/   # 各服务端模块实现
│  ├─ GPT-SoVITS/                    # GPT-SoVITS 本地副本 + zongzi_api.py（含 /tts/abort）
│  └─ models/                        # 本地 ASR 权重目录（不入库，需自建）
├─ con-datapack-1.11/                # 共享数据协议与 Harness 包（导入名 condata，须 pip install -e）
├─ docs/                             # 开发维护文档（共用方法、重构计划、潜在优化点、开发笔记）
├─ generate_env_snapshots.py         # Conda 环境快照导出脚本
└─ 环境快照/                         # 4 个 Conda 环境的基底快照（environment-*.yml）
```

---

### 4.5 开发、测试与扩展

#### 离线回归测试

所有单元测试均为**纯离线用例**，不依赖 Core 服务在线或 GPU，且已接入 GitHub Actions CI（[`.github/workflows/bot-tests.yml`](.github/workflows/bot-tests.yml)）：

```powershell
conda activate MyAI
python -m pytest Bot/tests -c Bot/tests/pytest.ini       # 全量离线套件：当前收集 252 个用例

# 快速运行核心套件（p0 冒烟 + 全部 p1，共 203 个用例）
python -m pytest Bot/tests/test_p0_smoke.py Bot/tests/test_p1_security.py `
                 Bot/tests/test_p1_voice.py Bot/tests/test_p1_desktop.py `
                 Bot/tests/test_p1_harness.py Bot/tests/test_p1_prompt.py `
                 Bot/tests/test_p1_pseudo_markers.py Bot/tests/test_p1_llm_openai_compat.py `
                 Bot/tests/test_p1_model_id_freedom.py Bot/tests/test_p1_memory_retrieval.py -q
```

> `Bot/tests/pipeline/` 下为需要真实 Core 服务在线的人工联调脚本，已在 `Bot/tests/pytest.ini` 的 `norecursedirs` 中默认排除。随时统计最新用例数可运行 `python -m pytest Bot/tests --collect-only -q`。

#### 常用开发者工具

- **Harness 调用与缓存命中率看板**：
  ```powershell
  python Bot/tools/harness_report.py --out -
  ```
- **第三方包 API 面静态扫描**（升级 Python 或依赖前用于核查实际调用面）：
  ```powershell
  python Bot/tools/scan_api_surface.py --out api_surface.md
  ```

#### 扩展与二次开发文档

- 开发入口与全仓库可复用 API 速查 → [`docs/共用方法.md`](docs/共用方法.md)
- 新增 AI 工具规范 → [`Bot/docs/TOOLS_CREATION_GUIDE.md`](Bot/docs/TOOLS_CREATION_GUIDE.md)
- 新增 Skill 规范 → [`Bot/docs/SKILLS_CREATION_GUIDE.md`](Bot/docs/SKILLS_CREATION_GUIDE.md)
- Harness 日志与 `harness_recall` 细节 → [`Bot/docs/HARNESS_LOGGING.md`](Bot/docs/HARNESS_LOGGING.md)
- Core 服务注册与启动器细节 → [`Core/agent-stack/README.md`](Core/agent-stack/README.md)

---

### 4.6 已知限制

- **主力仅在 Windows 验证**：Core 服务端 Python 代码与 `Core/agent_stack.py` 跨平台，但 Bot 的音频采集、桌面热键与桌宠 GUI 仅在 Windows 10/11 上完整实测；`agent-stack.cmd`/`.ps1` 仅限 Windows。
- **SearXNG 须自行部署**：不安装 SearXNG 时，`web_search` 的 `search` 和 `news` 不可用（天气/Steam/GitHub/汇率仍可用）。
- **多环境拆分**：由于不同视觉/语音库对 `torch` 与 `numpy` 的互斥要求，本地全链路最少需要 3 个独立 Conda 环境。
- **`flash-attn` 在 Windows 上安装门槛高**：走 Qwen3-ASR 路线需自备精确匹配 `cp312 + torch2.8.0+cu128` 的预编译轮子，建议普通用户直接用 `sherpa-onnx` 或云端 ASR。
- **记忆 RAG 阈值需人工微调**：`memory.retrieval` 的召回相似度阈值与窗口天数目前为人工配置项，建议先用 `mode: 'observe'` 观察命中质量。
- **MCP 暂不支持 `stdio` 传输**：目前仅支持远程 Streamable HTTP 协议。
- **TTS 打断的粒度边界**：`Core/GPT-SoVITS/zongzi_api.py` 为线程安全通过 `_INFER_LOCK` 串行合成，现已提供 `POST /tts/abort` 接口在打断或正式合成到达时置位 `stop_flag` 跳出循环、尽快释放锁；但已经进入推理的那一个当前分句仍需执行完当前步。
- **桌面音频防自激**：同时启用 `desktop_audio` 捕获与本地 TTS 时，必须配合虚拟声卡（如 Voicemeeter）隔离通道，否则猫娘会听见自己的声音循环回复。
- **Conda 导出缺陷**：部分 conda 版本执行 `conda env export -n` 会静默导出 base 环境，请始终使用仓库根目录 `generate_env_snapshots.py`。

---

## License 与来源

本项目以 **MIT License** 发布，见 [`LICENSE`](LICENSE)（Copyright © 2026 STEXxx）。

### 内嵌第三方组件

| 组件 | 来源 | 许可 | 声明文件 |
|---|---|---|---|
| `Core/GPT-SoVITS/`（TTS 本地副本 + `zongzi_api.py`） | [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | MIT © 2024 RVC-Boss | [`LICENSE`](Core/GPT-SoVITS/LICENSE)、[`SOURCE.md`](Core/GPT-SoVITS/SOURCE.md) |
| `Bot/napcat/` | [NapNeko/NapCatQQ](https://github.com/NapNeko/NapCatQQ) | ⚠️ 上游未提供许可证文件，**不随本仓库发布** | —— |

### 早期骨架版权声明（保留自上游 MIT 项目）

| 本项目目录 | 上游项目 | 许可 | 声明文件 |
|---|---|---|---|
| `Bot/`（事件骨架、管线协议） | [AkagawaTsurunaki/ZerolanLiveRobot](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot) | MIT © 2024 AkagawaTsurunaki | [`Bot/NOTICE`](Bot/NOTICE) |
| `Core/`（模型服务层） | [AkagawaTsurunaki/zerolan-core](https://github.com/AkagawaTsurunaki/zerolan-core) | MIT © 2024 AkagawaTsurunaki | [`Core/LICENSE`](Core/LICENSE) |
| `con-datapack-1.11/`（数据协议包，导入名 `condata`） | [AkagawaTsurunaki/zerolan-data](https://github.com/AkagawaTsurunaki/zerolan-data) | MIT © 2024 AkagawaTsurunaki | [`con-datapack-1.11/LICENSE`](con-datapack-1.11/LICENSE) |

部分源文件头部保留了原作者的 `Author: AkagawaTsurunaki` 注释，同属 MIT 署名要求。

### 线上协议与使用须知

- `condata.protocol.protocol` 中的 `PROTOCOL_NAME = "ZongziProtocol"` 为线上通信格式标识，修改会导致旧协议端无法通信。
- 本项目会采集麦克风与桌面音频、截取屏幕画面、在沙箱目录读写文件并发起出站网络请求，请在明确知晓上述行为的前提下合理合规使用。

---

## Contact

| 渠道 | 地址 | 用途 |
|---|---|---|
| 📺 **Bilibili** | <https://space.bilibili.com/35778513> | 演示视频、效果展示与更新动态 —— 想先看实际效果看这里 |
| 🐛 **Issue** | 本仓库 Issues 页面 | 报 Bug、提建议、交流折腾心得（附 `Bot/logs/` 日志片段可大幅加快排查） |
| ⭐ **Star** | 本仓库右上角 | 如果项目帮到了你或带来了灵感，欢迎点一颗 Star 支持一下 |

> 权重等不随仓库发布的大文件（ASR / TTS / TurnSense / BGE），有需要可以在 B 站私信或 Issue 里找我。
