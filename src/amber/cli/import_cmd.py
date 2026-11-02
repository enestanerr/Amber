"""``amber import <workflow> (<session.jsonl> | --latest | --proxy DIR)``."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from amber.adapters.claude_transcript import import_transcript, latest_transcript
from amber.adapters.mcp_proxy import import_proxy
from amber.cli._context import open_store
from amber.cli._inputs import parse_inputs, parse_status
from amber.cli._output import JsonOpt, get_output, stderr
from amber.cli.record_cmd import render_summary
from amber.errors import UsageError
from amber.util.paths import display_path


def import_(
    ctx: typer.Context,
    workflow: Annotated[str, typer.Argument(help="Workflow name.")],
    path: Annotated[Path | None, typer.Argument(help="Claude Code transcript (.jsonl).")] = None,
    latest: Annotated[
        bool, typer.Option("--latest", help="Newest transcript for the cwd.")
    ] = False,
    proxy: Annotated[
        Path | None, typer.Option("--proxy", help="Fold `amber proxy` fragments from DIR.")
    ] = None,
    since: Annotated[
        str | None, typer.Option("--since", help="ISO timestamp; ignore older fragments.")
    ] = None,
    prompt: Annotated[str | None, typer.Option("--prompt", help="Task prompt to store.")] = None,
    input_: Annotated[list[str] | None, typer.Option("--input", help="Declared input k=v.")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag")] = None,
    status: Annotated[
        str | None, typer.Option("--status", help="success | failure | unknown.")
    ] = None,
    json_: JsonOpt = False,
) -> None:
    """Import a transcript or proxy fragments as a trace."""
    out = get_output(ctx, json_)
    sources = sum(1 for s in (path, latest, proxy) if s)
    if sources != 1:
        raise UsageError(
            "give exactly one source: a transcript path, --latest or --proxy DIR",
            hint="amber import weekly-report ~/.claude/projects/<cwd>/<session>.jsonl",
        )
    store = open_store(ctx)
    inputs = parse_inputs(input_ or [])
    status_value = parse_status(status)
    warnings: list[str] = []
    if proxy is not None:
        since_dt = datetime.fromisoformat(since) if since else None
        trace = import_proxy(
            proxy,
            workflow=workflow,
            prompt=prompt or "",
            inputs=inputs,
            tags=tag or [],
            status=status_value,
            since=since_dt,
        )
    else:
        source = path if path is not None else latest_transcript(Path.cwd())
        trace, warnings = import_transcript(
            source,
            workflow=workflow,
            prompt=prompt or "",
            inputs=inputs,
            tags=tag or [],
            status=status_value,
        )
    saved = store.save(trace)
    if out.json_mode:
        out.json(trace)
        return
    out.print(render_summary(trace))
    for w in warnings:
        stderr.print(f"[yellow]warning:[/] {w}")
    out.print(f"[dim]saved {display_path(saved)}[/]")
