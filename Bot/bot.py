import asyncio
import io
import json
from datetime import datetime
import faulthandler
import os
from concurrent.futures.thread import ThreadPoolExecutor
from pathlib import Path
import re
import threading
import time
import wave
from typing import List
import base64
from uuid import uuid4
from loguru import logger
from condata.harness import configure_harness
import numpy as np
from common.logging_setup import configure_app_logging
from condata.data.prompt import TTSPrompt
from condata.pipeline.asr import ASRPrediction, ASRStreamQuery
from condata.pipeline.img_cap import ImgCapQuery
from condata.pipeline.llm import Conversation, LLMQuery, LLMPrediction, RoleEnum
from condata.pipeline.ocr import OCRQuery
from condata.pipeline.tts import TTSQuery
from condata.pipeline.mllm import MLLMQuery, MLLMPrediction

from agent.api import sentiment_analyse, translate
from common.concurrent.abs_runnable import stop_all_runnable
from common.concurrent.killable_thread import KillableThread, kill_all_threads
from common.enumerator import Language
from common.io.api import save_audio
from common.io.file_type import AudioFileType
from common.pseudo_markers import iter_visible_segments
from common.utils.img_util import is_image_uniform
from common.utils.str_util import split_by_punc, is_blank
from event.event_data import DeviceMicrophoneVADEvent, DeviceKeyboardPressEvent, DeviceScreenCapturedEvent, \
    PipelineOutputLLMEvent, \
    PipelineImgCapEvent, \
    DeviceMicrophoneSwitchEvent, PipelineOutputTTSEvent, PipelineASREvent, DeviceDesktopAudioEvent,\
    PipelineOCREvent, SecondEvent, ConfigFileModifiedEvent, CharacterStateEvent
from event.event_emitter import emitter
from event.registry import EventKeyRegistry
from framework.base_bot import BaseBot
from manager.config_manager import get_config, get_project_dir
from manager.context_manager import ContextManager
from pipeline.ocr.ocr_sync import avg_confidence, stringify
from memory.manager import MemoryManager, resolve_memory_model_configs
from pipeline.vlm.vlm_sync import VLMQuery
from pipeline.mllm.mllm_sync import MLLMSyncPipeline
from pipeline.mllm.config import MLLMPipelineConfig
from pipeline.mllm.mllm_screenshot_manager import MLLMScreenshotManager
from pipeline.asr.turnsense import TurnSenseDetector
from services.web_search import (
    SearchRequest,
    SearchSubmissionStatus,
    WebSearchService,
    load_web_search_config,
)
from services.web_search.service import LocalSearchDirective
from services.intent_router import BGEIntentRouter, load_intent_router_config
from services.mcp import MCPService
from services.browser import BrowserService
from services.tool_model_gateway import ModelToolGateway, compose_tool_instructions
from services.sandbox import SandboxService
from services.skill_framework import (
    ModelSkillCreateDirective,
    ModelSkillDirective,
    SkillService,
)
from services.tool_coordinator import ToolCoordinationStatus
from services.emotion_prefix import DEFAULT_EMOTION, consume_emotion_prefix, normalize_emotion

faulthandler.enable(all_threads=True)

# 统一日志：loguru 文件 sink（轮转 + 保留）并桥接标准 logging（P1-26）
configure_app_logging(Path(__file__).resolve().parent)

_config = get_config()

class Bot(BaseBot):
    def __init__(self):
        configure_harness(project_root=Path(__file__).resolve().parent, **_config.service.harness.model_dump())
        # memory.max_history 是临时对话历史上限的唯一配置来源；
        # 在基类创建 llm_prompt_manager 前同步到聊天配置对象。
        _config.character.chat.max_history = _config.memory.max_history
        super().__init__()
        self.cur_lang = _config.character.speech.language
        self._timer_flag = True
        self._runtime_stopped = False
        self._runtime_ready = threading.Event()
        self.tts_thread_pool = ThreadPoolExecutor(max_workers=1)
        self.enable_sentiment_analysis = _config.system.enable_sentiment_analysis
        self.enable_split_by_punc = _config.system.enable_clause_split
        self.chat_window_config = _config.service.chat_window
        self.system_prompt = self.llm_prompt_manager.system_prompt
        self.context_manager = ContextManager()
        self.conversation_id = f"conv_{uuid4().hex}"
        self._turn_id_lock = threading.Lock()
        self._turn_id_counter = 0
        self._auto_chat_lock = threading.Lock()
        tool_config = _config.service.tool
        self.web_search_service = WebSearchService(
            load_web_search_config(_config),
            max_workers=tool_config.max_workers,
            max_rounds=tool_config.max_rounds,
            max_calls_per_round=tool_config.max_calls_per_round,
        )
        # 通用工具入口；其他模块和后续 Skills 无需依赖 WebSearchService。
        self.tool_runtime = self.web_search_service.tool_runtime
        self.tool_coordinator = self.web_search_service.tool_coordinator
        self.sandbox_service = SandboxService(
            _config.service.sandbox, self.tool_runtime, project_root=get_project_dir(),
        )
        self.mcp_service = MCPService(
            _config.service.mcp, self.tool_runtime, project_root=get_project_dir(),
        )
        self.browser_service = BrowserService(
            _config.service.browser, self.tool_runtime, self.sandbox_service, self.mcp_service,
        )
        self.model_tool_gateway = ModelToolGateway(
            self.tool_runtime, marker=tool_config.model_marker,
            policy_providers=(self.sandbox_service.model_instruction, self.mcp_service.model_instruction,
                              self.browser_service.model_instruction),
        )
        self.skill_service = SkillService(
            _config.service.skills,
            self.tool_runtime,
            project_root=get_project_dir(),
        )
        self.intent_router = BGEIntentRouter(
            load_intent_router_config(_config),
            tool_runtime=self.tool_runtime,
            auto_initialize=False,
        )
        # 初始化记忆管理器（支持记忆提取/合并使用单独模型配置）
        self.memory_manager: MemoryManager | None = None
        if _config.memory.enable:
            memory_model_configs = resolve_memory_model_configs(_config)
            self.memory_manager = MemoryManager(
                config=_config.memory,
                **memory_model_configs,
            )
            # 设置初始保存索引：注入块（system 提示 + injected_history）不参与压缩
            self.memory_manager.reset_saved_index(self.llm_prompt_manager.injected_len)
            logger.info("🧠 记忆管理器已初始化，并已设置注入历史索引。")
            if self.memory_manager:
                core_mem = self.memory_manager.get_core_memory()
                if core_mem:
                    self.system_prompt += f"\n\n关于用户的长期记忆:\n{core_mem}"
                    self.llm_prompt_manager.replace_system_prompt(self.system_prompt)
                    
        self._vlm_running = False          # VLM 是否正在执行
        self._vlm_result = None            # 最新 VLM 结果
        self._vlm_timer = None             # 计时器对象
        self._vlm_lock = threading.Lock()  # 线程锁
        self._vlm_stop = False             # 停止后台循环标志

        # 累积 VLM 结果列表，格式如 "【12:34:56】描述文本"
        self._vlm_continuous_results: List[str] = []
        # 无输入定时器，超时后自动发送累积结果
        self._vlm_continuous_timer: threading.Timer | None = None
        # ===== MLLM 集成（新增）=====
        self.mllm_cfg = _config.pipeline.mllm
        if self.mllm_cfg.enable:
            # 1. 禁用原有 LLM 和 VLM（覆盖基类赋值的属性）
            self.llm = None
            self.vlm = None
            # 2. 创建 MLLM Pipeline 客户端
            mllm_config = MLLMPipelineConfig(
                enable=self.mllm_cfg.enable,
                predict_url=self.mllm_cfg.predict_url,
                stream_predict_url=self.mllm_cfg.stream_predict_url,
                api_key=self.mllm_cfg.api_key,
                model_id=self.mllm_cfg.model_id,
            )
            self.mllm_pipeline = MLLMSyncPipeline(mllm_config)
            # 3. 截图管理器
            interval = self.mllm_cfg.screenshot_interval
            max_queue = self.mllm_cfg.max_queue_size
            self._mllm_screenshot_manager = MLLMScreenshotManager(self.screen, interval, max_queue)
            self._mllm_screenshot_manager.start()
            logger.info("🌐 已启用 MLLM，并停用 LLM 与 VLM；截图管理器已启动。")
        else:
            self.mllm_pipeline = None
            self._mllm_screenshot_manager = None
        # 麦克风ASR 累积文本缓冲区（用于拼接中间结果）
        self._asr_accumulated_text = ""
        self._asr_accumulated_lock = threading.Lock()
        self._pending_asr_count = 0
        self._final_pending = False
        self._asr_incomplete_since = 0.0
        self._asr_force_commit_timer: threading.Timer | None = None
        self._asr_timer_generation = 0
        self._asr_audio_chunks: list[np.ndarray] = []
        self._asr_audio_sample_rate = 0
        self._semantic_asr_interrupted = False
        self._semantic_asr_pending_complete = False
        self._semantic_asr_executor = ThreadPoolExecutor(max_workers=1)
        self.turnsense_detector = None
        self._refresh_semantic_asr_runtime_config(reset_state=False)
        # 桌面ASR缓冲区
        self.desktop_asr_buffer = []
        self.max_desktop_sentences = int(60 // _config.pipeline.asr.desktop_audio.send_interval)  # 根据发送间隔估算最大句子数
        self._mic_processing = False
        self._mic_processing_lock = threading.Lock()  # 保护标志的读写
        # 语音轮次是否仍然活跃：从收到语音块开始，直到这一轮文本真正提交为止。
        # 用它而不是"单块处理中"标志来抑制桌面 ASR，避免相邻音频块之间的空隙被抢跑（P1-16）。
        # 另外记录最后一个语音块的时间，防止某轮异常未提交时把桌面 ASR 永久挡住。
        self._mic_utterance_active = False
        self._mic_utterance_last_chunk = 0.0
        # 轮次标志的兜底过期时间（秒）
        self._mic_utterance_stale_seconds = 30.0
        self._asr_audio_sample_rate = 0
        # 语音轮次提交后是否需要重置远程流式 ASR（P1-14）
        self._reset_remote_after_turn = True
        # 桌面音频捕获初始化（受ASR启用控制）
        self.desktop_audio_capture = None
        if _config.pipeline.asr.enable and _config.pipeline.asr.desktop_audio.enable:
            from pipeline.asr.desktop_capture import DesktopAudioCapture
            desktop_cfg = _config.pipeline.asr.desktop_audio
            input_device = desktop_cfg.input_device
            self.desktop_audio_capture = DesktopAudioCapture(
                sample_rate=desktop_cfg.sample_rate,
                channels=desktop_cfg.channels,
                read_chunk_duration=desktop_cfg.chunk_duration,
                send_interval=desktop_cfg.send_interval,
                input_device=input_device
    )
            self.desktop_audio_capture.start()
            logger.info("已启用桌面音频捕获，设备：{}".format(input_device))

        # 去噪器初始化
        denoise_config = _config.pipeline.asr.denoise
        if denoise_config.mic_enable or denoise_config.desktop_enable:
            from pipeline.asr.denoiser import Denoiser
            self.denoiser = Denoiser(denoise_config)
        else:
            self.denoiser = None

        # TTS 任务管理
        self._tts_task_counter = 0
        self._tts_pending_futures = []      # 存储未完成的 Future
        self._tts_lock = threading.Lock()
        self._current_tts_future = None 
        self._interrupt_required = False
        self.speed_step = 0
        self._last_tts_enqueue_time = time.monotonic()

        self.init()
        logger.info("🤖 Bot 服务初始化成功。")

    def _next_turn_id(self) -> int:
        """返回当前对话范围内的下一个 turn_id。"""
        with self._turn_id_lock:
            self._turn_id_counter += 1
            return self._turn_id_counter

    async def start(self):
        logger.info("🤖 Bot 正在启动……")
        async with asyncio.TaskGroup() as tg:
            tg.create_task(emitter.start())
            threads = []
            if _config.service.microphone.enable:
                vad_thread = KillableThread(target=self.mic.start, daemon=True, name="VADThread")
                threads.append(vad_thread)

            if self.keyboard is not None:
                keyboard_thread = KillableThread(target=self.keyboard.start, daemon=True, name="KeyboardThread")
                threads.append(keyboard_thread)

            if self.intent_router.enabled:
                intent_router_thread = KillableThread(
                    target=self.intent_router.initialize,
                    daemon=True,
                    name="IntentRouterInitThread",
                )
                threads.append(intent_router_thread)

            speaker_thread = KillableThread(target=self.speaker.start, daemon=True, name="SpeakerThread")
            threads.append(speaker_thread)

            # 如果 VLM 启用且为常态模式，启动后台循环
            if self.vlm is not None and _config.pipeline.vlm.mode == "continuous" and not self.mllm_cfg.enable:
                vlm_thread = KillableThread(
                    target=self._vlm_continuous_loop,
                    daemon=True,
                    name="VLMContinuousThread"
                )
                threads.append(vlm_thread)

            for thread in threads:
                thread.start()

            try:
                await asyncio.sleep(0)
                logger.info("✅ Bot 已就绪。")
                self._runtime_ready.set()

                elapsed = 0
                while self._timer_flag:
                    await asyncio.sleep(1)
                    emitter.emit(SecondEvent(elapsed=elapsed))
                    elapsed += 1
            finally:
                await emitter.stop()
                kill_all_threads()
                self._runtime_stopped = True
            
        for thread in threads:
            thread.join(timeout=1)

    @property
    def runtime_ready(self):
        return self._runtime_ready.is_set()

    def request_stop(self):
        self._timer_flag = False

    async def stop(self):
        if self.memory_manager:
            self.memory_manager.maybe_save_snapshot(
                current_history=self.llm_prompt_manager.history_snapshot(),
                injected_len=self.llm_prompt_manager.injected_len,
                force=True,
                history_snapshot=self.llm_prompt_manager,
            )
            self.memory_manager.shutdown()
            logger.info("🧠 记忆管理器已关闭，并完成最终保存。")
        with self._vlm_lock:
            self._cancel_vlm_timer()
        with self._auto_chat_lock:
            self._cancel_auto_chat_timer()
        if self.desktop_audio_capture:
            self.desktop_audio_capture.stop()
        if self._mllm_screenshot_manager:
            self._mllm_screenshot_manager.stop()
        # 收尾顺序：先关麦克风采集并释放音频设备，再停 ASR 语义轮次与线程池。
        # 否则进程退出后麦克风可能仍被占用（设备句柄残留、其他程序打不开麦克风）。
        mic = getattr(self, "mic", None)
        if mic is not None:
            try:
                mic.shutdown()
            except Exception as exc:
                logger.warning(f"关闭麦克风采集时出错：{exc}")
        self._semantic_asr_reset(reset_remote=False, reset_microphone=False)
        self._semantic_asr_executor.shutdown(wait=False, cancel_futures=True)
        self.mcp_service.shutdown()
        self.web_search_service.shutdown()
        # 不等 TTS 队列排空：合成请求已有超时，等待只会拖住退出流程。
        self.tts_thread_pool.shutdown(wait=False, cancel_futures=True)
        if not self._runtime_stopped:
            await emitter.stop()
            kill_all_threads()
            self._runtime_stopped = True
        await stop_all_runnable()
        logger.info("再见！")

    def init(self):
        @emitter.on(EventKeyRegistry.Device.KEYBOARD_HOTKEY_PRESS)
        def hotkey_handler(event: DeviceKeyboardPressEvent):
            logger.info(f'收到热键切换事件：{event.hotkey}')
            # 判断 hotkey 内容
            try:
                if event.hotkey == _config.service.microphone.hotkey:
                    if _config.service.microphone.enable:
                        # 麦克风对象锁
                        with self.keyboard.microphone_state_lock:
                            if self.mic.is_set_talk_enabled_event():
                                logger.debug('热键已关闭麦克风。')

                                # 关麦
                                self.mic.unset_talk_enabled_event()

                                # 强制 emit 已经收集的片段
                                # 强制提交时，重置累积缓冲区（因为本次对话结束）
                                self._reset_asr_accumulator()
                                self.mic.force_commit(is_emit=True)

                                # TODO: 播放停止提示音
                                pass
                            else:
                                logger.debug('热键已开启麦克风。')

                                # 仅清空可能遗留的音频
                                self._reset_asr_accumulator()
                                self.mic.force_commit(is_emit=False)

                                # TODO: 播放开始提示音，block=True
                                pass

                                # 开麦
                                self.mic.set_talk_enabled_event()
                    else:
                        logger.info('config.yaml 中已禁用麦克风。')
                elif False:
                    # 示例
                    pass
            except Exception as e:
                logger.exception(e)

        @emitter.on(EventKeyRegistry.Device.MICROPHONE_SWITCH)
        def on_open_microphone(event: DeviceMicrophoneSwitchEvent):
            if self.mic.is_recording:
                if event.switch:
                    logger.warning("麦克风已经处于启用状态。")
                    return
                self.mic.pause()
            else:
                if not event.switch:
                    logger.warning("麦克风已经处于暂停状态。")
                    return
                self.mic.resume()

        @emitter.on(EventKeyRegistry.Device.MICROPHONE_VAD)
        def on_service_vad_speech_chunk(event: DeviceMicrophoneVADEvent):
            # 标记本轮语音活跃（跨音频块保持），供桌面 ASR 让路
            with self._mic_processing_lock:
                self._mic_processing = True
                self._mic_utterance_active = True
                self._mic_utterance_last_chunk = time.monotonic()
            try:
                self._reset_auto_chat_timer()
                # 提前重置一次continuous 自动上传计时器（为了解决计时器延后重置可能导致的用户输入和VLM超时输入同时触发的问题）
                speech, channels, sample_rate = event.speech, event.channels, event.sample_rate

                if _config.pipeline.asr.denoise.mic_enable and channels == 1:  # 只处理单声道，多声道暂不支持
                    logger.debug("正在对麦克风音频块应用降噪。")
                    try:
                        import io
                        import wave
                        # 1. 解码 WAV 到 numpy (int16 -> float32)
                        with io.BytesIO(speech) as wav_io:
                            with wave.open(wav_io, 'rb') as wf:
                                n_frames = wf.getnframes()
                                audio_int16 = np.frombuffer(wf.readframes(n_frames), dtype=np.int16)
                                audio_float = audio_int16.astype(np.float32) / 32768.0
                        # 2. 去噪处理
                        processed = self.denoiser.process(audio_float, sample_rate)
                        # 3. 转回 int16 并打包为 WAV
                        processed_int16 = (processed * 32768).clip(-32768, 32767).astype(np.int16)
                        with io.BytesIO() as out_io:
                            with wave.open(out_io, 'wb') as wf_out:
                                wf_out.setnchannels(1)          # 强制单声道
                                wf_out.setsampwidth(2)          # 16-bit
                                wf_out.setframerate(sample_rate)
                                wf_out.writeframes(processed_int16.tobytes())
                            speech = out_io.getvalue()
                        # 注意：去噪后强制单声道，但 channels 已经是 1，保持不变
                    except Exception as e:
                        logger.error(f"麦克风音频降噪失败，将使用原始音频：{e}")
                # if event.is_final and not speech:
                #     with self._asr_accumulated_lock:
                #         full_text = self._asr_accumulated_text.strip()
                #         self._asr_accumulated_text = ""
                #     if full_text:
                #         logger.info(f"最终 ASR 完整文本：{full_text}")
                #         dummy_pred = ASRPrediction(transcript=full_text)
                #         emitter.emit(PipelineASREvent(prediction=dummy_pred))
                #     else:
                #         logger.debug("已收到 ASR 最终标记，但累计文本为空，跳过处理。")
                #     return

                if self._is_semantic_asr_enabled():
                    logger.debug(
                        f"语义 ASR 路径已启用：is_final={event.is_final}，"
                        f"字节数={len(speech or b'')}"
                    )
                    self._submit_semantic_asr_task(
                        self._handle_semantic_streaming_asr,
                        event,
                        speech,
                        channels,
                        sample_rate,
                    )
                    return

                if event.is_final and not speech:
                    full_text = self._mark_asr_final()
                    if full_text:
                        self._emit_final_asr_text(full_text)
                    return

                with self._asr_accumulated_lock:
                    self._pending_asr_count += 1
                    logger.debug(f"ASR 待处理计数增加，当前为 {self._pending_asr_count}")

                transcript = ""
                append_transcript = False
                try:
                    query = ASRStreamQuery(
                        is_final=event.is_final,
                        audio_data=speech,
                        channels=channels,
                        sample_rate=sample_rate,
                        media_type=event.audio_type.value
                    )
                    for prediction in self.asr.stream_predict(query):
                        candidate = prediction.transcript
                        if not ASR_result_filter(candidate):
                            transcript = candidate
                            append_transcript = True
                            break

                    if append_transcript:
                        self._interrupt_tts()
                    else:
                        logger.info("ASR 结果已被过滤。")
                except Exception as e:
                    logger.error(f"ASR 流式识别失败：{e}", exc_info=True)
                finally:
                    full_text = self._finish_asr_request(
                        transcript=transcript if append_transcript else "",
                        is_final=event.is_final,
                    )
                    if full_text:
                        self._emit_final_asr_text(full_text)
            except Exception as e:
                logger.exception("处理 VAD 语音块时发生未处理异常。")
            finally:
                with self._mic_processing_lock:
                    self._mic_processing = False
                    if event.is_final or not self._asr_utterance_pending():
                        # 这一轮已经结束（或已无待提交文本），允许桌面 ASR 恢复
                        self._mic_utterance_active = False

        def on_text_input(text: str):
            """
            纯文本输入接口，模拟 ASR 识别结果，触发完整的对话处理流程。
            :param text: 用户输入的文本
            """
            # 构造一个简易的预测对象，只需具备 transcript 属性
            class DummyPrediction:
                def __init__(self, transcript):
                    self.transcript = transcript

            prediction = DummyPrediction(text)
            # 发出 PipelineASREvent，让 asr_handler 处理
            emitter.emit(PipelineASREvent(prediction=prediction))
        

        @emitter.on(EventKeyRegistry.Pipeline.ASR)
        def asr_handler(event: PipelineASREvent):
            logger.debug("收到 ASR 事件。")
            prediction = event.prediction
            transcript = event.prediction.transcript

            # 过滤空或仅含“嗯/啊”等填充词
            if ASR_result_filter(transcript):
                logger.debug(f"ASR 结果已过滤：'{transcript}'")
                return
            # 桌面ASR：仅积累上下文，不触发LLM
            if event.source == "desktop":
                if transcript:
                    self.desktop_asr_buffer.append(transcript)
                    if len(self.desktop_asr_buffer) > self.max_desktop_sentences:
                        self.desktop_asr_buffer.pop(0)
                    logger.debug(f"桌面 ASR：{transcript}")
                return

            turn_id = self._next_turn_id()

            user_skill_invocation = self.skill_service.parse_user_invocation(transcript)
            if user_skill_invocation is not None:
                remaining_text, skill = user_skill_invocation
                logger.info(
                    f"用户显式加载 Skill：Skill={skill.name}，turn_id={turn_id}"
                )
                self.emit_llm_prediction(
                    remaining_text or "请按照已加载的 Skill 执行当前任务。",
                    skip_tts=event.skip_tts,
                    _skill_context=self.skill_service.render_skill(skill),
                    _skip_web_route=True,
                    _turn_id=turn_id,
                )
                return

            router_active = bool(
                self.intent_router.enabled and self.intent_router.available
            )
            route_decision = self.intent_router.route(
                transcript, context={"conversation_id": self.conversation_id, "turn_id": turn_id}
            ) if router_active else None
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
            # 启用语义路由后，即使本地模型暂时不可用，也默认替代旧关键词匹配；
            # 此时原始请求继续交给云端模型选择工具。只有显式开启降级配置时，
            # 才恢复旧关键词匹配。
            allow_keyword_match = self.intent_router.allow_keyword_fallback

            if wants_vision:
                if not (_config.pipeline.mllm.enable or _config.pipeline.vlm.mode == 'continuous'):
                    img, img_save_path = self.screen.safe_capture(k=0.99)
                    if not self.check_img(img, send_message=False):
                        self.emit_llm_prediction(
                            f"{prediction.transcript}，但我什么也看不见。",
                            skip_tts=event.skip_tts,
                            _turn_id=turn_id,
                        )
                        return
                    if self.vlm is not None:
                        # 发送第一条消息（不带视觉内容），会自动追加“正在获取”提示
                        # self.emit_llm_prediction(prediction.transcript, skip_tts=event.skip_tts)
                        # 异步启动 VLM
                        self._vlm_async_predict(str(img_save_path), callback=self._on_vlm_result_ready)
            else:
                image_paths = None
                if self.mllm_cfg.enable and self._mllm_screenshot_manager:
                    image_paths = self._mllm_screenshot_manager.pop_all()
                if routed_tool_decision is not None:
                    self._submit_routed_tool(
                        routed_tool_decision,
                        prediction.transcript,
                        image_paths,
                        event.skip_tts,
                        turn_id,
                    )
                    return
                self.emit_llm_prediction(
                    prediction.transcript,
                    image_paths=image_paths,
                    skip_tts=event.skip_tts,
                    _allow_keyword_match=allow_keyword_match,
                    _turn_id=turn_id,
                )
                return
            image_paths = None
            if self.mllm_cfg.enable and self._mllm_screenshot_manager:
                image_paths = self._mllm_screenshot_manager.pop_all()
            self.emit_llm_prediction(
                prediction.transcript,
                image_paths=image_paths,
                skip_tts=event.skip_tts,
                _allow_keyword_match=allow_keyword_match,
                _turn_id=turn_id,
            )
            return
        
        @emitter.on(EventKeyRegistry.Device.DESKTOP_AUDIO)
        def on_desktop_audio(event: DeviceDesktopAudioEvent):
            # 麦克风一轮语音未结束前，桌面 ASR 一律让路（P1-16）
            if not self._desktop_asr_allowed():
                return
                
            audio_data = event.audio_data  # bytes of float32
            if _config.pipeline.asr.denoise.desktop_enable:
                try:
                    # 转为 numpy float32
                    audio_array = np.frombuffer(audio_data, dtype=np.float32)
                    # 去噪
                    processed = self.denoiser.process(audio_array, event.sample_rate)
                    # 转回 bytes
                    audio_data = processed.tobytes()
                except Exception as e:
                    logger.error(f"桌面音频降噪失败，将使用原始音频：{e}")
            query = ASRStreamQuery(
                is_final=True,
                audio_data=audio_data,
                channels=event.channels,
                sample_rate=event.sample_rate,
                media_type=event.media_type
            )
            for prediction in self.asr.stream_predict(query):
                if prediction.transcript and not is_blank(prediction.transcript):
                    emitter.emit(PipelineASREvent(
                        prediction=prediction,
                        source="desktop",
                        skip_tts=True
                    ))
            # 成功上传后，清空音频缓冲区
            #if self.desktop_audio_capture:
                # self.desktop_audio_capture.clear_buffer()

        @emitter.on(EventKeyRegistry.Device.SCREEN_CAPTURED)
        def on_device_screen_captured(event: DeviceScreenCapturedEvent):
            if self.mllm_cfg.enable:
                return
            img_path = event.img_path
            if isinstance(event.img_path, Path):
                img_path = str(event.img_path)
            if self.vlm is not None:
                # 使用 VLM 直接理解
                try:
                    caption = self.vlm.predict(VLMQuery(img_path=img_path, prompt="请客观简短地描述这张图片的主要内容和信息，不要包含冗余信息和猜测，不需要前缀（如“好的，以下是图片的主要内容描述”）"))
                    if caption:
                        logger.info(f"VLM 结果：{caption}")
                        # 拼装发送给 LLM
                        text = f"你看到了一张图片，内容为：{caption}。请根据此信息回答用户。"
                        self.emit_llm_prediction(text, skip_tts=event.skip_tts)
                        return
                except Exception as e:
                    logger.exception(e)

            # 降级方案：如果 VLM 不可用，使用原有的 OCR + ImgCap
            ocr_prediction = self.ocr.predict(OCRQuery(img_path=img_path))
            if avg_confidence(ocr_prediction) > 0.6:
                logger.info("OCR 结果：" + stringify(ocr_prediction.region_results))
                emitter.emit(PipelineOCREvent(prediction=ocr_prediction))
            else:
                img_cap_prediction = self.img_cap.predict(ImgCapQuery(prompt="There", img_path=img_path))
                src_lang = Language.value_of(img_cap_prediction.lang)
                caption = translate(src_lang, self.cur_lang, img_cap_prediction.caption)
                img_cap_prediction.caption = caption
                logger.info("图像描述结果：" + caption)
                emitter.emit(PipelineImgCapEvent(prediction=img_cap_prediction))

        
        @emitter.on(EventKeyRegistry.Pipeline.OCR)
        def on_pipeline_ocr(event: PipelineOCREvent):
            prediction = event.prediction
            text = "你看见了" + stringify(prediction.region_results) + "\n请总结一下"
            self.emit_llm_prediction(text)

        @emitter.on(EventKeyRegistry.Pipeline.IMG_CAP)
        def on_pipeline_img_cap(event: PipelineImgCapEvent):
            prediction = event.prediction
            text = "你看见了" + prediction.caption
            self.emit_llm_prediction(text)

        @emitter.on(EventKeyRegistry.Pipeline.LLM)
        def llm_query_handler(event: PipelineOutputLLMEvent):
            prediction = event.prediction
            text = prediction.response
            logger.info("LLM 回复：" + text)
            streamed_tts_result = event.skip_tts and isinstance(prediction, MLLMPrediction)
            tts_prompt = None
            ui_emotion = None
            if not streamed_tts_result:
                # 界面表情取自"模型给的情绪标签"；TTS 参考音频里没有对应情绪时
                # 只退化成 Default 音色，不该顺手把猫猫的表情也抹成默认。
                if event.emotion:
                    ui_emotion = event.emotion
                elif self.enable_sentiment_analysis:
                    ui_emotion = sentiment_analyse(sentiments=self.tts_prompt_manager.sentiments, text=text)
                else:
                    ui_emotion = self.tts_prompt_manager.default_tts_prompt.sentiment
                tts_prompt = self.tts_prompt_manager.get_tts_prompt(ui_emotion)
                emitter.emit(CharacterStateEvent(state="replied", emotion=ui_emotion))
            if event.skip_tts:
                # 仅打印日志，不播放语音
                logger.info("文本输入模式已跳过 TTS。")
            else:
                with self._tts_lock:
                    tts_round_task_id = self._tts_task_counter
                if self._enqueue_tts_text(text, tts_prompt, ui_emotion):
                    self._tts_round_end_without_block(tts_round_task_id)

        @emitter.on(EventKeyRegistry.System.CONFIG_FILE_MODIFIED)
        def on_config_modified(_: ConfigFileModifiedEvent):
            global _config
            _config = get_config()
            configure_harness(project_root=Path(__file__).resolve().parent, **_config.service.harness.model_dump())
            self.web_search_service.update_config(load_web_search_config(_config))
            self.tool_coordinator.update_limits(
                max_rounds=_config.service.tool.max_rounds,
                max_calls_per_round=_config.service.tool.max_calls_per_round,
            )
            self.skill_service.update_config(_config.service.skills)
            self.model_tool_gateway.update_marker(_config.service.tool.model_marker)
            self.sandbox_service.update_config(_config.service.sandbox)
            self.mcp_service.update_config(_config.service.mcp)
            self.browser_service.update_config(_config.service.browser)
            self.intent_router = BGEIntentRouter(
                load_intent_router_config(_config),
                tool_runtime=self.tool_runtime,
                auto_initialize=False,
            )
            if self.intent_router.enabled:
                threading.Thread(
                    target=self.intent_router.initialize,
                    daemon=True,
                    name="IntentRouterReloadThread",
                ).start()
            self._refresh_semantic_asr_runtime_config(reset_state=True)

    def _tts_without_block(
        self, tts_prompt: TTSPrompt, text: str, speed_factor: float = 1.0,
        emotion: str | None = None,
    ):
        def wrapper():
            with self._tts_lock:
                task_id = self._tts_task_counter
            try:
                query = TTSQuery(
                    text=text,
                    text_language="auto",
                    refer_wav_path=tts_prompt.audio_path,
                    prompt_text=tts_prompt.prompt_text,
                    prompt_language=tts_prompt.lang,
                    audio_type="wav",
                    speed_factor=speed_factor
                )
                prediction = self.tts.predict(query=query)

                if _config.pipeline.tts.cancel_on_new_input:
                    with self._tts_lock:
                        if task_id != self._tts_task_counter:
                            logger.info(f"TTS 任务 {task_id} 已过期，跳过播放。")
                            return
                self.play_tts(PipelineOutputTTSEvent(
                    prediction=prediction,
                    transcript=text,
                    # 用界面情绪而不是参考音频的情绪：只有 [Default] 音频时
                    # 后者恒为 Default，桌宠表情会一直没反应。
                    emotion=emotion or tts_prompt.sentiment,
                ))

            except Exception as e:
                logger.error(f"TTS 任务 {task_id} 失败：{e}")

        future = self.tts_thread_pool.submit(wrapper)

        def done_callback(f):
            with self._tts_lock:
                if f in self._tts_pending_futures:
                    self._tts_pending_futures.remove(f)

        with self._tts_lock:
            self._tts_pending_futures.append(future)
            self._current_tts_future = future
        future.add_done_callback(done_callback)

    def _tts_round_end_without_block(self, task_id: int):
        def wrapper():
            while True:
                with self._tts_lock:
                    if task_id != self._tts_task_counter:
                        logger.debug("TTS 轮次已过期，跳过角色状态恢复。")
                        return
                    pending_tts = bool(self._tts_pending_futures)
                audio_queue = getattr(self.speaker, "audio_clips", None)
                speaker_idle = not bool(getattr(self.speaker, "is_playing", False)) and (
                    audio_queue is None or audio_queue.empty()
                )
                if not pending_tts and speaker_idle:
                    break
                time.sleep(0.05)
            with self._tts_lock:
                if task_id != self._tts_task_counter:
                    logger.debug("TTS 轮次已过期，跳过角色状态恢复。")
                    return
                if _config.pipeline.tts.adaptive_speed.enable and self.speed_step > 0:
                    self.speed_step = max(self.speed_step - 1, 0)
                    logger.debug(f"TTS 轮次完成，speed_step 已递减为 {self.speed_step}。")
            state = "listening" if self.mic.is_recording else "idle"
            emitter.emit(CharacterStateEvent(state=state))

        threading.Thread(target=wrapper, name="TTSRoundEnd", daemon=True).start()

    def _build_turn_notices(self, *, tool_gate_open: bool, session_has_tools: bool, tool_round: int) -> list[str]:
        """生成"本轮系统状态"声明。

        这些内容每轮可能不同，所以必须放在用户轮里，不能写进 system（否则系统前缀
        每轮变化，prompt 缓存全废）。作用是把"闸门状态"告诉模型，而不是靠删掉
        工具协议来阻止调用——后者会让模型白调一次、被丢弃，主人拿不到回答。
        """
        notices: list[str] = []
        if session_has_tools and not tool_gate_open and tool_round >= _config.service.tool.max_rounds:
            notices.append(
                f"本轮的工具调用轮数已达上限（{_config.service.tool.max_rounds} 轮），"
                "工具本轮不可用：不要输出任何工具调用标记，直接用已有信息给出最终回答。"
            )
        return notices

    def _allowed_emotions(self) -> list[str]:
        """允许模型输出的情绪标签（来自 character.emotions，而不是 TTS 参考音频文件名）。

        以前这份白名单取自 `tts_prompt_manager.sentiments`，而它取决于参考音频文件名的
        情绪段。只有 Default 音频时白名单就只剩 Default，模型输出的任何情绪都会被丢弃，
        界面表情因此恒为默认。现在配置说了算，TTS 找不到对应音频时按 Default 播音。
        """
        emotions = getattr(getattr(_config, "character", None), "emotions", None) or []
        tags = [str(item).strip() for item in emotions if str(item).strip()]
        if DEFAULT_EMOTION not in tags:
            tags.insert(0, DEFAULT_EMOTION)
        return tags

    def _resolve_emotion_tag(self, requested_emotion: str | None) -> str | None:
        """校验模型请求的情绪标签；不在白名单内返回 None（保持原行为）。"""
        if not requested_emotion:
            return None
        allowed = self._allowed_emotions()
        if any(requested_emotion.casefold() == item.casefold() for item in allowed):
            return normalize_emotion(requested_emotion, allowed, DEFAULT_EMOTION)
        return None

    def _strip_response_emotion(self, response: str, *, has_pending_tool: bool) -> tuple[str, str | None]:
        """剥离可见回复开头的 `<emotion>…</emotion>`，返回（可见文本, 已校验情绪）。

        工具轮的中间回复同样会进气泡、进当前 turn 的历史并送 TTS，因此无论本轮是否
        有工具调用都必须剥离标签：只在最终回复分支剥离会让 `<emotion>` 原样泄漏给用户。
        """
        _, visible_response, requested_emotion = consume_emotion_prefix(response or "", flush=True)
        visible_text = visible_response.strip()
        emotion = self._resolve_emotion_tag(requested_emotion) if requested_emotion else None
        if has_pending_tool and not visible_text:
            # 模型只回了一个情绪标签就去调工具：给一句中性承接语，避免空回复。
            visible_text = "稍等，我处理一下。"
        return visible_text, emotion

    def _select_tts_prompt(self, text: str) -> TTSPrompt:
        if self.enable_sentiment_analysis:
            sentiment = sentiment_analyse(sentiments=self.tts_prompt_manager.sentiments, text=text)
            return self.tts_prompt_manager.get_tts_prompt(sentiment)
        return self.tts_prompt_manager.default_tts_prompt

    def _prepare_tts_text(self, text: str) -> str:
        if _config.pipeline.tts.filter:
            return clean_text_for_tts(text)
        return text

    def _enqueue_tts_text(
        self, text: str, tts_prompt: TTSPrompt | None = None, emotion: str | None = None
    ) -> bool:
        tts_text = self._prepare_tts_text(text)
        if not tts_text:
            return False
        prompt = tts_prompt or self._select_tts_prompt(tts_text)
        logger.debug(f"TTS 文本：{tts_text}")
        adaptive_speed = _config.pipeline.tts.adaptive_speed
        speed_factor = 1.0
        if adaptive_speed.enable:
            long_text_threshold = max(int(adaptive_speed.long_text_threshold), 1)
            max_speed = max(float(adaptive_speed.max_speed), 1.0)
            max_speed_step = max(int(round((max_speed - 1.0) / 0.05)), 0)
            idle_decay_seconds = max(float(getattr(adaptive_speed, "idle_decay_seconds", 0.0)), 0.0)
            long_text_speed_steps = 0
            if len(tts_text) > long_text_threshold:
                long_text_speed_steps = int((len(tts_text) - long_text_threshold) / (long_text_threshold * 0.05))
            with self._tts_lock:
                now = time.monotonic()
                if idle_decay_seconds > 0 and self.speed_step > 0:
                    idle_steps = int((now - self._last_tts_enqueue_time) // idle_decay_seconds)
                    if idle_steps > 0:
                        self.speed_step = max(self.speed_step - idle_steps, 0)
                        logger.debug(
                            f"TTS 空闲衰减：空闲步数={idle_steps}，"
                            f"speed_step={self.speed_step}。"
                        )
                self._last_tts_enqueue_time = now
                interrupt_speed_step = self.speed_step
                effective_speed_step = min(max(interrupt_speed_step + long_text_speed_steps, 0), max_speed_step)
            speed_factor = 1.0 + effective_speed_step * 0.05
            logger.debug(
                f"TTS 自适应语速：打断增量={interrupt_speed_step}，"
                f"长文本增量={long_text_speed_steps}，有效增量={effective_speed_step}，"
                f"语速倍率={speed_factor:.2f}"
            )
        else:
            with self._tts_lock:
                self._last_tts_enqueue_time = time.monotonic()
        enqueued = False
        if self.enable_split_by_punc:
            transcripts = split_by_punc(tts_text, self.cur_lang)
            if len(transcripts) > 0:
                for idx, transcript in enumerate(transcripts):
                    self._tts_without_block(prompt, transcript, speed_factor, emotion)
                    enqueued = True
            else:
                self._tts_without_block(prompt, tts_text, speed_factor, emotion)
                enqueued = True
        else:
            self._tts_without_block(prompt, tts_text, speed_factor, emotion)
            enqueued = True
        return enqueued

    def _pop_stream_tts_segments(self, buffer: str, flush: bool = False) -> tuple[list[str], str]:
        if not buffer:
            return [], ""
        segments = []
        start = 0
        for match in re.finditer(r"[。！？!?；;\n]+", buffer):
            end = match.end()
            segment = buffer[start:end].strip()
            if segment:
                segments.append(segment)
            start = end
        rest = buffer[start:]
        if flush and rest.strip():
            segments.append(rest.strip())
            rest = ""
        return segments, rest

    @staticmethod
    def _merge_web_search_requests(
        primary: SearchRequest,
        pending: list[SearchRequest],
    ) -> SearchRequest:
        requests_to_merge = [primary, *pending]
        texts = [item.text.strip() for item in requests_to_merge if item.text and item.text.strip()]
        if len(texts) <= 1:
            merged_text = texts[0] if texts else ""
        else:
            merged_text = texts[0] + "\n\n【用户在联网等待期间补充】\n" + "\n".join(texts[1:])
        return SearchRequest(
            text=merged_text,
            image_paths=[path for item in requests_to_merge for path in item.image_paths],
            images_base64=[image for item in requests_to_merge for image in item.images_base64],
            skip_tts=all(item.skip_tts for item in requests_to_merge),
            directive=primary.directive,
            allow_keyword_match=primary.allow_keyword_match,
            conversation_id=primary.conversation_id,
            turn_id=primary.turn_id,
            round=primary.round,
            turn_history=primary.turn_history,
        )

    @staticmethod
    def _render_tool_output(result) -> str:
        """把工具结果渲染成注入上下文的文本；两条工具路径共用同一套上限。

        本地路由工具（intent -> ToolRuntime）与通用工具的结果进入同一个上下文预算，
        因此统一用 service.tool.max_result_chars 截断，并显式标注，避免模型误以为读全了。
        """
        limit = int(_config.service.tool.max_result_chars)
        output = json.dumps(result.output, ensure_ascii=False, default=str) if result.ok else str(result.error)
        if len(output) > limit:
            output = output[:limit] + "\n【工具结果已截断】"
        return output

    def _emit_tool_acknowledgement(self, text: str, skip_tts: bool):
        acknowledgement = (text or self.web_search_service.config.acknowledgement).strip()
        if not acknowledgement:
            return
        # 与 system_prompt 同一套模板渲染：配置里写 {bot_name} 才会变成角色名
        acknowledgement = self.llm_prompt_manager.render_bot_name(acknowledgement)
        prediction_cls = MLLMPrediction if self.mllm_cfg.enable else LLMPrediction
        prediction = prediction_cls(response=acknowledgement, history=[])
        emitter.emit(PipelineOutputLLMEvent(prediction=prediction, skip_tts=skip_tts))

    def _submit_routed_tool(
        self,
        route_decision,
        original_text: str,
        image_paths,
        skip_tts: bool,
        turn_id: int,
    ) -> None:
        """通过共享 ToolRuntime 执行所有本地路由选中的工具。"""
        call = route_decision.tool_call.with_context(
            conversation_id=self.conversation_id,
            turn_id=turn_id,
            round=1,
        )
        primary = SearchRequest(
            text=original_text,
            image_paths=list(image_paths or []),
            skip_tts=skip_tts,
            conversation_id=self.conversation_id,
            turn_id=turn_id,
            round=call.round,
        )

        def on_ready(primary_request, pending, results):
            merged = self._merge_web_search_requests(primary_request, pending)
            contexts = []
            for result in results:
                if result.ok:
                    logger.info(
                        f"工具调用完成：conversation_id={result.call.conversation_id}，"
                        f"turn_id={result.call.turn_id}，round={result.call.round}，"
                        f"call_id={result.call.call_id}，工具={result.call.name}，"
                        f"耗时={result.duration_ms:.1f} ms"
                    )
                    contexts.append(
                        f"工具：{result.call.name}\n"
                        f"执行耗时：{result.duration_ms:.1f} ms\n"
                        f"结果：{self._render_tool_output(result)}"
                    )
                else:
                    contexts.append(
                        f"工具：{result.call.name}\n执行失败：{self._render_tool_output(result)}"
                    )
            self.emit_llm_prediction(
                merged.text,
                image_paths=merged.image_paths or None,
                skip_tts=merged.skip_tts,
                _tool_context="\n\n".join(contexts),
                _turn_history=merged.turn_history,
                _skip_web_route=True,
                _allow_model_search=True,
                _images_base64=merged.images_base64 or None,
                _turn_id=turn_id,
                _tool_round=call.round,
                _continue_tool_turn=True,
            )

        try:
            submission = self.tool_coordinator.submit(
                call,
                primary=primary,
                on_ready=on_ready,
                on_deferred=self._on_web_search_deferred,
            )
        except RuntimeError as exc:
            logger.warning(f"关闭过程中已忽略本地路由工具调用：{exc}")
            return

        if submission.status == ToolCoordinationStatus.STARTED:
            logger.info(
                f"工具调用已启动：conversation_id={call.conversation_id}，"
                f"turn_id={call.turn_id}，round={call.round}，call_id={call.call_id}，"
                f"工具={call.name}，来源={call.source}"
            )
            acknowledgement = route_decision.acknowledgement or "稍等，我处理一下。"
            self._cancel_pending_tts()
            self._emit_tool_acknowledgement(acknowledgement, skip_tts)
        elif submission.status == ToolCoordinationStatus.BUFFERED:
            logger.info(
                f"工具请求已进入缓冲区：conversation_id={self.conversation_id}，"
                f"turn_id={turn_id}"
            )
        elif submission.status == ToolCoordinationStatus.REJECTED:
            logger.info(
                f"工具请求被拒绝："
                f"conversation_id={self.conversation_id}，turn_id={turn_id}"
            )

    def _on_web_search_ready(
        self,
        primary: SearchRequest,
        pending: list[SearchRequest],
        context: str,
        _error: str | None,
    ):
        merged = self._merge_web_search_requests(primary, pending)
        self.emit_llm_prediction(
            merged.text,
            image_paths=merged.image_paths or None,
            skip_tts=merged.skip_tts,
            _tool_context=context,
            _turn_history=merged.turn_history,
            _skip_web_route=True,
            _images_base64=merged.images_base64 or None,
            _allow_model_search=True,
            _turn_id=merged.turn_id,
            _tool_round=merged.round,
            _continue_tool_turn=True,
        )

    def _on_web_search_deferred(self, pending: list[SearchRequest]):
        if not pending:
            return
        merged = self._merge_web_search_requests(pending[0], pending[1:])
        self.emit_llm_prediction(
            merged.text,
            image_paths=merged.image_paths or None,
            skip_tts=merged.skip_tts,
            _images_base64=merged.images_base64 or None,
            _web_directive=merged.directive,
            _allow_keyword_match=merged.allow_keyword_match,
            _turn_id=merged.turn_id,
            _tool_round=max(merged.round - 1, 0),
            _turn_history=merged.turn_history,
        )

    def _begin_model_skill(
        self,
        request: SearchRequest,
        directive: ModelSkillDirective | ModelSkillCreateDirective,
        *,
        continue_active: bool = False,
    ) -> ToolCoordinationStatus:
        """在当前 turn 内加载或创建模型选中的 Skill。"""
        operation = "加载" if directive.inject_result_as_skill else "创建"
        call = directive.tool_call().with_context(
            conversation_id=request.conversation_id or self.conversation_id,
            turn_id=request.turn_id or self._next_turn_id(),
            round=request.round,
        )

        def on_ready(primary, pending, results):
            merged = self._merge_web_search_requests(primary, pending)
            result = results[0]
            if result.ok:
                logger.info(
                    f"Skill {operation}完成：conversation_id={result.call.conversation_id}，"
                    f"turn_id={result.call.turn_id}，round={result.call.round}，"
                    f"call_id={result.call.call_id}，Skill={directive.name}，"
                    f"耗时={result.duration_ms:.1f} ms"
                )
                if directive.inject_result_as_skill:
                    skill_context = str(result.output)
                    tool_context = None
                else:
                    skill_context = None
                    tool_context = str(result.output)
            else:
                logger.error(
                    f"Skill“{directive.name}”{operation}失败：{result.error}"
                )
                skill_context = None
                tool_context = (
                    f"Skill“{directive.name}”{operation}失败：{result.error}"
                )
            self.emit_llm_prediction(
                merged.text,
                image_paths=merged.image_paths or None,
                skip_tts=merged.skip_tts,
                _tool_context=tool_context,
                _turn_history=merged.turn_history,
                _skill_context=skill_context,
                _skip_web_route=True,
                _allow_model_search=True,
                _images_base64=merged.images_base64 or None,
                _turn_id=call.turn_id,
                _tool_round=call.round,
                _continue_tool_turn=True,
            )

        try:
            submission = self.tool_coordinator.submit(
                call,
                primary=request,
                on_ready=on_ready,
                on_deferred=self._on_web_search_deferred,
                continue_active=continue_active,
            )
        except RuntimeError as exc:
            logger.warning(f"关闭过程中已忽略 Skill {operation}请求：{exc}")
            return ToolCoordinationStatus.REJECTED
        if submission.status == ToolCoordinationStatus.STARTED:
            logger.info(
                f"Skill {operation}已启动：conversation_id={call.conversation_id}，"
                f"turn_id={call.turn_id}，round={call.round}，call_id={call.call_id}，"
                f"Skill={directive.name}"
            )
        return submission.status

    def _begin_model_tool(
        self,
        request: SearchRequest,
        tool_call,
        *,
        skill_context: str | None = None,
        continue_active: bool = False,
    ) -> ToolCoordinationStatus:
        """通过共享调度器执行网页研究、MCP 或 Sandbox 工具，保持同一 turn。"""
        call = tool_call.with_context(
            conversation_id=request.conversation_id or self.conversation_id,
            turn_id=request.turn_id or self._next_turn_id(),
            round=request.round,
        )

        def on_ready(primary, pending, results):
            merged = self._merge_web_search_requests(primary, pending)
            contexts = []
            for result in results:
                output = self._render_tool_output(result)
                limit = int(_config.service.tool.max_result_chars)
                arguments = json.dumps(result.call.arguments, ensure_ascii=False, default=str)
                if len(arguments) > limit:
                    arguments = arguments[:limit] + "\n【工具参数已截断】"
                contexts.append(
                    f"工具：{result.call.name}\n参数：{arguments}\n"
                    f"状态：{'成功' if result.ok else '失败'}\n结果：{output}"
                )
                logger.info(
                    f"通用工具执行完成：工具={result.call.name}，turn_id={call.turn_id}，"
                    f"round={call.round}，call_id={result.call.call_id}，成功={result.ok}"
                )
            self.emit_llm_prediction(
                merged.text,
                image_paths=merged.image_paths or None,
                skip_tts=merged.skip_tts,
                _tool_context="\n\n".join(contexts),
                _turn_history=merged.turn_history,
                _skill_context=skill_context,
                _skip_web_route=True,
                _images_base64=merged.images_base64 or None,
                _turn_id=call.turn_id,
                _tool_round=call.round,
                _continue_tool_turn=True,
            )

        try:
            submission = self.tool_coordinator.submit(
                call, primary=request, on_ready=on_ready,
                on_deferred=self._on_web_search_deferred,
                continue_active=continue_active,
            )
        except RuntimeError as exc:
            logger.warning(f"关闭过程中已忽略通用工具请求：{exc}")
            return ToolCoordinationStatus.REJECTED
        return submission.status

    def _extract_tool_visible_text(self, buffer: str, flush: bool = False) -> tuple[str, str]:
        """按实际配置统一隐藏各类机器协议，供日志、历史和 TTS 共用。

        隐藏两类东西：
        1. 本应用自己的机器协议标记（工具/联网/Skill）——有开关时才有意义；
        2. 模型自造的伪系统块（`<system>`/`<analysis>` 等）——2026-10-12 真实泄漏事故：
           模型把自造的内部计划当正文吐出来，进了气泡、TTS 与历史。它们没有"开关"，
           一律丢弃（见 `common/pseudo_markers.py`）。
        """
        web_config = getattr(self.web_search_service, "config", None)
        skill_config = getattr(self.skill_service, "config", None)
        markers = (
            self.model_tool_gateway.marker,
            getattr(web_config, "model_marker", ""),
            getattr(skill_config, "model_marker", ""),
            getattr(skill_config, "model_create_marker", ""),
        )
        visible, pending = self.model_tool_gateway.extract_stream_visible_text(
            buffer, flush=flush, markers=markers
        )
        # 先对"已隐藏完机器协议"的可见部分做伪系统块过滤。两个过滤器的输出契约不同：
        # `visible` 是已经判定为可见的文本，`pending` 是各方都没判定的尾巴（可能是半截
        # 机器标记，也可能只是普通正文）。把 pending 拼回去再过一遍就会让半截标记重新
        # 变成"可见文本"——所以这里只按 pending 的长度从尾部保留，绝不放出。
        segments = iter_visible_segments(visible, flush=flush)
        plain = "".join(segment for segment, _ in segments)
        consumed = segments[-1][1] if segments else 0
        pseudo_pending = visible[consumed:]
        if pending:
            return plain, pseudo_pending + pending
        return plain, pseudo_pending

    def emit_llm_prediction(
        self,
        text,
        image_paths=None,
        direct_return=False,
        skip_tts=False,
        _tool_context: str | None = None,
        _skill_context: str | None = None,
        _skip_web_route: bool = False,
        _images_base64: list[str] | None = None,
        _allow_model_search: bool = True,
        _web_directive: LocalSearchDirective | None = None,
        _allow_keyword_match: bool = True,
        _turn_id: int | None = None,
        _tool_round: int = 0,
        _continue_tool_turn: bool = False,
        _turn_history: tuple[Conversation, ...] = (),
    ) -> LLMPrediction | None:
        turn_id = _turn_id if _turn_id is not None else self._next_turn_id()
        logger.debug(
            f"调用 emit_llm_prediction：conversation_id={self.conversation_id}，"
            f"turn_id={turn_id}，已完成工具轮数={_tool_round}"
        )
        if not _skip_web_route and not direct_return and text:
            web_request = SearchRequest(
                text=text,
                image_paths=list(image_paths or []),
                images_base64=list(_images_base64 or []),
                skip_tts=skip_tts,
                conversation_id=self.conversation_id,
                turn_id=turn_id,
                round=_tool_round + 1,
                turn_history=_turn_history,
            )
            submission = self.web_search_service.submit_or_queue(
                web_request,
                self._on_web_search_ready,
                self._on_web_search_deferred,
                directive=_web_directive,
                allow_keyword_match=_allow_keyword_match,
            )
            if submission.status == SearchSubmissionStatus.STARTED:
                logger.info(
                    f"联网工具已启动：路由={submission.route_name or '旧关键词'}，"
                    f"工具={submission.tool or 'search'}，查询={submission.query}"
                )
                self._cancel_pending_tts()
                self._emit_tool_acknowledgement(
                    submission.acknowledgement
                    or self.web_search_service.config.acknowledgement,
                    skip_tts,
                )
                return None
            if submission.status == SearchSubmissionStatus.QUEUED:
                logger.info("工具调用期间收到的 LLM 请求已进入缓冲区。")
                return None
            if submission.status == SearchSubmissionStatus.REJECTED:
                logger.info("独占工具运行期间已拒绝新的 LLM 请求。")
                return None

        # 感知结果只是本轮上下文，不写入对话历史。
        user_text = text if text else ""
        environment_context: list[tuple[str, str]] = []
        # ---------- 主动搭话提示 ----------
        proactive = not text
        # ---------- VLM 状态拼接 ----------
        mode = _config.pipeline.vlm.mode
        with self._vlm_lock:
            if mode == "keyword":
                if self._vlm_running:
                    environment_context.append(("屏幕", "屏幕信息正在获取，暂时没有可用画面。"))
                elif self._vlm_result is not None:
                    environment_context.append(("屏幕", self._vlm_result))
                    self._vlm_result = None
                    self._cancel_vlm_timer()   # 锁内调用无锁版
            elif mode == "continuous":
                if self._vlm_continuous_results:
                    merged = "；".join(self._vlm_continuous_results)
                    self._vlm_continuous_results.clear()
                    environment_context.append(("近期屏幕变化", merged))
                # 注意：不在这里取消定时器，定时器在函数末尾统一重置
        # ---------- ASR 桌面音频拼接 ----------
        if self.desktop_asr_buffer:
            recent = self.desktop_asr_buffer[-self.max_desktop_sentences:]
            desktop_text = "。".join(recent)
            environment_context.append(("近期桌面音频", desktop_text))
            self.desktop_asr_buffer.clear()
        browser_service = getattr(self, "browser_service", None)
        if browser_service is not None:
            research_catalog = browser_service.context_catalog()
            if research_catalog:
                environment_context.append(("已存研究资料", research_catalog))
        logger.debug(
            f"上下文来源：环境项数={len(environment_context)}，"
            f"包含工具上下文={bool(_tool_context)}，包含文本={bool(text)}"
        )

        # 1. 构建结构化上下文。System Prompt 只包含稳定内容；
        # 时间、环境与工具结果均放入当前轮，为 Prompt Cache 保留稳定前缀。
        model_instructions = []
        # 两类开关必须分开：
        #  - session 级：这份会话是否"启用工具协议"。它决定 system 里有没有工具说明，
        #    会话内必须恒定，否则每轮系统前缀一变，prompt 缓存全部作废；
        #  - 每轮级：这一轮的工具闸门（轮数上限、direct_return 等）。
        #    闸门关闭时不再靠"删掉工具说明"来阻止调用，而是在用户轮里声明
        #    "本轮工具不可用"——既保住恒定前缀，也避免模型白调一次被丢弃、主人拿不到回答。
        session_has_tools = bool(self.model_tool_gateway.definitions)
        session_web_tools = self.web_search_service.model_decision_enabled
        session_has_skill = bool(_config.service.skills.enable)
        # 每轮闸门（沿用旧名 allow_model_search：后面旧联网协议解析仍要看它）
        allow_model_search = (
            _allow_model_search and not direct_return
            and _tool_round < _config.service.tool.max_rounds
        )
        tool_gate_open = allow_model_search
        if session_has_tools:
            model_instructions.append(self.model_tool_gateway.model_instruction(include_common=False))
        if session_web_tools:
            model_instructions.append(self.web_search_service.model_instruction(include_common=False))
        if session_has_skill:
            skill_instruction = self.skill_service.model_instruction(include_common=False)
            if skill_instruction:
                model_instructions.append(skill_instruction)
        # 通用工具还要求会话里真的注册了工具；联网/Skill 的开关沿用每轮闸门
        allow_model_tools = allow_model_search and session_has_tools
        allow_model_skill = allow_model_search and session_has_skill
        turn_notices = self._build_turn_notices(
            tool_gate_open=tool_gate_open,
            session_has_tools=session_has_tools or session_web_tools or session_has_skill,
            tool_round=_tool_round,
        )
        emotion_tags = self._allowed_emotions()
        emotion_instruction = ""
        if not direct_return and emotion_tags:
            emotion_instruction = (
                "\n\n普通用户可见的最终回复必须以 <emotion>情绪标签</emotion> 开头，"
                "标签只能从以下 TTS 情绪中选择："
                + ", ".join(emotion_tags)
                + "。标签后直接写回复正文，不要解释标签；调用工具、联网或 Skill 时保持原协议格式，不加情绪标签。"
                "回复正文只写要念给主人听的话：不写 <system>/<analysis>/<plan> 之类的内部块，"
                "也不要在正文里复述本次该怎么做。"
            )
        prepared_context = self.context_manager.prepare(
            base_system_prompt=self.llm_prompt_manager.system_prompt + emotion_instruction,
            current_history=[*self.llm_prompt_manager.current_history, *_turn_history],
            user_text=user_text,
            environment=environment_context,
            tool_context=_tool_context,
            skill_context=_skill_context,
            tool_instruction=compose_tool_instructions(model_instructions),
            turn_notices=turn_notices,
            current_time=datetime.now(),
            proactive=proactive,
        )
        final_text = prepared_context.user_message
        temp_history = prepared_context.history
    
        # 2. 图片处理（仅 MLLM 启用时）
        images_base64 = list(_images_base64 or [])
        if self.mllm_cfg.enable and image_paths:
            for img_path in image_paths:
                if os.path.exists(img_path):
                    try:
                        with open(img_path, "rb") as f:
                            images_base64.append(base64.b64encode(f.read()).decode())
                        os.remove(img_path)  # 清理临时文件
                    except Exception as e:
                        logger.warning(f"处理图片 {img_path} 失败：{e}")
        logger.debug(
            f"MLLM 请求准备完成：图片数={len(images_base64)}，"
            f"历史消息数={len(temp_history)}，文本字符数={len(final_text)}"
        )

        # 3. 预测调用（原 `self.llm.predict` 替换为条件分支）
        reasoning_keywords = getattr(_config.pipeline.mllm, "reasoning_keywords", []) or []
        enable_thinking = any(keyword and keyword in user_text for keyword in reasoning_keywords)
        thinking_budget = int(getattr(_config.pipeline.mllm, "reasoning_budget_on_keyword", 80))
        use_mllm_stream = (
            self.mllm_cfg.enable
            and self.mllm_pipeline is not None
            and getattr(self.mllm_cfg, "stream_output", False)
            and not direct_return
            and not allow_model_search
            and not allow_model_skill
            and not allow_model_tools
        )
        trace_context = {"conversation_id": self.conversation_id, "turn_id": turn_id, "round": _tool_round,
                         "source": "dialogue"}
        if self.mllm_cfg.enable and self.mllm_pipeline is not None:
            query = MLLMQuery(text=final_text, history=temp_history, images=images_base64)
            object.__setattr__(query, "_harness_context", trace_context)
            object.__setattr__(query, "enable_thinking", enable_thinking)
            if enable_thinking:
                object.__setattr__(query, "thinking_budget", thinking_budget)
                logger.debug(f"关键词已启用 MLLM 思考：thinking_budget={thinking_budget}")
            if use_mllm_stream:
                logger.debug("开始调用 MLLM 流式接口。")
                prediction = self._emit_streaming_mllm_prediction(query, text, skip_tts)
                logger.debug("MLLM 流式接口已返回。")
                return prediction
            logger.debug("开始调用 MLLM 预测接口。")
            prediction = self.mllm_pipeline.predict(query)
            logger.debug("MLLM 预测接口已返回。")
        else:
            # 原有 LLM 预测（忽略图片）
            query = LLMQuery(text=final_text, history=temp_history)
            object.__setattr__(query, "_harness_context", trace_context)
            logger.debug("开始调用 LLM 预测接口。")
            prediction = self.llm.predict(query)
            logger.debug("LLM 预测接口已返回。")

        pending_model_directive = None
        pending_skill_directive = None
        pending_tool_call = None
        if prediction is not None:
            tool_visible, tool_call = self.model_tool_gateway.parse_model_response(
                prediction.response or "", context={**trace_context, "model_call_id": getattr(query, "_harness_call_id", None)})
            output_truncation_guidance = None
            output_truncation_failed = False
            output_finish_reason = str(getattr(prediction, "finish_reason", "") or "").lower()
            output_limit_reached = output_finish_reason in {"length", "max_tokens", "token_limit"}
            max_output_retries = int(getattr(_config.service.tool, "max_output_retries", 1))
            if allow_model_tools and output_limit_reached and self.model_tool_gateway.last_error:
                output_truncation_guidance = self.model_tool_gateway.format_output_truncation(
                    getattr(prediction, "max_output_tokens", None)
                )
                if max_output_retries:
                    logger.info(
                        f"模型输出达到 token 上限，触发分段恢复：turn_id={turn_id}，"
                        f"上限={getattr(prediction, 'max_output_tokens', None)}"
                    )
                    query = query.model_copy(update={
                        "text": final_text + "\n\n【输出长度恢复】\n" + output_truncation_guidance
                    })
                    prediction = (
                        self.mllm_pipeline.predict(query)
                        if self.mllm_cfg.enable and self.mllm_pipeline is not None
                        else self.llm.predict(query)
                    )
                    if prediction is None:
                        return None
                    tool_visible, tool_call = self.model_tool_gateway.parse_model_response(
                        prediction.response or "",
                        context={**trace_context, "model_call_id": getattr(query, "_harness_call_id", None),
                                 "output_recovery": 1},
                    )
                    retry_finish_reason = str(getattr(prediction, "finish_reason", "") or "").lower()
                    if retry_finish_reason in {"length", "max_tokens", "token_limit"} and tool_call is None:
                        output_truncation_failed = True
                else:
                    output_truncation_failed = True
            retries = 0
            max_parse_retries = getattr(_config.service.tool, "max_parse_retries", 1)
            while (allow_model_tools and not output_truncation_failed
                   and self.model_tool_gateway.last_error and retries < max_parse_retries):
                retries += 1
                logger.info(f"通用工具指令格式纠正：第 {retries}/{max_parse_retries} 次，turn_id={turn_id}")
                correction = self.model_tool_gateway.format_correction()
                query = query.model_copy(update={"text": final_text + "\n\n【工具协议纠正】\n" + correction})
                prediction = (self.mllm_pipeline.predict(query) if self.mllm_cfg.enable and self.mllm_pipeline is not None
                              else self.llm.predict(query))
                if prediction is None:
                    return None
                tool_visible, tool_call = self.model_tool_gateway.parse_model_response(
                    prediction.response or "", context={**trace_context, "model_call_id": getattr(query, "_harness_call_id", None),
                                                       "parse_retry": retries})
            if output_truncation_failed:
                prediction.response = "模型输出达到长度上限，未执行不完整的工具调用。请缩短本次文件内容或分段继续。"
                tool_visible = prediction.response
                tool_call = None
            elif self.model_tool_gateway.last_error:
                prediction.response = "工具调用格式无效，本次未执行任何操作。请重试。"
                tool_visible = prediction.response
            prediction.response = tool_visible
            if tool_call is not None and allow_model_tools:
                pending_tool_call = tool_call
            elif tool_call is not None:
                logger.warning("通用工具已禁用或达到轮数上限，未执行模型工具请求。")
            raw_model_response = prediction.response or ""
            acknowledgement, directive = self.web_search_service.parse_model_response(
                raw_model_response
            )
            if acknowledgement != raw_model_response:
                prediction.response = acknowledgement
            if directive is not None and allow_model_search and pending_tool_call is None:
                logger.info(
                    f"已解析模型联网指令：工具={directive.tool}，"
                    f"查询={directive.query}，turn_id={turn_id}，"
                    f"下一轮={_tool_round + 1}"
                )
                pending_model_directive = directive
            if directive is not None:
                if pending_model_directive is None and pending_tool_call is None:
                    logger.warning(
                        f"工具调用已禁用或达到轮数上限，已忽略模型联网指令："
                        f"工具={directive.tool}，查询={directive.query}，round={_tool_round}"
                    )
            skill_visible, skill_directive = self.skill_service.parse_model_response(
                prediction.response or ""
            )
            if skill_visible != prediction.response:
                prediction.response = skill_visible
            if skill_directive is not None and allow_model_skill:
                if pending_model_directive is None and pending_tool_call is None:
                    logger.info(
                        f"已解析模型 Skill 指令：Skill={skill_directive.name}，"
                        f"turn_id={turn_id}，下一轮={_tool_round + 1}"
                    )
                    pending_skill_directive = skill_directive
                else:
                    logger.warning("模型同时请求多种工具协议，本轮仅执行优先选中的工具。")
            elif skill_directive is not None:
                logger.warning(
                    f"Skills 已禁用或达到轮数上限，已忽略模型 Skill 指令："
                    f"Skill={skill_directive.name}，round={_tool_round}"
                )
    
        if prediction is None:
            return None
        prediction.response = self._extract_tool_visible_text(prediction.response or "", flush=True)[0].strip()
        # 4. 过滤
        has_pending_tool = (
            pending_model_directive is not None or pending_skill_directive is not None or pending_tool_call is not None
        )
        response_emotion = None
        if not direct_return:
            prediction.response, response_emotion = self._strip_response_emotion(
                prediction.response or "", has_pending_tool=has_pending_tool
            )
        is_filtered = self.filter.filter(prediction.response)
        if is_filtered:
            logger.warning(f"LLM 输出已被过滤：{prediction.response}")
            return None
        if prediction.response and prediction.response[0] == '\n':
            prediction.response = prediction.response[1:]
        self._cancel_pending_tts()   # 尝试取消旧的 TTS 任务（内部判断开关）

        turn_history = _turn_history
        if has_pending_tool:
            turn_history = (
                *_turn_history,
                Conversation(role=RoleEnum.user, content=prepared_context.turn_user_message),
                Conversation(role=RoleEnum.assistant, content=prediction.response),
            )
            if output_truncation_guidance:
                turn_history = (
                    *turn_history,
                    Conversation(role=RoleEnum.user, content=output_truncation_guidance),
                )
            logger.debug("工具中间记录仅缓存于当前 turn，不写入对话历史或长期记忆。")
        else:
            # 读-改-写必须在历史锁内完成：压缩线程会同时改写同一份历史，
            # 在锁外基于旧快照追加会把压缩结果覆盖掉（P1-1）。
            def _append_turn(current_history):
                return [
                    *current_history,
                    Conversation(role=RoleEnum.user, content=text),
                    Conversation(role=RoleEnum.assistant, content=prediction.response),
                ]

            if not self.llm_prompt_manager.commit_history_update(_append_turn):
                logger.warning("本轮对话未能写入历史（历史提交被拒绝）。")

            # 6. 仅在本轮对话已写入历史后触发记忆保存。
            if self.memory_manager:
                self.memory_manager.maybe_save_snapshot(
                    current_history=self.llm_prompt_manager.history_snapshot(),
                    injected_len=self.llm_prompt_manager.injected_len,
                    force=False,
                    history_snapshot=self.llm_prompt_manager,
                )
    
        # 7. 事件发射
        if not direct_return:
            emitter.emit(PipelineOutputLLMEvent(
                prediction=prediction, skip_tts=skip_tts, emotion=response_emotion
            ))
            logger.debug("已发射 LLM 结果事件。")
    
        # 8. 重要：每次完成一次 LLM 交互后，在**锁外**重置 auto_chat 模式的无输入定时器（内部会自己加锁）
        self._reset_auto_chat_timer()

        if pending_tool_call is not None:
            status = self._begin_model_tool(
                SearchRequest(
                    text=text, images_base64=images_base64, skip_tts=skip_tts,
                    conversation_id=self.conversation_id, turn_id=turn_id,
                    round=_tool_round + 1,
                    turn_history=turn_history,
                ),
                pending_tool_call,
                skill_context=_skill_context,
                continue_active=_continue_tool_turn,
            )
            if status == ToolCoordinationStatus.REJECTED:
                logger.warning(f"通用工具请求被拒绝：turn_id={turn_id}，round={_tool_round + 1}")
        elif pending_model_directive is not None:
            submission = self.web_search_service.begin_model_search(
                SearchRequest(
                    text=text,
                    images_base64=images_base64,
                    skip_tts=skip_tts,
                    conversation_id=self.conversation_id,
                    turn_id=turn_id,
                    round=_tool_round + 1,
                    turn_history=turn_history,
                ),
                pending_model_directive,
                self._on_web_search_ready,
                self._on_web_search_deferred,
                continue_active=_continue_tool_turn,
            )
            if submission.status == SearchSubmissionStatus.QUEUED:
                logger.info("模型工具请求已排入当前工具调用之后的缓冲区。")
            elif submission.status == SearchSubmissionStatus.REJECTED:
                logger.warning(
                    f"模型的后续工具调用被拒绝：turn_id={turn_id}，"
                    f"round={_tool_round + 1}"
                )
        elif pending_skill_directive is not None:
            status = self._begin_model_skill(
                SearchRequest(
                    text=text,
                    images_base64=images_base64,
                    skip_tts=skip_tts,
                    conversation_id=self.conversation_id,
                    turn_id=turn_id,
                    round=_tool_round + 1,
                    turn_history=turn_history,
                ),
                pending_skill_directive,
                continue_active=_continue_tool_turn,
            )
            if status == ToolCoordinationStatus.BUFFERED:
                logger.info("模型 Skill 请求已进入工具缓冲区。")
            elif status == ToolCoordinationStatus.REJECTED:
                logger.warning(
                    f"模型 Skill 后续加载被拒绝：turn_id={turn_id}，"
                    f"round={_tool_round + 1}"
                )

        return prediction

    def _emit_streaming_mllm_prediction(self, query: MLLMQuery, original_text: str, skip_tts: bool) -> MLLMPrediction | None:
        logger.debug("开始执行 MLLM 流式输出处理。")
        self._cancel_pending_tts()
        full_response = ""
        tts_visible_buffer = ""
        tts_raw_buffer = ""
        emotion_prefix_buffer = ""
        emotion_prefix_resolved = False
        response_emotion = None
        tts_prompt = None
        tts_round_task_id = None
        tts_enqueued = False
        if not skip_tts:
            with self._tts_lock:
                tts_round_task_id = self._tts_task_counter

        for chunk_prediction in self.mllm_pipeline.stream_predict(query):
            chunk = chunk_prediction.response or ""
            if not chunk:
                continue
            if chunk.startswith("Error:"):
                # 错误数据块也可能夹带机器参数，不将原文送入显示日志。
                logger.error("MLLM 流式输出失败，已停止处理；错误数据块未输出。")
                return None
            if not full_response:
                logger.debug("已收到 MLLM 的首个流式数据块。")
            full_response += chunk
            if emotion_prefix_resolved:
                visible_prefix_text = chunk
            else:
                emotion_prefix_buffer += chunk
                emotion_prefix_resolved, visible_prefix_text, requested_emotion = consume_emotion_prefix(
                    emotion_prefix_buffer
                )
                if emotion_prefix_resolved:
                    emotion_prefix_buffer = ""
                    if requested_emotion:
                        resolved_emotion = self._resolve_emotion_tag(requested_emotion)
                        if resolved_emotion:
                            response_emotion = resolved_emotion
                            tts_prompt = self.tts_prompt_manager.get_tts_prompt(response_emotion)
            tts_raw_buffer += visible_prefix_text
            visible_text, tts_raw_buffer = self._extract_tool_visible_text(tts_raw_buffer)
            if visible_text:
                logger.info("MLLM 流式输出：" + visible_text)
            if skip_tts or not getattr(self.mllm_cfg, "stream_tts_by_sentence", True):
                continue
            tts_visible_buffer += visible_text
            segments, tts_visible_buffer = self._pop_stream_tts_segments(
                tts_visible_buffer
            )
            for segment in segments:
                if not self.filter.filter(segment):
                    tts_enqueued = self._enqueue_tts_text(segment, tts_prompt, response_emotion) or tts_enqueued

        if not emotion_prefix_resolved:
            _, visible_prefix_text, requested_emotion = consume_emotion_prefix(
                emotion_prefix_buffer, flush=True
            )
            if requested_emotion:
                resolved_emotion = self._resolve_emotion_tag(requested_emotion)
                if resolved_emotion:
                    response_emotion = resolved_emotion
                    tts_prompt = self.tts_prompt_manager.get_tts_prompt(response_emotion)
            tts_raw_buffer += visible_prefix_text
        visible_response, _ = self._extract_tool_visible_text(
            full_response,
            flush=True,
        )
        _, response_without_emotion, _ = consume_emotion_prefix(full_response, flush=True)
        if visible_response != response_without_emotion:
            logger.warning(
                "已隐藏 MLLM 流式响应中意外出现的机器工具指令，未执行该指令。"
            )
        full_response = visible_response.strip()
        _, full_response, requested_emotion = consume_emotion_prefix(full_response, flush=True)
        if requested_emotion:
            resolved_emotion = self._resolve_emotion_tag(requested_emotion)
            if resolved_emotion:
                response_emotion = resolved_emotion
                tts_prompt = self.tts_prompt_manager.get_tts_prompt(response_emotion)
        full_response = full_response.strip()
        if not full_response:
            logger.warning("MLLM 流式接口返回了空响应。")
            return None
        if self.filter.filter(full_response):
            logger.warning(f"MLLM 流式输出已被过滤：{full_response}")
            return None
        if skip_tts:
            prompt = tts_prompt or self._select_tts_prompt(full_response)
            emitter.emit(CharacterStateEvent(
                state="replied", emotion=response_emotion or prompt.sentiment
            ))
        else:
            if getattr(self.mllm_cfg, "stream_tts_by_sentence", True):
                visible_tail, _ = self._extract_tool_visible_text(
                    tts_raw_buffer,
                    flush=True,
                )
                tts_visible_buffer += visible_tail
                segments, _ = self._pop_stream_tts_segments(
                    tts_visible_buffer,
                    flush=True,
                )
                for segment in segments:
                    tts_enqueued = self._enqueue_tts_text(segment, tts_prompt, response_emotion) or tts_enqueued
            else:
                tts_enqueued = self._enqueue_tts_text(full_response, tts_prompt, response_emotion) or tts_enqueued
            if tts_round_task_id is not None and tts_enqueued:
                self._tts_round_end_without_block(tts_round_task_id)
            elif not tts_enqueued:
                prompt = tts_prompt or self._select_tts_prompt(full_response)
                emitter.emit(CharacterStateEvent(
                    state="replied", emotion=response_emotion or prompt.sentiment
                ))

        from condata.pipeline.llm import Conversation, RoleEnum
        prediction = MLLMPrediction(response=full_response, history=query.history)

        # 与普通对话路径一致：历史写入必须在历史锁内完成，避免覆盖压缩结果（P1-1）。
        def _append_stream_turn(current_history):
            return [
                *current_history,
                Conversation(role=RoleEnum.user, content=original_text),
                Conversation(role=RoleEnum.assistant, content=full_response),
            ]

        if not self.llm_prompt_manager.commit_history_update(_append_stream_turn):
            logger.warning("MLLM 流式轮次未能写入历史（历史提交被拒绝）。")
        if self.memory_manager:
            self.memory_manager.maybe_save_snapshot(
                current_history=self.llm_prompt_manager.history_snapshot(),
                injected_len=self.llm_prompt_manager.injected_len,
                force=False,
                history_snapshot=self.llm_prompt_manager,
            )
        emitter.emit(PipelineOutputLLMEvent(prediction=prediction, skip_tts=True, emotion=response_emotion))
        logger.debug("已发射 MLLM 流式输出的最终 LLM 事件。")
        self._reset_auto_chat_timer()
        logger.debug("MLLM 流式输出处理完成。")
        return prediction

    def _on_vlm_result_ready(self, caption):
        if not caption:
            return
        with self._vlm_lock:
            mode = _config.pipeline.vlm.mode
            if mode == "keyword":
                self._vlm_result = caption
                self._start_vlm_timer()  # 锁内调用，安全
                logger.debug(f"[关键词模式] 已保存 VLM 结果并启动 5 秒定时器：{caption[:30]}...")
            elif mode == "continuous":
                time_str = datetime.now().strftime("%H:%M:%S")
                self._vlm_continuous_results.append(f"【{time_str}】{caption}")
                logger.debug(
                    f"[连续模式] 已追加 VLM 结果，当前总数："
                    f"{len(self._vlm_continuous_results)}"
                )
                # 注意：不在这里重置无输入定时器，定时器只在用户输入或自动发送后重置

    def _vlm_continuous_loop(self):
        """常态模式下后台持续截图并调用 VLM"""
        while not self._vlm_stop and self.vlm is not None:
            img, img_save_path = self.screen.safe_capture(k=0.99)
            if img and not is_image_uniform(img):
                self._vlm_async_predict(
                    str(img_save_path),
                    callback=self._on_vlm_result_ready
                )
            time.sleep(_config.pipeline.vlm.continuous_interval)
    
    def _vlm_async_predict(self, img_path: str, callback):
        """异步调用 VLM，设置运行标志，完成后回调"""
        with self._vlm_lock:
            if self._vlm_running:
                logger.warning("VLM 已在运行，跳过本次请求。")
                return False
            self._vlm_running = True

        def task():
            try:
                caption = self.vlm.predict(VLMQuery(
                    img_path=img_path,
                    prompt="请客观简短地描述这张图片的主要内容和信息，不要包含冗余信息和猜测，不需要前缀（如“好的，以下是图片的主要内容描述”）"
                ))
                if callback:
                    callback(caption)
            except Exception as e:
                logger.exception(f"VLM 异步预测失败：{e}")
                if callback:
                    callback(None)
            finally:
                # === 删除临时文件 ===
                try:
                    if os.path.exists(img_path):
                        os.remove(img_path)
                        logger.debug(f"已删除 VLM 临时图片：{img_path}")
                except Exception as e:
                    logger.warning(f"删除临时图片 {img_path} 失败：{e}")
                # 回调放在 finally 外，确保清理后再通知
                if callback:
                    callback(caption)
                with self._vlm_lock:
                    self._vlm_running = False
        logger.debug("VLM 异步预测任务已启动。")
        threading.Thread(target=task, daemon=True).start()
        return True

    def _cancel_vlm_timer(self):
        """取消 keyword 模式定时器。调用者必须已持有 self._vlm_lock。"""
        if self._vlm_timer:
            self._vlm_timer.cancel()
            self._vlm_timer = None

    def _start_vlm_timer(self):
        """启动 keyword 模式定时器。调用者必须已持有 self._vlm_lock。"""
        self._cancel_vlm_timer()
        self._vlm_timer = threading.Timer(
            5.0,
            lambda: self.emit_llm_prediction("", skip_tts=_config.service.chat_window.skip_tts)
        )
        self._vlm_timer.daemon = True
        self._vlm_timer.start()

    def _cancel_auto_chat_timer(self):
        """取消 auto_chat 无输入定时器。调用者需持有 self._auto_chat_lock。"""
        if self._vlm_continuous_timer:
            self._vlm_continuous_timer.cancel()
            self._vlm_continuous_timer = None

    def _reset_auto_chat_timer(self):
        """重置 auto_chat 模式的无输入定时器。可任意线程调用，内部会加锁。"""
        with self._auto_chat_lock:
            if not _config.system.auto_chat.enable:
                return
            self._cancel_auto_chat_timer()  # 已在锁内
            timeout = getattr(_config.system.auto_chat, 'timeout', 45)
            self._vlm_continuous_timer = threading.Timer(
                timeout,
                lambda: self.emit_llm_prediction("", skip_tts=_config.service.chat_window.skip_tts)
            )
            self._vlm_continuous_timer.daemon = True
            self._vlm_continuous_timer.start()

    def change_lang(self, lang: Language):
        self.cur_lang = lang
        if self.tts_prompt_manager is not None:
            self.tts_prompt_manager.set_lang(self.cur_lang)

    def check_img(self, img, send_message=True) -> bool:
        if is_image_uniform(img):
            logger.warning("屏幕截图为纯黑画面，请检查屏幕捕获是否正常。")
            if send_message:
                self.emit_llm_prediction("你忽然什么都看不见了！请向你的开发者求助！")
            return False
        return True

    def play_tts(self, event: PipelineOutputTTSEvent):
        logger.debug("开始播放 TTS。")
        prediction = event.prediction
        text = event.transcript
        audio_path = save_audio(wave_data=prediction.wave_data, format=AudioFileType(prediction.audio_type),
                                prefix='tts')
        # 实际输出设备由 Speaker 在播放时读取（devices/speaker.py）。
        logger.debug("TTS 输出设备：{}", _config.pipeline.tts.output_device)
        self.speaker.enqueue_sound(audio_path, event.emotion)
        # 清理临时文件（注意：播放是异步的,不能立即删除,删除位于speaker.py）

    def _cancel_pending_tts(self):
        """取消所有待处理的 TTS 任务，并清空 Speaker 队列。"""
        if not _config.pipeline.tts.cancel_on_new_input:
            return
        futures_to_cancel = []
        with self._tts_lock:
            # 1. 递增计数器，使所有正在运行或未开始的任务失效
            self._tts_task_counter += 1
            # 2. 取消所有尚未开始的任务
            futures_to_cancel = list(self._tts_pending_futures)
            self._tts_pending_futures.clear()
        for future in futures_to_cancel:
            future.cancel()
        # 3. 清空 Speaker 播放队列（避免旧音频继续播放）
        if hasattr(self.speaker, 'clear_queue'):
            self.speaker.clear_queue()
            logger.info("收到新输入，已清空 TTS 扬声器队列。")

    def _interrupt_tts(self):
        """打断当前 TTS 播放"""
        try:
            future_to_cancel = None
            with self._tts_lock:
                self._tts_task_counter += 1
                future_running = self._current_tts_future is not None and not self._current_tts_future.done()
                speaker_playing = bool(getattr(self.speaker, "is_playing", False))
                speaker_queued = bool(
                    getattr(self.speaker, "audio_clips", None) is not None
                    and not self.speaker.audio_clips.empty()
                )
                should_accelerate = future_running or speaker_playing or speaker_queued
                adaptive_speed = _config.pipeline.tts.adaptive_speed
                if adaptive_speed.enable and should_accelerate:
                    max_speed = max(float(adaptive_speed.max_speed), 1.0)
                    max_speed_step = max(int(round((max_speed - 1.0) / 0.05)), 0)
                    old_speed_step = self.speed_step
                    self.speed_step = min(
                        self.speed_step + int(adaptive_speed.interrupt_speed_step),
                        max_speed_step,
                    )
                    logger.debug(
                        f"TTS 已被打断，speed_step：{old_speed_step} -> {self.speed_step}。"
                    )
                elif adaptive_speed.enable:
                    logger.debug("收到 TTS 打断请求，但当前没有活动任务；speed_step 保持不变。")
                if future_running:
                    future_to_cancel = self._current_tts_future
                    self._current_tts_future = None
                self._interrupt_required = True

            if future_to_cancel:
                future_to_cancel.cancel()
                logger.info("TTS 异步任务已取消。")
            if self.speaker:
                try:
                    self.speaker.force_clear_queue()
                except Exception as e:
                    logger.error(f"强制清空播放队列失败：{e}", exc_info=True)
        except Exception as e:
            logger.exception(f"打断 TTS 失败：{e}")


    @staticmethod
    def _as_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _is_turnsense_config_enabled(self) -> bool:
        return self._as_bool(getattr(_config.pipeline.asr.turnsense, "enable", False))

    def _is_semantic_asr_enabled(self) -> bool:
        return self._is_turnsense_config_enabled() and self.turnsense_detector is not None

    def _submit_semantic_asr_task(self, callback, *args):
        try:
            future = self._semantic_asr_executor.submit(callback, *args)
            future.add_done_callback(self._log_semantic_asr_task_error)
        except RuntimeError as exc:
            logger.debug(f"关闭过程中已忽略语义 ASR 任务：{exc}")

    @staticmethod
    def _log_semantic_asr_task_error(future):
        try:
            exc = future.exception()
        except Exception as callback_exc:
            logger.error(f"检查语义 ASR 任务失败：{callback_exc}", exc_info=True)
            return
        if exc is not None:
            logger.opt(exception=exc).error(f"语义 ASR 任务失败：{exc}")

    def _refresh_semantic_asr_runtime_config(self, reset_state: bool = True):
        turnsense_config_enabled = self._is_turnsense_config_enabled()

        if not turnsense_config_enabled:
            if reset_state:
                self._semantic_asr_reset(reset_remote=False, reset_microphone=False)
            self.turnsense_detector = None
            self._configure_microphone_semantic_mode()
            logger.info(
                f"TurnSense 语义 ASR 模式：配置启用={turnsense_config_enabled}，"
                f"当前激活={self._is_semantic_asr_enabled()}，"
                f"模型 ID={getattr(_config.pipeline.asr.model_id, 'value', _config.pipeline.asr.model_id)}"
            )
            return

        if self.turnsense_detector is None:
            try:
                self.turnsense_detector = TurnSenseDetector(_config.pipeline.asr.turnsense)
                logger.info("🧠 TurnSense 检测器初始化完成。")
            except Exception as e:
                logger.error(f"TurnSense 初始化失败：{e}", exc_info=True)
                self.turnsense_detector = None
                if not _config.pipeline.asr.turnsense.fail_open:
                    raise

        self._configure_microphone_semantic_mode()
        logger.info(
            f"TurnSense 语义 ASR 模式：配置启用={turnsense_config_enabled}，"
            f"当前激活={self._is_semantic_asr_enabled()}，"
            f"模型 ID={getattr(_config.pipeline.asr.model_id, 'value', _config.pipeline.asr.model_id)}"
        )

    def _configure_microphone_semantic_mode(self):
        mic = getattr(self, 'mic', None)
        if mic is None:
            return

        if not self._is_semantic_asr_enabled():
            mic.semantic_turn_mode = False
            mic.streaming_asr_enable = False
            vad_interim_cfg = getattr(_config.service.microphone, 'vad_interim', None)
            if vad_interim_cfg is not None:
                mic.interim_enable = self._as_bool(getattr(vad_interim_cfg, 'enable', mic.interim_enable))
                mic.interim_silence_timeout = float(getattr(vad_interim_cfg, 'interim_silence_timeout', mic.interim_silence_timeout))
                min_interim_audio_sec = float(getattr(vad_interim_cfg, 'min_interim_audio_sec', 0.5))
                mic.min_interim_frames = max(1, int(min_interim_audio_sec * mic._sample_rate / mic._chunk_size))
            logger.info(
                f"🎙️ 标准麦克风 ASR 模式：语义轮次={mic.semantic_turn_mode}，"
                f"候选流式识别={mic.streaming_asr_enable}，"
                f"中间结果={getattr(mic, 'interim_enable', False)}，"
                f"中间结果静音超时={getattr(mic, 'interim_silence_timeout', 0.0):.2f} 秒"
            )
            return

        stream_cfg = getattr(_config.service.microphone, 'streaming_asr', None)
        turnsense_cfg = _config.pipeline.asr.turnsense
        mic.semantic_turn_mode = True
        mic.streaming_asr_enable = True
        if stream_cfg is not None:
            chunk_audio_sec = float(getattr(stream_cfg, 'chunk_audio_sec', 0.24))
            mic.streaming_chunk_frames = max(1, int(chunk_audio_sec * mic._sample_rate / mic._chunk_size))
        mic.interim_silence_timeout = float(getattr(turnsense_cfg, 'timeout', 0.8))
        logger.info(
            f"🎙️ 语义麦克风分块：语义轮次={mic.semantic_turn_mode}，"
            f"候选流式识别={mic.streaming_asr_enable}，"
            f"分块帧数={mic.streaming_chunk_frames}，"
            f"暂停刷新超时={mic.interim_silence_timeout:.2f} 秒"
        )

    def _handle_semantic_streaming_asr(self, event: DeviceMicrophoneVADEvent, speech: bytes, channels: int, sample_rate: int):
        if not self._is_semantic_asr_enabled():
            return
        if not speech:
            if event.is_final:
                logger.debug("语义 ASR 收到最终标记，正在结束缓冲流。")
                self._semantic_asr_complete_timeout_commit(reason="semantic-final-marker")
            return

        self._semantic_asr_append_audio(speech, channels, sample_rate)
        result = self._semantic_asr_predict_audio()
        if result is None:
            logger.debug("TurnSense 音频结果暂不可用，继续保留候选音频。")
            if not self._asr_incomplete_since:
                self._asr_incomplete_since = time.monotonic()
            self._schedule_semantic_asr_timeout()
            return

        raw_label = result.label
        label = raw_label
        logger.info(
            f"TurnSense 音频候选：标签={label}，置信度={result.confidence:.3f}，"
            f"延迟={result.latency:.3f} 秒，字节数={len(speech)}，采样率={sample_rate}"
        )

        invalid_confidence_threshold = float(
            getattr(_config.pipeline.asr.turnsense, 'invalid_confidence_threshold', 0.8)
        )
        if label == "invalid" and result.confidence >= invalid_confidence_threshold:
            with self._asr_accumulated_lock:
                has_valid_turn = (
                    bool(self._asr_accumulated_text.strip())
                    or self._semantic_asr_pending_complete
                )
            if has_valid_turn:
                logger.info(
                    "当前轮次已有有效文本，已忽略 TurnSense 无效尾音并保留待执行的最终提交。"
                )
                if not self._semantic_asr_pending_complete:
                    self._schedule_semantic_asr_complete_commit()
                return
            if getattr(_config.pipeline.asr.turnsense, 'invalid_action', 'drop') == 'drop':
                logger.info("TurnSense 候选结果无效，丢弃当前语义 ASR 轮次。")
                self._semantic_asr_reset(reset_remote=True, reset_microphone=True)
            return
        if label == "invalid":
            label = "incomplete"

        complete_confidence_threshold = float(
            getattr(_config.pipeline.asr.turnsense, 'complete_confidence_threshold', 0.75)
        )
        if label == "complete" and result.confidence < complete_confidence_threshold:
            label = "incomplete"

        if self._semantic_asr_pending_complete:
            logger.info("语义 ASR 在完成超时前收到新的有效音频，继续当前轮次。")
            self._semantic_asr_pending_complete = False
            with self._asr_accumulated_lock:
                self._asr_timer_generation += 1
                if self._asr_force_commit_timer:
                    self._asr_force_commit_timer.cancel()
                    self._asr_force_commit_timer = None

        transcript = self._semantic_asr_transcribe_candidate(
            speech=speech,
            channels=channels,
            sample_rate=sample_rate,
            is_final=False,
        )
        if transcript:
            self._semantic_asr_update_text(transcript, is_final=(label == "complete"), commit=False)
            audio, audio_sample_rate = self._semantic_asr_get_audio()
            audio_seconds = (len(audio) / audio_sample_rate) if audio is not None and audio_sample_rate else 0.0
            interrupt_min_audio_sec = float(
                getattr(_config.pipeline.asr.turnsense, 'interrupt_min_audio_sec', 0.48)
            )
            interrupt_min_confidence = float(
                getattr(_config.pipeline.asr.turnsense, 'interrupt_min_confidence', 0.75)
            )
            interrupt_confirmed = (
                raw_label != "invalid"
                and audio_seconds >= interrupt_min_audio_sec
                and result.confidence >= interrupt_min_confidence
            )
            if not self._semantic_asr_interrupted and interrupt_confirmed:
                self._interrupt_tts()
                self._semantic_asr_interrupted = True
            elif not self._semantic_asr_interrupted:
                logger.debug(
                    f"延后 TTS 打断：音频={audio_seconds:.2f}/{interrupt_min_audio_sec:.2f} 秒，"
                    f"置信度={result.confidence:.3f}/{interrupt_min_confidence:.3f}"
                )

        if label == "incomplete":
            if not self._asr_incomplete_since:
                self._asr_incomplete_since = time.monotonic()
            self._schedule_semantic_asr_timeout()
            return

        if label == "complete":
            self._asr_incomplete_since = 0.0
            self._schedule_semantic_asr_complete_commit()

    def _semantic_asr_transcribe_candidate(self, speech: bytes, channels: int, sample_rate: int, is_final: bool) -> str:
        try:
            query = ASRStreamQuery(
                is_final=is_final,
                audio_data=speech,
                channels=channels,
                sample_rate=sample_rate,
                media_type='wav',
            )
            transcript = ""
            for prediction in self.asr.stream_predict(query):
                candidate = prediction.transcript or ""
                if candidate and not ASR_result_filter(candidate):
                    transcript = candidate
            if transcript:
                state = "最终" if is_final else "有效"
                logger.info(f"语义 ASR 转写（{state}）：{transcript}")
            return transcript
        except Exception as exc:
            logger.error(f"语义 ASR 候选音频转写失败：{exc}", exc_info=True)
            return ""

    def _semantic_asr_commit_buffered_text(self, reason: str, reset_remote: bool | None = None):
        self._semantic_asr_pending_complete = False
        with self._asr_accumulated_lock:
            full_text = self._asr_accumulated_text.strip()
        if not full_text:
            logger.debug(f"语义 ASR 缓冲文本为空（{reason}），跳过提交并丢弃当前轮次。")
            self._semantic_asr_reset(reset_remote=self._should_reset_remote(reset_remote), reset_microphone=False)
            self._end_mic_utterance()
            return
        logger.info(f"语义 ASR 正在提交（{reason}）：{full_text}")
        # 一轮语音结束就重置远程流：否则远端会话仍持有上一轮音频，
        # 下一轮从同一条流里取回旧文本，表现为"同一句话被提交两次"（P1-14）。
        self._semantic_asr_reset(reset_remote=self._should_reset_remote(reset_remote), reset_microphone=False)
        self._emit_final_asr_text(full_text)

    def _desktop_asr_allowed(self) -> bool:
        """桌面 ASR 是否可以让路给麦克风（P1-16）。

        返回 False 表示麦克风正忙：可能是单个音频块处理中，也可能本轮语音还没结束。
        """
        with self._mic_processing_lock:
            if self._mic_processing:
                logger.debug("麦克风 ASR 正在运行，已跳过桌面 ASR。")
                return False
            if not self._mic_utterance_active:
                return True
            if time.monotonic() - self._mic_utterance_last_chunk > self._mic_utterance_stale_seconds:
                # 兜底：某轮异常未提交时不能让桌面 ASR 永久失效
                logger.warning(
                    "麦克风语音轮次已超过 "
                    f"{self._mic_utterance_stale_seconds:.0f} 秒未更新，视为已结束并恢复桌面 ASR。"
                )
                self._mic_utterance_active = False
                return True
            logger.debug("麦克风本轮语音尚未结束，已跳过桌面 ASR（避免两个 ASR 请求并发）。")
            return False

    def _should_reset_remote(self, reset_remote: bool | None) -> bool:
        if reset_remote is not None:
            return bool(reset_remote)
        return bool(self._reset_remote_after_turn)

    def _semantic_asr_finalize_stream_and_commit(self, reason: str):
        transcript = self._semantic_asr_finalize_stream()
        if transcript:
            self._semantic_asr_update_text(transcript, is_final=True, commit=False)
        self._semantic_asr_commit_buffered_text(reason=reason)

    def _semantic_asr_complete_timeout_commit(self, reason: str):
        self._semantic_asr_transcribe_and_commit(reason=reason)

    def _semantic_asr_append_audio(self, speech: bytes, channels: int, sample_rate: int):
        if not speech:
            return
        if self.turnsense_detector is None or not getattr(self.turnsense_detector, "supports_audio", False):
            return
        try:
            audio = self._decode_turnsense_audio(speech, channels)
        except Exception as exc:
            logger.warning(f"TurnSense 音频解码失败，将降级使用文本：{exc}")
            return
        with self._asr_accumulated_lock:
            if self._asr_audio_sample_rate and self._asr_audio_sample_rate != sample_rate:
                logger.warning(
                    f"TurnSense 音频采样率从 {self._asr_audio_sample_rate} 变为 "
                    f"{sample_rate}，正在清空音频缓冲区。"
                )
                self._asr_audio_chunks = []
            self._asr_audio_sample_rate = sample_rate
            self._asr_audio_chunks.append(audio)

    @staticmethod
    def _decode_turnsense_audio(speech: bytes, channels: int) -> np.ndarray:
        try:
            with io.BytesIO(speech) as wav_io:
                with wave.open(wav_io, 'rb') as wav_file:
                    n_frames = wav_file.getnframes()
                    sample_width = wav_file.getsampwidth()
                    wav_channels = wav_file.getnchannels()
                    raw = wav_file.readframes(n_frames)
            if sample_width != 2:
                raise ValueError(f"unsupported wav sample width: {sample_width}")
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if wav_channels > 1:
                audio = audio.reshape(-1, wav_channels).mean(axis=1)
            return audio.astype(np.float32)
        except wave.Error:
            audio = np.frombuffer(speech, dtype=np.float32).astype(np.float32)
            if channels > 1:
                audio = audio.reshape(-1, channels).mean(axis=1)
            return audio

    def _semantic_asr_get_audio(self):
        with self._asr_accumulated_lock:
            if not self._asr_audio_chunks or not self._asr_audio_sample_rate:
                return None, 0
            return np.concatenate(self._asr_audio_chunks).astype(np.float32), self._asr_audio_sample_rate

    def _semantic_asr_update_text(self, transcript: str, is_final: bool = False, commit: bool = True):
        with self._asr_accumulated_lock:
            transcript = transcript.strip()
            full_text = transcript or self._asr_accumulated_text.strip()
            self._asr_accumulated_text = full_text
        logger.debug(f"语义 ASR 累计文本：{full_text}")
        if commit:
            self._semantic_asr_commit_if_needed(force=False, reason="final" if is_final else "turnsense")

    def _semantic_asr_commit_if_needed(self, force: bool, reason: str):
        if not force:
            return
        self._semantic_asr_transcribe_and_commit(reason=reason)

    def _semantic_asr_predict_audio(self):
        if self.turnsense_detector is None or not getattr(self.turnsense_detector, "supports_audio", False):
            return None
        audio, audio_sample_rate = self._semantic_asr_get_audio()
        if audio is None:
            return None
        return self.turnsense_detector.predict_audio(audio, audio_sample_rate)

    def _semantic_asr_transcribe_and_commit(self, reason: str):
        tail = self._semantic_asr_take_microphone_tail()
        if tail is not None:
            tail_speech, tail_channels, tail_sample_rate = tail
            self._semantic_asr_append_audio(tail_speech, tail_channels, tail_sample_rate)

        if getattr(_config.pipeline.asr.turnsense, 'streaming_asr', True):
            if tail is not None:
                transcript = self._semantic_asr_transcribe_candidate(
                    speech=tail_speech,
                    channels=tail_channels,
                    sample_rate=tail_sample_rate,
                    is_final=True,
                )
                if transcript:
                    self._semantic_asr_update_text(transcript, is_final=True, commit=False)
                self._semantic_asr_commit_buffered_text(reason=reason)
                return
            self._semantic_asr_finalize_stream_and_commit(reason=reason)
            return

        audio, audio_sample_rate = self._semantic_asr_get_audio()
        if audio is None:
            logger.debug("没有累计音频，已跳过语义 ASR 转写。")
            return

        transcript = self._semantic_asr_transcribe_audio(audio, audio_sample_rate)
        if not transcript:
            logger.debug(f"语义 ASR 转写返回空文本（{reason}），丢弃当前轮次。")
            self._semantic_asr_reset(reset_remote=True, reset_microphone=False)
            self._end_mic_utterance()
            return

        self._semantic_asr_update_text(transcript, is_final=True, commit=False)
        self._semantic_asr_commit_buffered_text(reason=reason)

    def _semantic_asr_finalize_stream(self) -> str:
        _, audio_sample_rate = self._semantic_asr_get_audio()
        sample_rate = audio_sample_rate or _config.pipeline.asr.sample_rate
        try:
            query = ASRStreamQuery(
                is_final=True,
                audio_data=b'',
                channels=1,
                sample_rate=sample_rate,
                media_type='wav',
            )
            transcript = ""
            for prediction in self.asr.stream_predict(query):
                candidate = prediction.transcript or ""
                if candidate and not ASR_result_filter(candidate):
                    transcript = candidate
            if transcript:
                logger.info(f"语义 ASR 转写（流结束）：{transcript}")
            return transcript
        except Exception as exc:
            logger.error(f"语义 ASR 流结束处理失败：{exc}", exc_info=True)
            return ""

    def _semantic_asr_transcribe_audio(self, audio: np.ndarray, sample_rate: int) -> str:
        try:
            wave_data = self._encode_turnsense_audio(audio, sample_rate)
            query = ASRStreamQuery(
                is_final=True,
                audio_data=wave_data,
                channels=1,
                sample_rate=sample_rate,
                media_type='wav',
            )
            transcript = ""
            for prediction in self.asr.stream_predict(query):
                candidate = prediction.transcript or ""
                if candidate and not ASR_result_filter(candidate):
                    transcript = candidate
            if transcript:
                logger.info(f"语义 ASR 转写（TurnSense 结束）：{transcript}")
            return transcript
        except Exception as exc:
            logger.error(f"语义 ASR 最终转写失败：{exc}", exc_info=True)
            return ""

    @staticmethod
    def _encode_turnsense_audio(audio: np.ndarray, sample_rate: int) -> bytes:
        values = np.asarray(audio, dtype=np.float32).reshape(-1)
        if values.size == 0:
            return b''
        int16_audio = (np.clip(values, -1.0, 1.0) * 32767.0).astype(np.int16)
        with io.BytesIO() as wav_io:
            with wave.open(wav_io, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(int16_audio.tobytes())
            return wav_io.getvalue()

    def _schedule_semantic_asr_complete_commit(self):
        timeout = float(getattr(_config.pipeline.asr.turnsense, 'final_timeout', 0.8))
        with self._asr_accumulated_lock:
            self._asr_timer_generation += 1
            generation = self._asr_timer_generation
            if self._asr_force_commit_timer:
                self._asr_force_commit_timer.cancel()
            self._semantic_asr_pending_complete = True
            self._asr_force_commit_timer = threading.Timer(
                timeout,
                lambda: self._submit_semantic_asr_task(
                    self._run_semantic_asr_timer,
                    generation,
                    "complete",
                    lambda: self._semantic_asr_complete_timeout_commit(reason="turnsense-complete-timeout"),
                ),
            )
            self._asr_force_commit_timer.daemon = True
            self._asr_force_commit_timer.start()
        logger.info(f"语义 ASR 检测到完成信号，延迟 {timeout:.2f} 秒进行最终提交。")

    def _semantic_asr_take_microphone_tail(self):
        mic = getattr(self, 'mic', None)
        if mic is None or not hasattr(mic, 'take_pending_candidate'):
            return None
        try:
            speech = mic.take_pending_candidate()
            if not speech:
                return None
            logger.info(f"语义 ASR 已同步获取麦克风尾部音频：字节数={len(speech)}")
            return speech, mic._channels, mic._sample_rate
        except Exception as exc:
            logger.warning(f"获取语义 ASR 麦克风尾部音频失败：{exc}")
            return None

    def _schedule_semantic_asr_timeout(self):
        turnsense_cfg = _config.pipeline.asr.turnsense
        with self._asr_accumulated_lock:
            self._asr_timer_generation += 1
            generation = self._asr_timer_generation
            if self._asr_force_commit_timer:
                self._asr_force_commit_timer.cancel()
            incomplete_since = getattr(self, '_asr_incomplete_since', 0.0) or time.monotonic()
            remaining = max(0.1, turnsense_cfg.max_wait - (time.monotonic() - incomplete_since))
            logger.debug(f"语义 ASR 最大等待定时器将在 {remaining:.2f} 秒后触发。")
            self._asr_force_commit_timer = threading.Timer(
                remaining,
                lambda: self._submit_semantic_asr_task(
                    self._run_semantic_asr_timer,
                    generation,
                    "max-wait",
                    lambda: self._semantic_asr_commit_if_needed(force=True, reason="max-wait-timer"),
                ),
            )
            self._asr_force_commit_timer.daemon = True
            self._asr_force_commit_timer.start()


    def _run_semantic_asr_timer(self, generation: int, timer_name: str, callback):
        with self._asr_accumulated_lock:
            if generation != self._asr_timer_generation:
                logger.debug(
                    f"忽略过期的语义 ASR {timer_name} 定时器：generation={generation}，"
                    f"当前 generation={self._asr_timer_generation}"
                )
                return
            self._asr_force_commit_timer = None
        logger.debug(f"语义 ASR 的 {timer_name} 定时器已触发：generation={generation}")
        callback()

    def _semantic_asr_reset(self, reset_remote: bool = False, reset_microphone: bool = False):
        with self._asr_accumulated_lock:
            self._asr_accumulated_text = ""
            self._pending_asr_count = 0
            self._final_pending = False
            self._asr_incomplete_since = 0.0
            self._asr_audio_chunks = []
            self._asr_audio_sample_rate = 0
            self._semantic_asr_interrupted = False
            self._semantic_asr_pending_complete = False
            self._asr_timer_generation += 1
            if self._asr_force_commit_timer:
                self._asr_force_commit_timer.cancel()
                self._asr_force_commit_timer = None
        if reset_microphone and getattr(self, 'mic', None) is not None:
            self.mic.force_commit(is_emit=False)
        if reset_remote:
            self._reset_remote_streaming_asr()

    def _reset_remote_streaming_asr(self):
        try:
            # 用本轮实际使用的采样率重置，避免与音频采样率不一致（P1-15 的连带项）
            with self._asr_accumulated_lock:
                sample_rate = self._asr_audio_sample_rate or _config.pipeline.asr.sample_rate
            query = ASRStreamQuery(
                is_final=True,
                audio_data=b'',
                channels=_config.pipeline.asr.channels,
                sample_rate=sample_rate,
                media_type='wav',
            )
            for _ in self.asr.stream_predict(query):
                pass
        except Exception as e:
            logger.warning(f"重置远程流式 ASR 失败：{e}")

    def _reset_asr_accumulator(self):
        """重置 ASR 累积文本（在对话结束或强制提交时调用）"""
        if self._is_semantic_asr_enabled():
            self._semantic_asr_reset(reset_remote=True)
            return
        with self._asr_accumulated_lock:
            self._asr_accumulated_text = ""
            self._pending_asr_count = 0
            self._final_pending = False

    def _mark_asr_final(self) -> str:
        """标记 final 已到达；如无 pending ASR 请求则返回可提交文本。"""
        with self._asr_accumulated_lock:
            if self._pending_asr_count > 0:
                self._final_pending = True
                logger.debug("收到 ASR 最终标记，但仍有待处理请求，继续等待。")
                return ""

            self._final_pending = False
            full_text = self._asr_accumulated_text.strip()
            self._asr_accumulated_text = ""
            return full_text

    def _finish_asr_request(self, transcript: str, is_final: bool) -> str:
        """完成一次 ASR 请求，保证计数递减，并在 final 条件满足时返回完整文本。"""
        with self._asr_accumulated_lock:
            self._pending_asr_count = max(0, self._pending_asr_count - 1)
            logger.debug(f"ASR 待处理计数减少，当前为 {self._pending_asr_count}")

            if transcript:
                previous = self._asr_accumulated_text.strip()
                transcript = transcript.strip()
                if previous and transcript.startswith(previous):
                    self._asr_accumulated_text = transcript
                elif previous and previous.endswith(transcript):
                    self._asr_accumulated_text = previous
                elif previous and transcript in previous:
                    self._asr_accumulated_text = previous
                else:
                    self._asr_accumulated_text = transcript if not previous else f"{previous}{transcript}"
                logger.debug(f"ASR 累计文本：{self._asr_accumulated_text}")

            if is_final:
                self._final_pending = True

            if self._final_pending and self._pending_asr_count == 0:
                self._final_pending = False
                full_text = self._asr_accumulated_text.strip()
                self._asr_accumulated_text = ""
                return full_text

            return ""

    def _asr_utterance_pending(self) -> bool:
        """是否还有未提交的语音内容（调用方需自行持有 _asr_accumulated_lock 或容忍竞态）。"""
        with self._asr_accumulated_lock:
            return bool(self._asr_accumulated_text.strip()) or self._pending_asr_count > 0

    def _end_mic_utterance(self) -> None:
        """本轮语音已提交或已丢弃：允许桌面 ASR 恢复（P1-16）。"""
        with self._mic_processing_lock:
            self._mic_processing = False
            self._mic_utterance_active = False

    def _emit_final_asr_text(self, full_text: str):
        if full_text:
            logger.info(f"最终 ASR 完整文本：{full_text}")
            dummy_pred = ASRPrediction(transcript=full_text)
            emitter.emit(PipelineASREvent(prediction=dummy_pred))
        else:
            logger.debug("最终 ASR 文本为空，跳过处理。")
        # 无论是否产出文本，这一轮都已结束
        self._end_mic_utterance()

def clean_text_for_tts(text: str) -> str:
    # 去掉 *星号包裹* 的内容
    text = re.sub(r'\*[^*]*\*', '', text)
    # 去掉中文全角括号（ ）里的内容
    text = re.sub(r'（[^）]*）', '', text)
    # 去掉半角括号 ( ) 里的内容
    text = re.sub(r'\([^)]*\)', '', text)
    # 去掉方括号 [ ] 或 【 】里的内容
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = re.sub(r'【[^】]*】', '', text)
    # 去掉尖括号 < > 里的内容
    text = re.sub(r'<[^>]*>', '', text)
    # 清理多余空白（多个空格、换行、制表符等）
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def ASR_result_filter(transcript: str):
    return not transcript or len(transcript) < 2 or transcript in ["utter", "我。","“我”","嗯。","嗯嗯","啊啊","哦哦","呃呃","我们是。","我感觉，我"]