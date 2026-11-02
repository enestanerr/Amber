"""Helpers shared by the mock MCP servers: scenario selection and the outbox path.

Scenario ``A`` is the baseline; ``B`` is the drifted world (``make drift``). The scenario comes
from ``AMBER_SCENARIO`` or from a ``.scenario`` file next to the example's ``amber.toml``.
"""

from __future__ import annotations

import os
from pathlib import Path


def scenario(default: str = "A") -> str:
    env = os.environ.get("AMBER_SCENARIO")
    if env:
        return env.strip().upper()
    marker = Path.cwd() / ".scenario"
    if marker.is_file():
        return marker.read_text(encoding="utf-8").strip().upper() or default
    return default


def set_scenario(value: str, root: Path | None = None) -> Path:
    marker = (root or Path.cwd()) / ".scenario"
    marker.write_text(value.strip().upper() + "\n", encoding="utf-8")
    return marker


def outbox_path() -> Path:
    env = os.environ.get("AMBER_OUTBOX")
    return Path(env) if env else Path.cwd() / ".outbox" / "slack.jsonl"
