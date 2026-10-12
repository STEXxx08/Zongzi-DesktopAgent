"""共享调用观测：独立 JSONL 文件，不把诊断正文送入聊天日志或模型上下文。"""

import contextvars
import functools
import inspect
import json
import logging
import re
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4


_lock = threading.RLock()
_handler = None
_include_payloads = True
_max_payload_chars = 32000
_current_span = contextvars.ContextVar("harness_current_span", default=None)
_model_query = contextvars.ContextVar("harness_model_query", default=None)
_secret_keys = {"api_key", "apikey", "authorization", "password", "secret", "access_token", "token"}


class _PrivateFileHandler(RotatingFileHandler):
    def handleError(self, record):
        # 标准处理器会在 stderr 打印整个失败记录，这里只报告短消息。
        logging.getLogger(__name__).warning("Harness 日志写入失败，未输出诊断正文。")


def configure_harness(*, project_root: Path, enable=True, path="logs/harness.jsonl",
                      include_payloads=True, max_payload_chars=32000,
                      max_bytes=10485760, backup_count=5):
    """由应用初始化或配置刷新调用；相对路径始终以应用根目录为基准。"""
    global _handler, _include_payloads, _max_payload_chars
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Harness 日志路径必须为应用目录内的相对路径")
    with _lock:
        if _handler is not None:
            _handler.close()
            _handler = None
        _include_payloads = bool(include_payloads)
        _max_payload_chars = int(max_payload_chars)
        if enable:
            target = project_root.resolve() / relative
            if not target.resolve().is_relative_to(project_root.resolve()):
                raise ValueError("Harness 日志路径不能通过链接越出应用目录")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                _handler = _PrivateFileHandler(target, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
            except OSError:
                logging.getLogger(__name__).warning("Harness 日志文件无法打开，已停用诊断写入；业务调用继续。")


def _safe(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict):
        return {str(key): "[已隐藏]" if str(key).lower() in _secret_keys else _safe(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": len(value), "content": "[二进制内容未记录]"}
    if isinstance(value, str):
        value = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [已隐藏]", value)
        value = re.sub(r'(?i)(["\']?(?:api_key|password|secret|access_token|authorization)["\']?\s*[:=]\s*)["\']?[^\s,}\n"\']+["\']?', r'\1[已隐藏]', value)
        if len(value) > _max_payload_chars:
            return {"text": value[:_max_payload_chars], "truncated": True, "original_chars": len(value)}
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return _safe(str(value))


def record_event(kind, event, **fields):
    """诊断写入失败不得改变调用结果；日志不传播到控制台或 GUI。"""
    with _lock:
        if _handler is None:
            return
        try:
            if kind == "model":
                fields = {key: value for key, value in fields.items()
                          if key not in {"request", "response", "raw_response", "http_response"}}
                if isinstance(fields.get("error"), dict):
                    fields["error"] = {key: value for key, value in fields["error"].items()
                                       if key != "http_response"}
            payload = _safe(fields)
            if not _include_payloads:
                for key in ("request", "response", "raw_response", "http_response"):
                    payload.pop(key, None)
                if isinstance(payload.get("error"), dict):
                    payload["error"].pop("http_response", None)
            line = json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                               "kind": kind, "event": event, **payload}, ensure_ascii=False)
            _handler.emit(logging.LogRecord("harness", logging.INFO, "", 0, line, (), None))
        except Exception:
            logging.getLogger(__name__).warning("Harness 诊断日志写入失败，业务调用继续。")


def usage_dict(usage):
    """保留服务商真实用量；缺失用量返回 None，不进行字符数估算。"""
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return dict(usage)
    try:
        return dict(usage)
    except (TypeError, ValueError):
        return None


def normalize_usage(usage):
    raw = usage_dict(usage)
    if not raw:
        return {"status": "unknown", "input_tokens": None, "output_tokens": None, "total_tokens": None}
    input_tokens = raw.get("prompt_tokens", raw.get("input_tokens"))
    output_tokens = raw.get("completion_tokens", raw.get("output_tokens"))
    total_tokens = raw.get("total_tokens")
    if total_tokens is None and isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total_tokens = input_tokens + output_tokens
    return {"status": "reported", "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": total_tokens, "provider_usage": raw}


def _error_data(error):
    if not isinstance(error, BaseException):
        return {"message": str(error)}
    detail = {"type": type(error).__name__, "message": str(error),
              "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))}
    if isinstance(error, json.JSONDecodeError):
        detail.update(reason=error.msg, line=error.lineno, column=error.colno, position=error.pos)
    response = getattr(error, "response", None)
    if response is not None:
        detail.update(http_status=getattr(response, "status_code", None), http_response=getattr(response, "text", None))
    return detail


class CallSpan:
    def __init__(self, kind, name, **fields):
        self.kind, self.name = kind, name
        self.fields = {"call_id": f"trace_{uuid4().hex}", **fields}
        self.error, self.response, self.usage = None, None, None
        self.response_chars = 0

    def fail(self, error):
        self.error = _error_data(error)

    def observe_model(self, prediction):
        if prediction is None:
            self.fail("模型未返回预测结果")
            return
        text = getattr(prediction, "response", None)
        if self.fields.get("streaming") and isinstance(text, str):
            self.response_chars += len(text)
        usage = getattr(prediction, "usage", None)
        if usage is not None:
            # 流式用量通常是累计值，保留最新值，不逐块求和。
            self.usage = usage
        if isinstance(text, str) and text.startswith("Error:"):
            self.fail(text)

    def __enter__(self):
        self.started = time.perf_counter()
        self.token = _current_span.set(self)
        record_event(self.kind, "started", name=self.name, **self.fields)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.fail(exc)
        fields = {**self.fields, "name": self.name, "duration_ms": (time.perf_counter() - self.started) * 1000,
                  "status": "error" if self.error else "success", "error": self.error,
                  "response": self.response}
        if self.kind == "model":
            fields["usage"] = normalize_usage(self.usage)
            if self.fields.get("streaming"):
                fields["response_chars"] = self.response_chars
        record_event(self.kind, "finished", **fields)
        _current_span.reset(self.token)


def record_protocol_error(protocol, raw_response, error):
    """记录解析异常及原始回复，普通聊天日志仍只输出简短提示。"""
    span = _current_span.get()
    if span is not None:
        span.fail(error)
    record_event("protocol", "error", name=protocol, raw_response=raw_response,
                 error=_error_data(error), **(span.fields if span is not None else {}))


@contextmanager
def _model_span(owner, query, stream):
    # 子类和基类包装同一请求时只记录一次，避免重复统计 token。
    if _model_query.get() == id(query):
        yield None
        return
    token = _model_query.set(id(query))
    context = getattr(query, "_harness_context", {})
    try:
        with CallSpan("model", type(owner).__name__, model_id=str(getattr(owner, "model_id", getattr(owner, "_model_id", "unknown"))),
                      streaming=stream, **context) as span:
            object.__setattr__(query, "_harness_call_id", span.fields["call_id"])
            yield span
    finally:
        _model_query.reset(token)


def trace_model_call(function):
    """观测同步、异步及流式客户端；不改变预测内容和异常传播行为。"""
    stream = function.__name__ == "stream_predict"
    if inspect.isasyncgenfunction(function):
        @functools.wraps(function)
        async def async_stream(owner, query, *args, **kwargs):
            with _model_span(owner, query, True) as span:
                async for prediction in function(owner, query, *args, **kwargs):
                    if span:
                        span.observe_model(prediction)
                    yield prediction
        return async_stream
    if inspect.iscoroutinefunction(function):
        @functools.wraps(function)
        async def async_call(owner, query, *args, **kwargs):
            with _model_span(owner, query, stream) as span:
                result = await function(owner, query, *args, **kwargs)
                if span:
                    span.observe_model(result)
                return result
        return async_call
    if stream:
        @functools.wraps(function)
        def sync_stream(owner, query, *args, **kwargs):
            with _model_span(owner, query, True) as span:
                for prediction in function(owner, query, *args, **kwargs):
                    if span:
                        span.observe_model(prediction)
                    yield prediction
        return sync_stream
    @functools.wraps(function)
    def sync_call(owner, query, *args, **kwargs):
        with _model_span(owner, query, False) as span:
            result = function(owner, query, *args, **kwargs)
            if span:
                span.observe_model(result)
            return result
    return sync_call