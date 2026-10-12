from PIL.Image import Image
import PIL.Image
import mss.screenshot
import numpy as np
from devices.screen.base_screen import BaseScreen
import platform
from typing import Tuple, Optional
import mss
from loguru import logger
from common.io.file_sys import fs
import PIL
from pathlib import Path


class LinuxScreen(BaseScreen):

    def __init__(self):
        os_name = platform.system()
        if os_name != "Linux":
            raise NotImplementedError("Only support Linux platform.")

    def safe_capture(self, win_title: str = None, k: float = 1.0) -> Tuple[Optional[Image], Optional[Path]]:
        try:
            if win_title is None:
                return self._capture_screen(k)
            else:
                return self._capture_window(win_title, k)
        except Exception as e:
            logger.exception(e)
            logger.error("Capture failed. See exception above.")
            return None, None

    def _capture_screen(self, k: float) -> Tuple[Image, Path]:
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Assume you have only 1 monitor
            if k != 1.0:
                width = int(monitor["width"] * k)
                height = int(monitor["height"] * k)
                left = monitor["left"] + (monitor["width"] - width) // 2
                top = monitor["top"] + (monitor["height"] - height) // 2
                monitor = {"left": left, "top": top,
                           "width": width, "height": height}

            img: mss.screenshot.ScreenShot = sct.grab(monitor)
            imgarr = np.array(img)
            pil_image = PIL.Image.fromarray(imgarr[:, :, [2, 1, 0]], 'RGB')  # Alpha channel is dropped.

            img_save_path = fs.create_temp_file_descriptor(prefix="screenshot", suffix=".png", type="image")
            pil_image.save(img_save_path)

            return pil_image, img_save_path

    def _capture_window(self, win_title: str, k: float) -> Tuple[Image, Path]:
        logger.warning(
            f"Window title provided but Linux cannot reliably capture specific windows. Capturing full screen instead.")
        return self._capture_screen(k)
