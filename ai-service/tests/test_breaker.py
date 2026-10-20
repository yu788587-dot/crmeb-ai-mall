"""熔断器单元测试：状态机转换、半开探测、指标上报。"""

import asyncio

import pytest

from app import metrics
from app.resilience import AIServiceError, CircuitBreaker, Reason


@pytest.mark.asyncio
async def test_breaker_stays_closed_under_threshold():
    b = CircuitBreaker(name="t1", failure_threshold=3)
    for _ in range(2):
        await b.acquire()
        await b.record_failure()
    assert b.state.value == "closed"


@pytest.mark.asyncio
async def test_breaker_opens_at_threshold():
    b = CircuitBreaker(name="t2", failure_threshold=2)
    for _ in range(2):
        await b.acquire()
        await b.record_failure()
    assert b.state.value == "open"
    with pytest.raises(AIServiceError) as exc_info:
        await b.acquire()
    assert exc_info.value.reason == Reason.CIRCUIT_OPEN


@pytest.mark.asyncio
async def test_breaker_half_open_allows_probe():
    b = CircuitBreaker(name="t3", failure_threshold=1, reset_seconds=0.01)
    await b.acquire()
    await b.record_failure()
    assert b.state.value == "open"

    await asyncio.sleep(0.02)
    await b.acquire()  # 应转为 half_open 并放行
    assert b.state.value == "half_open"
    await b.record_success()
    await b.release_half_open()
    assert b.state.value == "closed"


@pytest.mark.asyncio
async def test_breaker_half_open_rejects_excess_probes():
    b = CircuitBreaker(name="t4", failure_threshold=1, reset_seconds=0.01, half_open_max_calls=1)
    await b.acquire()
    await b.record_failure()
    await asyncio.sleep(0.02)
    await b.acquire()  # 第一个探测放行
    with pytest.raises(AIServiceError):
        await b.acquire()  # 第二个应被拒
    await b.record_success()
    await b.release_half_open()


@pytest.mark.asyncio
async def test_breaker_snapshot_contains_state():
    b = CircuitBreaker(name="snap")
    snap = b.snapshot()
    assert snap["name"] == "snap"
    assert snap["state"] == "closed"
    assert snap["trips"] == 0
