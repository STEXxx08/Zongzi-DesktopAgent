from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
import os
from typing import Any, Callable

from loguru import logger
import requests

from common.pseudo_markers import iter_visible_segments
from condata.pipeline.llm import Conversation, LLMPrediction, LLMQuery, RoleEnum
from condata.pipeline.mllm import MLLMPrediction, MLLMQuery
from event.event_data import CharacterStateEvent, PipelineOutputLLMEvent
from event.event_emitter import emitter
from services.emotion_prefix import (
    DEFAULT_EMOTION,
    consume_emotion_prefix,
    normalize_emotion,
)
from services.skill_framework import ModelSkillCreateDirective, ModelSkillDirective
from services.tool_coordinator import ToolCoordinationStatus
from services.tool_model_gateway import compose_tool_instructions
from services.web_search import SearchRequest, SearchSubmissionStatus
from services.web_search.service import LocalSearchDirective


@dataclass(slots=True)
class BotTurnRequest:
    """单轮对话请求参数对象（替代多参数传递）。"""

    text: str
    image_paths: list[str] | None = None
    direct_return: bool = False
    skip_tts: bool = False
    tool_context: str | None = None
    skill_context: str | None = None
    skip_web_route: bool = False
    images_base64: list[str] | None = None
    allow_model_search: bool = True
    web_directive: LocalSearchDirective | None = None
    allow_keyword_match: bool = True
    turn_id: int | None = None
    tool_round: int = 0
    continue_tool_turn: bool = False
    turn_history: tuple[Conversation, ...] = ()


class BotTurnOrchestrator:
    """直连 Bot 主控的对话轮次编排器：负责上下文拼装、流式/非流式推理、重试与工具调度。"""

    def __init__(
        self,
        *,
        config_provider: Callable[[], Any],
        conversation_id_provider: Callable[[], str],
        next_turn_id: Callable[[], int],
        llm_provider: Callable[[], Any],
        mllm_provider: Callable[[], Any],
        mllm_cfg_provider: Callable[[], Any],
        llm_prompt_manager_provider: Callable[[], Any],
        context_manager_provider: Callable[[], Any],
        filter_provider: Callable[[], Any],
        memory_manager_provider: Callable[[], Any],
        memory_retriever_provider: Callable[[], Any],
        model_tool_gateway_provider: Callable[[], Any],
        web_search_service_provider: Callable[[], Any],
        skill_service_provider: Callable[[], Any],
        tool_coordinator_provider: Callable[[], Any],
        browser_service_provider: Callable[[], Any],
        tts_scheduler: Any,
        asr_controller: Any,
        vision_controller: Any,
        on_user_text: Callable[[str], None] = lambda _text: None,
        emit_event: Callable[[Any], None] = emitter.emit,
    ) -> None:
        self._config_provider = config_provider
        self._conversation_id_provider = conversation_id_provider
        self._next_turn_id = next_turn_id
        self._llm_provider = llm_provider
        self._mllm_provider = mllm_provider
        self._mllm_cfg_provider = mllm_cfg_provider
        self._llm_prompt_manager_provider = llm_prompt_manager_provider
        self._context_manager_provider = context_manager_provider
        self._filter_provider = filter_provider
        self._memory_manager_provider = memory_manager_provider
        self._memory_retriever_provider = memory_retriever_provider
        self._model_tool_gateway_provider = model_tool_gateway_provider
        self._web_search_service_provider = web_search_service_provider
        self._skill_service_provider = skill_service_provider
        self._tool_coordinator_provider = tool_coordinator_provider
        self._browser_service_provider = browser_service_provider
        self._tts_scheduler = tts_scheduler
        self._asr_controller = asr_controller
        self._vision_controller = vision_controller
        self._on_user_text = on_user_text
        self._emit_event = emit_event

    def _build_turn_notices(
        self, *, tool_gate_open: bool, session_has_tools: bool, tool_round: int
    ) -> list[str]:
        """生成“本轮系统状态”声明（放入用户轮以保留 Prompt Cache 前缀）。"""
        cfg = self._config_provider()
        notices: list[str] = []
        if (
            session_has_tools
            and not tool_gate_open
            and tool_round >= cfg.service.tool.max_rounds
        ):
            notices.append(
                f"本轮的工具调用轮数已达上限（{cfg.service.tool.max_rounds} 轮），"
                "工具本轮不可用：不要输出任何工具调用标记，直接用已有信息给出最终回答。"
            )
        return notices

    def _allowed_emotions(self) -> list[str]:
        """允许模型输出的情绪标签（来自 character.emotions）。"""
        cfg = self._config_provider()
        emotions = getattr(getattr(cfg, "character", None), "emotions", None) or []
        tags = [str(item).strip() for item in emotions if str(item).strip()]
        if DEFAULT_EMOTION not in tags:
            tags.insert(0, DEFAULT_EMOTION)
        return tags

    def _resolve_emotion_tag(self, requested_emotion: str | None) -> str | None:
        """校验模型请求的情绪标签；不在白名单内返回 None。"""
        if not requested_emotion:
            return None
        allowed = self._allowed_emotions()
        if any(requested_emotion.casefold() == item.casefold() for item in allowed):
            return normalize_emotion(requested_emotion, allowed, DEFAULT_EMOTION)
        return None

    def _strip_response_emotion(
        self, response: str, *, has_pending_tool: bool
    ) -> tuple[str, str | None]:
        """剥离可见回复开头的 `<emotion>…</emotion>`，返回（可见文本, 已校验情绪）。"""
        _, visible_response, requested_emotion = consume_emotion_prefix(
            response or "", flush=True
        )
        visible_text = visible_response.strip()
        emotion = (
            self._resolve_emotion_tag(requested_emotion) if requested_emotion else None
        )
        if has_pending_tool and not visible_text:
            visible_text = "稍等，我处理一下。"
        return visible_text, emotion

    @staticmethod
    def _merge_web_search_requests(
        primary: SearchRequest,
        pending: list[SearchRequest],
    ) -> SearchRequest:
        requests_to_merge = [primary, *pending]
        texts = [
            item.text.strip()
            for item in requests_to_merge
            if item.text and item.text.strip()
        ]
        if len(texts) <= 1:
            merged_text = texts[0] if texts else ""
        else:
            merged_text = (
                texts[0] + "\n\n【用户在联网等待期间补充】\n" + "\n".join(texts[1:])
            )
        return SearchRequest(
            text=merged_text,
            image_paths=[
                path for item in requests_to_merge for path in item.image_paths
            ],
            images_base64=[
                image for item in requests_to_merge for image in item.images_base64
            ],
            skip_tts=all(item.skip_tts for item in requests_to_merge),
            directive=primary.directive,
            allow_keyword_match=primary.allow_keyword_match,
            conversation_id=primary.conversation_id,
            turn_id=primary.turn_id,
            round=primary.round,
            turn_history=primary.turn_history,
        )

    def render_tool_output(self, result) -> str:
        """把工具结果渲染成注入上下文的文本，并按配置上限截断。"""
        cfg = self._config_provider()
        limit = int(cfg.service.tool.max_result_chars)
        output = (
            json.dumps(result.output, ensure_ascii=False, default=str)
            if result.ok
            else str(result.error)
        )
        if len(output) > limit:
            output = output[:limit] + "\n【工具结果已截断】"
        return output

    def emit_tool_acknowledgement(self, text: str, skip_tts: bool) -> None:
        web_service = self._web_search_service_provider()
        acknowledgement = (text or web_service.config.acknowledgement).strip()
        if not acknowledgement:
            return
        prompt_manager = self._llm_prompt_manager_provider()
        acknowledgement = prompt_manager.render_bot_name(acknowledgement)
        mllm_cfg = self._mllm_cfg_provider()
        prediction_cls = MLLMPrediction if mllm_cfg.enable else LLMPrediction
        prediction = prediction_cls(response=acknowledgement, history=[])
        self._emit_event(
            PipelineOutputLLMEvent(prediction=prediction, skip_tts=skip_tts)
        )

    def submit_routed_tool(
        self,
        route_decision,
        original_text: str,
        image_paths,
        skip_tts: bool,
        turn_id: int,
    ) -> None:
        """通过共享 ToolRuntime 执行所有本地路由选中的工具。"""
        conversation_id = self._conversation_id_provider()
        call = route_decision.tool_call.with_context(
            conversation_id=conversation_id,
            turn_id=turn_id,
            round=1,
        )
        primary = SearchRequest(
            text=original_text,
            image_paths=list(image_paths or []),
            skip_tts=skip_tts,
            conversation_id=conversation_id,
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
                        f"结果：{self.render_tool_output(result)}"
                    )
                else:
                    contexts.append(
                        f"工具：{result.call.name}\n执行失败：{self.render_tool_output(result)}"
                    )
            self.run_turn(
                BotTurnRequest(
                    text=merged.text,
                    image_paths=merged.image_paths or None,
                    skip_tts=merged.skip_tts,
                    tool_context="\n\n".join(contexts),
                    turn_history=merged.turn_history,
                    skip_web_route=True,
                    allow_model_search=True,
                    images_base64=merged.images_base64 or None,
                    turn_id=turn_id,
                    tool_round=call.round,
                    continue_tool_turn=True,
                )
            )

        coordinator = self._tool_coordinator_provider()
        try:
            submission = coordinator.submit(
                call,
                primary=primary,
                on_ready=on_ready,
                on_deferred=self.on_web_search_deferred,
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
            self._tts_scheduler.cancel_pending()
            self.emit_tool_acknowledgement(acknowledgement, skip_tts)
        elif submission.status == ToolCoordinationStatus.BUFFERED:
            logger.info(
                f"工具请求已进入缓冲区：conversation_id={conversation_id}，"
                f"turn_id={turn_id}"
            )
        elif submission.status == ToolCoordinationStatus.REJECTED:
            logger.info(
                f"工具请求被拒绝：conversation_id={conversation_id}，turn_id={turn_id}"
            )

    def on_web_search_ready(
        self,
        primary: SearchRequest,
        pending: list[SearchRequest],
        context: str,
        _error: str | None,
    ) -> None:
        merged = self._merge_web_search_requests(primary, pending)
        self.run_turn(
            BotTurnRequest(
                text=merged.text,
                image_paths=merged.image_paths or None,
                skip_tts=merged.skip_tts,
                tool_context=context,
                turn_history=merged.turn_history,
                skip_web_route=True,
                images_base64=merged.images_base64 or None,
                allow_model_search=True,
                turn_id=merged.turn_id,
                tool_round=merged.round,
                continue_tool_turn=True,
            )
        )

    def on_web_search_deferred(self, pending: list[SearchRequest]) -> None:
        if not pending:
            return
        merged = self._merge_web_search_requests(pending[0], pending[1:])
        self.run_turn(
            BotTurnRequest(
                text=merged.text,
                image_paths=merged.image_paths or None,
                skip_tts=merged.skip_tts,
                images_base64=merged.images_base64 or None,
                web_directive=merged.directive,
                allow_keyword_match=merged.allow_keyword_match,
                turn_id=merged.turn_id,
                tool_round=max(merged.round - 1, 0),
                turn_history=merged.turn_history,
            )
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
        conversation_id = self._conversation_id_provider()
        call = directive.tool_call().with_context(
            conversation_id=request.conversation_id or conversation_id,
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
                logger.error(f"Skill“{directive.name}”{operation}失败：{result.error}")
                skill_context = None
                tool_context = f"Skill“{directive.name}”{operation}失败：{result.error}"
            self.run_turn(
                BotTurnRequest(
                    text=merged.text,
                    image_paths=merged.image_paths or None,
                    skip_tts=merged.skip_tts,
                    tool_context=tool_context,
                    turn_history=merged.turn_history,
                    skill_context=skill_context,
                    skip_web_route=True,
                    allow_model_search=True,
                    images_base64=merged.images_base64 or None,
                    turn_id=call.turn_id,
                    tool_round=call.round,
                    continue_tool_turn=True,
                )
            )

        coordinator = self._tool_coordinator_provider()
        try:
            submission = coordinator.submit(
                call,
                primary=request,
                on_ready=on_ready,
                on_deferred=self.on_web_search_deferred,
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
        conversation_id = self._conversation_id_provider()
        call = tool_call.with_context(
            conversation_id=request.conversation_id or conversation_id,
            turn_id=request.turn_id or self._next_turn_id(),
            round=request.round,
        )

        def on_ready(primary, pending, results):
            cfg = self._config_provider()
            merged = self._merge_web_search_requests(primary, pending)
            contexts = []
            for result in results:
                output = self.render_tool_output(result)
                limit = int(cfg.service.tool.max_result_chars)
                arguments = json.dumps(
                    result.call.arguments, ensure_ascii=False, default=str
                )
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
            self.run_turn(
                BotTurnRequest(
                    text=merged.text,
                    image_paths=merged.image_paths or None,
                    skip_tts=merged.skip_tts,
                    tool_context="\n\n".join(contexts),
                    turn_history=merged.turn_history,
                    skill_context=skill_context,
                    skip_web_route=True,
                    images_base64=merged.images_base64 or None,
                    turn_id=call.turn_id,
                    tool_round=call.round,
                    continue_tool_turn=True,
                )
            )

        coordinator = self._tool_coordinator_provider()
        try:
            submission = coordinator.submit(
                call,
                primary=request,
                on_ready=on_ready,
                on_deferred=self.on_web_search_deferred,
                continue_active=continue_active,
            )
        except RuntimeError as exc:
            logger.warning(f"关闭过程中已忽略通用工具请求：{exc}")
            return ToolCoordinationStatus.REJECTED
        return submission.status

    def extract_tool_visible_text(
        self, buffer: str, flush: bool = False
    ) -> tuple[str, str]:
        """按实际配置统一隐藏各类机器协议与伪系统块，供日志、历史和 TTS 共用。"""
        web_config = getattr(self._web_search_service_provider(), "config", None)
        skill_config = getattr(self._skill_service_provider(), "config", None)
        gateway = self._model_tool_gateway_provider()
        markers = (
            gateway.marker,
            getattr(web_config, "model_marker", ""),
            getattr(skill_config, "model_marker", ""),
            getattr(skill_config, "model_create_marker", ""),
        )
        visible, pending = gateway.extract_stream_visible_text(
            buffer, flush=flush, markers=markers
        )
        segments = iter_visible_segments(visible, flush=flush)
        plain = "".join(segment for segment, _ in segments)
        consumed = segments[-1][1] if segments else 0
        pseudo_pending = visible[consumed:]
        if pending:
            return plain, pseudo_pending + pending
        return plain, pseudo_pending

    def _memory_context_for_turn(
        self, user_text: str, *, allow_random: bool
    ) -> str | None:
        """组装当轮 `MEMORY` 块（常驻窗口 + 随机回忆），异常时降级跳过。"""
        retriever = self._memory_retriever_provider()
        if retriever is None:
            return None
        try:
            retriever.refresh()
        except Exception as exc:
            logger.warning(f"🧠 记忆档案热重载检查失败（忽略）：{exc}")
        try:
            turn = retriever.build_turn_memory(
                query=user_text or None, allow_random=allow_random
            )
        except Exception as exc:
            logger.warning(f"🧠 组装记忆块失败，本轮不加记忆块：{exc}")
            return None
        for warning in turn.warnings:
            logger.error(f"🧠 记忆检索降级：{warning}")
        return turn.text

    def _predict_sync(self, query):
        mllm_cfg = self._mllm_cfg_provider()
        mllm_pipeline = self._mllm_provider()
        if mllm_cfg.enable and mllm_pipeline is not None:
            return mllm_pipeline.predict(query)
        return self._llm_provider().predict(query)

    def _commit_history_and_memory(
        self, user_content: str, assistant_content: str, *, log_label: str = "本轮对话"
    ) -> None:
        prompt_manager = self._llm_prompt_manager_provider()

        def _append_turn(current_history):
            return [
                *current_history,
                Conversation(role=RoleEnum.user, content=user_content),
                Conversation(role=RoleEnum.assistant, content=assistant_content),
            ]

        if not prompt_manager.commit_history_update(_append_turn):
            logger.warning(f"{log_label}未能写入历史（历史提交被拒绝）。")

        memory_manager = self._memory_manager_provider()
        if memory_manager:
            memory_manager.maybe_save_snapshot(
                current_history=prompt_manager.history_snapshot(),
                injected_len=prompt_manager.injected_len,
                force=False,
                history_snapshot=prompt_manager,
            )

    def run_turn(self, request: BotTurnRequest) -> LLMPrediction | None:
        cfg = self._config_provider()
        conversation_id = self._conversation_id_provider()
        turn_id = request.turn_id if request.turn_id is not None else self._next_turn_id()
        self._tts_scheduler.abort_warmup()
        logger.debug(
            f"调用 emit_llm_prediction：conversation_id={conversation_id}，"
            f"turn_id={turn_id}，已完成工具轮数={request.tool_round}"
        )

        web_service = self._web_search_service_provider()
        if not request.skip_web_route and not request.direct_return and request.text:
            web_request = SearchRequest(
                text=request.text,
                image_paths=list(request.image_paths or []),
                images_base64=list(request.images_base64 or []),
                skip_tts=request.skip_tts,
                conversation_id=conversation_id,
                turn_id=turn_id,
                round=request.tool_round + 1,
                turn_history=request.turn_history,
            )
            submission = web_service.submit_or_queue(
                web_request,
                self.on_web_search_ready,
                self.on_web_search_deferred,
                directive=request.web_directive,
                allow_keyword_match=request.allow_keyword_match,
            )
            if submission.status == SearchSubmissionStatus.STARTED:
                logger.info(
                    f"联网工具已启动：路由={submission.route_name or '旧关键词'}，"
                    f"工具={submission.tool or 'search'}，查询={submission.query}"
                )
                self._tts_scheduler.cancel_pending()
                self.emit_tool_acknowledgement(
                    submission.acknowledgement or web_service.config.acknowledgement,
                    request.skip_tts,
                )
                return None
            if submission.status == SearchSubmissionStatus.QUEUED:
                logger.info("工具调用期间收到的 LLM 请求已进入缓冲区。")
                return None
            if submission.status == SearchSubmissionStatus.REJECTED:
                logger.info("独占工具运行期间已拒绝新的 LLM 请求。")
                return None

        user_text = request.text if request.text else ""
        self._on_user_text(user_text)

        environment_context, user_text, proactive = (
            self._vision_controller.consume_vlm_context(user_text)
        )
        desktop_text = self._asr_controller.take_desktop_asr_text()
        if desktop_text:
            environment_context.append(("近期桌面音频", desktop_text))
        browser_service = self._browser_service_provider()
        if browser_service is not None:
            research_catalog = browser_service.context_catalog()
            if research_catalog:
                environment_context.append(("已存研究资料", research_catalog))
        logger.debug(
            f"上下文来源：环境项数={len(environment_context)}，"
            f"包含工具上下文={bool(request.tool_context)}，包含文本={bool(request.text)}"
        )

        gateway = self._model_tool_gateway_provider()
        skill_service = self._skill_service_provider()
        prompt_manager = self._llm_prompt_manager_provider()
        context_manager = self._context_manager_provider()

        model_instructions = []
        session_has_tools = bool(gateway.definitions)
        session_web_tools = web_service.model_decision_enabled
        session_has_skill = bool(cfg.service.skills.enable)
        allow_model_search = (
            request.allow_model_search
            and not request.direct_return
            and request.tool_round < cfg.service.tool.max_rounds
        )
        tool_gate_open = allow_model_search
        if session_has_tools:
            model_instructions.append(gateway.model_instruction(include_common=False))
        if session_web_tools:
            model_instructions.append(
                web_service.model_instruction(include_common=False)
            )
        if session_has_skill:
            skill_instruction = skill_service.model_instruction(include_common=False)
            if skill_instruction:
                model_instructions.append(skill_instruction)
        allow_model_tools = allow_model_search and session_has_tools
        allow_model_skill = allow_model_search and session_has_skill
        turn_notices = self._build_turn_notices(
            tool_gate_open=tool_gate_open,
            session_has_tools=session_has_tools
            or session_web_tools
            or session_has_skill,
            tool_round=request.tool_round,
        )
        emotion_tags = self._allowed_emotions()
        emotion_instruction = ""
        if not request.direct_return and emotion_tags:
            emotion_instruction = (
                "\n\n普通用户可见的最终回复必须以 <emotion>情绪标签</emotion> 开头，"
                "标签只能从以下 TTS 情绪中选择："
                + ", ".join(emotion_tags)
                + "。标签后直接写回复正文，不要解释标签；调用工具、联网或 Skill 时保持原协议格式，不加情绪标签。"
                "回复正文只写要念给主人听的话：不写 <system>/<analysis>/<plan> 之类的内部块，"
                "也不要在正文里复述本次该怎么做。"
            )
        prepared_context = context_manager.prepare(
            base_system_prompt=prompt_manager.system_prompt + emotion_instruction,
            current_history=[*prompt_manager.current_history, *request.turn_history],
            user_text=user_text,
            environment=environment_context,
            tool_context=request.tool_context,
            skill_context=request.skill_context,
            tool_instruction=compose_tool_instructions(model_instructions),
            memory_context=self._memory_context_for_turn(
                user_text,
                allow_random=(
                    request.tool_context is None and request.skill_context is None
                ),
            ),
            turn_notices=turn_notices,
            current_time=datetime.now(),
            proactive=proactive,
        )
        final_text = prepared_context.user_message
        temp_history = prepared_context.history

        mllm_cfg = self._mllm_cfg_provider()
        mllm_pipeline = self._mllm_provider()
        llm = self._llm_provider()

        images_base64 = list(request.images_base64 or [])
        if mllm_cfg.enable and request.image_paths:
            for img_path in request.image_paths:
                if os.path.exists(img_path):
                    try:
                        with open(img_path, "rb") as f:
                            images_base64.append(base64.b64encode(f.read()).decode())
                        os.remove(img_path)
                    except Exception as exc:
                        logger.warning(f"处理图片 {img_path} 失败：{exc}")
        logger.debug(
            f"MLLM 请求准备完成：图片数={len(images_base64)}，"
            f"历史消息数={len(temp_history)}，文本字符数={len(final_text)}"
        )

        reasoning_keywords = getattr(cfg.pipeline.mllm, "reasoning_keywords", []) or []
        enable_thinking = any(
            keyword and keyword in user_text for keyword in reasoning_keywords
        )
        thinking_budget = int(
            getattr(cfg.pipeline.mllm, "reasoning_budget_on_keyword", 80)
        )
        use_mllm_stream = (
            mllm_cfg.enable
            and mllm_pipeline is not None
            and getattr(mllm_cfg, "stream_output", False)
            and not request.direct_return
            and not allow_model_search
            and not allow_model_skill
            and not allow_model_tools
        )
        trace_context = {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "round": request.tool_round,
            "source": "dialogue",
        }
        if mllm_cfg.enable and mllm_pipeline is not None:
            query = MLLMQuery(
                text=final_text, history=temp_history, images=images_base64
            )
            object.__setattr__(query, "_harness_context", trace_context)
            object.__setattr__(query, "enable_thinking", enable_thinking)
            if enable_thinking:
                object.__setattr__(query, "thinking_budget", thinking_budget)
                logger.debug(
                    f"关键词已启用 MLLM 思考：thinking_budget={thinking_budget}"
                )
            if use_mllm_stream:
                logger.debug("开始调用 MLLM 流式接口。")
                prediction = self.emit_streaming_mllm_prediction(
                    query, request.text, request.skip_tts
                )
                logger.debug("MLLM 流式接口已返回。")
                return prediction
            logger.debug("开始调用 MLLM 预测接口。")
            prediction = mllm_pipeline.predict(query)
            logger.debug("MLLM 预测接口已返回。")
        else:
            query = LLMQuery(text=final_text, history=temp_history)
            object.__setattr__(query, "_harness_context", trace_context)
            use_llm_stream = (
                getattr(cfg.pipeline.llm, "stream_output", True)
                and hasattr(llm, "stream_predict")
                and not request.direct_return
                and not allow_model_search
                and not allow_model_skill
                and not allow_model_tools
            )
            if use_llm_stream:
                logger.debug("开始调用 LLM 流式接口。")
                streamed, prediction = self.emit_streaming_llm_prediction(
                    query, request.text, request.skip_tts
                )
                if streamed:
                    logger.debug("LLM 流式接口已返回。")
                    return prediction
                logger.debug("LLM 流式接口不可用，回退到非流式 predict。")
            logger.debug("开始调用 LLM 预测接口。")
            prediction = llm.predict(query)
            logger.debug("LLM 预测接口已返回。")

        pending_model_directive = None
        pending_skill_directive = None
        pending_tool_call = None
        output_truncation_guidance = None
        if prediction is not None:
            tool_visible, tool_call = gateway.parse_model_response(
                prediction.response or "",
                context={
                    **trace_context,
                    "model_call_id": getattr(query, "_harness_call_id", None),
                },
            )
            output_truncation_failed = False
            output_finish_reason = str(
                getattr(prediction, "finish_reason", "") or ""
            ).lower()
            output_limit_reached = output_finish_reason in {
                "length",
                "max_tokens",
                "token_limit",
            }
            max_output_retries = int(getattr(cfg.service.tool, "max_output_retries", 1))
            if allow_model_tools and output_limit_reached and gateway.last_error:
                output_truncation_guidance = gateway.format_output_truncation(
                    getattr(prediction, "max_output_tokens", None)
                )
                if max_output_retries:
                    logger.info(
                        f"模型输出达到 token 上限，触发分段恢复：turn_id={turn_id}，"
                        f"上限={getattr(prediction, 'max_output_tokens', None)}"
                    )
                    query = query.model_copy(
                        update={
                            "text": final_text
                            + "\n\n【输出长度恢复】\n"
                            + output_truncation_guidance
                        }
                    )
                    prediction = self._predict_sync(query)
                    if prediction is None:
                        return None
                    tool_visible, tool_call = gateway.parse_model_response(
                        prediction.response or "",
                        context={
                            **trace_context,
                            "model_call_id": getattr(query, "_harness_call_id", None),
                            "output_recovery": 1,
                        },
                    )
                    retry_finish_reason = str(
                        getattr(prediction, "finish_reason", "") or ""
                    ).lower()
                    if (
                        retry_finish_reason in {"length", "max_tokens", "token_limit"}
                        and tool_call is None
                    ):
                        output_truncation_failed = True
                else:
                    output_truncation_failed = True

            retries = 0
            max_parse_retries = getattr(cfg.service.tool, "max_parse_retries", 1)
            while (
                allow_model_tools
                and not output_truncation_failed
                and gateway.last_error
                and retries < max_parse_retries
            ):
                retries += 1
                logger.info(
                    f"通用工具指令格式纠正：第 {retries}/{max_parse_retries} 次，turn_id={turn_id}"
                )
                correction = gateway.format_correction()
                query = query.model_copy(
                    update={"text": final_text + "\n\n【工具协议纠正】\n" + correction}
                )
                prediction = self._predict_sync(query)
                if prediction is None:
                    return None
                tool_visible, tool_call = gateway.parse_model_response(
                    prediction.response or "",
                    context={
                        **trace_context,
                        "model_call_id": getattr(query, "_harness_call_id", None),
                        "parse_retry": retries,
                    },
                )

            if output_truncation_failed:
                prediction.response = (
                    "模型输出达到长度上限，未执行不完整的工具调用。请缩短本次文件内容或分段继续。"
                )
                tool_visible = prediction.response
                tool_call = None
            elif gateway.last_error:
                prediction.response = "工具调用格式无效，本次未执行任何操作。请重试。"
                tool_visible = prediction.response
            prediction.response = tool_visible

            if tool_call is not None and allow_model_tools:
                pending_tool_call = tool_call
            elif tool_call is not None:
                logger.warning("通用工具已禁用或达到轮数上限，未执行模型工具请求。")

            raw_model_response = prediction.response or ""
            acknowledgement, directive = web_service.parse_model_response(
                raw_model_response
            )
            if acknowledgement != raw_model_response:
                prediction.response = acknowledgement
            if (
                directive is not None
                and allow_model_search
                and pending_tool_call is None
            ):
                logger.info(
                    f"已解析模型联网指令：工具={directive.tool}，"
                    f"查询={directive.query}，turn_id={turn_id}，"
                    f"下一轮={request.tool_round + 1}"
                )
                pending_model_directive = directive
            if directive is not None:
                if pending_model_directive is None and pending_tool_call is None:
                    logger.warning(
                        f"工具调用已禁用或达到轮数上限，已忽略模型联网指令："
                        f"工具={directive.tool}，查询={directive.query}，round={request.tool_round}"
                    )

            skill_visible, skill_directive = skill_service.parse_model_response(
                prediction.response or ""
            )
            if skill_visible != prediction.response:
                prediction.response = skill_visible
            if skill_directive is not None and allow_model_skill:
                if pending_model_directive is None and pending_tool_call is None:
                    logger.info(
                        f"已解析模型 Skill 指令：Skill={skill_directive.name}，"
                        f"turn_id={turn_id}，下一轮={request.tool_round + 1}"
                    )
                    pending_skill_directive = skill_directive
                else:
                    logger.warning("模型同时请求多种工具协议，本轮仅执行优先选中的工具。")
            elif skill_directive is not None:
                logger.warning(
                    f"Skills 已禁用或达到轮数上限，已忽略模型 Skill 指令："
                    f"Skill={skill_directive.name}，round={request.tool_round}"
                )

        if prediction is None:
            return None
        prediction.response = self.extract_tool_visible_text(
            prediction.response or "", flush=True
        )[0].strip()

        has_pending_tool = (
            pending_model_directive is not None
            or pending_skill_directive is not None
            or pending_tool_call is not None
        )
        response_emotion = None
        if not request.direct_return:
            prediction.response, response_emotion = self._strip_response_emotion(
                prediction.response or "", has_pending_tool=has_pending_tool
            )
        content_filter = self._filter_provider()
        if content_filter.filter(prediction.response):
            logger.warning(f"LLM 输出已被过滤：{prediction.response}")
            return None
        if prediction.response and prediction.response[0] == "\n":
            prediction.response = prediction.response[1:]
        self._tts_scheduler.cancel_pending()

        turn_history = request.turn_history
        if has_pending_tool:
            turn_history = (
                *request.turn_history,
                Conversation(
                    role=RoleEnum.user, content=prepared_context.turn_user_message
                ),
                Conversation(role=RoleEnum.assistant, content=prediction.response),
            )
            if output_truncation_guidance:
                turn_history = (
                    *turn_history,
                    Conversation(
                        role=RoleEnum.user, content=output_truncation_guidance
                    ),
                )
            logger.debug("工具中间记录仅缓存于当前 turn，不写入对话历史或长期记忆。")
        else:
            self._commit_history_and_memory(
                request.text, prediction.response, log_label="本轮对话"
            )

        if not request.direct_return:
            self._emit_event(
                PipelineOutputLLMEvent(
                    prediction=prediction,
                    skip_tts=request.skip_tts,
                    emotion=response_emotion,
                )
            )
            logger.debug("已发射 LLM 结果事件。")

        self._vision_controller.reset_auto_chat_timer()

        next_search_request = SearchRequest(
            text=request.text,
            images_base64=images_base64,
            skip_tts=request.skip_tts,
            conversation_id=conversation_id,
            turn_id=turn_id,
            round=request.tool_round + 1,
            turn_history=turn_history,
        )
        if pending_tool_call is not None:
            status = self._begin_model_tool(
                next_search_request,
                pending_tool_call,
                skill_context=request.skill_context,
                continue_active=request.continue_tool_turn,
            )
            if status == ToolCoordinationStatus.REJECTED:
                logger.warning(
                    f"通用工具请求被拒绝：turn_id={turn_id}，round={request.tool_round + 1}"
                )
        elif pending_model_directive is not None:
            submission = web_service.begin_model_search(
                next_search_request,
                pending_model_directive,
                self.on_web_search_ready,
                self.on_web_search_deferred,
                continue_active=request.continue_tool_turn,
            )
            if submission.status == SearchSubmissionStatus.QUEUED:
                logger.info("模型工具请求已排入当前工具调用之后的缓冲区。")
            elif submission.status == SearchSubmissionStatus.REJECTED:
                logger.warning(
                    f"模型的后续工具调用被拒绝：turn_id={turn_id}，"
                    f"round={request.tool_round + 1}"
                )
        elif pending_skill_directive is not None:
            status = self._begin_model_skill(
                next_search_request,
                pending_skill_directive,
                continue_active=request.continue_tool_turn,
            )
            if status == ToolCoordinationStatus.BUFFERED:
                logger.info("模型 Skill 请求已进入工具缓冲区。")
            elif status == ToolCoordinationStatus.REJECTED:
                logger.warning(
                    f"模型 Skill 后续加载被拒绝：turn_id={turn_id}，"
                    f"round={request.tool_round + 1}"
                )

        return prediction

    def emit_streaming_mllm_prediction(
        self, query: MLLMQuery, original_text: str, skip_tts: bool
    ) -> MLLMPrediction | None:
        mllm_cfg = self._mllm_cfg_provider()
        mllm_pipeline = self._mllm_provider()
        _, prediction = self.emit_streaming_prediction(
            label="MLLM",
            stream_fn=mllm_pipeline.stream_predict,
            prediction_cls=MLLMPrediction,
            query=query,
            original_text=original_text,
            skip_tts=skip_tts,
            stream_tts_by_sentence=getattr(mllm_cfg, "stream_tts_by_sentence", True),
            cumulative=False,
            fallback_on_unstarted_error=False,
        )
        return prediction

    def emit_streaming_llm_prediction(
        self, query: LLMQuery, original_text: str, skip_tts: bool
    ) -> tuple[bool, LLMPrediction | None]:
        cfg = self._config_provider()
        llm = self._llm_provider()
        cumulative = True if getattr(llm, "_is_openai_format", False) else None
        return self.emit_streaming_prediction(
            label="LLM",
            stream_fn=llm.stream_predict,
            prediction_cls=LLMPrediction,
            query=query,
            original_text=original_text,
            skip_tts=skip_tts,
            stream_tts_by_sentence=getattr(
                cfg.pipeline.llm, "stream_tts_by_sentence", True
            ),
            cumulative=cumulative,
            fallback_on_unstarted_error=True,
        )

    def emit_streaming_prediction(
        self,
        *,
        label: str,
        stream_fn,
        prediction_cls,
        query,
        original_text: str,
        skip_tts: bool,
        stream_tts_by_sentence: bool,
        cumulative: bool | None,
        fallback_on_unstarted_error: bool,
    ):
        logger.debug(f"开始执行 {label} 流式输出处理。")
        self._tts_scheduler.cancel_pending()
        raw_accumulated = ""
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
            tts_round_task_id = self._tts_scheduler.current_task_id()

        content_filter = self._filter_provider()

        def _apply_emotion(requested: str | None) -> None:
            nonlocal response_emotion, tts_prompt
            if not requested:
                return
            resolved = self._resolve_emotion_tag(requested)
            if resolved:
                response_emotion = resolved
                tts_prompt = self._tts_scheduler.get_prompt(response_emotion)

        def _enqueue_segments(segments: list[str]) -> None:
            nonlocal tts_enqueued
            for segment in segments:
                if not content_filter.filter(segment):
                    tts_enqueued = (
                        self._tts_scheduler.enqueue(
                            segment, tts_prompt, response_emotion
                        )
                        or tts_enqueued
                    )

        try:
            for chunk_prediction in stream_fn(query):
                raw_piece = chunk_prediction.response or ""
                if not raw_piece:
                    continue
                if raw_piece.startswith("Error:"):
                    logger.error(
                        f"{label} 流式输出失败，已停止处理；错误数据块未输出。"
                    )
                    return True, None
                if cumulative is True:
                    chunk = (
                        raw_piece[len(raw_accumulated) :]
                        if raw_piece.startswith(raw_accumulated)
                        else raw_piece
                    )
                elif cumulative is None:
                    is_cumulative_repeat = bool(
                        raw_accumulated
                        and raw_piece.startswith(raw_accumulated)
                        and (
                            len(raw_piece) > len(raw_accumulated)
                            or bool(getattr(chunk_prediction, "history", None))
                            or getattr(chunk_prediction, "usage", None) is not None
                        )
                    )
                    chunk = (
                        raw_piece[len(raw_accumulated) :]
                        if is_cumulative_repeat
                        else raw_piece
                    )
                else:
                    chunk = raw_piece
                if not chunk:
                    continue
                if not full_response:
                    logger.debug(f"已收到 {label} 的首个流式数据块。")
                raw_accumulated += chunk
                full_response += chunk
                if emotion_prefix_resolved:
                    visible_prefix_text = chunk
                else:
                    emotion_prefix_buffer += chunk
                    (
                        emotion_prefix_resolved,
                        visible_prefix_text,
                        requested_emotion,
                    ) = consume_emotion_prefix(emotion_prefix_buffer)
                    if emotion_prefix_resolved:
                        emotion_prefix_buffer = ""
                        _apply_emotion(requested_emotion)
                tts_raw_buffer += visible_prefix_text
                visible_text, tts_raw_buffer = self.extract_tool_visible_text(
                    tts_raw_buffer
                )
                if visible_text:
                    logger.info(f"{label} 流式输出：" + visible_text)
                if skip_tts or not stream_tts_by_sentence:
                    continue
                tts_visible_buffer += visible_text
                segments, tts_visible_buffer = self._tts_scheduler.pop_stream_segments(
                    tts_visible_buffer
                )
                _enqueue_segments(segments)
        except (NotImplementedError, requests.RequestException) as exc:
            if fallback_on_unstarted_error and not full_response and not tts_enqueued:
                logger.debug(f"{label} 流式请求未启动即失败，回退非流式：{exc}")
                return False, None
            raise

        if not emotion_prefix_resolved:
            _, visible_prefix_text, requested_emotion = consume_emotion_prefix(
                emotion_prefix_buffer, flush=True
            )
            _apply_emotion(requested_emotion)
            tts_raw_buffer += visible_prefix_text
        visible_response, _ = self.extract_tool_visible_text(
            full_response,
            flush=True,
        )
        _, response_without_emotion, _ = consume_emotion_prefix(
            full_response, flush=True
        )
        if visible_response != response_without_emotion:
            logger.warning(
                f"已隐藏 {label} 流式响应中意外出现的机器工具指令，未执行该指令。"
            )
        full_response = visible_response.strip()
        _, full_response, requested_emotion = consume_emotion_prefix(
            full_response, flush=True
        )
        _apply_emotion(requested_emotion)
        full_response = full_response.strip()
        if not full_response:
            logger.warning(f"{label} 流式接口返回了空响应。")
            return True, None
        if content_filter.filter(full_response):
            logger.warning(f"{label} 流式输出已被过滤：{full_response}")
            return True, None
        if skip_tts:
            prompt = tts_prompt or self._tts_scheduler.select_prompt(full_response)
            self._emit_event(
                CharacterStateEvent(
                    state="replied", emotion=response_emotion or prompt.sentiment
                )
            )
        else:
            if stream_tts_by_sentence:
                visible_tail, _ = self.extract_tool_visible_text(
                    tts_raw_buffer,
                    flush=True,
                )
                tts_visible_buffer += visible_tail
                segments, _ = self._tts_scheduler.pop_stream_segments(
                    tts_visible_buffer,
                    flush=True,
                )
                _enqueue_segments(segments)
            else:
                tts_enqueued = (
                    self._tts_scheduler.enqueue(
                        full_response, tts_prompt, response_emotion
                    )
                    or tts_enqueued
                )
            if tts_round_task_id is not None and tts_enqueued:
                self._tts_scheduler.watch_round_end(tts_round_task_id)
            elif not tts_enqueued:
                prompt = tts_prompt or self._tts_scheduler.select_prompt(full_response)
                self._emit_event(
                    CharacterStateEvent(
                        state="replied", emotion=response_emotion or prompt.sentiment
                    )
                )

        prediction = prediction_cls(response=full_response, history=query.history)
        object.__setattr__(prediction, "_streamed_tts", True)

        self._commit_history_and_memory(
            original_text, full_response, log_label=f"{label} 流式轮次"
        )
        self._emit_event(
            PipelineOutputLLMEvent(
                prediction=prediction, skip_tts=True, emotion=response_emotion
            )
        )
        logger.debug(f"已发射 {label} 流式输出的最终 LLM 事件。")
        self._vision_controller.reset_auto_chat_timer()
        logger.debug(f"{label} 流式输出处理完成。")
        return True, prediction
