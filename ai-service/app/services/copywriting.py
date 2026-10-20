"""后台 AI 文案生成：窗口 5 的轻量功能，这里只占一个路由位。

与 chat 不同：不需要 RAG、不需要幂等、不需要同会话串行；
但同样走熔断 + 背压 + trace，保证可观测性一致。
"""

from __future__ import annotations

import logging
import time

from app.config import Settings
from app.llm import ResilientLLM
from app.providers.base import ChatMessage
from app.resilience import AIServiceError, Backpressure, Reason
from app.schemas import CopywritingRequest
from app.trace import current_trace_id

log = logging.getLogger("ai.copywriting")

PROMPTS = {
    "product_title": "请为以下商品生成一个简洁吸引人的电商标题（20字以内）：\n{seed}",
    "product_desc": "请为以下商品写一段 100 字左右的商品详情描述，突出卖点：\n{seed}",
    "speechcraft": "请根据以下客服场景生成一条专业友好的话术模板：\n{seed}",
    "ad_copy": "请为以下推广素材写一条短文案（30字以内），适合朋友圈/社群转发：\n{seed}",
}


class CopywritingService:
    def __init__(self, settings: Settings, llm: ResilientLLM, backpressure: Backpressure) -> None:
        self.settings = settings
        self.llm = llm
        self.backpressure = backpressure

    async def generate(self, req: CopywritingRequest) -> dict:
        started = time.perf_counter()
        await self.backpressure.acquire(timeout=self.settings.sla_seconds * 0.5)
        try:
            prompt = PROMPTS.get(req.kind, PROMPTS["product_desc"]).format(seed=req.seed)
            messages = [
                ChatMessage(role="system", content=f"你是电商文案专家，语气{req.tone}，不要 markdown 格式。"),
                ChatMessage(role="user", content=prompt),
            ]
            result = await self.llm.chat(messages, max_tokens=req.max_tokens)
            elapsed_ms = (time.perf_counter() - started) * 1000
            log.info(
                "copywriting_completed",
                extra={
                    "trace_id": current_trace_id(),
                    "kind": req.kind,
                    "latency_ms": round(elapsed_ms, 1),
                    "tokens": result.prompt_tokens + result.completion_tokens,
                },
            )
            return {"text": result.text, "model": result.model, "tokens": result.prompt_tokens + result.completion_tokens}
        except AIServiceError as exc:
            log.warning("copywriting_degraded", extra={"reason": exc.reason.value, "message": str(exc)})
            return {"text": "", "degraded": True, "reason": exc.reason.value}
        finally:
            self.backpressure.release()
