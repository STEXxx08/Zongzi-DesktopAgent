# con-datapack（导入名 `condata`）

![Static Badge](https://img.shields.io/badge/Python-3.10%2B-blue) ![Static Badge](https://img.shields.io/badge/License-MIT-orange)

本项目（Zongzi）各模块之间共享的数据协议包：用 `pydantic.BaseModel` 定义
Bot 与 Core 之间、以及各模型服务之间交换的全部消息与数据结构，带严格 schema 校验。

> **来源**：本包的早期骨架来自一个 MIT 许可的开源数据协议包
> （MIT License，Copyright (c) 2024 AkagawaTsurunaki），其原始版权声明保留在 [LICENSE](LICENSE) 中。
> 本项目在其基础上重写包结构，并新增了 MLLM、web_search、调用观测（harness）等数据定义。

## 安装

本包**不在 PyPI**，必须从源码以可编辑模式安装（Bot 与 Core 的每个 conda 环境都要装）：

```shell
pip install -e con-datapack-1.11
```

安装后导入名是 `condata`（发行名 `con-datapack`）。

## 目录结构

```
src/condata
├─ harness.py                 # 调用观测：CallSpan / trace_model_call / usage normalize
├─ data
│  ├─ prompt.py               # TTS 提示（参考音频标签）
│  └─ state.py                # 服务状态
├─ pipeline                   # 各类模型服务的请求 / 响应定义
│  ├─ abs_data.py             # 抽象 query / prediction 基类
│  ├─ asr.py                  # 语音识别
│  ├─ defense.py              # 防御模型（⚠️ 见下方说明）
│  ├─ img_cap.py              # 图像描述
│  ├─ llm.py                  # 大语言模型（含 Conversation / RoleEnum）
│  ├─ mllm.py                 # 多模态大语言模型
│  ├─ ocr.py                  # 光学字符识别
│  ├─ tts.py                  # 文本转语音
│  └─ web_search.py           # 联网搜索 / 新闻 / 天气 / Steam / GitHub / 汇率
└─ protocol
   └─ protocol.py             # ZongziProtocol：JSON + WebSocket 消息对象
```

> ⚠️ `condata/pipeline/defense.py` 是上游遗留模块，内部仍 `from data.pipeline.abs_data import ...`，
> 在当前包结构下导入会失败。它未被本项目使用，保留仅为减少与上游的差异。

## 文档

接口字段说明见 [`docs/doc_en.md`](docs/doc_en.md)。
该文档由 `docs/scripts/doc_gen.py` 生成——注意该脚本当前会因 `defense.py` 的导入问题中断，
需要时先修掉该模块再重新生成。

## License

MIT，见 [LICENSE](LICENSE)。原始版权声明（Copyright (c) 2024 AkagawaTsurunaki）与
本项目新增部分的声明（Copyright (c) 2026 STEXxx）均在该文件中。
