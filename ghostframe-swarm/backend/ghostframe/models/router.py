"""Model router — the sole model-call path.

Responsibilities: provider selection with fallback, pre-spend budget
enforcement, cost accounting, and emitting a `model.call` event for every
single completion (the "no hidden prompts" guarantee).
"""

from __future__ import annotations

from pydantic import BaseModel

from ..events import CostDelta, Event, InMemoryEventBus
from .base import CompletionRequest, CompletionResponse, ModelProvider, ProviderError


class ModelRef(BaseModel):
    provider: str
    model: str | None = None  # provider default if None


class ModelPolicy(BaseModel):
    preferred: ModelRef
    fallback: ModelRef | None = None


class TaskBudget(BaseModel):
    """Mutable per-task spend tracker. Checked BEFORE each call."""

    max_tokens: int | None = None
    max_usd: float | None = None
    spent_tokens: int = 0
    spent_usd: float = 0.0

    def would_exceed(self, est_tokens: int, est_usd: float) -> bool:
        if self.max_tokens is not None and self.spent_tokens + est_tokens > self.max_tokens:
            return True
        if self.max_usd is not None and self.spent_usd + est_usd > self.max_usd:
            return True
        return False

    def record(self, tokens: int, usd: float) -> None:
        self.spent_tokens += tokens
        self.spent_usd += usd


class BudgetExceeded(Exception):
    def __init__(self, budget: TaskBudget, est_usd: float) -> None:
        super().__init__(
            f"budget gate: spent ${budget.spent_usd:.4f} of "
            f"${budget.max_usd if budget.max_usd is not None else float('inf'):.2f}; "
            f"next call estimated ${est_usd:.4f}"
        )
        self.budget = budget


class CallContext(BaseModel):
    """Attribution for events emitted by the router."""

    project_id: str = "default"
    workflow_run_id: str | None = None
    task_id: str | None = None
    worker_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None


class ModelRouter:
    def __init__(self, bus: InMemoryEventBus) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._bus = bus

    def register(self, provider: ModelProvider) -> None:
        self._providers[provider.name] = provider

    def provider(self, name: str) -> ModelProvider:
        try:
            return self._providers[name]
        except KeyError:
            raise ProviderError(f"unknown provider {name!r}; registered: "
                                f"{sorted(self._providers)}") from None

    @staticmethod
    def estimate_usd(provider: ModelProvider, model: str | None,
                     req: CompletionRequest) -> float:
        cin, cout = provider.cost_per_mtok(model or "")
        return (req.approx_tokens_in() * cin + req.max_tokens * cout) / 1_000_000

    async def complete(
        self,
        req: CompletionRequest,
        policy: ModelPolicy,
        *,
        budget: TaskBudget | None = None,
        ctx: CallContext | None = None,
    ) -> CompletionResponse:
        ctx = ctx or CallContext()
        refs = [policy.preferred] + ([policy.fallback] if policy.fallback else [])
        last_err: Exception | None = None

        for ref in refs:
            provider = self.provider(ref.provider)
            request = req.model_copy(update={"model": ref.model})

            if budget is not None:
                est_usd = self.estimate_usd(provider, ref.model, request)
                est_tokens = request.approx_tokens_in() + request.max_tokens
                if budget.would_exceed(est_tokens, est_usd):
                    raise BudgetExceeded(budget, est_usd)

            try:
                response = await provider.complete(request)
            except ProviderError as e:
                last_err = e
                await self._emit(ctx, "model.call_failed", {
                    "provider": ref.provider, "model": ref.model,
                    "error": str(e), "transient": e.transient,
                    "will_fallback": ref is not refs[-1],
                })
                continue

            cin, cout = provider.cost_per_mtok(response.model)
            usd = (response.usage.tokens_in * cin + response.usage.tokens_out * cout) / 1_000_000
            if budget is not None:
                budget.record(response.usage.tokens_in + response.usage.tokens_out, usd)

            await self._emit(
                ctx, "model.call",
                {
                    "provider": provider.name,
                    "model": response.model,
                    # full prompt and output, always — this is the audit trail
                    "messages": [m.model_dump() for m in request.messages],
                    "response": response.text,
                },
                cost=CostDelta(tokens_in=response.usage.tokens_in,
                               tokens_out=response.usage.tokens_out, usd=usd),
            )
            return response

        raise last_err or ProviderError("no providers in policy")

    async def _emit(self, ctx: CallContext, kind: str, payload: dict,
                    cost: CostDelta | None = None) -> None:
        fields: dict = {
            "kind": kind,
            "project_id": ctx.project_id,
            "workflow_run_id": ctx.workflow_run_id,
            "task_id": ctx.task_id,
            "worker_id": ctx.worker_id,
            "causation_id": ctx.causation_id,
            "payload": payload,
            "cost": cost,
        }
        if ctx.correlation_id:
            fields["correlation_id"] = ctx.correlation_id
        await self._bus.publish(Event(**fields))
