"""Logging setup: ``rich`` handler on stderr; ``AMBER_LOG=debug|info|warning`` picks the level."""

from __future__ import annotations

import logging
import os

from rich.console import Console
from rich.logging import RichHandler

_CONFIGURED = False


def make_console(*, stderr: bool = False) -> Console:
    """A rich console that honours ``FORCE_COLOR`` on Windows pipes too.

    With ``FORCE_COLOR`` set and output piped (CI logs, recordings), rich on Windows picks its
    legacy Win32 renderer, which writes no escape codes at all; ANSI is what the user asked for.
    """
    legacy = False if os.environ.get("FORCE_COLOR") else None
    return Console(stderr=stderr, legacy_windows=legacy)


def setup_logging(level: str | None = None) -> None:
    """Configure the root ``amber`` logger once; later calls only adjust the level."""
    global _CONFIGURED
    name = (level or os.environ.get("AMBER_LOG") or "warning").upper()
    numeric = logging.getLevelName(name)
    if not isinstance(numeric, int):
        numeric = logging.WARNING
    logger = logging.getLogger("amber")
    logger.setLevel(numeric)
    if not _CONFIGURED:
        handler = RichHandler(
            console=make_console(stderr=True),
            show_path=numeric <= logging.DEBUG,
            rich_tracebacks=False,
            markup=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
        _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Child logger under ``amber``."""
    return logging.getLogger(name if name.startswith("amber") else f"amber.{name}")
