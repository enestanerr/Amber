"""``amber bench <example> [--runs N] [--offline]`` — thin wrapper around ``bench/run_bench.py``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from amber.errors import AmberError


def bench(
    example: Annotated[
        str, typer.Argument(help="weekly-report | issue-triage | price-watch | all")
    ],
    runs: Annotated[int, typer.Option("--runs", help="Runs per side.")] = 3,
    offline: Annotated[
        bool, typer.Option("--offline", help="Use existing recordings as the baseline.")
    ] = False,
) -> None:
    """Measure agent baseline vs compiled program (writes bench/results.{json,md})."""
    script = _find_script()
    args = [sys.executable, str(script), "--runs", str(runs)]
    args += ["--all"] if example == "all" else ["--example", example]
    if offline:
        args.append("--offline")
    raise typer.Exit(subprocess.run(args, check=False).returncode)


def _find_script() -> Path:
    for base in (Path.cwd(), *Path.cwd().parents, Path(__file__).resolve().parents[3]):
        candidate = base / "bench" / "run_bench.py"
        if candidate.is_file():
            return candidate
    raise AmberError(
        "bench/run_bench.py not found",
        hint="Run from a checkout of the Amber repository (the bench script is not packaged).",
    )
