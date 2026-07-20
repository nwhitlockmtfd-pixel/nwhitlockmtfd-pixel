"""LocalSwarm — in-process assembly of the whole kernel.

The zero-dependency dev mode: SQLite + in-memory bus. The CLI, tests, and
examples all wire the system through here; the FastAPI server (v0.1 tail end)
wraps the same object.
"""

from __future__ import annotations

from pathlib import Path

from .events import InMemoryEventBus, SQLiteEventStore
from .kernel import ApprovalEngine, WorkflowDef, WorkflowEngine, load_workflows
from .kernel.approvals import Resolver, auto_approve
from .memory import SQLiteMemoryStore
from .models import (
    AnthropicProvider,
    ModelRouter,
    OllamaProvider,
    OpenAIProvider,
    ScriptedProvider,
    TaskBudget,
)
from .tools import ToolRunner, builtin_tools
from .workers import Worker, load_worker_definitions


class LocalSwarm:
    def __init__(
        self,
        project_dir: str | Path = ".",
        *,
        resolver: Resolver | None = None,
        offline: bool = False,
        db_path: str | None = None,
        scripted: ScriptedProvider | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        ghost_dir = self.project_dir / ".ghostframe"
        if db_path is None:
            ghost_dir.mkdir(exist_ok=True)
            db_path = str(ghost_dir / "ghost.db")

        self.bus = InMemoryEventBus()
        self.events = SQLiteEventStore(db_path)
        self.memory = SQLiteMemoryStore(db_path if db_path == ":memory:"
                                        else db_path + ".mem")

        self.router = ModelRouter(self.bus)
        if offline or scripted is not None:
            # Sensible canned action so `ghost run --offline` demos the full
            # pipeline (submit with approve verdict) instead of parse-failing.
            offline_default = (
                '{"action": "submit", "summary": "[offline] scripted provider - '
                'no model was called", "artifacts": {"verdict": "approve"}, '
                '"confidence": 1.0}'
            )
            self.scripted = scripted or ScriptedProvider(default=offline_default)
            self.router.register(self.scripted)
        else:
            self.router.register(AnthropicProvider())
            self.router.register(OpenAIProvider())
            self.router.register(OllamaProvider())

        self.tools = ToolRunner(self.bus, self.project_dir)
        self.tools.register_all(builtin_tools())

        self.approvals = ApprovalEngine(self.bus, resolver=resolver or auto_approve)

        agents_dir = self.project_dir / "agents"
        definitions = load_worker_definitions(agents_dir) if agents_dir.is_dir() else {}
        if offline or scripted is not None:
            for d in definitions.values():  # route every worker at the scripted provider
                d.model_policy.preferred.provider = "scripted"
                d.model_policy.fallback = None
        self.workers = {
            wid: Worker(d, self.router, self.tools, self.memory, self.bus)
            for wid, d in definitions.items()
        }

        workflows_dir = self.project_dir / "workflows"
        self.workflows: dict[str, WorkflowDef] = (
            load_workflows(workflows_dir) if workflows_dir.is_dir() else {}
        )

        self.engine = WorkflowEngine(self.workers, self.approvals, self.bus)

        # persistence is just another subscriber
        self.bus.subscribe(["*"], self.events.handle, name="event-store")

    async def run(self, goal: str, workflow: str,
                  budget_usd: float | None = None,
                  budget_tokens: int | None = None):
        wf = self.workflows.get(workflow)
        if wf is None:
            raise KeyError(f"unknown workflow {workflow!r}; "
                           f"available: {sorted(self.workflows)}")
        budget = TaskBudget(max_usd=budget_usd, max_tokens=budget_tokens)
        result = await self.engine.run(wf, goal, budget=budget)
        await self.bus.drain()
        return result

    async def close(self) -> None:
        await self.bus.close()
        self.events.close()
        self.memory.close()
