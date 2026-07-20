"""v0.1 built-in tools: fs.read, fs.write, fs.list, shell.run.

Shell runs in a subprocess with a timeout and output caps — that bounds a
confused agent, it is not a security boundary against hostile code (see
docs/design-review.md §5).
"""

from __future__ import annotations

import asyncio

from .base import Tool, ToolContext, ToolResult

_MAX_READ = 200_000
_MAX_OUTPUT = 50_000


class FsRead:
    name = "fs.read"
    description = "Read a text file. args: {path}"
    side_effects = ["reads_fs"]

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        p = ctx.check_path(str(args["path"]))
        if not p.is_file():
            return ToolResult.failure(f"not a file: {args['path']}")
        text = p.read_text(errors="replace")
        truncated = len(text) > _MAX_READ
        return ToolResult(ok=True, data={"content": text[:_MAX_READ], "truncated": truncated})


class FsWrite:
    name = "fs.write"
    description = "Write a text file (creates parent dirs). args: {path, content}"
    side_effects = ["writes_fs"]

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        p = ctx.check_path(str(args["path"]))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(args.get("content", "")))
        return ToolResult(ok=True, data={"path": str(args["path"]), "bytes": p.stat().st_size})


class FsList:
    name = "fs.list"
    description = "List files under a directory. args: {path (default '.')}"
    side_effects = ["reads_fs"]

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        root = ctx.workdir.resolve()
        base = (root / str(args.get("path", "."))).resolve()
        if not str(base).startswith(str(root)):
            return ToolResult.failure("path escapes workdir")
        if not base.is_dir():
            return ToolResult.failure(f"not a directory: {args.get('path', '.')}")
        entries = sorted(
            str(p.relative_to(root)) for p in base.rglob("*")
            if p.is_file() and ".git" not in p.parts
        )[:500]
        return ToolResult(ok=True, data={"files": entries})


class ShellRun:
    name = "shell.run"
    description = "Run a shell command in the workdir. args: {command, timeout_s (default 60)}"
    side_effects = ["shell", "reads_fs", "writes_fs"]

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        command = str(args["command"])
        timeout = min(float(args.get("timeout_s", 60)), 300.0)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=ctx.workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult.failure(f"timed out after {timeout}s: {command}")
        return ToolResult(
            ok=proc.returncode == 0,
            data={
                "returncode": proc.returncode,
                "stdout": out.decode(errors="replace")[-_MAX_OUTPUT:],
                "stderr": err.decode(errors="replace")[-_MAX_OUTPUT:],
            },
            error=None if proc.returncode == 0 else f"exit code {proc.returncode}",
        )


def builtin_tools() -> list[Tool]:
    return [FsRead(), FsWrite(), FsList(), ShellRun()]
