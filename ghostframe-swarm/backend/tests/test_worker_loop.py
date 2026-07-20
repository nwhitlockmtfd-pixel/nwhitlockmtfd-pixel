import json

from ghostframe.models.router import CallContext
from ghostframe.workers.actions import Escalate, SubmitWork
from ghostframe.workers.loop import TaskFrame


def frame(**kw):
    defaults = dict(task_id="t1", goal="write hello.txt containing 'hi'")
    defaults.update(kw)
    return TaskFrame(**defaults)


async def test_tool_then_submit(worker, scripted, tmp_path, bus, store):
    scripted.push(
        json.dumps({"action": "use_tool", "tool": "fs.write",
                    "args": {"path": "hello.txt", "content": "hi"},
                    "reasoning": "create the file", "confidence": 0.9}),
        json.dumps({"action": "submit", "summary": "wrote hello.txt",
                    "artifacts": {"files": ["hello.txt"]}, "confidence": 0.9}),
    )
    outcome = await worker.run_task(frame(), CallContext(task_id="t1"))
    assert isinstance(outcome.final, SubmitWork)
    assert (tmp_path / "hello.txt").read_text() == "hi"
    assert outcome.iterations == 2

    await bus.drain()
    delibs = store.query(kind="worker.deliberation")
    assert len(delibs) == 2
    # the exact prompt the model saw is in the event — no hidden prompts
    assert "hello.txt" in json.dumps(delibs[0].payload["context"])


async def test_tool_result_fed_back_into_context(worker, scripted, tmp_path):
    (tmp_path / "data.txt").write_text("SECRET_42")
    scripted.push(
        json.dumps({"action": "use_tool", "tool": "fs.read",
                    "args": {"path": "data.txt"}, "confidence": 0.9}),
        json.dumps({"action": "submit", "summary": "read it", "confidence": 0.9}),
    )
    await worker.run_task(frame(), CallContext())
    # second deliberation's request must contain the first tool's result
    second_request = scripted.requests[1]
    assert "SECRET_42" in second_request.messages[1].content


async def test_parse_errors_retry_then_escalate(worker, scripted):
    scripted.push("not json", "still not json", "nope")
    outcome = await worker.run_task(frame(), CallContext())
    assert isinstance(outcome.final, Escalate)
    assert "unparseable" in outcome.final.question


async def test_low_confidence_becomes_escalation(worker, scripted):
    scripted.push(json.dumps({"action": "use_tool", "tool": "fs.list",
                              "args": {}, "confidence": 0.1}))
    outcome = await worker.run_task(frame(), CallContext())
    assert isinstance(outcome.final, Escalate)
    assert "low confidence" in outcome.final.question


async def test_review_feedback_reaches_prompt(worker, scripted):
    scripted.push(json.dumps({"action": "submit", "summary": "done",
                              "confidence": 0.9}))
    await worker.run_task(
        frame(review_feedback=["fix the null check in parser.py"], attempt=2),
        CallContext(),
    )
    prompt = scripted.requests[0].messages[1].content
    assert "fix the null check in parser.py" in prompt
    assert "attempt 2" in prompt


async def test_max_iterations_escalates(worker, scripted):
    worker.definition.max_iterations = 3
    for _ in range(3):
        scripted.push(json.dumps({"action": "use_tool", "tool": "fs.list",
                                  "args": {}, "confidence": 0.9}))
    outcome = await worker.run_task(frame(), CallContext())
    assert isinstance(outcome.final, Escalate)
    assert "max_iterations" in outcome.final.question


async def test_submit_writes_task_memory(worker, scripted, memory):
    scripted.push(json.dumps({"action": "submit", "summary": "the outcome text",
                              "confidence": 0.9}))
    await worker.run_task(frame(task_id="t77"), CallContext())
    entries = memory.retrieve(layer="task", scope_id="t77")
    assert entries and entries[0].content == "the outcome text"
