"""``amber verify <workflow> [--version N] [--trace ID]... [--live-llm] [--unordered] [--json]``."""

from __future__ import annotations

from typing import Annotated

import typer

from amber.cli._context import open_store, project
from amber.cli._output import JsonOpt, fail, get_output
from amber.compile.versions import get_version
from amber.errors import AmberError, VerifyFailed
from amber.models.backend import ModelBackend
from amber.models.select import resolve_backend
from amber.runtime.verify import mark_usable, verify_version


def verify(
    ctx: typer.Context,
    workflow: Annotated[str, typer.Argument(help="Workflow name.")],
    version: Annotated[int | None, typer.Option("--version", "-v", help="Program version.")] = None,
    trace: Annotated[
        list[str] | None, typer.Option("--trace", help="Replay only these trace ids (repeatable).")
    ] = None,
    live_llm: Annotated[
        bool, typer.Option("--live-llm", help="Also call the real judgment model and compare.")
    ] = False,
    unordered: Annotated[
        bool, typer.Option("--unordered", help="Match recorded calls in any order.")
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Replay the program against its source traces; exit 3 on any failure."""
    out = get_output(ctx, json_)
    root, cfg = project(ctx)
    info = get_version(root, workflow, version)
    manifest = info.manifest()
    if manifest is None:
        raise AmberError(f"{info.path} has no manifest.json", hint="Recompile the workflow.")
    store = open_store(ctx)
    ids = list(trace) if trace else list(manifest.source_traces)
    traces = [store.load(t) for t in ids]
    backend: tuple[ModelBackend, str] | None = None
    if live_llm:
        backend = (resolve_backend(cfg.models), cfg.models.judgment)
    report = verify_version(info, traces, strict_order=not unordered, live_llm=backend)
    if not trace and not unordered:
        mark_usable(info, report)
    if out.json_mode:
        out.json(report.to_json())
    else:
        out.print(report.table())
    if not report.passed:
        fail(
            VerifyFailed(
                f"{sum(1 for r in report.reports if r.status != 'pass')} of "
                f"{len(report.reports)} traces diverged",
                hint="See the diff above; `amber align --show` explains the step order.",
            )
        )
