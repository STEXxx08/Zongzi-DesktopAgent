"""
Author: Github@AkagawaTsurunaki

这些 API 是无记忆的。因此适合于单次使用。
These APIs are memory-free. Therefore, it is suitable for single use.
"""
from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING, Any, List

from loguru import logger
from condata.pipeline.ocr import RegionResult

from manager.config_manager import get_config
from common.decorator import log_run_time
from common.enumerator import Language
from pipeline.base.base_sync import PipelineDisabledException
from pipeline.ocr.ocr_sync import stringify

if TYPE_CHECKING:
    from langchain_core.messages import AIMessage
    from agent.adaptor import LangChainAdaptedLLM

_model: LangChainAdaptedLLM | None = None
_model_signature: tuple | None = None


def _get_model() -> LangChainAdaptedLLM:
    """惰性获取辅助 Agent 模型（兼容仅启用 LLM 或仅启用 MLLM 的配置）。"""
    global _model, _model_signature
    cfg = get_config()
    if getattr(cfg.pipeline.llm, "enable", False):
        target_cfg = cfg.pipeline.llm
        kind = "llm"
    elif getattr(cfg.pipeline.mllm, "enable", False):
        target_cfg = cfg.pipeline.mllm
        kind = "mllm"
    else:
        raise PipelineDisabledException(
            "At least one of pipeline.llm or pipeline.mllm must be enabled."
        )

    signature = (
        kind,
        getattr(target_cfg, "model_id", ""),
        getattr(target_cfg, "predict_url", ""),
        getattr(target_cfg, "api_key", None),
        getattr(target_cfg, "openai_format", False),
    )
    if _model is None or _model_signature != signature:
        from agent.adaptor import LangChainAdaptedLLM

        _model = LangChainAdaptedLLM(config=target_cfg)
        _model_signature = signature
    return _model


def _invoke_template(system_template: str, user_template: str, variables: dict[str, Any]) -> AIMessage:
    """统一构造并执行单次无记忆提示词模板调用。"""
    from langchain_core.prompts import ChatPromptTemplate

    prompt_template = ChatPromptTemplate.from_messages(
        [("system", system_template), ("user", user_template)]
    )
    result = prompt_template.invoke(variables)
    return _get_model().invoke(result)


@log_run_time()
def find_focus(region_results: List[RegionResult]) -> RegionResult:
    system_template = "你的任务：你需要在下列的OCR识别结果中找到最具有值得注意的信息，最后返回其标号 [i]。"
    response = _invoke_template(
        system_template,
        "{region_results}",
        {"region_results": stringify(region_results)},
    )

    numbers = re.findall(r'\d+', response.content)

    if len(numbers) == 0:
        idx = random.randint(0, len(numbers) - 1)
    else:
        idx = int(numbers[0])

    logger.info(f"Location attention: [{idx}]{region_results[idx].content}")

    return region_results[idx]


@log_run_time()
def answer_question(text: str, question: str) -> str:
    system_template = "你现在是一个问答助手，请仔细阅读文章内容，基于你阅读的内容，充分、正确地回答用户提出的问题。"
    response = _invoke_template(
        system_template,
        "{text} \n\n【你的任务】现在根据上述文段，回答问题：\n{question}",
        {"text": text, "question": question},
    )
    return response.content


@log_run_time()
def sentiment_analyse(sentiments: List[str], text: str) -> str:
    # If you only set 1 tts prompt, then there is no need to analyse sentiment
    if len(sentiments) == 1:
        return sentiments[0]

    system_template = "你的任务：你现在是一个情感分析助手，你将要对所给的文句进行情感分析，你必须从以下情感标签中挑选一个作为答案 {sentiments}。\n输出格式：必须仅返回情感标签内容，不要输出多余内容。"
    response = _invoke_template(
        system_template,
        "{text}",
        {"sentiments": sentiments, "text": text},
    )
    # Try to parse the results of the LLM analysis
    #   1. If the sentiment tag is first matched in response, it is returned
    #   2. If the sentiment tag is not found, try to return the first match in ["normal", "正常", "default", "默认"]
    #   3. If the default/normal sentiment tag is not found, try returning the first sentiment tag
    for sentiment in sentiments:
        if sentiment in response.content:
            return sentiment
    for sentiment in sentiments:
        for default in ["Default", "Normal", "默认", "正常"]:
            if default in response.content:
                return sentiment
    return sentiments[0]


@log_run_time()
def translate(text: str, src_lang: str | Language, tgt_lang: str | Language) -> str:
    if isinstance(src_lang, Language):
        src_lang = src_lang.name()
    if isinstance(tgt_lang, Language):
        tgt_lang = tgt_lang.name()

    system_template = "Translate the following from {src_lang} into {tgt_lang}"
    response = _invoke_template(
        system_template,
        "{text}",
        {"src_lang": src_lang, "tgt_lang": tgt_lang, "text": text},
    )
    return response.content


@log_run_time()
def summary(text: str, max_len: int = 100) -> AIMessage:
    """
    Summary the provided text within `max_len`.
    Args:
        text: The long text to summary.
        max_len: The maximum number of text of the summary.

    Returns:

    """
    system_template = "请仔细阅读文章内容，根据你认为最有价值和信息量的重要内容，总结这段文本为一段话。"
    return _invoke_template(
        system_template,
        "{text} \n任务：总结以上文本，不超过{max_len}字。",
        {"text": text, "max_len": max_len},
    )

