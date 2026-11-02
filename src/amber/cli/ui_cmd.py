"""``amber ui [--port 8787] [--open]`` — the local panel (needs the ``[ui]`` extra)."""

from __future__ import annotations

from typing import Annotated

import typer

from amber.cli._context import project
from amber.errors import AmberError


def ui(
    ctx: typer.Context,
    port: Annotated[int, typer.Option("--port", help="Port on 127.0.0.1.")] = 8787,
    open_: Annotated[bool, typer.Option("--open", help="Open the browser.")] = False,
) -> None:
    """Start the read-mostly web panel over .amber/amber.db and programs/."""
    root, _ = project(ctx)
    try:
        from amber.ui.app import run_ui
    except ImportError as e:
        raise AmberError(
            "the web panel needs the [ui] extra",
            hint="Install it: uv pip install 'amber-compiler[ui]' (fastapi, uvicorn, pygments).",
        ) from e
    typer.echo(f"amber panel on http://127.0.0.1:{port}/ (Ctrl+C to stop)")
    run_ui(root, port=port, open_browser=open_)
