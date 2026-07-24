"""The canonical event envelope.

Every state change in GhostFrame — task transitions, worker deliberations,
model calls, tool invocations, approvals — is an immutable Event. Current
state is always a projection over the event log.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

from pydantic import BaseModel, Field

_id_lock = threading.Lock()
_id_last = 0


def new_id() -> str:
    """Time-ordered unique id: (ms timestamp << 20 | sequence) hex + random hex.

    Monotonic within the process even for ids minted in the same millisecond —
    lexicographic order == creation order, which the store and all projections
    rely on.
    """
    global _id_last
    with _id_lock:
        candidate = int(time.time() * 1000) << 20
        _id_last = candidate if candidate > _id_last else _id_last + 1
        stamp = _id_last
    return f"{stamp:016x}{os.urandom(6).hex()}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CostDelta(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0


class Event(BaseModel):
    model_config = {"frozen": True}

    id: str = Field(default_factory=new_id)
    kind: str  # dotted taxonomy: "task.created", "worker.deliberation", "model.call", ...
    schema_version: int = 1
    occurred_at: datetime = Field(default_factory=utcnow)
    project_id: str = "default"
    workflow_run_id: str | None = None
    task_id: str | None = None
    worker_id: str | None = None
    causation_id: str | None = None  # event that directly caused this one
    correlation_id: str = Field(default_factory=new_id)  # threads the whole story
    payload: dict = Field(default_factory=dict)
    cost: CostDelta | None = None
