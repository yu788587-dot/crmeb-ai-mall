"""智能客服问答核心测试：覆盖正常、降级、幂等、背压四条路径。"""

import pytest

from app.rag.documents import product_document
from app.resilience import Reason
from app.schemas import ChatRequest


@pytest.mark.asyncio
async def test_chat_with_kb_returns_sources(chat_service, store, fake_llm):
    # 先灌入一条商品知识
    doc = product_document({
        "id": 99, "store_name": "测试手机", "keyword": "手机",
        "store_info": "高性能旗舰", "price": 1999.0, "stock": 50,
        "unit_name": "台", "sales": 10,
    })
    vec = (await fake_llm.embed([doc.embed_text]))[0]
    await store.upsert([doc], [vec])

    req = ChatRequest(user_msg_id="kb-1", uid=2, message="测试手机多少钱")
    data = await chat_service.handle(req)

    assert not data.degraded
    assert data.kb_used is True
    assert len(data.sources) > 0
    assert any(s.title == "测试手机" for s in data.sources)
    assert data.reply  # 非空回复


@pytest.mark.asyncio
async def test_chat_without_kb_still_answers(chat_service):
    req = ChatRequest(user_msg_id="no-kb-1", uid=2, message="你好", kb_enabled=False)
    data = await chat_service.handle(req)
    assert data.reply
    assert data.kb_used is False


@pytest.mark.asyncio
async def test_chat_idempotency_returns_cached(chat_service):
    req = ChatRequest(user_msg_id="idem-1", uid=2, message="第一次")
    first = await chat_service.handle(req)
    second = await chat_service.handle(req)
    assert first.reply == second.reply
    assert first.model == second.model


@pytest.mark.asyncio
async def test_chat_degrades_on_llm_failure(monkeypatch, chat_service, fake_settings):
    from app.providers.fake import FakeProvider
    from app.llm import ResilientLLM, CircuitBreaker

    bad = FakeProvider(embed_dim=fake_settings.embed_dim, fail_mode="5xx")
    breaker = CircuitBreaker(name="test", failure_threshold=100)
    chat_service.llm = ResilientLLM(bad, breaker)

    req = ChatRequest(user_msg_id="fail-1", uid=2, message="会失败吗")
    data = await chat_service.handle(req)
    assert data.degraded is True
    assert data.reason in {Reason.LLM_UNAVAILABLE.value, Reason.BAD_UPSTREAM_RESPONSE.value}
    assert data.fallback_text  # 有兜底话术
