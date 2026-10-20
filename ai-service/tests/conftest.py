"""pytest 公共 fixtures：所有测试都用 fake provider + 临时 chroma，零网络、零磁盘残留。"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from typing import AsyncGenerator

import pytest

from app.config import Settings
from app.kb.mysql_source import ReadOnlyMySQLSource
from app.kb.service import KBSyncService
from app.llm import ResilientLLM, make_llm
from app.providers.fake import FakeProvider
from app.rag.store import KnowledgeStore
from app.resilience import Backpressure, CircuitBreaker, ConversationSerializer
from app.services.chat import ChatService


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def tmp_chroma_path(tmp_path):
    p = tmp_path / "chroma"
    p.mkdir()
    return str(p)


@pytest.fixture
def fake_settings(tmp_chroma_path) -> Settings:
    """测试专用配置：fake provider + 临时 chroma + 宽松超时。"""
    return Settings(
        llm_provider="fake",
        fake_fail_mode="none",
        fake_latency_ms=0,
        embed_dim=64,
        kb_dir=tmp_chroma_path,
        kb_collection="test_kb",
        kb_top_k=3,
        kb_min_score=-1.0,  # 测试里不过滤分数
        connect_timeout_seconds=5.0,
        request_timeout_seconds=10.0,
        sla_seconds=30.0,
        retry_max_attempts=0,
        breaker_failure_threshold=100,
        max_inflight=16,
        max_queue=64,
        idempotency_ttl_seconds=60,
        idempotency_max_entries=256,
        mysql_host="127.0.0.1",
        mysql_port=33061,
        mysql_user="crmeb",
        mysql_password="123456",
        mysql_database="crmeb",
    )


@pytest.fixture
def fake_llm(fake_settings) -> ResilientLLM:
    return make_llm(fake_settings)


@pytest.fixture
async def store(fake_settings) -> AsyncGenerator[KnowledgeStore, None]:
    s = KnowledgeStore(path=fake_settings.kb_dir, collection=fake_settings.kb_collection, dim=fake_settings.embed_dim)
    yield s


@pytest.fixture
def backpressure() -> Backpressure:
    return Backpressure(endpoint="chat", max_inflight=16, max_queue=64)


@pytest.fixture
def serializer() -> ConversationSerializer:
    return ConversationSerializer(max_keys=64)


@pytest.fixture
async def chat_service(fake_settings, fake_llm, store, backpressure, serializer) -> ChatService:
    return ChatService(
        settings=fake_settings,
        llm=fake_llm,
        store=store,
        backpressure=backpressure,
        serializer=serializer,
        idempotency_cache={},
    )
