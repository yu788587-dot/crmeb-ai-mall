"""可靠性原语：超时分层、重试策略、熔断、背压、同会话串行。

窗口 3 的七项可靠性标准里，除「trace_id 贯穿」和「健康检查」外的全部实现都在这里，
并且每项都有单测（test_resilience.py / test_breaker.py / test_backpressure.py）。
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from app import metrics


class Reason(str, enum.Enum):
    """降级原因枚举 —— 必须可枚举，否则后台监控页与 Prometheus label 无法聚合。"""

    LLM_TIMEOUT = "llm_timeout"
    LLM_UNAVAILABLE = "llm_unavailable"
    CIRCUIT_OPEN = "circuit_open"
    OVERLOADED = "overloaded"
    NO_SOURCES = "no_sources"
    LOW_CONFIDENCE = "low_confidence"
    BAD_UPSTREAM_RESPONSE = "bad_upstream_response"
    DISABLED = "disabled"


class AIServiceError(Exception):
    """业务侧统一异常：带 reason 与 retryable，路由层据此决定 code/HTTP 状态。"""

    def __init__(self, reason: Reason, message: str = "", *, retryable: bool = False, **ctx: Any) -> None:
        super().__init__(message or reason.value)
        self.reason = reason
        self.retryable = retryable
        self.context = ctx


class OverloadedError(AIServiceError):
    def __init__(self, message: str = "等待配额的队列已满") -> None:
        super().__init__(Reason.OVERLOADED, message, retryable=True)


class UpstreamError(AIServiceError):
    """上游（SiliconFlow）返回 5xx/4xx 或响应结构不合法。"""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(
            Reason.BAD_UPSTREAM_RESPONSE if not retryable else Reason.LLM_UNAVAILABLE,
            message,
            retryable=retryable,
            status=status,
        )


class UpstreamTimeoutError(AIServiceError):
    def __init__(self, message: str = "上游模型调用超时") -> None:
        super().__init__(Reason.LLM_TIMEOUT, message, retryable=False)


# ---------------------------------------------------------------- 重试策略

class RetryPolicy:
    """只对「连接失败」和「上游 5xx」重试一次；超时和 4xx 绝不重试。

    - 超时不重试：一次已经跑满 15s 的请求，再试一次必然撞穿 20s SLA，还会重复计费；
    - 4xx 不重试：参数/鉴权错误重试没有意义，只会掩盖真实故障；
    - 连接失败与 5xx 重试：这两类是瞬时故障，一次退避重试能显著降低降级率。
    """

    def __init__(self, max_attempts: int = 1, backoff_seconds: float = 0.2) -> None:
        self.max_attempts = max(0, max_attempts)
        self.backoff_seconds = backoff_seconds

    def should_retry(self, attempt: int, *, exc: BaseException | None = None, status: int | None = None) -> bool:
        if attempt >= self.max_attempts:
            return False
        if status is not None:
            return 500 <= status <= 599
        if exc is None:
            return False
        # httpx 的连接类异常（含 connect timeout）属于「没打到服务」，重试安全
        name = type(exc).__name__
        return name in {"ConnectError", "ConnectTimeout", "RemoteProtocolError"}

    async def before_retry(self, attempt: int) -> None:
        metrics.llm_retries_total.labels(reason="upstream_failure").inc()
        if self.backoff_seconds > 0:
            await asyncio.sleep(self.backoff_seconds * (2 ** attempt))


# ---------------------------------------------------------------- 熔断器

class BreakerState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """K 次连续失败 → 打开 X 秒 → 半开放行少量探测请求 → 成功即恢复。

    半开状态用 `half_open_in_flight` 限制并发探测数，避免恢复瞬间被同一批请求打死。
    """

    name: str = "llm"
    failure_threshold: int = 5
    reset_seconds: float = 30.0
    half_open_max_calls: int = 2

    state: BreakerState = field(default=BreakerState.CLOSED)
    consecutive_failures: int = field(default=0)
    opened_at: float = field(default=0.0)
    half_open_in_flight: int = field(default=0)
    trips: int = field(default=0)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "opened_seconds_ago": round(time.monotonic() - self.opened_at, 1) if self.opened_at else None,
            "trips": self.trips,
        }

    async def acquire(self) -> None:
        """调用前申请许可；熔断打开期间直接抛 AIServiceError(CIRCUIT_OPEN)。"""
        async with self._lock:
            now = time.monotonic()
            if self.state is BreakerState.OPEN:
                if now - self.opened_at < self.reset_seconds:
                    raise AIServiceError(Reason.CIRCUIT_OPEN, f"熔断器 {self.name} 处于 open，剩余 {self.reset_seconds - (now - self.opened_at):.1f}s", retryable=True)
                self.state = BreakerState.HALF_OPEN
                self.half_open_in_flight = 0
            if self.state is BreakerState.HALF_OPEN and self.half_open_in_flight >= self.half_open_max_calls:
                raise AIServiceError(Reason.CIRCUIT_OPEN, "熔断器半开探测配额已满", retryable=True)
            if self.state is BreakerState.HALF_OPEN:
                self.half_open_in_flight += 1
            self._publish_state()

    async def record_success(self) -> None:
        async with self._lock:
            self.consecutive_failures = 0
            if self.state is not BreakerState.CLOSED:
                self.state = BreakerState.CLOSED
                self.half_open_in_flight = 0
            self._publish_state()

    async def record_failure(self, *, count: bool = True) -> None:
        async with self._lock:
            if self.state is BreakerState.HALF_OPEN:
                self._trip(now=time.monotonic(), reason="half_open_probe_failed")
            elif count:
                self.consecutive_failures += 1
                if self.state is BreakerState.CLOSED and self.consecutive_failures >= self.failure_threshold:
                    self._trip(now=time.monotonic(), reason="failure_threshold")
            self._publish_state()

    async def release_half_open(self) -> None:
        """半开探测结束后释放占用，无论成功失败都要调用（放在 finally）。"""
        async with self._lock:
            if self.state is BreakerState.HALF_OPEN and self.half_open_in_flight > 0:
                self.half_open_in_flight -= 1

    def _trip(self, *, now: float, reason: str) -> None:
        self.state = BreakerState.OPEN
        self.opened_at = now
        self.trips += 1
        self.half_open_in_flight = 0
        metrics.circuit_breaker_trips_total.labels(breaker=self.name).inc()
        metrics.degradations_total.labels(reason=Reason.CIRCUIT_OPEN.value).inc()

    def _publish_state(self) -> None:
        value = {BreakerState.CLOSED: 0, BreakerState.HALF_OPEN: 1, BreakerState.OPEN: 2}[self.state]
        metrics.circuit_breaker_state.labels(breaker=self.name).set(value)


# ---------------------------------------------------------------- 背压

@dataclass
class Backpressure:
    """固定并发 + 有限等待队列。

    没有队列上限时，突发流量会把请求堆在协程里，最后一起超时（雪崩式劣化）；
    有上限则快速失败返回 503，让 PHP 立刻走本地兜底话术，用户体验反而更可控。
    """

    endpoint: str = "chat"
    max_inflight: int = 8
    max_queue: int = 32
    _sem: asyncio.Semaphore = field(init=False)
    _waiting: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._sem = asyncio.Semaphore(self.max_inflight)

    async def acquire(self, *, timeout: float) -> None:
        async with self._lock:
            if self._waiting >= self.max_queue:
                metrics.queue_rejected_total.labels(endpoint=self.endpoint).inc()
                metrics.degradations_total.labels(reason=Reason.OVERLOADED.value).inc()
                raise OverloadedError(f"等待队列已满（{self._waiting}/{self.max_queue}）")
            self._waiting += 1
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            async with self._lock:
                self._waiting -= 1
            metrics.queue_rejected_total.labels(endpoint=self.endpoint).inc()
            raise AIServiceError(Reason.OVERLOADED, f"排队超过 {timeout:.1f}s 未获得处理配额", retryable=True)
        async with self._lock:
            self._waiting -= 1
        metrics.http_inflight.labels(endpoint=self.endpoint).inc()

    def release(self) -> None:
        self._sem.release()
        metrics.http_inflight.labels(endpoint=self.endpoint).dec()

    def snapshot(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "max_inflight": self.max_inflight,
            "max_queue": self.max_queue,
            "available_permits": self._sem._value,  # noqa: SLF001: 只用于健康快照展示
            "waiting": self._waiting,
        }


class ConversationSerializer:
    """同一会话（同一 uid）的消息串行处理。

    并发跑两条同会话请求时，第二条会读到还没落库的上下文，容易出现「答非所问的重复回复」，
    也让幂等缓存来不及生效。这里按 uid 分配 asyncio.Lock，并做 LRU 回收防止无限增长。
    """

    def __init__(self, max_keys: int = 2048) -> None:
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._max_keys = max_keys

    def lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        self._locks.move_to_end(key)
        while len(self._locks) > self._max_keys:
            old_key, old_lock = self._locks.popitem(last=False)
            if old_lock.locked():  # 仍在使用就放回，避免破坏串行保证
                self._locks[old_key] = old_lock
                self._locks.move_to_end(old_key)
                break
        return lock

    def snapshot(self) -> dict[str, int]:
        return {"active_keys": len(self._locks)}
