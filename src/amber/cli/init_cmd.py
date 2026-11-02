"""``amber init``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from amber.cli._output import JsonOpt, get_output, get_state
from amber.project import init_project


def init(
    ctx: typer.Context,
    json_: JsonOpt = False,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing amber.toml.")
    ] = False,
) -> None:
    """Create .amber/ and amber.toml in the project directory (idempotent)."""
    out = get_output(ctx, json_)
    root = get_state(ctx).project or Path.cwd()
    result = init_project(root, force=force)

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_row("root", result.root)
    table.add_row("created", ", ".join(result.created) or "nothing (already initialised)")
    if result.kept:
        table.add_row("kept", ", ".join(result.kept))
    table.add_row(
        "models",
        f"synthesis={result.synthesis_model} judgment={result.judgment_model} "
        f"({result.models_source})",
    )
    out.emit(result, table)
    if not out.json_mode:
        out.print(
            'Next: `amber record <workflow> --input k=v --mcp mcp.json -- claude -p "<task>"`'
        )
