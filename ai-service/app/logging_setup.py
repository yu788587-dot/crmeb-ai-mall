"""结构化日志：一行一条 JSON，方便后续被 Loki/ELK 或 grep+python 直接聚合。

为什么不用 print/普通文本：可观测性这一栏要量化「降级触发次数、RAG 命中率、P95 延迟」，
JSON 字段（event/endpoint/result/latency_ms/reason）才能被机器统计，纯文本只能靠人眼。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from app.trace import current_trace_id

# 只允许这些顶级字段出现在日志里，其余通过 extra={} 传入
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonLogFormatter(logging.Formatter):
    def __init__(self, *, with_console_prefix: bool = False) -> None:
        super().__init__()
        self.with_console_prefix = with_console_prefix

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname.lower(),
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", None) or current_trace_id(),
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        if self.with_console_prefix:  # 本机调试时人眼友好
            return f"[{time.strftime('%H:%M:%S', time.localtime(record.created))}] {line}"
        return line


def setup_logging(level: str = "INFO", *, console_prefix: bool | None = None) -> None:
    fmt = JsonLogFormatter(with_console_prefix=console_prefix if console_prefix is not None else level == "DEBUG")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # uvicorn 默认自带一条纯文本 access log，会破坏「全链路 JSON」的假设，改为走我们的 handler
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(noisy)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """统一入口：event 是动词短语（如 chat_completed），fields 是可聚合指标。"""
    logger.info(event, extra=fields)
