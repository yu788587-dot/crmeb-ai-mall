"""离线提供方：零网络、确定性，用于单测、故障注入和「没配 key 也能跑通链路」的演示。

- embed() 用特征哈希（feature hashing）把文本投影到固定维度：词面重叠越多、余弦相似度越高，
  所以离线状态下 RAG 检索仍然是**有意义**的，而不是随机噪声；
- chat() 从上下文里挑一条最相关的资料，拼成可读回答，token 数按字符长度估算；
- fail_mode / latency_ms 支持故障注入，窗口 4 的演练脚本直接复用。
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
from typing import Sequence

from app.providers.base import ChatMessage, ChatResult
from app.resilience import Reason, UpstreamError, UpstreamTimeoutError

_TOKEN_SPLIT = re.compile(r"[^\w]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _tokens(text: str) -> list[str]:
    """英文按词切，中文按字 + 相邻二字组切 —— 保证中文短问句也能有可用的词面特征。"""
    out: list[str] = []
    for part in _TOKEN_SPLIT.split(text.lower()):
        if not part:
            continue
        if _CJK.search(part):
            chars = [c for c in part if _CJK.match(c)]
            out.extend(chars)
            out.extend(a + b for a, b in zip(chars, chars[1:]))
        else:
            out.append(part)
    return out


def feature_hash_vector(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for tok in _tokens(text):
        h = hashlib.md5(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if h[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0:
        return vec
    return [v / norm for v in vec]


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        *,
        embed_dim: int = 1024,
        fail_mode: str = "none",
        latency_ms: float = 0.0,
        seed: int = 20261020,
    ) -> None:
        self.embed_dim = embed_dim
        self.fail_mode = fail_mode  # none | timeout | connect | 5xx | 4xx
        self.latency_ms = latency_ms
        self.calls: list[str] = []
        self._rng = random.Random(seed)

    async def _maybe_fail(self, kind: str) -> None:
        self.calls.append(kind)
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        mode = self.fail_mode
        if mode == "none":
            return
        if mode == "timeout":
            raise UpstreamTimeoutError(f"[fake] 注入超时（{kind}）")
        if mode == "connect":
            raise UpstreamError("[fake] 注入连接失败", status=None, retryable=True)
        if mode == "5xx":
            raise UpstreamError("[fake] 注入 503", status=503, retryable=True)
        if mode == "4xx":
            raise UpstreamError("[fake] 注入 401 未授权", status=401, retryable=False)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        await self._maybe_fail("embed")
        return [feature_hash_vector(t, self.embed_dim) for t in texts]

    async def chat(self, messages: Sequence[ChatMessage], *, max_tokens: int | None = None, temperature: float | None = None) -> ChatResult:
        await self._maybe_fail("chat")
        system = "\n".join(m.content for m in messages if m.role == "system")
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        picked = self._pick_evidence(system, user)
        if picked:
            text = f"根据商品与客服资料：{picked}"
        else:
            text = "（离线模型）我在资料库里没有找到与该问题相关的依据，建议联系人工客服确认。"
        prompt_chars = sum(len(m.content) for m in messages)
        return ChatResult(
            text=text,
            model=f"fake/{self.fail_mode}",
            prompt_tokens=max(1, prompt_chars // 2),
            completion_tokens=max(1, len(text) // 2),
            finish_reason="stop",
            raw={"temperature": temperature, "max_tokens": max_tokens},
        )

    @staticmethod
    def _pick_evidence(system: str, user: str) -> str:
        """从提示词的资料块里选词面重叠最高的一行，模拟「有依据的回答」。"""
        lines = [ln.strip() for ln in system.splitlines() if ln.strip().startswith("[")]
        if not lines:
            return ""
        scored = []
        for ln in lines:
            overlap = len(set(_tokens(ln)) & set(_tokens(user)))
            scored.append((overlap, ln))
        best, line = max(scored, key=lambda x: x[0])
        return line if best > 0 else ""

    async def aclose(self) -> None:
        return None
