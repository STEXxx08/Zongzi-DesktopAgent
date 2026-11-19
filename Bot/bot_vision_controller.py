from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from loguru import logger

from agent.api import translate
from common.enumerator import Language
from common.utils.img_util import is_image_uniform
from condata.pipeline.img_cap import ImgCapQuery
from condata.pipeline.ocr import OCRQuery
from event.event_data import (
    DeviceScreenCapturedEvent,
    PipelineImgCapEvent,
    PipelineOCREvent,
)
from event.event_emitter import emitter
from pipeline.ocr.ocr_sync import avg_confidence, stringify
from pipeline.vlm.vlm_sync import VLMQuery

_DEFAULT_VLM_PROMPT = (
    "请客观简短地描述这张图片的主要内容和信息，不要包含冗余信息和猜测，"
    "不需要前缀（如“好的，以下是图片的主要内容描述”）"
)


class BotVisionController:
    """直连 Bot 主控的视觉与主动搭话控制器：管理 VLM 轮询/异步识别、截图降级与无输入定时器。"""

    def __init__(
        self,
        *,
        config_provider: Callable[[], Any],
        vlm_provider: Callable[[], Any],
        ocr_provider: Callable[[], Any],
        img_cap_provider: Callable[[], Any],
        screen_provider: Callable[[], Any],
        lang_provider: Callable[[], Language],
        on_trigger_turn: Callable[..., Any],
        emit_event: Callable[[Any], None] = emitter.emit,
    ) -> None:
        self._config_provider = config_provider
        self._vlm_provider = vlm_provider
        self._ocr_provider = ocr_provider
        self._img_cap_provider = img_cap_provider
        self._screen_provider = screen_provider
        self._lang_provider = lang_provider
        self._on_trigger_turn = on_trigger_turn
        self._emit_event = emit_event

        self._vlm_running = False
        self._vlm_result: str | None = None
        self._vlm_timer: threading.Timer | None = None
        self._vlm_lock = threading.Lock()
        self._vlm_stop = False
        self._vlm_continuous_results: list[str] = []

        self._auto_chat_lock = threading.Lock()
        self._vlm_continuous_timer: threading.Timer | None = None

    def consume_vlm_context(
        self, user_text: str
    ) -> tuple[list[tuple[str, str]], str, bool]:
        """原子取出当前轮次可用的 VLM 屏幕上下文，并判断是否为主动搭话。"""
        cfg = self._config_provider()
        mode = cfg.pipeline.vlm.mode
        environment_items: list[tuple[str, str]] = []
        proactive = not user_text

        with self._vlm_lock:
            if mode == "keyword":
                if self._vlm_running:
                    environment_items.append(
                        ("屏幕", "屏幕信息正在获取，暂时没有可用画面。")
                    )
                elif self._vlm_result is not None:
                    environment_items.append(("屏幕", self._vlm_result))
                    self._vlm_result = None
                    self._cancel_vlm_timer()
                    if not user_text:
                        proactive = False
                        user_text = (
                            "（[系统信息]屏幕画面识别已完成，请结合下方屏幕信息继续回应用户。）"
                        )
            elif mode == "continuous":
                if self._vlm_continuous_results:
                    merged = "；".join(self._vlm_continuous_results)
                    self._vlm_continuous_results.clear()
                    environment_items.append(("近期屏幕变化", merged))

        return environment_items, user_text, proactive

    def on_vlm_result_ready(self, caption: str | None) -> None:
        if not caption:
            return
        cfg = self._config_provider()
        with self._vlm_lock:
            mode = cfg.pipeline.vlm.mode
            if mode == "keyword":
                self._vlm_result = caption
                self._start_vlm_timer()
                logger.debug(
                    f"[关键词模式] 已保存 VLM 结果并启动 5 秒定时器：{caption[:30]}..."
                )
            elif mode == "continuous":
                time_str = datetime.now().strftime("%H:%M:%S")
                self._vlm_continuous_results.append(f"【{time_str}】{caption}")
                logger.debug(
                    f"[连续模式] 已追加 VLM 结果，当前总数："
                    f"{len(self._vlm_continuous_results)}"
                )

    def continuous_loop(self) -> None:
        """常态模式下后台持续截图并调用 VLM。"""
        while not self._vlm_stop and self._vlm_provider() is not None:
            screen = self._screen_provider()
            if screen is not None:
                img, img_save_path = screen.safe_capture(k=0.99)
                if img and not is_image_uniform(img):
                    self.async_predict(
                        str(img_save_path),
                        callback=self.on_vlm_result_ready,
                    )
            cfg = self._config_provider()
            time.sleep(cfg.pipeline.vlm.continuous_interval)

    def async_predict(self, img_path: str, callback=None) -> bool:
        """异步调用 VLM，设置运行标志，完成后回调（异常也会确保释放运行锁）。"""
        with self._vlm_lock:
            if self._vlm_running:
                logger.warning("VLM 已在运行，跳过本次请求。")
                return False
            self._vlm_running = True

        def task():
            caption = None
            try:
                vlm = self._vlm_provider()
                caption = vlm.predict(
                    VLMQuery(
                        img_path=img_path,
                        prompt=_DEFAULT_VLM_PROMPT,
                    )
                )
            except Exception as exc:
                logger.exception(f"VLM 异步预测失败：{exc}")
            finally:
                try:
                    if os.path.exists(img_path):
                        os.remove(img_path)
                        logger.debug(f"已删除 VLM 临时图片：{img_path}")
                except Exception as exc:
                    logger.warning(f"删除临时图片 {img_path} 失败：{exc}")
                with self._vlm_lock:
                    self._vlm_running = False
                if callback:
                    callback(caption)

        logger.debug("VLM 异步预测任务已启动。")
        threading.Thread(target=task, daemon=True).start()
        return True

    def handle_screen_captured(self, event: DeviceScreenCapturedEvent) -> None:
        """处理屏幕捕获事件：优先 VLM，否则降级至 OCR + ImgCap。"""
        cfg = self._config_provider()
        if cfg.pipeline.mllm.enable:
            return
        img_path = str(event.img_path) if isinstance(event.img_path, Path) else event.img_path

        vlm = self._vlm_provider()
        if vlm is not None:
            try:
                caption = vlm.predict(
                    VLMQuery(
                        img_path=img_path,
                        prompt=_DEFAULT_VLM_PROMPT,
                    )
                )
                if caption:
                    logger.info(f"VLM 结果：{caption}")
                    text = f"你看到了一张图片，内容为：{caption}。请根据此信息回答用户。"
                    self._on_trigger_turn(text, skip_tts=event.skip_tts)
                    return
            except Exception as exc:
                logger.exception(exc)

        ocr = self._ocr_provider()
        ocr_prediction = ocr.predict(OCRQuery(img_path=img_path))
        if avg_confidence(ocr_prediction) > 0.6:
            logger.info("OCR 结果：" + stringify(ocr_prediction.region_results))
            self._emit_event(PipelineOCREvent(prediction=ocr_prediction))
        else:
            img_cap = self._img_cap_provider()
            img_cap_prediction = img_cap.predict(
                ImgCapQuery(prompt="There", img_path=img_path)
            )
            src_lang = Language.value_of(img_cap_prediction.lang)
            caption = translate(src_lang, self._lang_provider(), img_cap_prediction.caption)
            img_cap_prediction.caption = caption
            logger.info("图像描述结果：" + caption)
            self._emit_event(PipelineImgCapEvent(prediction=img_cap_prediction))

    def check_img(self, img, send_message: bool = True) -> bool:
        if is_image_uniform(img):
            logger.warning("屏幕截图为纯黑画面，请检查屏幕捕获是否正常。")
            if send_message:
                self._on_trigger_turn("你忽然什么都看不见了！请向你的开发者求助！")
            return False
        return True

    def _cancel_vlm_timer(self) -> None:
        """取消 keyword 模式定时器（调用方须已持有 `self._vlm_lock`）。"""
        if self._vlm_timer:
            self._vlm_timer.cancel()
            self._vlm_timer = None

    def _start_vlm_timer(self) -> None:
        """启动 keyword 模式定时器（调用方须已持有 `self._vlm_lock`）。"""
        self._cancel_vlm_timer()
        cfg = self._config_provider()
        self._vlm_timer = threading.Timer(
            5.0,
            lambda: self._on_trigger_turn("", skip_tts=cfg.service.chat_window.skip_tts),
        )
        self._vlm_timer.daemon = True
        self._vlm_timer.start()

    def _cancel_auto_chat_timer(self) -> None:
        """取消 auto_chat 无输入定时器（调用方须持有 `self._auto_chat_lock`）。"""
        if self._vlm_continuous_timer:
            self._vlm_continuous_timer.cancel()
            self._vlm_continuous_timer = None

    def reset_auto_chat_timer(self) -> None:
        """重置 auto_chat 无输入定时器（任意线程安全）。"""
        cfg = self._config_provider()
        with self._auto_chat_lock:
            if not cfg.system.auto_chat.enable:
                return
            self._cancel_auto_chat_timer()
            timeout = getattr(cfg.system.auto_chat, "timeout", 45)
            self._vlm_continuous_timer = threading.Timer(
                timeout,
                lambda: self._on_trigger_turn(
                    "", skip_tts=cfg.service.chat_window.skip_tts
                ),
            )
            self._vlm_continuous_timer.daemon = True
            self._vlm_continuous_timer.start()

    def shutdown(self) -> None:
        self._vlm_stop = True
        with self._vlm_lock:
            self._cancel_vlm_timer()
        with self._auto_chat_lock:
            self._cancel_auto_chat_timer()
