"""FastAPI 应用入口：lifespan 管理资源、中间件注入 trace_id、统一异常处理。

启动命令（开发）：
    cd ai-service && .venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info

生产部署见 Dockerfile + docker-compose.yml（窗口 6）。
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app import metrics
from app.config import Settings, get_settings
from app.kb.mysql_source import ReadOnlyMySQLSource
from app.kb.service import KBSyncService
from app.llm import make_llm
from app.logging_setup import setup_logging
from app.rag.store import KnowledgeStore
from app.resilience import AIServiceError, Backpressure, ConversationSerializer, OverloadedError
from app.routers import router
from app.services.chat import ChatService
from app.services.copywriting import CopywritingService
from app.trace import TRACE_HEADER, reset_trace_id, sanitize_trace_id, set_trace_id

log = logging.getLogger("ai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    log.info("startup", extra={"config": settings.masked()})

    # 指标：版本信息
    metrics.build_info.labels(version=settings.app_version, provider=settings.llm_provider).set(1)

    llm = make_llm(settings)
    store = KnowledgeStore(path=settings.kb_dir, collection=settings.kb_collection, dim=settings.embed_dim)
    source = ReadOnlyMySQLSource(settings)
    kb_sync = KBSyncService(settings, source, store, llm)

    bp_chat = Backpressure(endpoint="chat", max_inflight=settings.max_inflight, max_queue=settings.max_queue)
    bp_copy = Backpressure(endpoint="copywriting", max_inflight=max(2, settings.max_inflight // 2), max_queue=settings.max_queue)
    serializer = ConversationSerializer()
    idempotency_cache: dict[str, tuple[float, Any]] = {}

    chat_service = ChatService(settings, llm, store, bp_chat, serializer, idempotency_cache)
    copy_service = CopywritingService(settings, llm, bp_copy)

    app.state.settings = settings
    app.state.llm = llm
    app.state.store = store
    app.state.source = source
    app.state.kb_sync_service = kb_sync
    app.state.chat_service = chat_service
    app.state.copywriting_service = copy_service
    app.state.backpressure_chat = bp_chat

    yield

    await llm.aclose()
    await source.close()
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    app.include_router(router)

    @app.middleware("http")
    async def trace_and_metrics_middleware(request: Request, call_next):
        raw = request.headers.get(TRACE_HEADER)
        tid = sanitize_trace_id(raw)
        if tid is None:
            from app.trace import new_trace_id
            tid = new_trace_id()
        token = set_trace_id(tid)
        try:
            response: Response = await call_next(request)
            response.headers[TRACE_HEADER] = tid
            return response
        finally:
            reset_trace_id(token)

    @app.exception_handler(AIServiceError)
    async def ai_error_handler(request: Request, exc: AIServiceError):
        status = 503 if isinstance(exc, OverloadedError) else 200
        body = {
            "code": 2 if isinstance(exc, OverloadedError) else 1,
            "trace_id": "",  # 中间件已设，这里留空由响应头补
            "latency_ms": 0.0,
            "data": None,
            "error": str(exc),
            "retryable": exc.retryable,
        }
        return JSONResponse(content=body, status_code=status)

    @app.get("/metrics")
    async def prometheus_metrics():
        return Response(content=metrics.render(), media_type=metrics.content_type())

    return app


app = create_app()
