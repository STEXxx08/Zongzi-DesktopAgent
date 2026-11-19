"""桌面陪伴壳层主控：组合桌宠窗口、聊天窗口、托盘、配置中心与 Bot 后台生命周期。"""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSystemTrayIcon
from loguru import logger

from manager.config_manager import get_config, save_bot_settings, save_desktop_settings
from services.config import DesktopShellConfig
from ui.bridge import BotRunner, BotUiBridge
from ui.chat_window import ChatWindow
from ui.mascot import MascotCanvas, create_mascot_tray_icon, resolve_preset
from ui.pet_window import PetWindow, SpeechBubble
from ui.theme import MESSAGE_BOX_QSS, _ui_font, create_styled_menu

__all__ = [
    "BotUiBridge",
    "BotRunner",
    "MascotCanvas",
    "ChatWindow",
    "SpeechBubble",
    "DesktopSettingsDialog",
    "PetWindow",
    "DesktopShell",
    "_ui_font",
    "run_desktop_shell",
]


def __getattr__(name: str):
    if name == "DesktopSettingsDialog":
        from ui.settings import DesktopSettingsDialog

        globals()["DesktopSettingsDialog"] = DesktopSettingsDialog
        return DesktopSettingsDialog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class DesktopShell(QObject):
    """桌面陪伴界面的生命周期与窗口协调中心。"""

    def __init__(
        self,
        bot_name,
        title,
        width,
        height,
        font_size,
        input_font_size,
        skip_tts,
        always_on_top,
    ) -> None:
        super().__init__()
        self._app = QApplication.instance() or QApplication(sys.argv[:1])
        self._app.setQuitOnLastWindowClosed(False)
        self._bridge = BotUiBridge()
        self._runner = BotRunner()
        self._bridge.set_mic_state_provider(self._runner.is_microphone_recording)
        self._exiting = False
        self._restart_after_stop = False
        self._restart_launched = False

        config = get_config()
        desktop_config = config.service.desktop_shell
        # 退出看门狗：只有收尾彻底卡死才会开火，正常收尾会在 _on_bot_finished 里取消。
        self._exit_grace_ms = int(float(getattr(desktop_config, "exit_grace_seconds", 45.0)) * 1000)
        self._exit_watchdog = QTimer(self)
        self._exit_watchdog.setSingleShot(True)
        self._exit_watchdog.timeout.connect(self._force_exit_on_timeout)

        self._chat_window = ChatWindow(
            bot_name,
            title,
            width,
            height,
            font_size,
            input_font_size,
            skip_tts,
            self._bridge,
            enabled=config.service.chat_window.enable,
            on_toggle_mic=self._toggle_microphone,
            on_open_settings=self.open_settings,
            avatar_mode=getattr(desktop_config, "avatar_mode", "vector"),
            avatar_path=getattr(desktop_config, "avatar_path", ""),
        )
        self._pet_window = PetWindow(
            bot_name,
            self._chat_window,
            self._bridge,
            self.request_exit,
            self.open_settings,
            always_on_top,
            desktop_config.bubble_offset_x,
            desktop_config.bubble_offset_y,
            desktop_config=desktop_config,
            on_toggle_mic=self._toggle_microphone,
            on_toggle_topmost=self._toggle_always_on_top,
            on_switch_avatar_mode=self._switch_avatar_mode,
            on_reset_position=self._move_pet_to_default_position,
        )
        self._tray = None
        self._create_tray(bot_name)
        self._restore_pet_position(desktop_config)
        self._runner.ready.connect(self._on_bot_ready)
        self._runner.failed.connect(self._bridge.report_error)
        self._runner.finished.connect(self._on_bot_finished)
        self._pet_window.show()
        self._runner.start()

    def _toggle_microphone(self, enabled: bool) -> None:
        actual = self._runner.set_microphone_enabled(enabled)
        self._pet_window.set_microphone_active(actual)
        self._chat_window.set_microphone_active(actual)
        if enabled and not actual:
            self._pet_window.set_state("麦克风未启用")

    def _toggle_always_on_top(self, enabled: bool) -> None:
        try:
            config = get_config()
            save_bot_settings({"service": {"desktop_shell": {"always_on_top": bool(enabled)}}})
            self._pet_window.set_always_on_top(bool(enabled))
            logger.info("已切换桌宠置顶状态：{}（{}）", enabled, config.character.bot_name)
        except Exception as exc:
            logger.warning("保存置顶状态失败（仅当前会话生效）：{}", exc)

    def _switch_avatar_mode(self, preset_key: str) -> None:
        try:
            current_path = get_config().service.desktop_shell.avatar_path
            mode, path = resolve_preset(preset_key, current_path)
            parsed = save_bot_settings(
                {"service": {"desktop_shell": {"avatar_mode": mode, "avatar_path": path}}}
            )
            self._pet_window.apply_desktop_config(parsed.service.desktop_shell)
            self._chat_window.set_avatar(
                parsed.service.desktop_shell.avatar_mode,
                parsed.service.desktop_shell.avatar_path,
            )
            fallback = self._pet_window._mascot.fallback_message
            if fallback:
                self._show_message_box(QMessageBox.Icon.Information, "形象切换提示", fallback)
        except Exception as exc:
            logger.exception("Unable to switch avatar mode")
            self._show_message_box(QMessageBox.Icon.Warning, "切换失败", f"未能切换形象模式：{exc}")

    def _default_pet_position(self):
        """桌宠默认位置：主屏右下角内缩 24 像素。没有屏幕信息时返回 None。"""
        screen = self._app.primaryScreen()
        if screen is None:
            return None
        available = screen.availableGeometry()
        return (
            available.right() - self._pet_window.width() - 24,
            available.bottom() - self._pet_window.height() - 24,
        )

    def _move_pet_to_default_position(self):
        """把桌宠移回默认位置（设置对话框或菜单的"回到默认位置"）。"""
        position = self._default_pet_position()
        if position is None:
            return False
        self._pet_window.move(*position)
        logger.info(f"桌宠已回到默认位置：({position[0]}, {position[1]})")
        return True

    def _restore_pet_position(self, desktop_config):
        """恢复上次保存的桌宠位置；没有记录或已不在可见范围内时落到主屏右下角。"""
        saved_x = int(getattr(desktop_config, "pet_x", -1))
        saved_y = int(getattr(desktop_config, "pet_y", -1))
        if saved_x >= 0 and saved_y >= 0:
            if self._position_visible(saved_x, saved_y):
                self._pet_window.move(saved_x, saved_y)
                logger.info(f"已恢复桌宠位置：({saved_x}, {saved_y})")
                return
            logger.warning(
                f"保存的桌宠位置 ({saved_x}, {saved_y}) 不在任何屏幕的可见范围内，改回默认位置。"
            )
        self._move_pet_to_default_position()

    def _position_visible(self, x: int, y: int) -> bool:
        """坐标是否与任一屏幕的可用区域相交（拔掉外接屏后不至于把桌宠留在屏幕外）。"""
        width = self._pet_window.width()
        height = self._pet_window.height()
        for screen in self._app.screens():
            area = screen.availableGeometry()
            if x + width > area.left() and x < area.right() and y + height > area.top() and y < area.bottom():
                return True
        return False

    def _save_pet_position(self):
        """退出时保存桌宠位置（只在退出时写，避免频繁改动 config.yaml）。"""
        try:
            config = get_config()
            position = self._pet_window.pos()
            desktop_config = DesktopShellConfig.model_validate({
                **config.service.desktop_shell.model_dump(),
                "pet_x": int(position.x()),
                "pet_y": int(position.y()),
            })
            save_desktop_settings(desktop_config, config.service.chat_window)
            logger.info(f"已保存桌宠位置：({position.x()}, {position.y()})")
        except Exception as exc:
            logger.warning(f"保存桌宠位置失败（不影响退出）：{exc}")

    def _pet_is_topmost(self) -> bool:
        """桌宠当前是否处于“始终置顶”状态。"""
        return bool(self._pet_window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def show_popup(self, popup, *, modal=Qt.WindowModality.ApplicationModal):
        """所有弹窗（对话框、消息框、以后新增的）的统一入口。

        顺序不能改：Qt 的 setWindowModality() 会重建窗口标志，先把
        WindowStaysOnTopHint 设上去再改模态，置顶标志会被静默丢掉——桌宠
        开着“始终置顶”时，弹窗就会被压在猫猫下面。所以模态先设、置顶后设。
        """
        popup.setWindowModality(modal)
        flags = popup.windowFlags()
        if self._pet_is_topmost():
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        popup.setWindowFlags(flags)
        return popup.exec()

    def open_settings(self):
        from ui.settings import DesktopSettingsDialog

        self._pet_window._speech_bubble.hide()
        old_config = get_config().model_copy(deep=True)
        dialog = DesktopSettingsDialog(old_config, self._pet_window)
        try:
            if self.show_popup(dialog) != QDialog.DialogCode.Accepted:
                return

            updates = dialog.bot_config_updates()
            chat_title = updates.get("service", {}).get("chat_window", {}).get("title", "")
            if not chat_title:
                self._show_message_box(
                    QMessageBox.Icon.Warning, "标题无效", "聊天窗口标题不能为空。"
                )
                return
            pipeline_cfg = updates.get("pipeline", {})
            llm_on = bool(pipeline_cfg.get("llm", {}).get("enable", False))
            mllm_on = bool(pipeline_cfg.get("mllm", {}).get("enable", False))
            if not llm_on and not mllm_on:
                self._show_message_box(
                    QMessageBox.Icon.Warning,
                    "模型配置无效",
                    "LLM 与 MLLM 不能同时关闭，请至少启用其中一项。",
                )
                return

            try:
                parsed_config = save_bot_settings(updates)
            except Exception as exc:
                logger.exception("Unable to save bot settings")
                self._show_message_box(
                    QMessageBox.Icon.Critical, "保存失败", f"配置未保存：{exc}"
                )
                return

            new_bot_name = parsed_config.character.bot_name
            self._chat_window.set_bot_name(new_bot_name)
            self._chat_window.apply_settings(parsed_config.service.chat_window)
            self._pet_window.setWindowTitle(new_bot_name)
            self._pet_window.set_chat_enabled(parsed_config.service.chat_window.enable)
            self._pet_window.apply_desktop_config(parsed_config.service.desktop_shell)
            self._chat_window.set_avatar(
                parsed_config.service.desktop_shell.avatar_mode,
                parsed_config.service.desktop_shell.avatar_path,
            )
            if self._tray is not None:
                self._tray.setToolTip(new_bot_name)

            if getattr(dialog, "reset_position_requested", False):
                self._move_pet_to_default_position()

            self._runner.request_reload()

            fallback_msg = self._pet_window._mascot.fallback_message
            restart_reasons = dialog.detect_restart_reasons(old_config)
            if restart_reasons:
                reason_lines = "\n".join(f"• {item}" for item in restart_reasons[:8])
                if len(restart_reasons) > 8:
                    reason_lines += f"\n• …等共 {len(restart_reasons)} 项"
                answer = self._show_message_box(
                    QMessageBox.Icon.Question,
                    "需要重启",
                    f"设置已保存！以下更改需要重启程序后才能完整生效：\n{reason_lines}\n\n是否现在重启程序？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._restart_after_stop = True
                    self.request_exit()
                    return
                extra = f"\n\n注意：{fallback_msg}" if fallback_msg else ""
                self._show_message_box(
                    QMessageBox.Icon.Information,
                    "设置已保存",
                    f"可热更新的配置已即时生效；上述项将在下次启动时生效。{extra}",
                )
            else:
                msg = "所有更改已保存并即时热生效。"
                if fallback_msg:
                    msg += f"\n\n形象回退提示：{fallback_msg}"
                self._show_message_box(QMessageBox.Icon.Information, "设置已保存", msg)
        finally:
            dialog.deleteLater()

    def _show_message_box(
        self, icon, title, text, buttons=QMessageBox.StandardButton.Ok, default_button=None
    ):
        message_box = QMessageBox(icon, title, text, buttons, self._pet_window)
        try:
            message_box.setStyleSheet(MESSAGE_BOX_QSS)
            message_box.setFont(_ui_font(10))
            if default_button is not None:
                message_box.setDefaultButton(default_button)
            return self.show_popup(message_box)
        finally:
            message_box.deleteLater()

    def _create_tray(self, bot_name):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self._create_tray_icon(), self._pet_window)
        self._pet_window._has_tray = True
        self._tray.setToolTip(bot_name)
        menu = create_styled_menu(self._pet_window)
        open_chat = QAction("💬 打开聊天", menu)
        open_chat.triggered.connect(self._pet_window.open_chat)
        show_pet = QAction("🐱 显示桌宠", menu)
        show_pet.triggered.connect(self._pet_window.show)
        hide_pet = QAction("🫥 隐藏桌宠", menu)
        hide_pet.triggered.connect(self._pet_window.hide)
        open_settings = QAction("⚙️ 设置中心", menu)
        open_settings.triggered.connect(self.open_settings)
        quit_action = QAction("⏻ 退出", menu)
        quit_action.triggered.connect(self.request_exit)
        menu.addAction(open_chat)
        menu.addAction(show_pet)
        menu.addAction(hide_pet)
        menu.addAction(open_settings)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    @staticmethod
    def _create_tray_icon():
        return create_mascot_tray_icon()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._pet_window.show()
            self._pet_window.raise_()

    def _on_bot_ready(self):
        self._chat_window.set_runtime_ready(True)
        self._pet_window.set_state("已就绪")
        mic_active = self._runner.is_microphone_recording()
        self._pet_window.set_microphone_active(mic_active)
        self._chat_window.set_microphone_active(mic_active)

    def _on_bot_finished(self):
        if self._exiting:
            self._exit_watchdog.stop()
            self._bridge.close()
            self._launch_restart()
            self._app.quit()

    def _force_exit_on_timeout(self):
        logger.error(
            f"⏱️ 退出收尾超过 {self._exit_grace_ms / 1000:.0f} 秒仍未完成，强制结束进程。"
            "（通常说明有线程卡在无法中断的调用里；最后一次记忆快照可能未落盘）"
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    def _launch_restart(self):
        if not self._restart_after_stop or self._restart_launched:
            return
        self._restart_launched = True
        try:
            subprocess.Popen([sys.executable, *sys.argv], cwd=os.getcwd())
        except Exception as exc:
            logger.exception("Unable to restart desktop application")
            self._show_message_box(
                QMessageBox.Icon.Critical,
                "重启失败",
                f"自动重启失败，请手动重新启动程序：{exc}",
            )

    def request_exit(self):
        if self._exiting:
            return
        self._exiting = True
        # 只在退出时记录桌宠位置（拖动过程中不写配置）
        self._save_pet_position()
        self._pet_window._allow_close = True
        self._chat_window.hide()
        self._pet_window._speech_bubble.hide()
        self._pet_window.hide()
        self._pet_window.cleanup()
        if self._tray is not None:
            self._tray.hide()
        self._runner.request_stop()
        if not self._runner.is_running():
            self._bridge.close()
            self._launch_restart()
            self._app.quit()
            return
        # 收尾可能卡在无法中断的线程里（界面已经全隐藏，用户无法再操作），
        # 因此起一个一次性看门狗：正常收尾会取消它，超时才强退。
        logger.info(
            f"正在收尾（含最后一次记忆快照落盘），最长等待 {self._exit_grace_ms / 1000:.0f} 秒。"
        )
        self._exit_watchdog.start(self._exit_grace_ms)

    def exec(self):
        return self._app.exec()


def run_desktop_shell(bot_name, title, width, height, font_size, input_font_size, skip_tts, always_on_top=True):
    shell = DesktopShell(
        bot_name, title, width, height, font_size, input_font_size, skip_tts, always_on_top
    )
    return shell.exec()
