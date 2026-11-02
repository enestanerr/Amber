"""Project-aware helpers for commands (root discovery, config, store)."""

from __future__ import annotations

from pathlib import Path

import typer

from amber.cli._output import get_state
from amber.config import AmberConfig, require_project
from amber.trace.store import TraceStore
from amber.util.paths import amber_home


def project(ctx: typer.Context) -> tuple[Path, AmberConfig]:
    return require_project(get_state(ctx).project)


def open_store(ctx: typer.Context) -> TraceStore:
    root, _ = project(ctx)
    return TraceStore(amber_home(root))


def preview(value: object, width: int = 60) -> str:
    """One-line compact preview of a JSON value for tables."""
    from amber.util.json import dumps_compact

    text = value if isinstance(value, str) else dumps_compact(value)
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"
