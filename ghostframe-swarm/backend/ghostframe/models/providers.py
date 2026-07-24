"""Built-in model providers.

Anthropic uses the official SDK; OpenAI and Ollama speak their HTTP APIs via
httpx. All are hidden behind the ModelProvider protocol — nothing outside
this package knows which vendor is in play.

ScriptedProvider exists so the entire kernel is testable (and demoable)
without API keys: it replays a queue of canned responses.
"""

from __future__ import annotations

import json
import os
from collections import deque

import httpx

from .base import CompletionRequest, CompletionResponse, Message, ProviderError, Usage

_JSON_INSTRUCTION = (
    "Respond with a single valid JSON object and nothing else - no prose, "
    "no markdown fences."
)


def _split_system(messages: list[Message]) -> tuple[str, list[dict]]:
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    rest = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
    return system, rest


class ScriptedProvider:
    """Deterministic provider for tests, examples, and `ghost dev --offline`."""

    name = "scripted"

    def __init__(self, responses: list[str] | None = None, default: str = "") -> None:
        self._queue: deque[str] = deque(responses or [])
        self._default = default
        self.requests: list[CompletionRequest] = []  # inspectable by tests

    def push(self, *responses: str) -> None:
        self._queue.extend(responses)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.requests.append(req)
        text = self._queue.popleft() if self._queue else self._default
        return CompletionResponse(
            text=text,
            model="scripted",
            usage=Usage(tokens_in=req.approx_tokens_in(), tokens_out=len(text) // 4),
        )

    def cost_per_mtok(self, model: str) -> tuple[float, float]:
        return (0.0, 0.0)


class AnthropicProvider:
    name = "anthropic"
    DEFAULT_MODEL = "claude-opus-4-8"
    # usd per 1M tokens (input, output)
    PRICES = {
        "claude-opus-4-8": (5.00, 25.00),
        "claude-sonnet-5": (3.00, 15.00),
        "claude-haiku-4-5": (1.00, 5.00),
        "claude-fable-5": (10.00, 50.00),
    }

    def __init__(self, api_key: str | None = None) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        import anthropic

        system, messages = _split_system(req.messages)
        if req.force_json:
            system = f"{system}\n\n{_JSON_INSTRUCTION}" if system else _JSON_INSTRUCTION
        model = req.model or self.DEFAULT_MODEL
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=req.max_tokens,
                system=system or anthropic.NOT_GIVEN,
                messages=messages,
            )
        except anthropic.RateLimitError as e:
            raise ProviderError(str(e), transient=True) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(str(e), transient=e.status_code >= 500) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(str(e), transient=True) from e

        text = "".join(b.text for b in response.content if b.type == "text")
        return CompletionResponse(
            text=text,
            model=response.model,
            usage=Usage(
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
            ),
        )

    def cost_per_mtok(self, model: str) -> tuple[float, float]:
        return self.PRICES.get(model, self.PRICES[self.DEFAULT_MODEL])


class OpenAIProvider:
    """OpenAI (or any /v1/chat/completions-compatible endpoint)."""

    name = "openai"
    DEFAULT_MODEL = "gpt-4o"
    PRICES = {"gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.15, 0.60)}

    def __init__(self, api_key: str | None = None, base_url: str = "https://api.openai.com/v1"):
        self._key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client = httpx.AsyncClient(base_url=base_url, timeout=120.0)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        model = req.model or self.DEFAULT_MODEL
        body: dict = {
            "model": model,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        }
        if req.force_json:
            body["response_format"] = {"type": "json_object"}
        try:
            r = await self._client.post(
                "/chat/completions", json=body,
                headers={"Authorization": f"Bearer {self._key}"},
            )
        except httpx.HTTPError as e:
            raise ProviderError(str(e), transient=True) from e
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderError(f"openai {r.status_code}: {r.text[:200]}", transient=True)
        if r.status_code >= 400:
            raise ProviderError(f"openai {r.status_code}: {r.text[:200]}")
        data = r.json()
        usage = data.get("usage", {})
        return CompletionResponse(
            text=data["choices"][0]["message"]["content"] or "",
            model=data.get("model", model),
            usage=Usage(
                tokens_in=usage.get("prompt_tokens", 0),
                tokens_out=usage.get("completion_tokens", 0),
            ),
        )

    def cost_per_mtok(self, model: str) -> tuple[float, float]:
        return self.PRICES.get(model, self.PRICES[self.DEFAULT_MODEL])


class OllamaProvider:
    """Local models via Ollama's /api/chat."""

    name = "ollama"
    DEFAULT_MODEL = "llama3.1"

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=300.0)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        model = req.model or self.DEFAULT_MODEL
        body: dict = {
            "model": model,
            "stream": False,
            "options": {"temperature": req.temperature, "num_predict": req.max_tokens},
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        }
        if req.force_json:
            body["format"] = "json"
        try:
            r = await self._client.post("/api/chat", json=body)
        except httpx.HTTPError as e:
            raise ProviderError(str(e), transient=True) from e
        if r.status_code >= 400:
            raise ProviderError(f"ollama {r.status_code}: {r.text[:200]}",
                                transient=r.status_code >= 500)
        data = r.json()
        return CompletionResponse(
            text=data.get("message", {}).get("content", ""),
            model=model,
            usage=Usage(
                tokens_in=data.get("prompt_eval_count", 0),
                tokens_out=data.get("eval_count", 0),
            ),
        )

    def cost_per_mtok(self, model: str) -> tuple[float, float]:
        return (0.0, 0.0)  # local compute


def parse_json_response(text: str) -> dict:
    """Tolerant JSON extraction: models sometimes wrap JSON in fences or prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object found in model output: {text[:120]!r}")
    return json.loads(text[start : end + 1])
