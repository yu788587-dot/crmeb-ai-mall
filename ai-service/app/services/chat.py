"""智能客服问答编排：把「检索 → 生成 → 降级」串成一条可观测、可熔断的流水线。

这条函数是窗口 3 PHP 侧调用的核心入口，所有可靠性原语（trace、breaker、backpressure、
idempotency、deadline）都在这里生效；任何一处失败都必须给出明确的 degraded reason，
让 PHP 能立刻用自己的话术兜底，而不是让用户看到空白或转圈。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app import metrics
from app.config import Settings
from app.llm import ResilientLLM
from app.providers.base import ChatMessage
from app.rag.store import KnowledgeStore
from app.resilience import (
    AIServiceError,
    Backpressure,
    ConversationSerializer,
    Reason,
)
from app.schemas import ChatData, ChatRequest, ChatSource
from app.trace import current_trace_id

log = logging.getLogger("ai.chat")


class ChatService:
    def __init__(
        self,
        settings: Settings,
        llm: ResilientLLM,
        store: KnowledgeStore,
        backpressure: Backpressure,
        serializer: ConversationSerializer,
        idempotency_cache: dict[str, tuple[float, ChatData]],
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store
        self.backpressure = backpressure
        self.serializer = serializer
        self.idempotency_cache = idempotency_cache

    async def handle(self, req: ChatRequest) -> ChatData:
        started = time.perf_counter()
        trace_id = current_trace_id()

        # 1. 幂等：相同 user_msg_id 在 TTL 内直接返回缓存结果
        cached = self._get_cached(req.user_msg_id)
        if cached is not None:
            metrics.idempotency_total.labels(result="hit").inc()
            log.info("chat_idempotent_hit", extra={"user_msg_id": req.user_msg_id})
            return cached
        metrics.idempotency_total.labels(result="miss").inc()

        # 2. 同会话串行：避免并发请求读到未落库的上下文
        conv_key = f"uid:{req.uid}"
        async with self.serializer.lock_for(conv_key):
            # 3. 背压：队列满直接降级，不让请求堆积超时
            await self.backpressure.acquire(timeout=max(0.5, self.settings.sla_seconds * 0.3))
            try:
                data = await self._run_pipeline(req, started)
            finally:
                self.backpressure.release()

        self._put_cached(req.user_msg_id, data)
        elapsed_ms = (time.perf_counter() - started) * 1000
        log.info(
            "chat_completed",
            extra={
                "trace_id": trace_id,
                "latency_ms": round(elapsed_ms, 1),
                "kb_used": data.kb_used,
                "degraded": data.degraded,
                "reason": data.reason,
                "sources": len(data.sources),
                "model": data.model,
                "tokens": data.tokens,
            },
        )
        return data

    async def _run_pipeline(self, req: ChatRequest, started: float) -> ChatData:
        """内部流水线：每一步都可能抛 AIServiceError，统一在这里捕获并转成 degraded 响应。"""
        try:
            # deadline 检查：剩余时间不够就跑完一次 LLM 调用，提前降级
            remaining_ms = self._remaining_deadline_ms(req, started)
            if remaining_ms is not None and remaining_ms < 2000:
                return self._degraded(Reason.LLM_TIMEOUT, "SLA 剩余不足 2s，主动降级")

            # RAG 检索
            sources: list[ChatSource] = []
            context_lines: list[str] = []
            kb_used = False
            if self.settings.kb_enabled and req.kb_enabled:
                hits = await self._retrieve(req.message)
                sources = [h.as_source() for h in hits]
                context_lines = [h.document.context_line for h in hits]
                kb_used = bool(hits)
                top_score = hits[0].score if hits else 0.0
                metrics.rag_top_score.observe(top_score)
                metrics.rag_queries_total.labels(result="hit" if hits else "miss").inc()
            else:
                metrics.rag_queries_total.labels(result="disabled").inc()

            # 构造提示词 + 调用 LLM
            messages = self._build_messages(req, context_lines)
            result = await self.llm.chat(messages)

            # 置信度判定：无资料命中且回复过短 → 视为低置信度
            confidence = self._estimate_confidence(result.text, context_lines)
            if not kb_used and len(result.text.strip()) < 20:
                return self._degraded(Reason.NO_SOURCES, "无资料命中且回复过短")
            if kb_used and confidence < 0.3:
                hint = "; ".join(s.title for s in sources[:2]) or "部分相关资料"
                fallback = self.settings.low_confidence_text.format(hint=hint)
                return ChatData(
                    reply=fallback,
                    sources=sources,
                    kb_used=True,
                    model=result.model,
                    tokens={"prompt": result.prompt_tokens, "completion": result.completion_tokens},
                    confidence=confidence,
                    degraded=True,
                    reason=Reason.LOW_CONFIDENCE.value,
                    fallback_text=fallback,
                )

            return ChatData(
                reply=result.text,
                sources=sources,
                kb_used=kb_used,
                model=result.model,
                tokens={"prompt": result.prompt_tokens, "completion": result.completion_tokens},
                confidence=confidence,
            )

        except AIServiceError as exc:
            return self._degraded(exc.reason, str(exc))

    async def _retrieve(self, query: str):
        vector = (await self.llm.embed([query]))[0]
        return await self.store.search(vector, top_k=self.settings.kb_top_k, min_score=self.settings.kb_min_score)

    @staticmethod
    def _build_messages(req: ChatRequest, context_lines: list[str]) -> list[ChatMessage]:
        system_parts = ["你是 CRMEB 商城的智能客服助手。回答必须基于下方提供的商品与客服资料，不要编造价格、库存、发货时间等事实。"]
        if context_lines:
            system_parts.append("\n【参考资料】")
            for i, line in enumerate(context_lines, 1):
                system_parts.append(f"{i}. {line}")
        system_parts.append("\n如果资料不足以回答，请诚实说明并建议联系人工客服。语气友好、简洁，避免 markdown 格式。")
        messages: list[ChatMessage] = [ChatMessage(role="system", content="\n".join(system_parts))]
        for turn in req.history[-6:]:  # 最多保留最近 6 轮，防止 token 爆炸
            role = "user" if turn.get("role") == "user" else "assistant"
            messages.append(ChatMessage(role=role, content=str(turn.get("text", ""))[:500]))
        messages.append(ChatMessage(role="user", content=req.message))
        return messages

    @staticmethod
    def _estimate_confidence(reply: str, context_lines: list[str]) -> float:
        """简易置信度：回复长度 + 资料覆盖度的加权启发式。真实场景应换成 LLM-as-judge 或 rerank。"""
        if not reply.strip():
            return 0.0
        length_score = min(1.0, len(reply.strip()) / 80)
        coverage_score = min(1.0, len(context_lines) / 3) if context_lines else 0.0
        return round(0.4 * length_score + 0.6 * coverage_score, 3)

    def _degraded(self, reason: Reason, detail: str) -> ChatData:
        metrics.degradations_total.labels(reason=reason.value).inc()
        log.warning("chat_degraded", extra={"reason": reason.value, "detail": detail})
        return ChatData(
            reply=self.settings.fallback_text,
            sources=[],
            kb_used=False,
            model=self.settings.chat_model,
            tokens={},
            degraded=True,
            reason=reason.value,
            fallback_text=self.settings.fallback_text,
        )

    def _remaining_deadline_ms(self, req: ChatRequest, started: float) -> float | None:
        if req.deadline_ms is None:
            return None
        elapsed_ms = (time.perf_counter() - started) * 1000
        return max(0.0, req.deadline_ms - elapsed_ms)

    def _get_cached(self, key: str) -> ChatData | None:
        entry = self.idempotency_cache.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.monotonic() - ts > self.settings.idempotency_ttl_seconds:
            del self.idempotency_cache[key]
            return None
        return data

    def _put_cached(self, key: str, data: ChatData) -> None:
        if len(self.idempotency_cache) >= self.settings.idempotency_max_entries:
            oldest = min(self.idempotency_cache, key=lambda k: self.idempotency_cache[k][0])
            del self.idempotency_cache[oldest]
        self.idempotency_cache[key] = (time.monotonic(), data)
