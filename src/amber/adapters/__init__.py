"""Adapters turn harness-specific events into canonical :class:`~amber.trace.models.Trace`.

This package (with ``amber.models``) is the only place that knows about Claude Code: its
``mcp__<server>__<tool>`` naming, stream-json events, transcript files and the Agent SDK.
``mcp_proxy`` is harness-agnostic and records any MCP client.
"""

from __future__ import annotations
