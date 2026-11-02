"""Align: merge 1..N traces of one workflow into a :class:`~amber.align.skeleton.Skeleton`.

Pipeline (``docs/04-compiler.md`` §A): sequence alignment → provenance mining →
classification → pruning → inferred inputs / volatile paths. No harness-specific code here.
"""

from __future__ import annotations

from amber.align.skeleton import (
    InputSpec,
    Predicate,
    Provenance,
    Skeleton,
    StepSpec,
    TemplatePart,
    build_skeleton,
)

__all__ = [
    "InputSpec",
    "Predicate",
    "Provenance",
    "Skeleton",
    "StepSpec",
    "TemplatePart",
    "build_skeleton",
]
