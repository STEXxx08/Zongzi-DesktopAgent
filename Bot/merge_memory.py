# 独立合并脚本，可手动运行或设置 crontab 定时执行（例如每日凌晨2点）

import sys
from pathlib import Path
from loguru import logger

# 确保项目路径在 sys.path 中（如果直接运行脚本）
_project_root = Path(__file__).parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from manager.config_manager import get_config

try:
    from manager import MemoryManager, resolve_memory_model_configs
except ImportError:
    from memory import MemoryManager
    from memory.manager import resolve_memory_model_configs


def main():
    try:
        # 加载全局配置
        config = get_config()
        if not config.memory.enable:
            logger.warning("Memory feature is disabled in config. Exiting.")
            return

        # 初始化 MemoryManager（支持记忆提取/合并使用单独模型配置）
        memory_model_configs = resolve_memory_model_configs(config)
        mgr = MemoryManager(
            config=config.memory,
            **memory_model_configs,
        )

        # 执行合并
        success = mgr.merge_all()
        if success:
            logger.info("✅ Memory merge completed successfully.")
        else:
            logger.error("❌ Memory merge failed. Check logs above.")

        # 清理资源
        mgr.shutdown()

    except KeyboardInterrupt:
        logger.info("Merge interrupted by user.")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()