import json

import pytest

from ghostframe.models import CompletionRequest, ScriptedProvider, TaskBudget
from ghostframe.models.base import Message, ProviderError
from ghostframe.models.providers import parse_json_response
from ghostframe.models.router import BudgetExceeded, CallContext, ModelPolicy, ModelRef
from ghostframe.tools.runner import PermissionDenied

from .conftest import make_definition


def _req(text="hello"):
    return CompletionRequest(messages=[Message(role="user", content=text)],
                             max_tokens=100)


POLICY = ModelPolicy(preferred=ModelRef(provider="scripted"))


async def test_router_emits_model_call_with_full_prompt(router, scripted, bus, store):
    scripted.push("world")
    resp = await router.complete(_req("hello"), POLICY,
                                 ctx=CallContext(task_id="t1", correlation_id="c1"))
    assert resp.text == "world"
    await bus.drain()
    events = store.query(kind="model.call")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["messages"][0]["content"] == "hello"   # exact prompt recorded
    assert payload["response"] == "world"                 # exact output recorded


async def test_budget_enforced_before_spend(router, scripted):
    scripted.push("never used")
    budget = TaskBudget(max_usd=0.0)
    budget.spent_usd = 0.0
    # scripted provider costs $0 — use token budget instead
    budget = TaskBudget(max_tokens=10)  # request alone estimates > 10
    with pytest.raises(BudgetExceeded):
        await router.complete(_req("x" * 400), POLICY, budget=budget)
    assert scripted.requests == []  # never reached the provider


async def test_fallback_provider_used(bus, store):
    from ghostframe.models import ModelRouter

    class Failing:
        name = "failing"

        async def complete(self, req):
            raise ProviderError("down", transient=True)

        def cost_per_mtok(self, model):
            return (0.0, 0.0)

    router = ModelRouter(bus)
    router.register(Failing())
    good = ScriptedProvider(["rescued"])
    router.register(good)

    policy = ModelPolicy(preferred=ModelRef(provider="failing"),
                         fallback=ModelRef(provider="scripted"))
    resp = await router.complete(_req(), policy)
    assert resp.text == "rescued"
    await bus.drain()
    assert len(store.query(kind="model.call_failed")) == 1


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_response('Sure! {"a": 1} hope that helps') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_response("no json here")


async def test_tool_permission_denied(tool_runner):
    d = make_definition(permissions={"tools": ["fs.read"], "fs_scope": ["*"]})
    with pytest.raises(PermissionDenied):
        await tool_runner.run(d, "shell.run", {"command": "echo hi"}, CallContext())


async def test_fs_scope_blocks_escape_and_out_of_scope(tool_runner, tmp_path):
    d = make_definition(permissions={"tools": ["fs.write"], "fs_scope": ["src/*"]})
    ctx = CallContext()

    escaped = await tool_runner.run(d, "fs.write",
                                    {"path": "../evil.txt", "content": "x"}, ctx)
    assert not escaped.ok and "permission" in escaped.error

    out_of_scope = await tool_runner.run(d, "fs.write",
                                         {"path": "README.md", "content": "x"}, ctx)
    assert not out_of_scope.ok

    allowed = await tool_runner.run(d, "fs.write",
                                    {"path": "src/ok.txt", "content": "hi"}, ctx)
    assert allowed.ok
    assert (tmp_path / "src" / "ok.txt").read_text() == "hi"


async def test_shell_and_events(tool_runner, bus, store):
    d = make_definition()
    result = await tool_runner.run(d, "shell.run", {"command": "echo swarm"},
                                   CallContext(task_id="t9"))
    assert result.ok and "swarm" in result.data["stdout"]
    await bus.drain()
    kinds = [e.kind for e in store.query(task_id="t9")]
    assert kinds == ["tool.invoked", "tool.completed"]


async def test_scripted_provider_records_requests(scripted):
    scripted.push(json.dumps({"ok": True}))
    await scripted.complete(_req("ping"))
    assert scripted.requests[0].messages[0].content == "ping"
