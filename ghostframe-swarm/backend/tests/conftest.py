import pytest

from ghostframe.events import InMemoryEventBus, SQLiteEventStore
from ghostframe.memory import SQLiteMemoryStore
from ghostframe.models import ModelRouter, ScriptedProvider
from ghostframe.models.router import ModelPolicy, ModelRef
from ghostframe.tools import ToolRunner, builtin_tools
from ghostframe.workers import Worker
from ghostframe.workers.definition import Budgets, Permissions, WorkerDefinition


@pytest.fixture
def bus():
    return InMemoryEventBus()


@pytest.fixture
def store(bus):
    s = SQLiteEventStore(":memory:")
    bus.subscribe(["*"], s.handle, name="store")
    return s


@pytest.fixture
def scripted():
    return ScriptedProvider()


@pytest.fixture
def router(bus, scripted):
    r = ModelRouter(bus)
    r.register(scripted)
    return r


@pytest.fixture
def memory():
    return SQLiteMemoryStore(":memory:")


@pytest.fixture
def tool_runner(bus, tmp_path):
    tr = ToolRunner(bus, tmp_path)
    tr.register_all(builtin_tools())
    return tr


def make_definition(**overrides) -> WorkerDefinition:
    base = dict(
        id="test_worker",
        role="test",
        system_prompt="You are a test worker.",
        model_policy=ModelPolicy(preferred=ModelRef(provider="scripted")),
        permissions=Permissions(tools=["fs.read", "fs.write", "fs.list", "shell.run"],
                                fs_scope=["*"]),
        budgets=Budgets(),
    )
    base.update(overrides)
    return WorkerDefinition.model_validate(base)


@pytest.fixture
def worker(router, tool_runner, memory, bus):
    return Worker(make_definition(), router, tool_runner, memory, bus)
