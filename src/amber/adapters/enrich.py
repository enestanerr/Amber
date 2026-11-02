"""Fill stub ``ToolSchema``s with real MCP schemas.

Claude's stream-json ``init`` event lists tool *names* only, so recorded traces start with
``input_schema={}`` stubs. When ``amber record --mcp cfg.json`` is used, the recorder connects
to each configured server afterwards, lists its tools and replaces the stubs, so the manifest
(P4) can carry real schema hashes and effects.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from amber.adapters.mcp_proxy import tool_effect, tool_to_dict
from amber.trace.models import ToolSchema, Trace
from amber.util.logging import get_logger
from amber.util.stdio import stdio_transport

log = get_logger("adapters.enrich")


def apply_tool_schemas(trace: Trace, schemas: Iterable[ToolSchema]) -> Trace:
    """Return a copy of *trace* whose stub tools are replaced by matching *schemas*."""
    by_canonical = {s.canonical: s for s in schemas}
    tools = [by_canonical.get(t.canonical, t) for t in trace.tools]
    return trace.model_copy(update={"tools": tools})


def schemas_from_listing(server: str, tools: Iterable[Any]) -> list[ToolSchema]:
    """MCP ``Tool`` objects (or dicts shaped like :func:`tool_to_dict`) → ``ToolSchema``s."""
    out: list[ToolSchema] = []
    for tool in tools:
        d = cast(dict[str, Any], tool) if isinstance(tool, dict) else tool_to_dict(tool)
        out.append(
            ToolSchema(
                server=server,
                name=str(d["name"]),
                input_schema=dict(d.get("input_schema") or {}),
                effect=tool_effect(d.get("annotations")),
            )
        )
    return out


def read_mcp_config(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return dict(cast(dict[str, dict[str, Any]], servers or {}))


async def fetch_tool_schemas(
    mcp_config: Path, *, only_servers: set[str] | None = None
) -> list[ToolSchema]:
    """Connect to every stdio/http server in a Claude ``--mcp-config`` file and list tools."""
    from mcp import Client, StdioServerParameters

    schemas: list[ToolSchema] = []
    for name, cfg in read_mcp_config(mcp_config).items():
        if only_servers is not None and name not in only_servers:
            continue
        try:
            if cfg.get("url"):
                target: Any = str(cfg["url"])
            else:
                target = stdio_transport(
                    StdioServerParameters(
                        command=str(cfg["command"]),
                        args=[str(a) for a in cfg.get("args") or []],
                        env={
                            **os.environ,
                            **{str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
                        },
                    )
                )
            async with Client(target) as client:
                res = await client.list_tools()
                schemas.extend(schemas_from_listing(name, list(res.tools)))
        except Exception as e:  # enrichment is best-effort; stubs remain valid
            log.warning("could not list tools of server %r: %s", name, e)
    return schemas
