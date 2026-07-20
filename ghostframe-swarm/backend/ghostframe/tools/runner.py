"""Tool runner — the only way workers touch the world.

Authorizes against the worker's declared permissions (never the worker's
claims), executes, and emits tool.invoked / tool.completed events.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..events import Event, InMemoryEventBus
from ..models.router import CallContext
from ..workers.definition import WorkerDefinition
from .base import PermissionDenied, Tool, ToolContext, ToolResult

__all__ = ["PermissionDenied", "ToolRunner"]


class ToolRunner:
    def __init__(self, bus: InMemoryEventBus, workdir: str | Path) -> None:
        self._bus = bus
        self._workdir = Path(workdir)
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def describe_for(self, definition: WorkerDefinition) -> str:
        """Tool palette text for a worker's context pack — permitted tools only."""
        lines = [
            f"- {t.name}: {t.description}"
            for t in self._tools.values()
            if t.name in definition.permissions.tools
        ]
        return "\n".join(lines) or "(no tools permitted)"

    async def run(
        self,
        definition: WorkerDefinition,
        name: str,
        args: dict,
        ctx: CallContext,
    ) -> ToolResult:
        if name not in definition.permissions.tools:
            raise PermissionDenied(
                f"worker {definition.id!r} may not use tool {name!r} "
                f"(allowed: {definition.permissions.tools})"
            )
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(f"unknown tool: {name}")

        await self._emit(ctx, "tool.invoked", {"tool": name, "args": args})
        started = time.monotonic()
        try:
            result = await tool.run(
                ToolContext(
                    workdir=self._workdir,
                    fs_scope=definition.permissions.fs_scope,
                    task_id=ctx.task_id,
                    worker_id=ctx.worker_id,
                ),
                args,
            )
        except PermissionError as e:
            result = ToolResult.failure(f"permission: {e}")
        except Exception as e:  # tool bugs become results, not crashes
            result = ToolResult.failure(f"{type(e).__name__}: {e}")

        await self._emit(ctx, "tool.completed", {
            "tool": name,
            "ok": result.ok,
            "error": result.error,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "result_digest": str(result.data)[:500],
        })
        return result

    async def _emit(self, ctx: CallContext, kind: str, payload: dict) -> None:
        fields: dict = {
            "kind": kind,
            "project_id": ctx.project_id,
            "workflow_run_id": ctx.workflow_run_id,
            "task_id": ctx.task_id,
            "worker_id": ctx.worker_id,
            "causation_id": ctx.causation_id,
            "payload": payload,
        }
        if ctx.correlation_id:
            fields["correlation_id"] = ctx.correlation_id
        await self._bus.publish(Event(**fields))
