"""``amber doctor <workflow> [--runs N] [--no-probe] [--json]`` — exit 5 unless healthy."""

from __future__ import annotations

from typing import Annotated

import typer

from amber.cli._context import open_store, project
from amber.cli._output import JsonOpt, fail, get_output
from amber.compile.versions import get_version
from amber.doctor.diagnosis import Diagnosis, diagnose
from amber.errors import DoctorFoundDrift
from amber.runtime.ledger import Ledger
from amber.util.paths import amber_home


def doctor(
    ctx: typer.Context,
    workflow: Annotated[str, typer.Argument(help="Workflow name.")],
    runs: Annotated[int, typer.Option("--runs", help="How many recent runs to inspect.")] = 10,
    version: Annotated[int | None, typer.Option("--version", "-v")] = None,
    no_probe: Annotated[
        bool, typer.Option("--no-probe", help="Skip connecting to the servers.")
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Check live schemas, recent runs and traces; print findings and a recommendation."""
    out = get_output(ctx, json_)
    root, cfg = project(ctx)
    info = get_version(root, workflow, version)
    diagnosis = diagnose(
        root, cfg, info,
        store=open_store(ctx), ledger=Ledger(amber_home(root) / "amber.db"),
        runs=runs, probe_servers=not no_probe,
    )  # fmt: skip
    if out.json_mode:
        out.json(diagnosis.to_json())
    else:
        out.print(render(diagnosis))
    if diagnosis.status != "healthy":
        fail(
            DoctorFoundDrift(
                f"{workflow} is {diagnosis.status}: {diagnosis.rationale}",
                hint=diagnosis.next_command() or None,
            )
        )


def render(d: Diagnosis) -> str:
    colour = {"healthy": "green", "degraded": "yellow", "broken": "red"}[d.status]
    lines = [f"Doctor [bold]{d.workflow}[/] v{d.version}  → [{colour}]{d.status}[/]"]
    for f in d.findings:
        mark = {"ok": "[green]✓[/]", "soft": "[yellow]~[/]", "hard": "[red]✗[/]"}[f.severity]
        lines.append(f"  {mark} {f.check:<14} {f.detail}")
    tail = f" — {d.next_command()}" if d.next_command() else ""
    lines.append(f"Recommendation: [bold]{d.recommendation}[/]{tail}")
    return "\n".join(lines)
