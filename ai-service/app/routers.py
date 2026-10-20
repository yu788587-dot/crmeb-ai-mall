"""FastAPI 路由层：把 schema、service、trace、metrics 粘在一起。

所有响应都走统一 Envelope，PHP 侧只需判断 code + trace_id，不用猜字段名。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request, Response

from app import metrics
from app.config import get_settings
from app.resilience import AIServiceError, OverloadedError, Reason
from app.schemas import ChatRequest, CopywritingRequest, Envelope
from app.trace import current_trace_id

log = logging.getLogger("ai.api")

router = APIRouter(prefix="/api")


def _envelope(code: int, data: dict[str, Any] | None = None, error: str | None = None, retryable: bool = False) -> dict:
    return {
        "code": code,
        "trace_id": current_trace_id(),
        "latency_ms": 0.0,  # 由中间件回填
        "data": data,
        "error": error,
        "retryable": retryable,
    }


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    svc = request.app.state.chat_service
    started = time.perf_counter()
    try:
        data = await svc.handle(req)
        body = _envelope(0, data.model_dump())
    except OverloadedError as exc:
        body = _envelope(2, error=str(exc), retryable=True)
    except AIServiceError as exc:
        body = _envelope(1, error=str(exc), retryable=exc.retryable)
    body["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    status = 503 if body["code"] == 2 else 200
    metrics.http_requests_total.labels(endpoint="chat", code=str(status)).inc()
    metrics.http_duration_seconds.labels(endpoint="chat").observe(time.perf_counter() - started)
    return Response(content=None, status_code=status, media_type="application/json") if False else body


@router.post("/copywriting")
async def copywriting(req: CopywritingRequest, request: Request):
    svc = request.app.state.copywriting_service
    started = time.perf_counter()
    try:
        data = await svc.generate(req)
        body = _envelope(0, data)
    except AIServiceError as exc:
        body = _envelope(1, error=str(exc), retryable=exc.retryable)
    body["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    metrics.http_requests_total.labels(endpoint="copywriting", code="200").inc()
    metrics.http_duration_seconds.labels(endpoint="copywriting").observe(time.perf_counter() - started)
    return body


@router.post("/kb/sync")
async def kb_sync(request: Request):
    svc = request.app.state.kb_sync_service
    started = time.perf_counter()
    result = await svc.sync(reset=True)
    body = _envelope(0, result)
    body["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    metrics.http_requests_total.labels(endpoint="kb_sync", code="200").inc()
    metrics.http_duration_seconds.labels(endpoint="kb_sync").observe(time.perf_counter() - started)
    return body


@router.get("/kb/stats")
async def kb_stats(request: Request):
    store = request.app.state.store
    desc = await store.describe()
    return _envelope(0, desc)


@router.get("/health")
async def health(request: Request):
    settings = get_settings()
    llm = request.app.state.llm
    bp_chat = request.app.state.backpressure_chat
    store = request.app.state.store
    kb_count = await store.count()
    status = "ok"
    if llm.breaker.state.value != "closed":
        status = "degraded"
    if kb_count == 0 and settings.kb_enabled:
        status = "degraded"
    return {
        "status": status,
        "version": settings.app_version,
        "provider": settings.llm_provider,
        "breaker": llm.breaker.snapshot(),
        "kb": {"documents": kb_count, "enabled": settings.kb_enabled},
        "backpressure": bp_chat.snapshot(),
        "config": settings.masked(),
    }
