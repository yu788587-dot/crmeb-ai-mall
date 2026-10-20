"""背压与同会话串行测试。"""

import asyncio

import pytest

from app.resilience import AIServiceError, Backpressure, ConversationSerializer, OverloadedError, Reason


@pytest.mark.asyncio
async def test_backpressure_rejects_when_queue_full():
    bp = Backpressure(endpoint="t", max_inflight=1, max_queue=0)
    # max_queue=0 时第一个 acquire 也会因 _waiting(0) >= max_queue(0) 被拒，
    # 这恰好验证了「队列容量为 0 = 完全不接受排队」的语义。
    with pytest.raises((AIServiceError, OverloadedError)) as exc_info:
        await bp.acquire(timeout=0.5)
    assert exc_info.value.reason == Reason.OVERLOADED


@pytest.mark.asyncio
async def test_backpressure_allows_within_capacity():
    bp = Backpressure(endpoint="t_cap", max_inflight=2, max_queue=4)
    await bp.acquire(timeout=1.0)
    await bp.acquire(timeout=1.0)
    bp.release()
    bp.release()


@pytest.mark.asyncio
async def test_backpressure_releases_permit():
    bp = Backpressure(endpoint="t2", max_inflight=1, max_queue=4)
    await bp.acquire(timeout=1.0)
    bp.release()
    await bp.acquire(timeout=1.0)  # 应成功
    bp.release()


@pytest.mark.asyncio
async def test_conversation_serializer_serializes_same_key():
    s = ConversationSerializer(max_keys=8)
    order: list[int] = []

    async def worker(key: str, idx: int, delay: float):
        async with s.lock_for(key):
            order.append(idx)
            await asyncio.sleep(delay)

    await asyncio.gather(
        worker("uid:1", 0, 0.05),
        worker("uid:1", 1, 0.05),
        worker("uid:1", 2, 0.05),
    )
    assert order == [0, 1, 2]


@pytest.mark.asyncio
async def test_conversation_serializer_allows_different_keys():
    s = ConversationSerializer(max_keys=8)
    started: list[str] = []

    async def worker(key: str):
        async with s.lock_for(key):
            started.append(key)
            await asyncio.sleep(0.05)

    await asyncio.gather(worker("a"), worker("b"))
    assert set(started) == {"a", "b"}
