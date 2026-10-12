"""Bot 测试的公共准备：把 Bot 应用目录加入 sys.path。

历史测试文件都按“从 Bot 目录运行”的假设写（`import common...` / `import manager...`），
从仓库根运行 pytest 时会全部 ImportError。这里统一修正，使两种运行方式都能收集到测试。

注意：历史上本目录在 .gitignore 中被忽略，但它是**入库的**（`Bot/tests/` 下文件随仓库发布）。
因此这里的用例必须保持"离线可跑"，不要引入对已删除模块（`services.live2d` / `services.obs` /
`services.playground` / `agent.custom_agent`）或他人本机绝对路径的引用。
"""

import sys
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parents[1]
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

# 用例会经 manager.config_manager.get_config() 读 Bot/config.yaml；该文件不入库，
# 缺失时 get_config() 会生成一份配置后 raise SystemExit(0)，让第一个碰配置的用例直接报错。
# 这里在收集阶段先补一份默认配置，使干净克隆上的 `pytest Bot/tests` 也能直接跑通。
_CONFIG_PATH = _BOT_DIR / "config.yaml"
if not _CONFIG_PATH.exists():
    from common.generator.config_gen import ConfigFileGenerator
    from config import BotConfig

    _CONFIG_PATH.write_text(
        ConfigFileGenerator().generate_yaml(BotConfig()), encoding="utf-8"
    )
