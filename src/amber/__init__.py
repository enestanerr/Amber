"""Amber — a compiler for agent runs.

Record an agent doing a workflow a few times, compile the recordings into a neuro-symbolic
program (deterministic Python + schema-checked ``llm()`` holes + replay tests), then run it
cheaply with guards, a doctor for drift and an agent fallback.
"""

from __future__ import annotations

from amber.version import __version__

__all__ = ["__version__"]
