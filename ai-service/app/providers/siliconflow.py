"""SiliconFlow（OpenAI 兼容）真实提供方。

超时分层在 httpx.Timeout 里显式拆开：connect 用 1s（连不上就快速判定不可达，把主动权交回 PHP），
read 用 15s（真实生成上限），pool 用 2s（配额排队不该吃掉整条 SLA）。
"""

from __future__ import annotations

import time
from typing import Any, Sequence

import httpx

from app import metrics
from app.config import Settings
from app.providers.base import ChatMessage, ChatResult
from app.resilience import Reason, RetryPolicy, UpstreamError, UpstreamTimeoutError


class SiliconFlowProvider:
    name = "siliconflow"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.retry = RetryPolicy(settings.retry_max_attempts, settings.retry_backoff_seconds)
        headers = {}
        if settings.siliconflow_api_key:
            headers["Authorization"] = f"Bearer {settings.siliconflow_api_key}"
        self._client = client or httpx.AsyncClient(
            base_url=settings.siliconflow_base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(
                connect=settings.connect_timeout_seconds,
                read=settings.request_timeout_seconds,
                write=settings.request_timeout_seconds,
                pool=2.0,
            ),
            limits=httpx.Limits(max_connections=settings.max_inflight * 2, max_keepalive_connections=settings.max_inflight),
        )

    # ---------------- 公共入口 ----------------

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.settings.embed_model, "input": list(texts), "encoding_format": "float"}
        data = await self._post("/embeddings", payload, kind="embed")
        items = data.get("data") or []
        vectors = [item.get("embedding") for item in sorted(items, key=lambda d: d.get("index", 0))]
        if len(vectors) != len(texts) or any(v is None for v in vectors):
            raise UpstreamError(f"embeddings 返回 {len(vectors)} 条，期望 {len(texts)} 条")
        return vectors  # type: ignore[return-value]

    async def chat(self, messages: Sequence[ChatMessage], *, max_tokens: int | None = None, temperature: float | None = None) -> ChatResult:
        if not self.settings.siliconflow_api_key:
            raise UpstreamError("未配置 SILICONFLOW_API_KEY", retryable=False)
        payload = {
            "model": self.settings.chat_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens or self.settings.chat_max_tokens,
            "temperature": self.settings.chat_temperature if temperature is None else temperature,
            "stream": False,
        }
        data = await self._post("/chat/completions", payload, kind="chat")
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise UpstreamError(f"chat 响应结构异常：{type(exc).__name__}") from exc
        usage = data.get("usage") or {}
        return ChatResult(
            text=text.strip(),
            model=data.get("model", self.settings.chat_model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=(choice.get("finish_reason") or ""),
        )

    async def probe(self) -> dict[str, Any]:
        """给 /api/health 用：一次最小 embedding 调用，确认 key 与网络真的可用。"""
        vectors = await self.embed(["ping"])
        return {"ok": bool(vectors), "dim": len(vectors[0]) if vectors else 0, "model": self.settings.embed_model}

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------- 传输 + 重试 ----------------

    async def _post(self, path: str, payload: dict[str, Any], *, kind: str) -> dict[str, Any]:
        attempt = 0
        started = time.perf_counter()
        while True:
            err: Exception | None = None
            status: int | None = None
            try:
                resp = await self._client.post(path, json=payload)
                status = resp.status_code
                if status == 200:
                    body = resp.json()
                    self._observe(kind, "success", started)
                    return body
                err = None
            except httpx.TimeoutException as exc:
                # 超时：明确不重试，直接向上汇报为降级
                self._observe(kind, "timeout", started)
                metrics.degradations_total.labels(reason=Reason.LLM_TIMEOUT.value).inc()
                raise UpstreamTimeoutError(f"{kind} 超时：{exc.__class__.__name__}") from exc
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.PoolTimeout) as exc:
                err = exc

            retryable = err is not None or (status is not None and 500 <= status <= 599)
            if retryable and self.retry.should_retry(attempt, exc=err, status=status):
                await self.retry.before_retry(attempt)
                attempt += 1
                continue

            self._observe(kind, "error", started)
            metrics.degradations_total.labels(reason=Reason.LLM_UNAVAILABLE.value).inc()
            if err is not None:
                raise UpstreamError(f"{kind} 连接失败：{err.__class__.__name__}", status=status, retryable=True) from err
            raise UpstreamError(f"{kind} 上游返回 {status}", status=status, retryable=retryable)

    @staticmethod
    def _observe(kind: str, outcome: str, started: float) -> None:
        elapsed = time.perf_counter() - started
        metrics.llm_requests_total.labels(provider="siliconflow", kind=kind, outcome=outcome).inc()
        metrics.llm_duration_seconds.labels(kind=kind).observe(elapsed)
