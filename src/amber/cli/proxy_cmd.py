"""``amber proxy --name <server> [--trace-dir DIR] -- <upstream command...>``.

Runs the recording MCP proxy over stdio. Nothing may be printed to stdout (that is the
protocol channel); logs go to stderr.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from amber.adapters.mcp_proxy import run_proxy
from amber.cli._output import get_state
from amber.util.paths import amber_home, find_project_root


def default_trace_dir(explicit: Path | None) -> Path:
    root = find_project_root(explicit=explicit)
    base = amber_home(root) if root is not None else Path(".amber")
    return base / "proxy"


def proxy(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Server name as the host sees it.")],
    upstream: Annotated[
        list[str] | None, typer.Argument(help="Upstream server command after `--`.")
    ] = None,
    trace_dir: Annotated[
        Path | None, typer.Option("--trace-dir", help="Fragment directory.")
    ] = None,
) -> None:
    """Run the recording MCP proxy (used from the host's MCP config, not interactively)."""
    target = trace_dir or default_trace_dir(get_state(ctx).project)
    asyncio.run(run_proxy(name, list(upstream or []), target))
