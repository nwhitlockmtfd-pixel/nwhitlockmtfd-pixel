"""`ghost` — the GhostFrame Swarm CLI (v0.1).

ghost init                       scaffold agents/ + workflows/ into a project
ghost run "goal" [-w wf] ...     run a workflow (interactive approvals by default)
ghost workers                    list worker definitions
ghost events [--kind] [--task]   inspect the event log
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import typer

from ..kernel.approvals import ApprovalRequest, auto_approve
from ..swarm import LocalSwarm

app = typer.Typer(no_args_is_help=True, add_completion=False)

_BUILTIN_ROOT = Path(__file__).resolve().parents[3]  # ghostframe-swarm/


async def _interactive_resolver(req: ApprovalRequest) -> tuple[str, str]:
    typer.secho("\n── HUMAN APPROVAL NEEDED " + "─" * 40, fg=typer.colors.YELLOW)
    typer.echo(req.question)
    for i, opt in enumerate(req.options, 1):
        typer.echo(f"  {i}. {opt}")
    answer = (await asyncio.to_thread(
        typer.prompt, "approve/deny [note after ':']", default="approve")).strip()
    decision, _, note = answer.partition(":")
    decision = decision.strip().lower()
    return ("approve" if decision.startswith("a") else "deny", note.strip())


@app.command()
def init(directory: str = typer.Argument(".", help="project directory")) -> None:
    """Scaffold a GhostFrame project: agents/, workflows/, ghostframe.yaml."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    for name in ("agents", "workflows"):
        src = _BUILTIN_ROOT / name
        dst = target / name
        if dst.exists():
            typer.echo(f"skip {dst} (exists)")
            continue
        shutil.copytree(src, dst)
        typer.echo(f"created {dst}")
    typer.secho("ready — try: ghost run \"say hello\" -w feature-dev-mini --offline",
                fg=typer.colors.GREEN)


@app.command()
def run(
    goal: str,
    workflow: str = typer.Option("feature-dev-mini", "--workflow", "-w"),
    project: str = typer.Option(".", "--project", "-p"),
    budget: float = typer.Option(10.0, "--budget", help="USD ceiling for the run"),
    yes: bool = typer.Option(False, "--yes", help="auto-approve all gates"),
    offline: bool = typer.Option(False, "--offline",
                                 help="scripted provider, no API keys needed"),
) -> None:
    """Run a workflow toward GOAL."""
    swarm = LocalSwarm(
        project,
        resolver=auto_approve if yes else _interactive_resolver,
        offline=offline,
    )

    async def go():
        try:
            result = await swarm.run(goal, workflow, budget_usd=budget)
        finally:
            await swarm.close()
        return result

    result = asyncio.run(go())
    color = {"completed": typer.colors.GREEN, "denied": typer.colors.YELLOW,
             "failed": typer.colors.RED}[result.status]
    typer.secho(f"\nrun {result.run_id}: {result.status.upper()}", fg=color, bold=True)
    for s in result.steps:
        typer.echo(f"  [{s.status:>6}] {s.step_id:<12} "
                   f"{'(' + s.worker + ') ' if s.worker else ''}{s.summary[:100]}")
    typer.echo(f"  spend: ${result.spent_usd:.4f} / {result.spent_tokens} tokens")


@app.command()
def workers(project: str = typer.Option(".", "--project", "-p")) -> None:
    """List worker definitions in the project."""
    from ..workers import load_worker_definitions

    agents_dir = Path(project) / "agents"
    if not agents_dir.is_dir():
        typer.secho(f"no agents/ in {project} — run `ghost init` first",
                    fg=typer.colors.RED)
        raise typer.Exit(1)
    for wid, d in load_worker_definitions(agents_dir).items():
        typer.echo(f"{wid:<20} tools={d.permissions.tools} "
                   f"budget=${d.budgets.per_task_usd} "
                   f"model={d.model_policy.preferred.provider}"
                   f"/{d.model_policy.preferred.model or 'default'}")


@app.command()
def events(
    project: str = typer.Option(".", "--project", "-p"),
    kind: str = typer.Option(None, "--kind", help="filter, e.g. 'model.*'"),
    task: str = typer.Option(None, "--task"),
    limit: int = typer.Option(50, "--limit"),
    full: bool = typer.Option(False, "--full", help="print full payloads"),
) -> None:
    """Inspect the event log (the audit trail)."""
    from ..events import SQLiteEventStore

    db = Path(project) / ".ghostframe" / "ghost.db"
    if not db.exists():
        typer.secho("no event log yet — run something first", fg=typer.colors.RED)
        raise typer.Exit(1)
    store = SQLiteEventStore(db)
    for e in store.query(kind=kind, task_id=task, limit=limit):
        line = (f"{e.occurred_at:%H:%M:%S} {e.kind:<22} "
                f"task={e.task_id or '-':<8.8} worker={e.worker_id or '-'}")
        typer.echo(line)
        if full:
            typer.echo(json.dumps(e.payload, indent=2, default=str)[:2000])


def main() -> None:  # console_scripts entry
    app()


if __name__ == "__main__":
    main()
