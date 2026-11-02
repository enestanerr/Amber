"""``amber align <workflow> [--show] [--json] [--keep-unreferenced] [--include-failed]``."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from amber.align.models import Skeleton, StepSpec
from amber.align.skeleton import build_skeleton
from amber.cli._context import open_store, project
from amber.cli._output import JsonOpt, get_output, stderr
from amber.errors import AmberError
from amber.trace.models import Trace
from amber.util.json import write_json
from amber.util.paths import amber_home


def select_traces(traces: list[Trace], *, include_failed: bool, warnings: list[str]) -> list[Trace]:
    """Successful traces (and unknown-status imports, with a warning) unless overridden."""
    kept: list[Trace] = []
    for t in traces:
        if t.outcome.status == "failure" and not include_failed:
            warnings.append(f"skipping failed trace {t.id} (use --include-failed)")
            continue
        if t.outcome.status == "unknown":
            warnings.append(f"trace {t.id} has unknown status; pass --status when importing")
        kept.append(t)
    return kept


def align(
    ctx: typer.Context,
    workflow: Annotated[str, typer.Argument(help="Workflow name.")],
    show: Annotated[bool, typer.Option("--show", help="Print the step table.")] = False,
    keep_unreferenced: Annotated[
        bool, typer.Option("--keep-unreferenced", help="Do not prune unreferenced reads.")
    ] = False,
    include_failed: Annotated[
        bool, typer.Option("--include-failed", help="Also align failed traces.")
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Merge the workflow's traces into a skeleton (.amber/build/<workflow>/skeleton.json)."""
    out = get_output(ctx, json_)
    root, _ = project(ctx)
    store = open_store(ctx)
    warnings: list[str] = []
    traces = select_traces(
        store.load_all(workflow), include_failed=include_failed, warnings=warnings
    )
    if not traces:
        raise AmberError(
            f"no usable traces for workflow {workflow!r}",
            hint=f'Record some first: `amber record {workflow} --input k=v -- claude -p "..."`.',
        )
    skeleton = build_skeleton(traces, keep_unreferenced=keep_unreferenced)
    skeleton.warnings = warnings + skeleton.warnings
    path = amber_home(root) / "build" / workflow / "skeleton.json"
    write_json(path, skeleton.model_dump(mode="json"))
    if out.json_mode:
        out.json(skeleton)
        return
    out.print(summary_line(skeleton))
    if show:
        out.print(step_table(skeleton))
        out.print(footer(skeleton))
    for w in skeleton.warnings:
        stderr.print(f"[yellow]warning:[/] {w}")
    out.print(f"[dim]wrote {path}[/]")


def summary_line(s: Skeleton) -> str:
    kinds = {k: sum(1 for st in s.steps if st.kind == k) for k in ("judgment", "transform-needed")}
    return (
        f"Skeleton [bold]{s.workflow}[/]  ({len(s.traces)} traces · {len(s.steps)} steps · "
        f"{kinds['judgment']} judgment · {kinds['transform-needed']} transform-needed · "
        f"{len(s.pruned)} pruned)"
    )


def provenance_summary(step: StepSpec) -> str:
    bits: list[str] = []
    for path, prov in step.args.items():
        key = path[2:] if path.startswith("$.") else path
        bits.append(f"{key} ← {prov.describe()}")
    if step.predicate is not None:
        bits.insert(0, f"if {step.predicate.describe()}")
    return " · ".join(bits) if bits else "(no args)"


def step_table(s: Skeleton) -> Table:
    table = Table(show_edge=False, pad_edge=False, box=None)
    table.add_column("#", justify="right")
    table.add_column("tool")
    table.add_column("kind")
    table.add_column("provenance", overflow="fold")
    table.add_column("in", justify="right")
    for st in s.steps:
        kind = (
            st.kind
            + (" · conditional" if st.conditional else "")
            + (" · observe" if st.observe else "")
        )
        table.add_row(
            str(st.idx + 1),
            st.tool + (" [dim]# write[/]" if st.effect == "write" else ""),
            kind,
            provenance_summary(st),
            f"{len(st.present_in)}/{len(s.traces)}",
        )
    for st in s.pruned:
        table.add_row("-", f"[dim]{st.tool} (pruned)[/]", "", f"[dim]{st.pruned_reason}[/]", "")
    return table


def footer(s: Skeleton) -> str:
    inputs = ", ".join(
        f"{n} ({spec.type}{', inferred' if spec.source == 'inferred' else ''})"
        for n, spec in s.inputs.items()
    )
    volatile = sum(len(v) for v in s.volatile_paths.values())
    return f"Inputs: {inputs or '-'}   Volatile result paths: {volatile}   Edges: {len(s.edges)}"
