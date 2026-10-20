"""Provider 工厂 + 熔断包装。

编排层（services/chat.py、services/copywriting.py）只依赖这里，
这样「换模型」「加熔断」「统计 token」不会散落各处。
"""

from __future__ import annotations

import time
from typing import Sequence

from app import metrics
from app.config import Settings
from app.providers.base import ChatMessage, ChatResult, LLMProvider
from app.providers.fake import FakeProvider
from app.providers.siliconflow import SiliconFlowProvider
from app.resilience import AIServiceError, CircuitBreaker, Reason, UpstreamError


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeProvider(
            embed_dim=settings.embed_dim,
            fail_mode=settings.fake_fail_mode,
            latency_ms=settings.fake_latency_ms,
        )
    return SiliconFlowProvider(settings)


class ResilientLLM:
    """在任意提供方外面套一层熔断 + 指标，提供方自己保持「一次调用」的简单语义。"""

    def __init__(self, provider: LLMProvider, breaker: CircuitBreaker) -> None:
        self.provider = provider
        self.breaker = breaker

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._run("embed", lambda: self.provider.embed(texts))

    async def chat(self, messages: Sequence[ChatMessage], *, max_tokens: int | None = None, temperature: float | None = None) -> ChatResult:
        return await self._run("chat", lambda: self.provider.chat(messages, max_tokens=max_tokens, temperature=temperature))

    async def _run(self, kind: str, call) -> object:
        await self.breaker.acquire()
        started = time.perf_counter()
        failed: AIServiceError | None = None
        try:
            result = await call()
        except AIServiceError as exc:
            failed = exc
            # 只有「上游故障」才计入熔断；本地降级判断（如无命中资料）不该让熔断打开
            if exc.reason in {Reason.LLM_TIMEOUT, Reason.LLM_UNAVAILABLE, Reason.BAD_UPSTREAM_RESPONSE}:
                await self.breaker.record_failure()
            else:
                await self.breaker.release_half_open()
            raise
        except Exception:
            await self.breaker.record_failure()
            raise
        else:
            await self.breaker.record_success()
            metrics.llm_requests_total.labels(provider=self.provider.name, kind=kind, outcome="success").inc()
            metrics.llm_duration_seconds.labels(kind=kind).observe(time.perf_counter() - started)
            if isinstance(result, ChatResult):
                metrics.llm_tokens_total.labels(provider=self.provider.name, kind="chat", direction="prompt").inc(result.prompt_tokens)
                metrics.llm_tokens_total.labels(provider=self.provider.name, kind="chat", direction="completion").inc(result.completion_tokens)
            return result
        finally:
            if failed is not None:
                metrics.llm_requests_total.labels(provider=self.provider.name, kind=kind, outcome="error").inc()
                metrics.llm_duration_seconds.labels(kind=kind).observe(time.perf_counter() - started)
                if failed.reason not in {Reason.CIRCUIT_OPEN}:
                    metrics.degradations_total.labels(reason=failed.reason.value).inc()

    async def aclose(self) -> None:
        await self.provider.aclose()


def make_llm(settings: Settings) -> ResilientLLM:
    provider = build_provider(settings)
    breaker = CircuitBreaker(
        name="llm",
        failure_threshold=settings.breaker_failure_threshold,
        reset_seconds=settings.breaker_reset_seconds,
        half_open_max_calls=settings.breaker_half_open_max_calls,
    )
    return ResilientLLM(provider, breaker)


__all__ = ["build_provider", "make_llm", "ResilientLLM", "ChatMessage", "UpstreamError"]
