"""Approval engine — human-in-the-loop as a scheduling primitive.

A parked task burns no tokens. Resolvers:
  - interactive (CLI prompts the human synchronously)
  - queued (dashboard/API resolves later)  [v0.1: CLI `ghost approvals`]
  - auto-approve (tests, examples, explicit `--yes` runs)
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from ..events import Event, InMemoryEventBus, new_id


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=new_id)
    task_id: str | None = None
    worker_id: str | None = None
    question: str
    options: list[str] = Field(default_factory=list)
    state: str = "pending"  # pending | approved | denied
    note: str = ""


Resolver = Callable[[ApprovalRequest], Awaitable[tuple[str, str]]]  # (decision, note)


class ApprovalEngine:
    def __init__(self, bus: InMemoryEventBus, resolver: Resolver | None = None) -> None:
        self._bus = bus
        self._resolver = resolver
        self._pending: dict[str, ApprovalRequest] = {}
        self._waiters: dict[str, asyncio.Event] = {}

    @property
    def pending(self) -> list[ApprovalRequest]:
        return [r for r in self._pending.values() if r.state == "pending"]

    async def request(self, req: ApprovalRequest, *, correlation_id: str | None = None
                      ) -> ApprovalRequest:
        """Park until a human decides. Returns the resolved request."""
        self._pending[req.id] = req
        self._waiters[req.id] = asyncio.Event()
        await self._emit("approval.requested", req, correlation_id)

        if self._resolver is not None:
            decision, note = await self._resolver(req)
            self.resolve(req.id, decision, note)

        await self._waiters[req.id].wait()
        resolved = self._pending[req.id]
        await self._emit(f"approval.{resolved.state}", resolved, correlation_id)
        return resolved

    def resolve(self, approval_id: str, decision: str, note: str = "") -> None:
        req = self._pending.get(approval_id)
        if req is None or req.state != "pending":
            raise KeyError(f"no pending approval {approval_id!r}")
        req.state = "approved" if decision == "approve" else "denied"
        req.note = note
        self._waiters[approval_id].set()

    async def _emit(self, kind: str, req: ApprovalRequest,
                    correlation_id: str | None) -> None:
        fields: dict = {
            "kind": kind,
            "task_id": req.task_id,
            "worker_id": req.worker_id,
            "payload": req.model_dump(),
        }
        if correlation_id:
            fields["correlation_id"] = correlation_id
        await self._bus.publish(Event(**fields))


async def auto_approve(req: ApprovalRequest) -> tuple[str, str]:
    return "approve", "auto-approved (policy)"
