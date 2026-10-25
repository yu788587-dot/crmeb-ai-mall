"""请求/响应契约：所有对外字段都经过 pydantic 校验，避免「上游改一个字段名整条链路静默失效」。

窗口 3 PHP 侧按这里的 schema 构造请求；任何不匹配都会在 AI 服务入口直接 422 + 明确错误信息。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_msg_id: str = Field(..., min_length=1, max_length=128, description="幂等键，PHP 端生成")
    uid: int = Field(..., ge=0)
    kefu_uid: int = Field(0, ge=0)
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[dict[str, str]] = Field(default_factory=list, description="最近 N 轮对话，role=user|kefu")
    kb_enabled: bool = True
    deadline_ms: float | None = Field(None, ge=0, description="SLA 剩余毫秒，超时前主动降级")


class ChatSource(BaseModel):
    type: str
    type_label: str
    id: int
    title: str
    score: float


class ChatData(BaseModel):
    reply: str
    sources: list[ChatSource] = Field(default_factory=list)
    kb_used: bool
    model: str
    tokens: dict[str, int] = Field(default_factory=dict)
    confidence: float | None = None
    degraded: bool = False
    reason: str | None = None
    fallback_text: str | None = None


class Envelope(BaseModel):
    code: int = 0
    trace_id: str
    latency_ms: float
    data: dict[str, Any] | None = None
    error: str | None = None
    retryable: bool = False


class CopywritingRequest(BaseModel):
    kind: Literal["product_title", "product_sell_point", "product_desc", "product_seo_keywords", "speechcraft", "ad_copy"] = "product_desc"
    seed: str = Field(..., min_length=1, max_length=2000, description="原始素材/关键词")
    tone: str = Field("professional", description="语气：professional / friendly / promotional")
    max_tokens: int = Field(400, ge=50, le=2000)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unhealthy"]
    version: str
    provider: str
    breaker: dict[str, Any]
    kb: dict[str, Any]
    backpressure: dict[str, Any]
    config: dict[str, Any]
