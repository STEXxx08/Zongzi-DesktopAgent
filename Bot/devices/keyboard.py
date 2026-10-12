from typing import Union

from common.concurrent.abs_runnable import ThreadRunnable
from devices.headless import is_headless
from event.event_emitter import emitter
from event.event_data import DeviceKeyboardPressEvent
import threading
from loguru import logger

if not is_headless():
    try:
        from pynput import keyboard
        from pynput.keyboard import Key, KeyCode
    except:
        raise ImportError(f'Pynput not installed, please try "pip install pynput" to solve this problem.')

"""
Keyborad 函数只监听所有特定的按键, 并触发控制函数 hotkey_handler
(目前只在 Windows11 下测试过)
"""
class SmartKeyboard(ThreadRunnable):
    def __init__(self, hotkeys: list):
        super().__init__()
        self._hotkeys = set()
        for string in hotkeys:
            assert type(string) is str, "hotkeys must be string type"
            if string in self._hotkeys:
                assert False, "some hotkeys are set to be the same, please check your config.yaml setting"
            self._hotkeys.add(self.str_to_key(string))
        self._current_hotkey: Union[Key, KeyCode] = None
        self._toggle_debounce: bool = False   # 防抖
        self._key_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self._key_listener.daemon = False

        # 外部环境可用的锁
        self.microphone_state_lock = threading.Lock()

    def start(self):
        super().start()
        try:
            self._key_listener.start()
        except Exception as e:
            logger.error(e)

    def stop(self):
        super().stop()
        self._key_listener.stop()
    
    def _on_key_press(self, key):
        # logger.debug(f'Press {key}')
        if key not in self._hotkeys:
            return
        
        if self._toggle_debounce:
            return
        self._toggle_debounce = True
        self._current_hotkey = key

        emitter.emit(DeviceKeyboardPressEvent(hotkey=self.key_to_str(key)))

    def _on_key_release(self, key):
        # logger.debug(f'Release {key}')
        if key == self._current_hotkey:
            self._toggle_debounce = False
            self._current_hotkey = None
    
    def name(self):
        return "SmartKeyboard"
    
    @staticmethod
    def str_to_key(s: str) -> Union[Key, KeyCode]:
        if not isinstance(s, str):
            raise TypeError("hotkey must be str")

        s = s.strip()
        if not s:
            raise ValueError("hotkey string is empty")

        # 不允许传 "Key.f8" 这种写法
        # if s.lower().startswith("key."):
        #     s = s.split(".", 1)[1].strip()

        name = s.lower()

        if name in Key.__members__:
            return Key[name]

        if len(s) == 1:
            return KeyCode.from_char(s)

        raise ValueError(
            f"Unknown key: {s!r}. "
            f"Try one of Key names like: {', '.join(list(Key.__members__.keys())[:20])} ..."
        )
    
    @staticmethod
    def key_to_str(k: Union[Key, KeyCode]) -> str:
        if hasattr(k, "name") and k.name is not None:   # Key
            return k.name
        if hasattr(k, "char") and k.char is not None:   # KeyCode
            return k.char
        return str(k)