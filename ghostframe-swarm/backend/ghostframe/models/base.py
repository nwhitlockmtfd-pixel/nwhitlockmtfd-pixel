"""Model provider interface — the only shape the rest of the system knows."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str


class CompletionRequest(BaseModel):
    messages: list[Message]
    model: str | None = None  # provider default if None
    max_tokens: int = 4096
    temperature: float = 0.2
    force_json: bool = False  # ask the provider for a JSON object response

    def approx_tokens_in(self) -> int:
        # Pre-spend estimate for the budget gate; actuals reconcile after.
        return sum(len(m.content) for m in self.messages) // 4 + 8 * len(self.messages)


class Usage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0


class CompletionResponse(BaseModel):
    text: str
    model: str
    usage: Usage = Field(default_factory=Usage)


class ProviderError(Exception):
    """Raised by providers on API failure. transient=True means retryable."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...

    def cost_per_mtok(self, model: str) -> tuple[float, float]:
        """(usd per 1M input tokens, usd per 1M output tokens)."""
        ...
