"""Rendering helpers shared by all commands.

Every command accepts ``--json``: machine output on stdout, human output suppressed. Human
output goes through a ``rich`` console; errors always go to stderr.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from pydantic import BaseModel
from rich.console import Console

from amber.errors import AmberError
from amber.util.json import dumps_canonical

JsonOpt = Annotated[
    bool, typer.Option("--json", help="Machine-readable JSON on stdout (human output suppressed).")
]

stdout = Console()
stderr = Console(stderr=True)


@dataclass
class State:
    """Root-level options carried in ``ctx.obj``."""

    project: Path | None = None
    json: bool = False


class Output:
    """Human or JSON output for one command invocation."""

    _current: Output | None = None

    def __init__(self, json_mode: bool) -> None:
        self.json_mode = json_mode
        Output._current = self

    def print(self, *renderables: Any) -> None:
        """Human output (suppressed in ``--json`` mode)."""
        if not self.json_mode:
            stdout.print(*renderables)

    def json(self, payload: BaseModel | dict[str, Any] | list[Any]) -> None:
        """Emit canonical JSON on stdout (Pydantic models are dumped in JSON mode)."""
        data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        typer.echo(dumps_canonical(data), nl=False)

    def emit(self, payload: BaseModel | dict[str, Any], *renderables: Any) -> None:
        """JSON in ``--json`` mode, otherwise the given rich renderables."""
        if self.json_mode:
            self.json(payload)
        else:
            self.print(*renderables)

    @classmethod
    def json_mode_active(cls) -> bool:
        return bool(cls._current and cls._current.json_mode)


def get_output(ctx: typer.Context, json_flag: bool) -> Output:
    """Combine the root ``--json`` with the command-level flag."""
    state = ctx.find_root().obj
    root_json = bool(getattr(state, "json", False))
    return Output(json_mode=json_flag or root_json)


def get_state(ctx: typer.Context) -> State:
    state = ctx.find_root().obj
    return state if isinstance(state, State) else State()


def fail(err: AmberError) -> NoReturn:
    """Render *err* and exit with its documented code (used by verify/run/doctor so the code
    is visible to callers that invoke the Typer app directly, not only through ``main``)."""
    render_error(err)
    raise typer.Exit(err.exit_code)


def render_error(err: AmberError) -> None:
    """Print an error (and hint) to stderr; JSON shape in ``--json`` mode."""
    if Output.json_mode_active():
        payload: dict[str, Any] = {
            "error": type(err).__name__,
            "message": err.message,
            "hint": err.hint,
            "exit_code": err.exit_code,
        }
        typer.echo(dumps_canonical(payload), nl=False, err=True)
        return
    stderr.print(f"[bold red]error:[/] {err.message}")
    if err.hint:
        stderr.print(f"[dim]hint:[/] {err.hint}")
