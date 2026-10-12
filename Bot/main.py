import asyncio
import os
import sys
import threading
from bot import Bot
from loguru import logger
from manager.config_manager import get_config

# 强制退出兜底：正常收尾会取消这个看门狗；只有收尾卡死（例如阻塞在打不断的
# C 扩展或没有超时的 HTTP 调用里）才会走到这里，避免进程残留。
_EXIT_WATCHDOG_SECONDS = 60.0


def _arm_exit_watchdog():
    def _force_exit():
        logger.error(
            f"⏱️ 退出收尾超过 {_EXIT_WATCHDOG_SECONDS:.0f} 秒仍未完成，强制结束进程。"
            "（最后一次记忆快照可能未落盘）"
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    watchdog = threading.Timer(_EXIT_WATCHDOG_SECONDS, _force_exit)
    watchdog.daemon = True
    watchdog.start()
    return watchdog


async def _run_console_bot(bot: Bot) -> int:
    stop_reason = "normal"
    try:
        await bot.start()
    except KeyboardInterrupt:
        stop_reason = "keyboard_interrupt"
        logger.info("收到 KeyboardInterrupt，正在走正常收尾流程。")
        bot.request_stop()
    except Exception as exc:
        stop_reason = "error"
        logger.exception(exc)

    # 无论正常结束、Ctrl+C 还是异常，都执行 bot.stop()：
    # 这一步包含最后一次记忆快照落盘、音频采集停止、MCP / web_search / TTS 线程池关闭。
    watchdog = _arm_exit_watchdog()
    try:
        # shield：Ctrl+C 之类的外部取消不能打断收尾（否则记忆最后一次落盘会被跳过）
        await asyncio.shield(asyncio.ensure_future(bot.stop()))
    except asyncio.CancelledError:
        logger.warning("收尾过程收到取消信号，等待强制退出兜底。")
    except Exception as exc:
        logger.exception(f"退出收尾阶段出错：{exc}")
    finally:
        watchdog.cancel()

    if stop_reason == "error":
        logger.error("❌️ Bot exited abnormally!")
        return 1
    return 0


if __name__ == '__main__':
    config = get_config()
    chat_window_cfg = config.service.chat_window
    desktop_cfg = config.service.desktop_shell

    # 桌宠界面由 service.desktop_shell.enable 决定（此前误读 chat_window.enable）；
    # chat_window.enable 只控制"聊天窗是否显示"，在桌面外壳内部生效。
    # （该项原名 service.text_input_gui，已更名；旧配置仍能加载，见 services/config.py）
    if desktop_cfg.enable:
        try:
            from ui.desktop_shell import run_desktop_shell
        except ModuleNotFoundError as exc:
            if exc.name == "PySide6":
                raise SystemExit(
                    "桌宠界面需要 PySide6，请安装 Bot/requirements-ui.txt；"
                    "或把 service.desktop_shell.enable 设为 false 以纯控制台方式运行。"
                ) from exc
            raise

        run_desktop_shell(
            bot_name=config.character.bot_name,
            title=chat_window_cfg.title,
            width=chat_window_cfg.width,
            height=chat_window_cfg.height,
            font_size=chat_window_cfg.font_size,
            input_font_size=chat_window_cfg.input_font_size,
            skip_tts=chat_window_cfg.skip_tts,
            always_on_top=desktop_cfg.always_on_top,
        )
    else:
        # 纯控制台方式（桌宠界面已关闭）
        sys.exit(asyncio.run(_run_console_bot(Bot())))
