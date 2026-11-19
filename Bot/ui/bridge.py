"""Qt UI 与后台 Bot 事件总线及运行时线程的桥接层。"""

import asyncio
from contextlib import nullcontext
import threading

from PySide6.QtCore import QObject, Signal
from loguru import logger

from event.event_data import ConfigFileModifiedEvent
from event.event_emitter import emitter
from event.registry import EventKeyRegistry


class BotUiBridge(QObject):
    user_message = Signal(str)
    bot_message = Signal(str)
    state_changed = Signal(str)
    emotion_changed = Signal(str)
    bot_error = Signal(str)
    mic_toggled = Signal(bool)

    def __init__(self):
        super().__init__()
        self._mic_state_provider = None
        self._asr_listener = emitter.on(EventKeyRegistry.Pipeline.ASR)(self._on_asr)
        self._llm_listener = emitter.on(EventKeyRegistry.Pipeline.LLM)(self._on_llm)
        self._speaker_listener = emitter.on(EventKeyRegistry.Device.SPEAKER_PLAY)(self._on_speaker_play)
        self._character_listener = emitter.on(EventKeyRegistry.Character.STATE)(self._on_character_state)
        self._error_listener = emitter.on(EventKeyRegistry.System.SYSTEM_UNHANDLED_ERROR)(self._on_error)
        self._mic_switch_listener = emitter.on(EventKeyRegistry.Device.MICROPHONE_SWITCH)(self._on_mic_switch)

    def set_mic_state_provider(self, provider) -> None:
        self._mic_state_provider = provider

    def _emit_synced_mic_state(self, fallback: bool | None = None) -> None:
        if callable(self._mic_state_provider):
            try:
                self.mic_toggled.emit(bool(self._mic_state_provider()))
                return
            except Exception:
                pass
        if fallback is not None:
            self.mic_toggled.emit(bool(fallback))

    def _on_asr(self, event):
        if event.source != "desktop":
            self.user_message.emit(event.prediction.transcript or "")
        self.state_changed.emit("思考中")

    def _on_llm(self, event):
        self.bot_message.emit(event.prediction.response or "")

    def _on_speaker_play(self, event):
        self.state_changed.emit("正在说话")
        self.emotion_changed.emit(event.emotion or "Default")

    def _on_character_state(self, event):
        states = {
            "idle": "待命中",
            "listening": "聆听中",
            "thinking": "思考中",
            "replied": "已回复",
            "speaking": "正在说话",
        }
        self.state_changed.emit(states.get(event.state, event.state))
        if event.emotion:
            self.emotion_changed.emit(event.emotion)
        self._emit_synced_mic_state()

    def _on_error(self, event):
        self.bot_error.emit(str(event.msg or "运行时发生错误"))
        self.state_changed.emit("遇到问题")

    def _on_mic_switch(self, event):
        self._emit_synced_mic_state(fallback=bool(getattr(event, "switch", False)))

    def report_error(self, message):
        self.bot_error.emit(message)

    def close(self):
        emitter.off(EventKeyRegistry.Pipeline.ASR, self._asr_listener)
        emitter.off(EventKeyRegistry.Pipeline.LLM, self._llm_listener)
        emitter.off(EventKeyRegistry.Device.SPEAKER_PLAY, self._speaker_listener)
        emitter.off(EventKeyRegistry.Character.STATE, self._character_listener)
        emitter.off(EventKeyRegistry.System.SYSTEM_UNHANDLED_ERROR, self._error_listener)
        emitter.off(EventKeyRegistry.Device.MICROPHONE_SWITCH, self._mic_switch_listener)


class BotRunner(QObject):
    ready = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self._thread = None
        self._bot = None
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, name="BotRuntime", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            from bot import Bot

            bot = Bot()
            with self._lock:
                self._bot = bot
            if self._stop_requested.is_set():
                bot.request_stop()
            asyncio.run(self._serve(bot))
        except Exception as exc:
            logger.exception("Desktop Bot runtime failed")
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    async def _serve(self, bot):
        start_task = asyncio.create_task(bot.start())
        try:
            while not bot.runtime_ready and not start_task.done():
                await asyncio.sleep(0.05)
            if bot.runtime_ready:
                self.ready.emit()
            await start_task
        finally:
            if not start_task.done():
                bot.request_stop()
                await start_task
            await bot.stop()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def is_microphone_recording(self) -> bool:
        with self._lock:
            bot = self._bot
        if bot is None:
            return False
        cfg = bot.get_runtime_config()
        mic = getattr(bot, "mic", None)
        return bool(
            cfg.service.microphone.enable
            and mic is not None
            and mic.is_set_talk_enabled_event()
        )

    def set_microphone_enabled(self, enabled: bool) -> bool:
        """与键盘热键 F8 一致地切换麦克风收音状态并返回最新开启状态。"""
        with self._lock:
            bot = self._bot
        if bot is None:
            return False
        cfg = bot.get_runtime_config()
        mic = getattr(bot, "mic", None)
        if not cfg.service.microphone.enable or mic is None:
            return False
        lock = getattr(getattr(bot, "keyboard", None), "microphone_state_lock", None) or nullcontext()
        with lock:
            is_open = bool(mic.is_set_talk_enabled_event())
            if enabled and not is_open:
                bot.asr_controller.reset_accumulator()
                mic.force_commit(is_emit=False)
                mic.set_talk_enabled_event()
            elif not enabled and is_open:
                mic.unset_talk_enabled_event()
                bot.asr_controller.reset_accumulator()
                mic.force_commit(is_emit=True)
            return bool(mic.is_set_talk_enabled_event())

    def request_reload(self) -> bool:
        """通知后台 Bot 热重载最新配置（通过事件总线或后台线程非阻塞执行）。"""
        with self._lock:
            bot = self._bot
        if bot is None:
            return False
        try:
            emitter.emit(ConfigFileModifiedEvent())
            return True
        except Exception as exc:
            logger.warning(f"通过事件总线触发热重载失败，改用独立线程执行：{exc}")
            threading.Thread(
                target=bot.reload_config,
                name="BotConfigReloadThread",
                daemon=True,
            ).start()
            return True

    def request_stop(self):
        self._stop_requested.set()
        with self._lock:
            bot = self._bot
        if bot is not None:
            bot.request_stop()
