from __future__ import annotations

from typing import Any

from loguru import logger

from agent.api import sentiment_analyse
from bot_asr_controller import ASR_result_filter
from condata.pipeline.mllm import MLLMPrediction
from event.event_data import (
    CharacterStateEvent,
    ConfigFileModifiedEvent,
    DeviceDesktopAudioEvent,
    DeviceKeyboardPressEvent,
    DeviceMicrophoneSwitchEvent,
    DeviceMicrophoneVADEvent,
    DeviceScreenCapturedEvent,
    PipelineASREvent,
    PipelineImgCapEvent,
    PipelineOCREvent,
    PipelineOutputLLMEvent,
)
from event.event_emitter import emitter
from event.registry import EventKeyRegistry
from pipeline.ocr.ocr_sync import stringify


class BotEventBinder:
    """直连 Bot 主控的事件总线绑定器：把设备/管线/配置事件分发到各核心控制器。"""

    def __init__(self, bot: Any) -> None:
        self._bot = bot

    def bind(self) -> None:
        emitter.on(EventKeyRegistry.Device.KEYBOARD_HOTKEY_PRESS)(
            self._on_keyboard_hotkey_press
        )
        emitter.on(EventKeyRegistry.Device.MICROPHONE_SWITCH)(
            self._on_microphone_switch
        )
        emitter.on(EventKeyRegistry.Device.MICROPHONE_VAD)(
            self._on_microphone_vad
        )
        emitter.on(EventKeyRegistry.Pipeline.ASR)(self._on_pipeline_asr)
        emitter.on(EventKeyRegistry.Device.DESKTOP_AUDIO)(self._on_desktop_audio)
        emitter.on(EventKeyRegistry.Device.SCREEN_CAPTURED)(
            self._on_screen_captured
        )
        emitter.on(EventKeyRegistry.Pipeline.OCR)(self._on_pipeline_ocr)
        emitter.on(EventKeyRegistry.Pipeline.IMG_CAP)(self._on_pipeline_img_cap)
        emitter.on(EventKeyRegistry.Pipeline.LLM)(self._on_pipeline_llm)
        emitter.on(EventKeyRegistry.System.CONFIG_FILE_MODIFIED)(
            self._on_config_file_modified
        )

    def _on_keyboard_hotkey_press(self, event: DeviceKeyboardPressEvent) -> None:
        bot = self._bot
        cfg = bot.get_runtime_config()
        logger.info(f"收到热键切换事件：{event.hotkey}")
        try:
            if event.hotkey == cfg.service.microphone.hotkey:
                if cfg.service.microphone.enable:
                    with bot.keyboard.microphone_state_lock:
                        if bot.mic.is_set_talk_enabled_event():
                            logger.debug("热键已关闭麦克风。")
                            bot.mic.unset_talk_enabled_event()
                            bot.asr_controller.reset_accumulator()
                            bot.mic.force_commit(is_emit=True)
                        else:
                            logger.debug("热键已开启麦克风。")
                            bot.asr_controller.reset_accumulator()
                            bot.mic.force_commit(is_emit=False)
                            bot.mic.set_talk_enabled_event()
                else:
                    logger.info("config.yaml 中已禁用麦克风。")
        except Exception as exc:
            logger.exception(exc)

    def _on_microphone_switch(self, event: DeviceMicrophoneSwitchEvent) -> None:
        mic = self._bot.mic
        if mic.is_recording:
            if event.switch:
                logger.warning("麦克风已经处于启用状态。")
                return
            mic.pause()
        else:
            if not event.switch:
                logger.warning("麦克风已经处于暂停状态。")
                return
            mic.resume()

    def _on_microphone_vad(self, event: DeviceMicrophoneVADEvent) -> None:
        self._bot.asr_controller.handle_vad_chunk(event)

    @staticmethod
    def _pop_mllm_images(bot: Any) -> list[str] | None:
        if bot.mllm_cfg.enable and bot._mllm_screenshot_manager:
            return bot._mllm_screenshot_manager.pop_all()
        return None

    def _on_pipeline_asr(self, event: PipelineASREvent) -> None:
        bot = self._bot
        cfg = bot.get_runtime_config()
        logger.debug("收到 ASR 事件。")
        prediction = event.prediction
        transcript = prediction.transcript

        if ASR_result_filter(transcript):
            logger.debug(f"ASR 结果已过滤：'{transcript}'")
            return
        if event.source == "desktop":
            if transcript:
                bot.asr_controller.append_desktop_asr(transcript)
                logger.debug(f"桌面 ASR：{transcript}")
            return

        turn_id = bot._next_turn_id()

        user_skill_invocation = bot.skill_service.parse_user_invocation(transcript)
        if user_skill_invocation is not None:
            remaining_text, skill = user_skill_invocation
            logger.info(f"用户显式加载 Skill：Skill={skill.name}，turn_id={turn_id}")
            bot.emit_llm_prediction(
                remaining_text or "请按照已加载的 Skill 执行当前任务。",
                skip_tts=event.skip_tts,
                _skill_context=bot.skill_service.render_skill(skill),
                _skip_web_route=True,
                _turn_id=turn_id,
            )
            return

        router_active = bool(bot.intent_router.enabled and bot.intent_router.available)
        route_decision = (
            bot.intent_router.route(
                transcript,
                context={"conversation_id": bot.conversation_id, "turn_id": turn_id},
            )
            if router_active
            else None
        )
        routed_tool_decision = (
            route_decision
            if route_decision is not None and route_decision.tool_call is not None
            else None
        )
        wants_vision = (
            route_decision is not None and route_decision.action == "vision"
            if router_active
            else ("看见" in transcript or "看看" in transcript)
        )
        allow_keyword_match = bot.intent_router.allow_keyword_fallback

        if wants_vision:
            if not (cfg.pipeline.mllm.enable or cfg.pipeline.vlm.mode == "continuous"):
                img, img_save_path = bot.screen.safe_capture(k=0.99)
                if not bot.vision_controller.check_img(img, send_message=False):
                    bot.emit_llm_prediction(
                        f"{prediction.transcript}，但我什么也看不见。",
                        skip_tts=event.skip_tts,
                        _turn_id=turn_id,
                    )
                    return
                if bot.vlm is not None:
                    bot.vision_controller.async_predict(
                        str(img_save_path),
                        callback=bot.vision_controller.on_vlm_result_ready,
                    )
                else:
                    emitter.emit(
                        DeviceScreenCapturedEvent(
                            img_path=img_save_path,
                            skip_tts=event.skip_tts,
                        )
                    )
                    return

        image_paths = self._pop_mllm_images(bot)
        if not wants_vision and routed_tool_decision is not None:
            bot.turn_orchestrator.submit_routed_tool(
                routed_tool_decision,
                prediction.transcript,
                image_paths,
                event.skip_tts,
                turn_id,
            )
            return
        bot.emit_llm_prediction(
            prediction.transcript,
            image_paths=image_paths,
            skip_tts=event.skip_tts,
            _allow_keyword_match=allow_keyword_match,
            _turn_id=turn_id,
        )

    def _on_desktop_audio(self, event: DeviceDesktopAudioEvent) -> None:
        self._bot.asr_controller.handle_desktop_audio(event)

    def _on_screen_captured(self, event: DeviceScreenCapturedEvent) -> None:
        self._bot.vision_controller.handle_screen_captured(event)

    def _on_pipeline_ocr(self, event: PipelineOCREvent) -> None:
        prediction = event.prediction
        text = "你看见了" + stringify(prediction.region_results) + "\n请总结一下"
        self._bot.emit_llm_prediction(text)

    def _on_pipeline_img_cap(self, event: PipelineImgCapEvent) -> None:
        prediction = event.prediction
        text = "你看见了" + prediction.caption
        self._bot.emit_llm_prediction(text)

    def _on_pipeline_llm(self, event: PipelineOutputLLMEvent) -> None:
        bot = self._bot
        prediction = event.prediction
        text = prediction.response
        logger.info("LLM 回复：" + text)
        streamed_tts_result = event.skip_tts and bool(
            getattr(prediction, "_streamed_tts", False)
            or isinstance(prediction, MLLMPrediction)
        )
        tts_prompt = None
        ui_emotion = None
        if not streamed_tts_result:
            if event.emotion:
                ui_emotion = event.emotion
            elif bot.enable_sentiment_analysis:
                ui_emotion = sentiment_analyse(
                    sentiments=bot.tts_prompt_manager.sentiments, text=text
                )
            else:
                ui_emotion = bot.tts_prompt_manager.default_tts_prompt.sentiment
            tts_prompt = bot.tts_prompt_manager.get_tts_prompt(ui_emotion)
            emitter.emit(CharacterStateEvent(state="replied", emotion=ui_emotion))
        if event.skip_tts:
            logger.info("文本输入模式已跳过 TTS。")
        else:
            tts_round_task_id = bot.tts_scheduler.current_task_id()
            if bot.tts_scheduler.enqueue(text, tts_prompt, ui_emotion):
                bot.tts_scheduler.watch_round_end(tts_round_task_id)

    def _on_config_file_modified(self, _: ConfigFileModifiedEvent) -> None:
        self._bot.reload_config()
