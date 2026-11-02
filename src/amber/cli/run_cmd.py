"""``amber run <workflow> [--input k=v]... [--version N] [--dry-run] [--allow-writes]
[--fallback agent|none] [--timeout S] [--force] [--json]`` — exit 4 when a guard stopped the run."""

from __future__ import annotations

from typing import Annotated

import typer

from amber.cli._context import project
from amber.cli._inputs import parse_inputs
from amber.cli._output import JsonOpt, fail, get_output, stderr
from amber.compile.versions import get_version
from amber.errors import GuardStopped, UsageError
from amber.models.select import resolve_backend
from amber.runtime.runner import RunResult, StepLog, run_program


def run(
    ctx: typer.Context,
    workflow: Annotated[str, typer.Argument(help="Workflow name.")],
    input_: Annotated[
        list[str] | None, typer.Option("--input", help="Input k=v (repeatable).")
    ] = None,
    version: Annotated[
        int | None,
        typer.Option("--version", "-v", help="Program version (default: latest usable)."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Never execute write-effect tools (the default).")
    ] = False,
    allow_writes: Annotated[
        bool, typer.Option("--allow-writes", help="Execute write-effect tools for real.")
    ] = False,
    fallback: Annotated[
        str, typer.Option("--fallback", help="agent | none (what to do on a guard stop).")
    ] = "none",
    timeout: Annotated[
        float | None, typer.Option("--timeout", help="Wall-clock budget in seconds.")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Run a version that did not pass verify.")
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Run the compiled program live against the configured MCP servers."""
    out = get_output(ctx, json_)
    if dry_run and allow_writes:
        raise UsageError("--dry-run and --allow-writes are mutually exclusive")
    if fallback not in ("agent", "none"):
        raise UsageError(f"bad --fallback {fallback!r}", hint="Use agent or none.")
    root, cfg = project(ctx)
    info = get_version(root, workflow, version, usable_only=version is None and not force)
    backend = resolve_backend(cfg.models)

    def progress(step: StepLog) -> None:
        if not out.json_mode:
            detail = f"  [dim]{step.detail}[/]" if step.detail else ""
            stderr.print(
                f"  [dim]#{step.idx + 1:<3}[/] {step.tool:<28} {step.status:<8} "
                f"{step.duration_ms:>6} ms{detail}",
                highlight=False,
            )

    result = run_program(
        root, cfg, info, parse_inputs(input_ or []),
        allow_writes=allow_writes,
        llm_backend=backend,
        timeout_s=timeout,
        force=force,
        on_step=progress,
    )  # fmt: skip
    if fallback == "agent" and result.status == "stopped":
        from amber.fallback.pipeline import run_fallback

        result = run_fallback(
            root,
            cfg,
            info,
            result,
            inputs=parse_inputs(input_ or []),
            allow_writes=allow_writes,
            out=out,
        )
    if out.json_mode:
        out.json(result.to_json())
    else:
        out.print(summary(result))
    if result.status == "stopped":
        fail(
            GuardStopped(
                f"run {result.run_id} stopped: {result.stop_kind} — {result.stop_detail}",
                hint=f"`amber doctor {workflow}` explains what drifted; "
                "`--fallback agent` hands the task back to the agent.",
            )
        )
    if result.status == "failed":
        raise typer.Exit(1)


def summary(r: RunResult) -> str:
    colour = {"ok": "green", "stopped": "yellow", "failed": "red"}[r.status]
    lines = [
        f"Run {r.workflow} v{r.version} → [{colour}]{r.status}[/]  ({r.run_id}, {r.mode})",
        f"  steps {r.steps} · llm calls {r.llm_calls} · tokens in {r.llm_input_tokens:,} · out "
        f"{r.llm_output_tokens:,} · {r.duration_ms / 1000:.1f} s · agent turns {r.agent_turns}",
    ]
    if r.output is not None:
        preview = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in list(r.output.items())[:4])
        lines.append(f"  output: {preview}")
    if r.error:
        lines.append(f"  [red]error:[/] {r.error}")
    soft = [e for e in r.guard_events if e.severity == "soft"]
    if soft:
        lines.append(
            f"  [yellow]soft guard events:[/] {len(soft)} (see `amber doctor {r.workflow}`)"
        )
    return "\n".join(lines)
