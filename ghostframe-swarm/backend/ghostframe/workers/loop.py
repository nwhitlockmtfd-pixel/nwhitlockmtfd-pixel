"""The worker agent loop.

One loop for every role. Role definitions supply data (prompt, tools,
permissions, gates); no worker subclass ever overrides control flow, which is
what keeps every worker observable the same way:

    hydrate context -> deliberate (model) -> check (gates) -> act -> record
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError

from ..events import Event, InMemoryEventBus, new_id
from ..memory import SQLiteMemoryStore
from ..models.base import CompletionRequest, Message
from ..models.providers import parse_json_response
from ..models.router import BudgetExceeded, CallContext, ModelRouter, TaskBudget
from ..tools.base import PermissionDenied
from .actions import ACTION_SCHEMA_DOC, Action, Escalate, UseTool, parse_action
from .definition import WorkerDefinition

if TYPE_CHECKING:  # avoids the tools.runner <-> workers.loop import cycle
    from ..tools.runner import ToolRunner


class TaskFrame(BaseModel):
    """What the engine hands a worker: the work, not the machinery."""

    task_id: str
    goal: str
    acceptance: list[str] = Field(default_factory=list)
    review_feedback: list[str] = Field(default_factory=list)  # from prior rejections
    attempt: int = 1
    extra_context: str = ""


class WorkerOutcome(BaseModel):
    final: Action
    iterations: int
    transcript: list[dict]


class Worker:
    def __init__(
        self,
        definition: WorkerDefinition,
        router: ModelRouter,
        tools: ToolRunner,
        memory: SQLiteMemoryStore,
        bus: InMemoryEventBus,
    ) -> None:
        self.definition = definition
        self._router = router
        self._tools = tools
        self._memory = memory
        self._bus = bus

    async def run_task(self, frame: TaskFrame, ctx: CallContext,
                       budget: TaskBudget | None = None) -> WorkerOutcome:
        d = self.definition
        transcript: list[dict] = []
        parse_failures = 0

        for iteration in range(1, d.max_iterations + 1):
            messages = self._build_context(frame, transcript)
            try:
                response = await self._router.complete(
                    CompletionRequest(messages=messages, force_json=True),
                    d.model_policy, budget=budget, ctx=ctx,
                )
            except BudgetExceeded as e:
                return self._finish(frame, ctx, transcript, iteration, Escalate(
                    question=f"Budget exhausted on task {frame.task_id}: {e}",
                    options=["raise budget and resume", "cancel task"],
                    confidence=1.0,
                ))

            try:
                action = parse_action(parse_json_response(response.text))
                parse_failures = 0
            except (ValueError, ValidationError) as e:
                parse_failures += 1
                transcript.append({"type": "parse_error", "error": str(e)[:500]})
                if parse_failures >= 3:
                    return self._finish(frame, ctx, transcript, iteration, Escalate(
                        question=f"Worker {d.id} produced unparseable output 3x on "
                                 f"task {frame.task_id}",
                        options=["reassign", "cancel"], confidence=1.0,
                    ))
                continue

            action = self._apply_gates(action, frame)

            await self._bus.publish(Event(
                kind="worker.deliberation",
                project_id=ctx.project_id,
                workflow_run_id=ctx.workflow_run_id,
                task_id=frame.task_id,
                worker_id=d.id,
                correlation_id=ctx.correlation_id or new_id(),
                payload={
                    "iteration": iteration,
                    "context": [m.model_dump() for m in messages],
                    "raw_output": response.text,
                    "action": action.model_dump(),
                },
            ))

            if isinstance(action, UseTool):
                try:
                    result = await self._tools.run(d, action.tool, action.args, ctx)
                except PermissionDenied as e:
                    return self._finish(frame, ctx, transcript, iteration, Escalate(
                        question=f"Permission denied: {e}",
                        options=["grant permission", "reassign", "cancel"],
                        confidence=1.0,
                    ))
                transcript.append({
                    "type": "tool",
                    "tool": action.tool,
                    "args": action.args,
                    "reasoning": action.reasoning,
                    "result": result.model_dump(),
                })
                continue

            # terminal actions: submit / reject / ask / escalate
            return self._finish(frame, ctx, transcript, iteration, action)

        return self._finish(frame, ctx, transcript, d.max_iterations, Escalate(
            question=f"Worker {d.id} hit max_iterations ({d.max_iterations}) on "
                     f"task {frame.task_id} without finishing",
            options=["retry with guidance", "reassign", "cancel"],
            confidence=1.0,
        ))

    # -- internals ---------------------------------------------------------

    def _build_context(self, frame: TaskFrame, transcript: list[dict]) -> list[Message]:
        d = self.definition
        system = "\n\n".join([
            d.system_prompt.strip(),
            "## Available tools\n" + self._tools.describe_for(d),
            "## Response protocol\n" + ACTION_SCHEMA_DOC,
        ])

        sections = [f"## Task {frame.task_id} (attempt {frame.attempt})\nGoal: {frame.goal}"]
        if frame.acceptance:
            sections.append("## Acceptance criteria\n" +
                            "\n".join(f"- {c}" for c in frame.acceptance))
        if frame.review_feedback:
            sections.append("## Reviewer feedback on your previous attempt\n" +
                            "\n".join(f"- {r}" for r in frame.review_feedback))
        memory = self._memory.frame(task_id=frame.task_id)
        if memory:
            sections.append("## Memory\n" + memory)
        if frame.extra_context:
            sections.append("## Additional context\n" + frame.extra_context)
        if transcript:
            sections.append("## Your work so far this attempt\n" +
                            json.dumps(transcript[-15:], default=str)[:20_000])
        sections.append("Choose your next action (JSON only).")

        return [Message(role="system", content=system),
                Message(role="user", content="\n\n".join(sections))]

    def _apply_gates(self, action: Action, frame: TaskFrame) -> Action:
        gates = self.definition.confidence
        if action.action in ("ask", "escalate"):
            return action
        if action.confidence < gates.escalate_below:
            return Escalate(
                question=(f"Worker {self.definition.id} has low confidence "
                          f"({action.confidence:.2f}) in its next step on task "
                          f"{frame.task_id}."),
                options=[f"approve proposed action: {action.model_dump_json()[:300]}",
                         "provide guidance", "reassign"],
                confidence=action.confidence,
            )
        return action

    def _finish(self, frame: TaskFrame, ctx: CallContext, transcript: list[dict],
                iterations: int, action: Action) -> WorkerOutcome:
        if action.action == "submit":
            self._memory.write(
                layer="task", scope_id=frame.task_id, kind="outcome",
                content=action.summary, created_by=self.definition.id,
                project_id=ctx.project_id,
            )
        return WorkerOutcome(final=action, iterations=iterations, transcript=transcript)
