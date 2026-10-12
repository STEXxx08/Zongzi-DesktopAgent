import platform
from pathlib import Path
from typing import Tuple, Optional

import pyautogui
import pygetwindow as gw
from PIL.Image import Image
from loguru import logger
from pygetwindow import Win32Window

from common.io.file_sys import fs
from devices.screen.base_screen import BaseScreen


class WindowsScreen(BaseScreen):

    def __init__(self):
        os_name = platform.system()
        if os_name != "Windows":
            raise NotImplementedError("Only support Windows platform.")
        assert hasattr(pyautogui, "screenshot")
        # Note: If you have a problem that the screenshot cannot be found, try updating the `pyautogui` library

    def safe_capture(self, win_title: str = None, k: float | None = None) -> Tuple[Optional[Image], Optional[Path]]:
        try:
            if win_title is None:
                return self.capture_activated_win(k)
            else:
                return self.capture_with_title(win_title, k)
        except ValueError as e:
            if str(e) == "Coordinate 'right' is less than 'left'":
                logger.warning(
                    "Window capture failed: Taking a screenshot in a split-screen situation may cause problems, please try placing the target window on the home screen.")
        except AssertionError as e:
            logger.warning(e)
        except gw.PyGetWindowException as e:
            if "Error code from Windows: 0" in str(e):
                logger.warning("Window capture failed: Lost focus. Is your target window activated?")
        except Exception as e:
            logger.exception(e)
            logger.warning("Window capture failed: Unknown error.")
        return None, None

    def capture_activated_win(self, k: float | None = None) -> Tuple[Optional[Image], Optional[Path]]:
        w = gw.getActiveWindow()
        if w is None:
            logger.debug("No active window available; skipping window screenshot.")
            return None, None
        return self._capture(w, k)

    def capture_with_title(self, win_title: str, k: float | None = None) -> Tuple[Image, Path]:
        # Get the window
        win_list = gw.getWindowsWithTitle(win_title)
        assert len(win_list) != 0, f'Window capture failed: Can not find {win_title}'
        w = win_list[0]
        # Activate the window
        w.activate()
        return self._capture(w, k)

    def _capture(self, w: Win32Window, k: float | None = None) -> Tuple[Image, Path]:

        if k is None:
            img = pyautogui.screenshot()
        else:
            region = (
                w.centerx - k * w.width / 2, w.centery - k * w.height / 2, w.centerx + k * w.width / 2,
                w.centery + k * w.height / 2)
            region = tuple(int(num) if num > 0 else 0 for num in region)
            img = pyautogui.screenshot(region=region)  # noqa

        img_save_path = fs.create_temp_file_descriptor(prefix="screenshot", suffix=".png", type="image")
        img.save(img_save_path)

        return img, img_save_path
