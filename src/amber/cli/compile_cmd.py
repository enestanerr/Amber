"""``amber compile <workflow> [--refresh] [--max-repair N] [--model ID] [--dry-run] [--json]``."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.table import Table

from amber.cli._context import open_store, project
from amber.cli._output import JsonOpt, get_output, stderr
from amber.cli.align_cmd import select_traces
from amber.compile.compiler import CompileResult, compile_workflow
from amber.errors import AmberError
from amber.models.backend import ModelBackend
from amber.models.select import resolve_backend


def compile_(
    ctx: typer.Context,
    workflow: Annotated[str, typer.Argument(help="Workflow name.")],
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-align with all traces and bump the version.")
    ] = False,
    max_repair: Annotated[
        int, typer.Option("--max-repair", help="Repair iterations per hole.")
    ] = 3,
    model: Annotated[
        str | None, typer.Option("--model", help="Synthesis model id (default from amber.toml).")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Write the program with unfilled holes.")
    ] = False,
    include_failed: Annotated[bool, typer.Option("--include-failed")] = False,
    keep_unreferenced: Annotated[bool, typer.Option("--keep-unreferenced")] = False,
    json_: JsonOpt = False,
) -> None:
    """Codegen + hole synthesis → programs/<workflow>/v<N>/ (verify marks it usable)."""
    out = get_output(ctx, json_)
    root, cfg = project(ctx)
    store = open_store(ctx)
    warnings: list[str] = []
    traces = select_traces(
        store.load_all(workflow), include_failed=include_failed, warnings=warnings
    )
    if not traces:
        raise AmberError(
            f"no usable traces for workflow {workflow!r}",
            hint="Record runs first with `amber record`.",
        )
    backend: ModelBackend | None = None if dry_run else resolve_backend(cfg.models)
    result = asyncio.run(
        compile_workflow(
            root,
            cfg,
            traces,
            backend=backend,
            model=model,
            refresh=refresh,
            max_repair=max_repair,
            dry_run=dry_run,
            keep_unreferenced=keep_unreferenced,
        )
    )
    result.warnings = warnings + result.warnings
    if out.json_mode:
        out.json(result.to_json())
        return
    out.print(render(result))
    for w in result.warnings:
        stderr.print(f"[yellow]warning:[/] {w}")


def render(result: CompileResult) -> Table:
    table = Table(title=f"compile {result.workflow} → v{result.version}  ({result.path})", box=None)
    table.add_column("hole")
    table.add_column("kind")
    table.add_column("step", justify="right")
    table.add_column("attempts", justify="right")
    table.add_column("result")
    for o in result.outcomes:
        status = "accepted" if o.accepted else "FAILED"
        if o.downgraded:
            status += " (downgraded from transform)"
        table.add_row(o.hole.id, o.hole.kind, str(o.hole.step_idx + 1), str(o.attempts), status)
    if not result.outcomes:
        label = (
            "holes left unfilled (--dry-run)" if result.dry_run else "no holes: fully deterministic"
        )
        table.add_row(label, "", "", "", "")
    table.caption = (
        f"holes filled {result.holes_filled}/{len(result.holes)} · usable: "
        f"{'yes' if result.usable else 'no'} — run `amber verify {result.workflow}`"
    )
    return table
