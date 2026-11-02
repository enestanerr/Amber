"""Live MCP backend (``docs/05-runtime.md`` §2.1): one ``mcp.Client`` per server from
``amber.toml [servers]``, tool listing cached at start (schema drift detected against the
manifest), calls normalized like recordings."""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from amber.adapters.mcp_proxy import tool_effect, tool_to_dict
from amber.compile.manifest import Manifest
from amber.config import ServerCfg
from amber.errors import AmberError, ConfigError
from amber.runtime.guards import GuardEvent
from amber.trace.canonical import ResultKind, normalize_result, split_canonical
from amber.trace.models import schema_hash
from amber.util.stdio import stdio_transport

TRANSIENT_PATTERNS = (
    "timeout",
    "timed out",
    "429",
    "500",
    "502",
    "503",
    "504",
    "connection reset",
    "temporarily",
)


class ToolError(AmberError):
    exit_code = 4

    def __init__(self, tool: str, text: str) -> None:
        self.tool = tool
        self.text = text
        super().__init__(f"{tool} returned an error: {text[:300]}")

    @property
    def transient(self) -> bool:
        low = self.text.lower()
        return any(p in low for p in TRANSIENT_PATTERNS)


@dataclass
class CallOutcome:
    value: Any
    kind: ResultKind
    duration_ms: int
    is_error: bool = False
    error_text: str = ""


@dataclass
class LiveBackend:
    manifest: Manifest
    servers: dict[str, ServerCfg]
    cwd: Path | None = None
    read_timeout: float = 60.0
    clients: dict[str, Any] = field(default_factory=dict[str, Any])
    tools: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    effects: dict[str, str] = field(default_factory=dict[str, str])
    drift_events: list[GuardEvent] = field(default_factory=list[GuardEvent])

    async def start(self) -> list[GuardEvent]:
        """Connect to every server the manifest needs; list tools; compute drift events."""
        from mcp import Client, StdioServerParameters

        for name in self.manifest.servers:
            if name == "builtin":
                continue
            cfg = self.servers.get(name)
            if cfg is None:
                raise ConfigError(
                    f"server {name!r} is used by the program but not configured",
                    hint=f"Add [servers.{name}] to amber.toml (command/args or url).",
                )
            if cfg.url:
                client = Client(cfg.url, read_timeout_seconds=self.read_timeout)
            else:
                params = StdioServerParameters(
                    command=str(cfg.command),
                    args=list(cfg.args),
                    env={**os.environ, **cfg.env},
                    cwd=str(self.cwd) if self.cwd else None,
                )
                client = Client(stdio_transport(params), read_timeout_seconds=self.read_timeout)
            await client.__aenter__()
            self.clients[name] = client
            listing = await client.list_tools()
            for tool in listing.tools:
                info = tool_to_dict(tool)
                canonical = f"{name}.{info['name']}"
                self.tools[canonical] = info
                self.effects[canonical] = tool_effect(info.get("annotations"))
        self.drift_events = self._drift()
        return self.drift_events

    def _drift(self) -> list[GuardEvent]:
        events: list[GuardEvent] = []
        for entry in self.manifest.tools:
            if entry.server == "builtin":
                continue
            step_idx = next((s.idx for s in self.manifest.steps if s.tool == entry.canonical), 0)
            live = self.tools.get(entry.canonical)
            if live is None:
                events.append(
                    GuardEvent(
                        "schema_drift",
                        "hard",
                        f"tool {entry.canonical} is missing on the server",
                        step_idx,
                    )
                )
                continue
            live_hash = schema_hash(dict(live.get("input_schema") or {}))
            if entry.schema_hash and entry.input_schema and live_hash != entry.schema_hash:
                events.append(
                    GuardEvent(
                        "schema_drift",
                        "hard",
                        f"{entry.canonical} input schema changed "
                        f"(hash {entry.schema_hash[:4]} → {live_hash[:4]})",
                        step_idx,
                    )
                )
        return events

    async def stop(self) -> None:
        for client in reversed(list(self.clients.values())):
            with contextlib.suppress(Exception):  # best effort; the subprocess is going away
                await client.__aexit__(None, None, None)
        self.clients.clear()

    def effect(self, tool: str) -> str | None:
        return self.effects.get(tool)

    async def call(self, tool: str, args: dict[str, Any]) -> CallOutcome:
        server, name = split_canonical(tool)
        client = self.clients.get(server)
        if client is None:
            raise ConfigError(f"no live connection for server {server!r}")
        t0 = time.monotonic()
        result = await client.call_tool(name, dict(args))
        duration_ms = int((time.monotonic() - t0) * 1000)
        value, kind = normalize_result(result)
        is_error = bool(getattr(result, "is_error", False))
        text = value if isinstance(value, str) else ""
        return CallOutcome(value, kind, duration_ms, is_error=is_error, error_text=text)
