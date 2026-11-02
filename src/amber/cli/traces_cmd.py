"""``amber traces ls|show|rm``."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from amber.cli._context import open_store, preview
from amber.cli._output import JsonOpt, get_output
from amber.trace.models import ToolCall, Trace

traces_app = typer.Typer(help="Inspect recorded traces.", no_args_is_help=True)


@traces_app.command("ls")
def ls(
    ctx: typer.Context,
    workflow: Annotated[str | None, typer.Argument(help="Filter by workflow.")] = None,
    json_: JsonOpt = False,
) -> None:
    """List traces (oldest first)."""
    out = get_output(ctx, json_)
    rows = open_store(ctx).list(workflow)
    if out.json_mode:
        out.json([r.model_dump(mode="json") for r in rows])
        return
    table = Table(title=f"Traces{f' · {workflow}' if workflow else ''} ({len(rows)})")
    for col in ("id", "workflow", "adapter", "created", "status", "calls", "in tok", "out tok"):
        table.add_column(col, justify="right" if "tok" in col or col == "calls" else "left")
    for r in rows:
        table.add_row(
            r.id,
            r.workflow,
            r.adapter,
            r.created_at.strftime("%Y-%m-%d %H:%M"),
            r.status,
            str(r.calls),
            f"{r.input_tokens:,}",
            f"{r.output_tokens:,}",
        )
    out.print(table)
    if not rows:
        out.print('No traces yet. Record one with `amber record <workflow> -- claude -p "..."`.')


@traces_app.command("show")
def show(
    ctx: typer.Context,
    trace_id: Annotated[str, typer.Argument(help="Trace id (see `amber traces ls`).")],
    json_: JsonOpt = False,
    full: Annotated[bool, typer.Option("--full", help="Dump the full trace JSON.")] = False,
) -> None:
    """Show a trace as a step table plus its outcome."""
    out = get_output(ctx, json_)
    trace = open_store(ctx).load(trace_id)
    if out.json_mode or full:
        out.json(trace)
        return
    out.print(_step_table(trace))
    o = trace.outcome
    out.print(
        f"outcome: {o.status} · calls {len(trace.calls)} · tokens in {o.usage.total_input:,}"
        f" (cached {o.usage.cache_read_tokens:,}) · out {o.usage.output_tokens:,}"
        f"{f' · ${o.cost_usd:.3f}' if o.cost_usd is not None else ''}"
        f"{f' · {o.duration_ms / 1000:.1f} s' if o.duration_ms is not None else ''}"
    )
    if o.final_text:
        out.print(f"[dim]final:[/] {preview(o.final_text, 100)}")


@traces_app.command("rm")
def rm(
    ctx: typer.Context,
    trace_id: Annotated[str, typer.Argument(help="Trace id to delete.")],
    json_: JsonOpt = False,
) -> None:
    """Delete a trace (file and index row)."""
    out = get_output(ctx, json_)
    deleted = open_store(ctx).delete(trace_id)
    if not deleted:
        raise typer.BadParameter(f"no trace {trace_id!r}")
    out.emit({"deleted": trace_id}, f"Deleted {trace_id}")


def _step_table(trace: Trace) -> Table:
    table = Table(title=f"{trace.id} · {trace.workflow} · {trace.adapter}")
    table.add_column("#", justify="right")
    table.add_column("kind")
    table.add_column("tool / text")
    table.add_column("args")
    table.add_column("result")
    table.add_column("ms", justify="right")
    for ev in trace.events:
        if isinstance(ev, ToolCall):
            tool = f"[red]{ev.tool}[/]" if ev.is_error else ev.tool
            table.add_row(
                str(ev.idx),
                "call",
                tool,
                preview(ev.args, 40),
                preview(ev.result, 40),
                "" if ev.duration_ms is None else str(ev.duration_ms),
            )
        else:
            table.add_row(str(ev.idx), ev.kind, preview(ev.text, 90), "", "", "")
    return table
