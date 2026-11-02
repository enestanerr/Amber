"""The ``amber`` Typer application.

Global options (``--version``, ``--project``, ``--json``) live on the root callback; every
command also accepts ``--json`` after the subcommand (``docs/03-cli-spec.md``). Commands are
registered from one module each so this file stays a table of contents.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Annotated

import typer

from amber.cli import init_cmd
from amber.cli._output import State, render_error
from amber.errors import AmberError
from amber.util.logging import setup_logging
from amber.version import __version__

app = typer.Typer(
    name="amber",
    help="Compile recorded agent runs into programs with replay tests, guards and a drift loop.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"amber {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    project: Annotated[
        Path | None,
        typer.Option(
            "--project",
            envvar="AMBER_PROJECT",
            help="Project root containing amber.toml (default: walk up from the cwd).",
            show_default=False,
        ),
    ] = None,
    json_: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON on stdout for the command.")
    ] = False,
) -> None:
    """Amber: record → align → compile → verify → run → doctor → fallback."""
    setup_logging()
    ctx.obj = State(project=project, json=json_)


app.command("init")(init_cmd.init)


def main() -> None:
    """Console-script entry point: run the app and map :class:`AmberError` to exit codes."""
    # JSON output is UTF-8 by contract (``ensure_ascii=False``); on Windows a piped stdout
    # would otherwise default to the ANSI code page and fail on the first non-ASCII char.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        app()
    except AmberError as err:
        render_error(err)
        raise SystemExit(err.exit_code) from None


if __name__ == "__main__":
    main()
