# conda 环境快照（带版本）

这四份文件是从**实际运行环境**导出的快照，作用是给 GPU 相关的依赖混乱留一个**可重建、可回滚的基线**：
`torch` / `funasr` / `sherpa-onnx` / `flash-attn` 这些是手工调过的版本，靠 `pip install -r requirements.txt` **装不出同一套**。

| 文件 | 环境 | 用途 | conda 包 | 剔除的 pip 包 |
|---|---|---|---|---|
| `environment-MyAI.yml` | `MyAI` | Bot 主程序（`cd Bot && python main.py`） | 24 | 223 |
| `environment-qwen3-asr.yml` | `qwen3-asr` | Core ASR 服务（11001） | 24 | 107 |
| `environment-llm_deepseek_online.yml` | `llm_deepseek_online` | Core LLM / MLLM / llm-memory / web_search（11000 / 10010 / 11012） | 25 | 85 |
| `environment-GPTSoVits.yml` | `GPTSoVits` | Core TTS 服务（11002，工作目录 `Core/GPT-SoVITS`） | 24 | 193 |

## 怎么重建

```shell
conda env create -f environment-MyAI.yml
# 然后补 pip 依赖（这些条目不进快照，见下）
conda activate MyAI
pip install -r ../Bot/requirements.txt
pip install -e ../con-datapack-1.11     # 共享协议包 condata，必须手动装
```

换机重建的顺序建议：**先建 `llm_deepseek_online`（服务最多）→ 再 `qwen3-asr` → 最后 `MyAI`**。

## 为什么快照里没有 pip 包

导出脚本刻意剔除了 `pip:` 段，原因有两条：

1. pip 依赖以 `Bot/requirements.txt` 与 `Core/requirements-<环境名>.txt`（见 `Core/requirements.txt` 里的对应表）为准，两处都写会导致**改一处漏一处**；
2. 重建时混用 conda 与 pip 的同一包（尤其 numpy / torch）容易把 GPU 版本覆盖成 CPU 版本。

## 已知问题

部分 conda 版本（实测 25.7.0）上，`conda env list` 会因内部报错
（`AttributeError: 'Index' object has no attribute '_system_packages'`）返回空列表。
后果：`conda env export -n <名字>` 会**静默导出 base 环境**（输出看着正常，内容却是错的）。
所以导出脚本用 `-p <环境路径>` 定位，并在导出前校验目录存在——**重新导出请用 `generate_env_snapshots.py`，不要手敲 `-n`。**

## 重新生成

```shell
python generate_env_snapshots.py                     # 更新全部四份
python generate_env_snapshots.py -n MyAI             # 只更新一个
python generate_env_snapshots.py --out Core/envs     # 换输出目录
```

快照会随依赖升级而过期；**装机/大版本升级后请重新生成并提交**，否则基线会误导下一个排查 GPU 版本的人。
