"""WorkerDefinition — a worker is data, not a subclass.

Loaded from YAML files in the project's agents/ directory. Every field is an
enforcement point in the kernel, not documentation.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ..models.router import ModelPolicy, ModelRef


class ConfidenceGates(BaseModel):
    ask_below: float = 0.5
    escalate_below: float = 0.3


class Budgets(BaseModel):
    per_task_tokens: int | None = None
    per_task_usd: float | None = None


class Permissions(BaseModel):
    tools: list[str] = Field(default_factory=list)
    fs_scope: list[str] = Field(default_factory=list)  # glob patterns, cwd-relative


class WorkerDefinition(BaseModel):
    id: str
    role: str
    system_prompt: str  # resolved text (loader reads prompt files)
    model_policy: ModelPolicy = ModelPolicy(preferred=ModelRef(provider="anthropic"))
    permissions: Permissions = Field(default_factory=Permissions)
    budgets: Budgets = Field(default_factory=Budgets)
    confidence: ConfidenceGates = Field(default_factory=ConfidenceGates)
    max_iterations: int = 20  # loop-runaway backstop per task attempt


def load_worker_definitions(agents_dir: str | Path) -> dict[str, WorkerDefinition]:
    """Load every *.yaml worker in a directory; `system_prompt: file:...` paths
    are resolved relative to the directory."""
    agents_dir = Path(agents_dir)
    defs: dict[str, WorkerDefinition] = {}
    for path in sorted(agents_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        prompt = raw.get("system_prompt", "")
        if isinstance(prompt, str) and prompt.startswith("file:"):
            raw["system_prompt"] = (agents_dir / prompt[5:]).read_text()
        definition = WorkerDefinition.model_validate(raw)
        defs[definition.id] = definition
    return defs
