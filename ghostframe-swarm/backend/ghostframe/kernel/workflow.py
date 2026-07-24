"""v0.1 workflow engine.

Linear sequences of two step types — `agent` and `human_gate` — plus the one
edge that makes a swarm a team: review rejection bounces work back with
structured feedback, bounded by max_loops, escalating to a human when loops
exhaust. Fan-out/join arrive in v0.5 (roadmap).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from ..events import Event, InMemoryEventBus, new_id
from ..models.router import CallContext, TaskBudget
from ..workers.actions import AskClarification, Escalate, RejectWork, SubmitWork
from ..workers.loop import TaskFrame, Worker, WorkerOutcome
from .approvals import ApprovalEngine, ApprovalRequest


class StepDef(BaseModel):
    id: str
    type: Literal["agent", "human_gate"]
    worker: str | None = None            # agent steps
    goal: str = "{goal}"                 # template: {goal}, {previous}
    acceptance: list[str] = Field(default_factory=list)
    on_reject: str | None = None         # step id to bounce back to on reject verdict
    max_loops: int = 3
    prompt: str = ""                     # human_gate question template


class WorkflowDef(BaseModel):
    id: str
    version: int = 1
    steps: list[StepDef]


class StepRecord(BaseModel):
    step_id: str
    task_id: str
    worker: str | None
    status: str          # done | failed | denied
    summary: str = ""
    artifacts: dict = Field(default_factory=dict)
    attempts: int = 1


class WorkflowResult(BaseModel):
    run_id: str
    workflow: str
    status: str          # completed | failed | denied
    steps: list[StepRecord]
    spent_usd: float = 0.0
    spent_tokens: int = 0


def load_workflows(workflows_dir: str | Path) -> dict[str, WorkflowDef]:
    defs: dict[str, WorkflowDef] = {}
    for path in sorted(Path(workflows_dir).glob("*.yaml")):
        wf = WorkflowDef.model_validate(yaml.safe_load(path.read_text()))
        defs[wf.id] = wf
    return defs


class WorkflowEngine:
    def __init__(self, workers: dict[str, Worker], approvals: ApprovalEngine,
                 bus: InMemoryEventBus) -> None:
        self._workers = workers
        self._approvals = approvals
        self._bus = bus

    async def run(self, workflow: WorkflowDef, goal: str,
                  budget: TaskBudget | None = None,
                  project_id: str = "default") -> WorkflowResult:
        run_id = new_id()
        correlation_id = new_id()
        records: list[StepRecord] = []
        previous_summary = ""

        await self._emit(correlation_id, run_id, None, "workflow.started",
                         {"workflow": workflow.id, "goal": goal})

        index = 0
        loop_counts: dict[str, int] = {}
        review_feedback: dict[str, list[str]] = {}  # step_id -> feedback for its next run

        while index < len(workflow.steps):
            step = workflow.steps[index]

            if step.type == "human_gate":
                question = (step.prompt or f"Approve step {step.id}?").format(
                    goal=goal, previous=previous_summary)
                resolved = await self._approvals.request(
                    ApprovalRequest(question=question, options=["approve", "deny"]),
                    correlation_id=correlation_id)
                status = "done" if resolved.state == "approved" else "denied"
                records.append(StepRecord(step_id=step.id, task_id=new_id(),
                                          worker=None, status=status,
                                          summary=resolved.note))
                if status == "denied":
                    return self._result(run_id, workflow, "denied", records, budget)
                index += 1
                continue

            # agent step
            worker = self._workers[step.worker]  # config errors surface loudly
            task_id = new_id()
            attempt = loop_counts.get(step.id, 0) + 1
            frame = TaskFrame(
                task_id=task_id,
                goal=step.goal.format(goal=goal, previous=previous_summary),
                acceptance=step.acceptance,
                review_feedback=review_feedback.pop(step.id, []),
                attempt=attempt,
                extra_context=f"Previous step output:\n{previous_summary}"
                              if previous_summary else "",
            )
            ctx = CallContext(project_id=project_id, workflow_run_id=run_id,
                              task_id=task_id, worker_id=worker.definition.id,
                              correlation_id=correlation_id)
            await self._emit(correlation_id, run_id, task_id, "task.assigned",
                             {"step": step.id, "worker": worker.definition.id,
                              "goal": frame.goal, "attempt": attempt})

            outcome = await worker.run_task(frame, ctx, budget=budget)
            handled = await self._handle_outcome(
                step, outcome, records, task_id, loop_counts, review_feedback,
                correlation_id, run_id)

            if handled == "advance":
                previous_summary = records[-1].summary
                index += 1
            elif handled == "bounce":
                index = next(i for i, s in enumerate(workflow.steps)
                             if s.id == step.on_reject)
            elif handled == "retry":
                continue  # same step, feedback attached
            else:  # "fail"
                return self._result(run_id, workflow, "failed", records, budget)

        result = self._result(run_id, workflow, "completed", records, budget)
        await self._emit(correlation_id, run_id, None, "workflow.completed",
                         {"status": result.status, "spent_usd": result.spent_usd})
        return result

    # -- outcome handling --------------------------------------------------

    async def _handle_outcome(self, step: StepDef, outcome: WorkerOutcome,
                              records: list[StepRecord], task_id: str,
                              loop_counts: dict[str, int],
                              review_feedback: dict[str, list[str]],
                              correlation_id: str, run_id: str) -> str:
        final = outcome.final

        if isinstance(final, SubmitWork):
            verdict = str(final.artifacts.get("verdict", "")).lower()
            record = StepRecord(step_id=step.id, task_id=task_id, worker=step.worker,
                                status="done", summary=final.summary,
                                artifacts=final.artifacts,
                                attempts=loop_counts.get(step.id, 0) + 1)
            records.append(record)

            if verdict in ("reject", "request_changes") and step.on_reject:
                loops = loop_counts.get(step.on_reject, 0) + 1
                loop_counts[step.on_reject] = loops
                reasons = [str(r) for r in final.artifacts.get("reasons", [])] \
                    or [final.summary]
                if loops >= step.max_loops:
                    resolved = await self._approvals.request(ApprovalRequest(
                        task_id=task_id, worker_id=step.worker,
                        question=(f"Review loop exhausted ({loops}x) at step "
                                  f"{step.id}. Latest reasons: {reasons}"),
                        options=["approve: continue anyway", "deny: fail the run"],
                    ), correlation_id=correlation_id)
                    return "advance" if resolved.state == "approved" else "fail"
                review_feedback[step.on_reject] = reasons
                return "bounce"
            return "advance"

        if isinstance(final, (Escalate, AskClarification)):
            retries = loop_counts.get(f"esc:{step.id}", 0) + 1
            loop_counts[f"esc:{step.id}"] = retries
            question = final.question
            options = getattr(final, "options", []) or ["approve: proceed", "deny"]
            resolved = await self._approvals.request(ApprovalRequest(
                task_id=task_id, worker_id=step.worker,
                question=question, options=options),
                correlation_id=correlation_id)
            if resolved.state == "approved" and retries > 3:
                records.append(StepRecord(step_id=step.id, task_id=task_id,
                                          worker=step.worker, status="failed",
                                          summary=f"escalation loop ({retries}x): "
                                                  f"{question}"))
                return "fail"
            if resolved.state == "approved":
                # human guidance feeds the retry as review feedback
                review_feedback[step.id] = [f"Human guidance: {resolved.note}"] \
                    if resolved.note else []
                return "retry"
            records.append(StepRecord(step_id=step.id, task_id=task_id,
                                      worker=step.worker, status="denied",
                                      summary=question))
            return "fail"

        if isinstance(final, RejectWork):
            resolved = await self._approvals.request(ApprovalRequest(
                task_id=task_id, worker_id=step.worker,
                question=(f"Worker {step.worker} rejected task at step {step.id}: "
                          f"{final.reasons}"),
                options=["approve: retry with guidance", "deny: fail the run"]),
                correlation_id=correlation_id)
            if resolved.state == "approved":
                review_feedback[step.id] = [f"Human guidance: {resolved.note}"]
                return "retry"
            records.append(StepRecord(step_id=step.id, task_id=task_id,
                                      worker=step.worker, status="failed",
                                      summary=str(final.reasons)))
            return "fail"

        records.append(StepRecord(step_id=step.id, task_id=task_id, worker=step.worker,
                                  status="failed", summary="unhandled outcome"))
        return "fail"

    # -- helpers -----------------------------------------------------------

    def _result(self, run_id: str, workflow: WorkflowDef, status: str,
                records: list[StepRecord], budget: TaskBudget | None) -> WorkflowResult:
        return WorkflowResult(
            run_id=run_id, workflow=workflow.id, status=status, steps=records,
            spent_usd=budget.spent_usd if budget else 0.0,
            spent_tokens=budget.spent_tokens if budget else 0,
        )

    async def _emit(self, correlation_id: str, run_id: str, task_id: str | None,
                    kind: str, payload: dict) -> None:
        await self._bus.publish(Event(kind=kind, workflow_run_id=run_id,
                                      task_id=task_id, correlation_id=correlation_id,
                                      payload=payload))
