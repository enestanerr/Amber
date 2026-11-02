"""Entry point used by ``amber run --fallback agent`` (filled in by P7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from amber.cli._output import Output
from amber.compile.versions import VersionInfo
from amber.config import AmberConfig
from amber.errors import AmberError
from amber.runtime.runner import RunResult


def run_fallback(
    root: Path,
    cfg: AmberConfig,
    info: VersionInfo,
    result: RunResult,
    *,
    inputs: dict[str, Any],
    allow_writes: bool,
    out: Output,
) -> RunResult:
    raise AmberError(
        "agent fallback is not available yet (P7)",
        hint="Re-run without --fallback, or run `amber doctor` for a diagnosis.",
    )
