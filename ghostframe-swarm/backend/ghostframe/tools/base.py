from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class PermissionDenied(Exception):
    pass


class ToolResult(BaseModel):
    ok: bool
    data: dict = Field(default_factory=dict)
    error: str | None = None

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(ok=False, error=error)


class ToolContext(BaseModel):
    """Capability handle passed to tools. Everything a tool touches goes
    through here so it can be scoped and audited."""

    model_config = {"arbitrary_types_allowed": True}

    workdir: Path
    fs_scope: list[str] = Field(default_factory=list)  # glob allowlist, empty = deny fs
    task_id: str | None = None
    worker_id: str | None = None

    def check_path(self, raw: str) -> Path:
        """Resolve a worker-supplied path and enforce the fs scope.

        Scope patterns are fnmatch-style against the workdir-relative posix
        path (note: `*` crosses `/`, so "src/*" covers the whole subtree).
        """
        p = (self.workdir / raw).resolve()
        root = self.workdir.resolve()
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            raise PermissionError(f"path escapes workdir: {raw}") from None
        if not any(fnmatch.fnmatch(rel, pattern) for pattern in self.fs_scope):
            raise PermissionError(f"path {rel!r} not allowed by fs_scope {self.fs_scope}")
        return p


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    side_effects: list[str]  # "reads_fs" | "writes_fs" | "shell" | "network"

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult: ...
