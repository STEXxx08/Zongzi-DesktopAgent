import os
import threading
from PIL import Image
from typing import List, Optional

from loguru import logger
from common.utils.img_util import is_image_uniform
from manager.config_manager import get_config

_config = get_config().pipeline.mllm
class MLLMScreenshotManager:
    def __init__(self, screen, interval: float, max_queue_size: int):
        self._screen = screen
        self._interval = interval
        self._max_queue_size = max_queue_size
        self._queue: List[str] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        with self._lock:
            for path in self._queue:
                try:
                    os.remove(path)
                except:
                    pass
            self._queue.clear()

    def _run(self):
        while not self._stop_event.is_set():
            try:
                img, save_path = self._screen.safe_capture(k=0.99)
                if not save_path:
                    self._stop_event.wait(self._interval)
                    continue
                if img and not is_image_uniform(img):
                    # 压缩图片：长边不超过_config.screenshot_definition像素，保存为WEBP格式
                    img.thumbnail((_config.screenshot_definition, _config.screenshot_definition), Image.Resampling.LANCZOS)  # 高质量缩放
                    # 生成新的文件路径（替换扩展名为 .webp）
                    webp_path = str(save_path).rsplit('.', 1)[0] + '.webp'
                    img.save(webp_path, format='WEBP', quality=85)  # quality=85 平衡体积与画质
                    # 删除原始临时文件
                    if os.path.exists(save_path):
                        os.remove(save_path)
                    with self._lock:
                        self._queue.append(webp_path)
                        if len(self._queue) > self._max_queue_size:
                            old = self._queue.pop(0)
                            try:
                                os.remove(old)
                            except Exception:
                                pass
                else:
                    # 若截图无效，直接删除原始文件
                    if os.path.exists(save_path):
                        os.remove(save_path)
                self._stop_event.wait(self._interval)
            except Exception as e:
                logger.exception(f"MLLM screenshot loop error: {e}")
                self._stop_event.wait(self._interval)

    def pop_all(self) -> List[str]:
        with self._lock:
            if not self._queue:
                return []
            paths = self._queue.copy()
            self._queue.clear()
            return paths