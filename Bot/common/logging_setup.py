"""应用日志统一配置（P1-26）。

背景：全仓有 385 处 loguru 调用和 11 个模块使用标准 logging，但没有任何
`logger.add` / `logging.basicConfig`，导致：
- loguru 只有默认 stderr，没有文件、没有轮转、级别固定为 DEBUG（噪声大）；
- 标准 logging 的消息被 logging 自己的 lastResort handler 输出，格式与 loguru 不一致。

本模块做两件事：
1. 给 loguru 装两个 sink：带轮转的文件 + 屏幕（可用环境变量调整级别）；
2. 把标准 logging 的记录桥接到 loguru，使两类日志进同一份文件、同一套格式。
"""

import logging
import os
import sys
from pathlib import Path

from loguru import logger

_configured = False


def _console_level() -> str:
    return (os.environ.get("BOT_LOG_LEVEL") or "INFO").strip().upper() or "INFO"


def _file_level() -> str:
    return (os.environ.get("BOT_FILE_LOG_LEVEL") or "DEBUG").strip().upper() or "DEBUG"


class _LoguruInterceptHandler(logging.Handler):
    """把标准 logging 的记录转交给 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_app_logging(project_root: Path | None = None, *, force: bool = False) -> None:
    """安装 loguru sink 并桥接标准 logging；重复调用只生效一次。"""
    global _configured
    if _configured and not force:
        return

    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parent
    log_dir = root / "logs"

    logger.remove()
    if sys.stderr is not None:
        logger.add(sys.stderr, level=_console_level(), backtrace=False, diagnose=False,
                   enqueue=False, colorize=False)

    # 日志文件先探测可写性：目录建不出来或写不进去时只保留屏幕输出，
    # 避免后台 sink 线程在之后每次写日志时抛异常。
    if log_dir is not None:
        log_path = log_dir / "bot.log"
        writable = True
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8"):
                pass
        except OSError as exc:
            writable = False
            logger.warning(f"日志文件 {log_path} 不可写：{exc}；仅使用屏幕输出。")
        if writable:
            logger.add(
                log_path,
                level=_file_level(),
                rotation="10 MB",
                retention="14 days",
                encoding="utf-8",
                enqueue=True,
                backtrace=False,
                diagnose=False,
            )

    # 标准 logging -> loguru：替换已有 handler，避免同一行输出两次
    intercept = _LoguruInterceptHandler()
    logging.basicConfig(handlers=[intercept], level=logging.INFO, force=True)
    for name in list(logging.root.manager.loggerDict):
        logging.getLogger(name).handlers = [intercept]
    logging.getLogger().handlers = [intercept]

    _configured = True
