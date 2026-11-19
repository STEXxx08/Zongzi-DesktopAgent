import asyncio
import faulthandler
from pathlib import Path
import threading
from uuid import uuid4
from loguru import logger
from condata.harness import configure_harness
from common.logging_setup import configure_app_logging
from condata.pipeline.llm import Conversation, LLMPrediction

from bot_asr_controller import ASR_result_filter, BotASRController
from bot_event_binder import BotEventBinder
from bot_tts_scheduler import BotTTSScheduler, clean_text_for_tts
from bot_turn_orchestrator import BotTurnOrchestrator, BotTurnRequest
from bot_vision_controller import BotVisionController
from common.concurrent.abs_runnable import stop_all_runnable
from common.concurrent.killable_thread import KillableThread, kill_all_threads
from common.enumerator import Language
from event.event_data import PipelineOutputTTSEvent, SecondEvent
from event.event_emitter import emitter
from framework.base_bot import BaseBot
from manager.config_manager import get_config, get_project_dir
from manager.context_manager import ContextManager
from manager.tts_prompt_manager import TTSPromptManager
from memory.manager import MemoryManager, resolve_memory_model_configs
from memory.retrieval import MODE_ON, MemoryRetriever, RetrievalUnavailable
from services.memory_recall import MemoryService
from services.harness_recall import HarnessRecallService, boot_offset as harness_boot_offset
from pipeline.mllm.mllm_sync import MLLMSyncPipeline
from pipeline.mllm.mllm_screenshot_manager import MLLMScreenshotManager
from services.web_search import (
    WebSearchService,
    load_web_search_config,
)
from services.web_search.service import LocalSearchDirective
from services.intent_router import BGEIntentRouter, load_intent_router_config
from services.mcp import MCPService
from services.browser import BrowserService
from services.tool_model_gateway import ModelToolGateway
from services.sandbox import SandboxService
from services.skill_framework import SkillService

faulthandler.enable(all_threads=True)

# 统一日志：loguru 文件 sink（轮转 + 保留）并桥接标准 logging（P1-26）
configure_app_logging(Path(__file__).resolve().parent)

_config = get_config()

class Bot(BaseBot):
    def __init__(self):
        configure_harness(project_root=Path(__file__).resolve().parent, **_config.service.harness.model_dump())
        # harness_recall 的窗口左边界：只在真正进程启动时按当前写入配置取一次。
        # 配置热重载**不得**重取，否则窗口清零，症状是"工具突然什么都查不到"。
        _harness_boot_offset = harness_boot_offset(
            Path(__file__).resolve().parent, _config.service.harness.path,
        )
        # memory.max_history 是临时对话历史上限的唯一配置来源；
        # 在基类创建 llm_prompt_manager 前同步到聊天配置对象。
        _config.character.chat.max_history = _config.memory.max_history
        super().__init__()
        self.cur_lang = _config.character.speech.language
        self._timer_flag = True
        self._runtime_stopped = False
        self._runtime_ready = threading.Event()
        self.enable_sentiment_analysis = _config.system.enable_sentiment_analysis
        self.enable_split_by_punc = _config.system.enable_clause_split
        self.chat_window_config = _config.service.chat_window
        self.system_prompt = self.llm_prompt_manager.system_prompt
        self.context_manager = ContextManager()
        self.conversation_id = f"conv_{uuid4().hex}"
        self._turn_id_lock = threading.Lock()
        self._turn_id_counter = 0
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
        # 工具记录回读：只读本次运行内的 harness 日志。路径不硬编码、也不另存一份，
        # 直接取 service.harness.path（与写入侧配置同源），配置走 provider 跟随热重载。
        self.harness_recall_service = HarnessRecallService(
            self.tool_runtime,
            lambda: _config,
            project_root=Path(__file__).resolve().parent,
            session_offset=_harness_boot_offset,
        )
        self.harness_recall_service.set_conversation_provider(lambda: self.conversation_id)
        self.sandbox_service = SandboxService(
            _config.service.sandbox, self.tool_runtime, project_root=get_project_dir(),
        )
        self.mcp_service = MCPService(
            _config.service.mcp, self.tool_runtime, project_root=get_project_dir(),
        )
        self.browser_service = BrowserService(
            _config.service.browser, self.tool_runtime, self.sandbox_service, self.mcp_service,
        )
        # 初始化记忆管理器（支持记忆提取/合并使用单独模型配置）。
        # 必须早于 ModelToolGateway：memory_recall 的说明要作为一条 policy 进去。
        self.memory_manager: MemoryManager | None = None
        self.memory_retriever: MemoryRetriever | None = None
        self.memory_service: MemoryService | None = None
        if _config.memory.enable:
            # 检索器与记忆管理器共享同一个实例：档案热重载与索引才是"成对替换"的。
            self.memory_retriever = MemoryRetriever(
                _config.memory.retrieval,
                data_dir=_config.memory.data_dir,
                core_path=_config.memory.data_dir / "core_memory.md",
                injection_max_chars=_config.memory.injection_max_chars,
            )
            memory_model_configs = resolve_memory_model_configs(_config)
            self.memory_manager = MemoryManager(
                config=_config.memory,
                retriever=self.memory_retriever,
                **memory_model_configs,
            )
            # 设置初始保存索引：注入块（system 提示 + injected_history）不参与压缩
            self.memory_manager.reset_saved_index(self.llm_prompt_manager.injected_len)
            self.memory_service = MemoryService(
                _config.memory, self.tool_runtime, self.memory_retriever
            )
            # 查询兜底链：模型（或意图路由）给的 query 空泛时，退回最近一轮用户话。
            self._last_user_text = ""
            self.memory_service.set_fallback_provider(lambda: self._last_user_text)
            logger.info("🧠 记忆管理器已初始化，并已设置注入历史索引。")
            # 记忆**不再**拼进 system_prompt：常驻窗口随日历滑动，进 system 会让
            # 每天零点全量 prompt 缓存失效。记忆改为当轮用户消息里的 MEMORY 块（方案 D2/M2）。
            self.memory_retriever.startup_check()

        self.model_tool_gateway = ModelToolGateway(
            self.tool_runtime, marker=tool_config.model_marker,
            policy_providers=tuple(filter(None, (
                self.sandbox_service.model_instruction, self.mcp_service.model_instruction,
                self.browser_service.model_instruction,
                self.memory_service.model_instruction if self.memory_service else None,
                self.harness_recall_service.model_instruction,
            ))),
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
        # ===== MLLM 集成（新增）=====
        self.mllm_cfg = _config.pipeline.mllm
        if self.mllm_cfg.enable:
            # 1. 禁用原有 LLM 和 VLM（覆盖基类赋值的属性）
            self.llm = None
            self.vlm = None
            # 2. 创建 MLLM Pipeline 客户端
            self.mllm_pipeline = MLLMSyncPipeline(self.mllm_cfg)
            # 3. 截图管理器
            interval = self.mllm_cfg.screenshot_interval
            max_queue = self.mllm_cfg.max_queue_size
            self._mllm_screenshot_manager = MLLMScreenshotManager(self.screen, interval, max_queue)
            self._mllm_screenshot_manager.start()
            logger.info("🌐 已启用 MLLM，并停用 LLM 与 VLM；截图管理器已启动。")
        else:
            self.mllm_pipeline = None
            self._mllm_screenshot_manager = None
        # TTS 调度器（预热、分句入队、自适应语速、打断与播放回收）
        self.tts_scheduler = BotTTSScheduler(
            config_provider=lambda: _config,
            tts_provider=lambda: self.tts,
            tts_prompt_manager_provider=lambda: self.tts_prompt_manager,
            speaker_provider=lambda: self.speaker,
            mic_provider=lambda: getattr(self, "mic", None),
            lang_provider=lambda: self.cur_lang,
            emit_event=emitter.emit,
        )

        # 语音输入控制器（TurnSense 语义 ASR 状态机、流式累加与桌面 ASR 缓冲）
        self.asr_controller = BotASRController(
            config_provider=lambda: _config,
            asr_provider=lambda: self.asr,
            mic_provider=lambda: getattr(self, "mic", None),
            on_interrupt_tts=self.tts_scheduler.interrupt,
            on_reset_auto_chat=lambda: self.vision_controller.reset_auto_chat_timer(),
            emit_event=emitter.emit,
        )

        # 视觉与主动搭话控制器（VLM 轮询/异步识别、截图降级与无输入定时器）
        self.vision_controller = BotVisionController(
            config_provider=lambda: _config,
            vlm_provider=lambda: self.vlm,
            ocr_provider=lambda: self.ocr,
            img_cap_provider=lambda: self.img_cap,
            screen_provider=lambda: self.screen,
            lang_provider=lambda: self.cur_lang,
            on_trigger_turn=self.emit_llm_prediction,
            emit_event=emitter.emit,
        )

        # 对话轮次编排器（上下文组装、流式/非流式推理、重试与工具调度）
        self.turn_orchestrator = BotTurnOrchestrator(
            config_provider=lambda: _config,
            conversation_id_provider=lambda: self.conversation_id,
            next_turn_id=self._next_turn_id,
            llm_provider=lambda: self.llm,
            mllm_provider=lambda: self.mllm_pipeline,
            mllm_cfg_provider=lambda: self.mllm_cfg,
            llm_prompt_manager_provider=lambda: self.llm_prompt_manager,
            context_manager_provider=lambda: self.context_manager,
            filter_provider=lambda: self.filter,
            memory_manager_provider=lambda: self.memory_manager,
            memory_retriever_provider=lambda: self.memory_retriever,
            model_tool_gateway_provider=lambda: self.model_tool_gateway,
            web_search_service_provider=lambda: self.web_search_service,
            skill_service_provider=lambda: self.skill_service,
            tool_coordinator_provider=lambda: self.tool_coordinator,
            browser_service_provider=lambda: getattr(self, "browser_service", None),
            tts_scheduler=self.tts_scheduler,
            asr_controller=self.asr_controller,
            vision_controller=self.vision_controller,
            on_user_text=lambda value: setattr(self, "_last_user_text", value),
            emit_event=emitter.emit,
        )

        self.event_binder = BotEventBinder(self)
        self.init()
        logger.info("🤖 Bot 服务初始化成功。")

    @staticmethod
    def get_runtime_config():
        return _config

    def reload_config(self) -> None:
        global _config
        _config = get_config()
        _config.character.chat.max_history = _config.memory.max_history

        self.bot_name = _config.character.bot_name
        self.enable_sentiment_analysis = _config.system.enable_sentiment_analysis
        self.enable_split_by_punc = _config.system.enable_clause_split
        self.chat_window_config = _config.service.chat_window
        self.cur_lang = _config.character.speech.language

        if self.llm_prompt_manager is not None:
            self.llm_prompt_manager.bot_name = _config.character.bot_name
            self.llm_prompt_manager.max_history = _config.memory.max_history
            rendered_prompt = self.llm_prompt_manager._render_template(
                _config.character.chat.system_prompt
            )
            self.llm_prompt_manager.replace_system_prompt(rendered_prompt)
            self.system_prompt = self.llm_prompt_manager.system_prompt

        if self.filter is not None:
            self.filter.set_words(list(_config.character.chat.filter.bad_words))

        if self.tts_prompt_manager is not None:
            try:
                self.tts_prompt_manager = TTSPromptManager(_config.character.speech)
            except Exception as exc:
                logger.warning(f"热重载 TTS 参考音频配置失败，保留原有配置：{exc}")

        self.mllm_cfg = _config.pipeline.mllm
        if self.mllm_pipeline is not None and self.mllm_cfg.enable:
            try:
                self.mllm_pipeline = MLLMSyncPipeline(self.mllm_cfg)
            except Exception as exc:
                logger.warning(f"热重载 MLLM 管线失败：{exc}")

        try:
            self.reload_pipeline()
        except Exception as exc:
            logger.warning(f"热重载模型管线时出错：{exc}")

        try:
            mic = getattr(self, "mic", None)
            if not _config.service.microphone.enable and mic is not None and mic.is_set_talk_enabled_event():
                mic.pause()
        except Exception as exc:
            logger.warning(f"热重载音频设备时出错：{exc}")

        configure_harness(
            project_root=Path(__file__).resolve().parent,
            **_config.service.harness.model_dump(),
        )
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
        self.harness_recall_service.update_config()
        if self.memory_manager is not None:
            self.memory_manager.config = _config.memory
            self.memory_manager.max_history = int(_config.memory.max_history)
            if int(_config.memory.save_interval_rounds) > 0:
                self.memory_manager.messages_per_save = int(_config.memory.save_interval_rounds) * 2
        if self.memory_retriever is not None:
            self.memory_retriever.update_config(
                _config.memory.retrieval,
                _config.memory.injection_max_chars,
            )
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
        self.asr_controller.refresh_runtime_config(reset_state=True)
        logger.info("♻️ Bot 运行时配置已热重载。")

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
                    target=self.vision_controller.continuous_loop,
                    daemon=True,
                    name="VLMContinuousThread"
                )
                threads.append(vlm_thread)

            for thread in threads:
                thread.start()

            # TTS 预热不参与上面的 KillableThread 生命周期（它可能阻塞在 HTTP 上），
            # 单独跑一个守护线程；Core 侧 TTS 服务可能比 Bot 晚就绪，失败会自动重试。
            self.tts_scheduler.start_warmup_thread()

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
        self.vision_controller.shutdown()
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
        self.asr_controller.shutdown()
        self.mcp_service.shutdown()
        self.web_search_service.shutdown()
        self.tts_scheduler.shutdown()
        if not self._runtime_stopped:
            await emitter.stop()
            kill_all_threads()
            self._runtime_stopped = True
        await stop_all_runnable()
        logger.info("再见！")

    def init(self):
        self.event_binder.bind()

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
        return self.turn_orchestrator.run_turn(
            BotTurnRequest(
                text=text,
                image_paths=image_paths,
                direct_return=direct_return,
                skip_tts=skip_tts,
                tool_context=_tool_context,
                skill_context=_skill_context,
                skip_web_route=_skip_web_route,
                images_base64=_images_base64,
                allow_model_search=_allow_model_search,
                web_directive=_web_directive,
                allow_keyword_match=_allow_keyword_match,
                turn_id=_turn_id,
                tool_round=_tool_round,
                continue_tool_turn=_continue_tool_turn,
                turn_history=_turn_history,
            )
        )

    def _reset_auto_chat_timer(self):
        self.vision_controller.reset_auto_chat_timer()

    def change_lang(self, lang: Language):
        self.cur_lang = lang
        if self.tts_prompt_manager is not None:
            self.tts_prompt_manager.set_lang(self.cur_lang)

    def play_tts(self, event: PipelineOutputTTSEvent):
        self.tts_scheduler.play(event)

    def _cancel_pending_tts(self):
        self.tts_scheduler.cancel_pending()

    def _interrupt_tts(self):
        self.tts_scheduler.interrupt()
