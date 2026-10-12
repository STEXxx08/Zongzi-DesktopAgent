# 本目录来源与改动说明

本目录是 **GPT-SoVITS** 的本地副本，供本项目作为 TTS 后端使用。

| 项 | 说明 |
|---|---|
| 上游项目 | [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) |
| 许可 | MIT，Copyright (c) 2024 RVC-Boss，完整声明见本目录 [`LICENSE`](LICENSE) |
| 用途 | 由 `Core/agent-stack/registry.json` 的 `tts` 服务拉起，对外暴露 `127.0.0.1:11002` |
| 启动命令 | `python zongzi_api.py -a 127.0.0.1 -p 11002`（工作目录即本目录） |
| conda 环境 | `GPTSoVits` |

## 本项目做的改动

1. **新增/改写了对外 API 服务**：`zongzi_api.py`。它按本项目的统一管线协议
   （`condata.pipeline.tts`）暴露 `/tts/predict` 与 `/tts/stream-predict`，
   供 Bot 与 Core 之间用统一接口通信。
2. **调整了内部设置以兼容较新显卡**：原版对较新代显卡型号识别失败，本项目改写了
   SoVITS 内部的设备识别设置。
3. **`environment.yaml`**：移除了上游已废弃的 `zerolan-data` 依赖，改为安装本仓库内的
   本地协议包 `con-datapack`（导入名 `condata`）。

## 注意

- 本目录**不是 git 子模块**，是直接复制进来的副本。若需更新上游代码，请自行比对合并。
- 本目录**只随仓库发布代码**。模型权重（`GPT_SoVITS/pretrained_models/`）与文本前端词典
  （`GPT_SoVITS/text/` 下的 `G2PWModel/`、`cmudict*.rep`、`ja_userdic/` 等）需按根目录 README §3.8 自行下载。
- 本目录**只随仓库发布代码**。模型权重（`GPT_SoVITS/pretrained_models/`）与文本前端词典
  （`GPT_SoVITS/text/` 下的 `G2PWModel/`、`cmudict*.rep`、`ja_userdic/` 等）需按根目录 README §3.8 自行下载。
