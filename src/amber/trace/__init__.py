"""Canonical trace format: models, canonicalization, JSON paths and the trace store.

Everything downstream (align, compile, verify) depends only on this package. It contains no
harness-specific code: adapters produce :class:`~amber.trace.models.Trace` objects.
"""

from __future__ import annotations

from amber.trace.models import (
    Event,
    Note,
    Outcome,
    Thought,
    ToolCall,
    ToolSchema,
    Trace,
    TraceInvariantError,
    Usage,
)

__all__ = [
    "Event",
    "Note",
    "Outcome",
    "Thought",
    "ToolCall",
    "ToolSchema",
    "Trace",
    "TraceInvariantError",
    "Usage",
]
