import json
from pathlib import Path

from ghostframe.kernel import ApprovalEngine, StepDef, WorkflowDef, WorkflowEngine
from ghostframe.kernel.approvals import auto_approve
from ghostframe.swarm import LocalSwarm
from ghostframe.workers import Worker

from .conftest import make_definition

REPO_ROOT = Path(__file__).resolve().parents[2]  # ghostframe-swarm/


def submit(summary, **artifacts):
    return json.dumps({"action": "submit", "summary": summary,
                       "artifacts": artifacts, "confidence": 0.9})


def mini_workflow():
    return WorkflowDef(id="mini", steps=[
        StepDef(id="plan", type="agent", worker="planner",
                goal="Plan: {goal}"),
        StepDef(id="implement", type="agent", worker="engineer",
                goal="Implement: {goal}"),
        StepDef(id="review", type="agent", worker="reviewer",
                goal="Review: {goal}", on_reject="implement", max_loops=3),
        StepDef(id="gate", type="human_gate", prompt="Ship {goal}?"),
    ])


def build_engine(router, tool_runner, memory, bus, resolver=auto_approve):
    workers = {}
    for wid in ("planner", "engineer", "reviewer"):
        d = make_definition(id=wid)
        workers[wid] = Worker(d, router, tool_runner, memory, bus)
    approvals = ApprovalEngine(bus, resolver=resolver)
    return WorkflowEngine(workers, approvals, bus), approvals


async def test_full_run_with_review_bounce(router, scripted, tool_runner,
                                           memory, bus, store):
    engine, _ = build_engine(router, tool_runner, memory, bus)
    scripted.push(
        submit("plan: create hello.txt"),                          # plan
        submit("wrote hello.txt", files=["hello.txt"]),            # implement #1
        submit("missing newline", verdict="request_changes",       # review: reject
               reasons=["file must end with newline"]),
        submit("fixed newline", files=["hello.txt"]),              # implement #2
        submit("looks good", verdict="approve"),                   # review: approve
    )

    result = await engine.run(mini_workflow(), "create hello.txt")

    assert result.status == "completed"
    assert [(r.step_id, r.status) for r in result.steps] == [
        ("plan", "done"), ("implement", "done"), ("review", "done"),
        ("implement", "done"), ("review", "done"), ("gate", "done"),
    ]
    # the bounce carried the reviewer's structured reasons into attempt 2
    attempt2_prompt = scripted.requests[3].messages[1].content
    assert "file must end with newline" in attempt2_prompt
    assert "attempt 2" in attempt2_prompt

    await bus.drain()
    kinds = [e.kind for e in store.query(limit=500)]
    assert "workflow.started" in kinds and "workflow.completed" in kinds
    assert kinds.count("task.assigned") == 5
    assert "approval.requested" in kinds  # the ship gate


async def test_review_loop_exhaustion_parks_for_human(router, scripted,
                                                      tool_runner, memory, bus):
    decisions = []

    async def denying_resolver(req):
        decisions.append(req.question)
        return "deny", "not good enough"

    engine, _ = build_engine(router, tool_runner, memory, bus,
                             resolver=denying_resolver)
    scripted.push(submit("plan"))
    for _ in range(3):  # implement/review reject, three times
        scripted.push(submit("attempt"),
                      submit("still bad", verdict="request_changes",
                             reasons=["nope"]))

    result = await engine.run(mini_workflow(), "impossible thing")
    assert result.status == "failed"
    assert any("loop exhausted" in q for q in decisions)


async def test_gate_denial_stops_run(router, scripted, tool_runner, memory, bus):
    async def deny_gates(req):
        return ("deny", "not shipping") if "Ship" in req.question \
            else ("approve", "")

    engine, _ = build_engine(router, tool_runner, memory, bus,
                             resolver=deny_gates)
    scripted.push(submit("plan"), submit("impl"),
                  submit("ok", verdict="approve"))
    result = await engine.run(mini_workflow(), "thing")
    assert result.status == "denied"
    assert result.steps[-1].status == "denied"


async def test_localswarm_loads_real_project_definitions():
    swarm = LocalSwarm(REPO_ROOT, offline=True, db_path=":memory:")
    try:
        assert set(swarm.workers) == {"planner", "backend_engineer", "reviewer"}
        assert "feature-dev-mini" in swarm.workflows
        # offline mode reroutes every worker to the scripted provider
        assert all(w.definition.model_policy.preferred.provider == "scripted"
                   for w in swarm.workers.values())
        # prompts resolved from files, not left as file: refs
        assert "Planner" in swarm.workers["planner"].definition.system_prompt

        swarm.scripted.push(
            submit("plan"), submit("impl"), submit("ok", verdict="approve"))
        result = await swarm.run("say hello", "feature-dev-mini")
        assert result.status == "completed"
        assert result.spent_usd == 0.0  # scripted is free
    finally:
        await swarm.close()
