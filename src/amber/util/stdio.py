"""Spawning stdio MCP servers with a stderr that always has a file descriptor."""

from __future__ import annotations

import os
import sys
from typing import Any, TextIO

_devnull: TextIO | None = None


def errlog_stream() -> TextIO:
    """Where MCP server subprocesses write their stderr.

    ``mcp.client.stdio.stdio_client`` binds ``sys.stderr`` as its default when the module is
    first imported and hands it to the subprocess, which needs a real file descriptor. If that
    first import happens while ``sys.stderr`` is replaced (typer's CliRunner, notebooks,
    ``pythonw``) every later spawn fails with ``UnsupportedOperation: fileno``. So pick the first
    stream that has a descriptor, at call time.
    """
    global _devnull
    for stream in (sys.stderr, sys.__stderr__):
        if stream is None:
            continue
        try:
            stream.fileno()
        except (AttributeError, OSError, ValueError):  # io.UnsupportedOperation is both
            continue
        return stream
    if _devnull is None:
        _devnull = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - process lifetime
    return _devnull


def stdio_transport(params: Any) -> Any:
    """``stdio_client(params)`` with :func:`errlog_stream` as the server's stderr; pass the result
    to ``mcp.Client`` in place of the ``StdioServerParameters``."""
    from mcp.client.stdio import stdio_client

    return stdio_client(params, errlog=errlog_stream())
