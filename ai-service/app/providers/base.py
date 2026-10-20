"""模型提供方接口：把「调用谁」和「怎么容错」解耦。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence


@dataclass
class ChatMessage:
    role: str  # system / user / assistant
    content: str


@dataclass
class ChatResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = ""
    raw: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    """实现约定：所有方法都必须自行处理 HTTP 层异常并抛出 app.resilience 里的异常类型，
    并且**不能**在内部做无限重试；重试次数由 RetryPolicy 决定。
    """

    name: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...

    async def chat(self, messages: Sequence[ChatMessage], *, max_tokens: int | None = None, temperature: float | None = None) -> ChatResult:
        ...

    async def aclose(self) -> None:
        ...
