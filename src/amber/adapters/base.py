"""Adapter protocol shared by the recorders (``docs/02-trace-format.md`` §3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from amber.trace.models import AdapterName, Status, Trace


@dataclass(frozen=True)
class RecordRequest:
    """Everything a recorder needs for one run."""

    workflow: str
    agent_command: list[str]
    inputs: dict[str, str] = field(default_factory=dict)
    mcp_config: Path | None = None
    allow: list[str] = field(default_factory=list)
    permission_mode: str | None = None
    tags: list[str] = field(default_factory=list)
    status_override: Status | None = None
    inject_inputs: bool = True
    cwd: Path | None = None


class Adapter(Protocol):
    name: AdapterName

    async def record(self, request: RecordRequest) -> Trace: ...
