"""``amber schedule add|ls|rm`` and ``amber scheduler`` (foreground loop)."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from amber.cli._context import project
from amber.cli._inputs import parse_inputs
from amber.cli._output import JsonOpt, get_output
from amber.compile.versions import get_version
from amber.models.select import optional_backend
from amber.runtime.ledger import Ledger
from amber.runtime.runner import run_program
from amber.runtime.scheduler import Schedule, Schedules, run_scheduler
from amber.util.paths import amber_home

schedule_app = typer.Typer(
    help="Manage cron-style schedules for compiled programs.", no_args_is_help=True
)


def _schedules(ctx: typer.Context) -> Schedules:
    root, _ = project(ctx)
    return Schedules(Ledger(amber_home(root) / "amber.db"))


@schedule_app.command("add")
def add(
    ctx: typer.Context,
    workflow: Annotated[str, typer.Argument()],
    cron: Annotated[str, typer.Option("--cron", help='Cron expression, e.g. "0 9 * * 1".')],
    input_: Annotated[
        list[str] | None, typer.Option("--input", help="Input k=v (repeatable).")
    ] = None,
    json_: JsonOpt = False,
) -> None:
    """Add a schedule (runs with writes allowed, fallback none)."""
    out = get_output(ctx, json_)
    s = _schedules(ctx).add(workflow, cron, parse_inputs(input_ or []))
    out.emit(s.__dict__, f"Added schedule {s.id}: {s.workflow} @ {s.cron} (next {s.next_run})")


@schedule_app.command("ls")
def ls(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """List schedules."""
    out = get_output(ctx, json_)
    rows = _schedules(ctx).list()
    if out.json_mode:
        out.json([r.__dict__ for r in rows])
        return
    table = Table(title=f"Schedules ({len(rows)})")
    for col in ("id", "workflow", "cron", "inputs", "enabled", "last run", "next run"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r.id), r.workflow, r.cron, ", ".join(f"{k}={v}" for k, v in r.inputs.items()),
            "yes" if r.enabled else "no", r.last_run or "-", r.next_run or "-",
        )  # fmt: skip
    out.print(table)


@schedule_app.command("rm")
def rm(
    ctx: typer.Context, schedule_id: Annotated[int, typer.Argument()], json_: JsonOpt = False
) -> None:
    """Remove a schedule."""
    out = get_output(ctx, json_)
    if not _schedules(ctx).remove(schedule_id):
        raise typer.BadParameter(f"no schedule {schedule_id}")
    out.emit({"removed": schedule_id}, f"Removed schedule {schedule_id}")


def scheduler(
    ctx: typer.Context,
    once: Annotated[bool, typer.Option("--once", help="Run due schedules once and exit.")] = False,
    poll: Annotated[float, typer.Option("--poll", help="Poll interval in seconds.")] = 30.0,
) -> None:
    """Foreground loop: run every due schedule (logs to .amber/logs/scheduler.log)."""
    root, cfg = project(ctx)
    schedules = _schedules(ctx)
    backend = optional_backend(cfg.models)

    def run_one(s: Schedule) -> str:
        info = get_version(root, s.workflow, usable_only=True)
        result = run_program(root, cfg, info, s.inputs, allow_writes=True, llm_backend=backend)
        return f"{result.status} ({result.run_id})"

    ran = run_scheduler(
        schedules,
        run_one,
        log_path=amber_home(root) / "logs" / "scheduler.log",
        poll_seconds=poll,
        max_iterations=1 if once else None,
    )
    if once:
        typer.echo(f"ran {ran} schedule(s)")
