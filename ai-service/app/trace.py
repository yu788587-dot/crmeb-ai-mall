"""trace_id 契约：一次客服消息在 PHP → AI → LLM → 落库的全链路用同一个 ID 串起来。

规则（窗口 3 PHP 侧按此实现，双方都不能私自改）：
- PHP 每次调用带 `X-Trace-Id: <id>`；没带就由 AI 生成，并在响应头与响应体里回传，
  这样即使 HTTP 层失败（超时/连接拒绝）PHP 也能把自己生成的 id 对上日志。
- 所有结构化日志强制带 trace_id，字段名统一 `trace_id`。
- ID 只允许 [A-Za-z0-9_.-]，长度 8~64，防止日志注入（换行、ANSI）。
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

TRACE_HEADER = "X-Trace-Id"
_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="-")
_SAFE = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def sanitize_trace_id(raw: str | None) -> str | None:
    """非法输入一律丢弃（返回 None 让调用方重新生成），绝不把外部字符串原样写进日志。"""
    if raw is None:
        return None
    raw = raw.strip()
    return raw if _SAFE.match(raw) else None


def set_trace_id(value: str) -> Token:
    return _TRACE_ID.set(value)


def reset_trace_id(token: Token) -> None:
    _TRACE_ID.reset(token)


def current_trace_id() -> str:
    return _TRACE_ID.get()
