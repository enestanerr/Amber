"""``mcp-proxy`` adapter: a recording stdio MCP server in front of any upstream server, plus
the importer that folds its fragments into a trace (``docs/02-trace-format.md`` §3 A4).

Fragment lines (one JSON object per line, ``DIR/<server>-<pid>.jsonl``)::

    {
        "kind": "tools",
        "ts": ...,
        "server": ...,
        "tools": [{"name", "description", "input_schema", "annotations"}],
    }
    {
        "kind": "call",
        "ts": ...,
        "server": ...,
        "tool": ...,
        "args": {...},
        "result": ...,
        "result_kind": ...,
        "is_error": bool,
        "duration_ms": int,
        "schema_hash": "...",
    }

Results larger than 64 KB go to ``DIR/blobs/<sha>.json`` and are referenced as ``{"$ref": ...}``.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from amber.errors import AmberError
from amber.trace.canonical import Effect, ResultKind, normalize_result
from amber.trace.models import Outcome, Status, ToolCall, ToolSchema, Trace, schema_hash
from amber.util.hashing import sha256_hex
from amber.util.json import dumps_compact, loads, write_json
from amber.util.logging import get_logger
from amber.util.time import iso, new_id, utcnow

log = get_logger("adapters.mcp_proxy")

MAX_INLINE_RESULT = 64 * 1024


def tool_effect(annotations: dict[str, Any] | None) -> Effect:
    """MCP ``ToolAnnotations`` → effect: ``read_only_hint`` wins, destructive → write."""
    if not annotations:
        return "unknown"
    if annotations.get("read_only_hint") is True:
        return "read"
    if annotations.get("destructive_hint") is True or annotations.get("read_only_hint") is False:
        return "write"
    return "unknown"


def tool_to_dict(tool: Any) -> dict[str, Any]:
    ann = getattr(tool, "annotations", None)
    annotations: dict[str, Any] | None = None
    if ann is not None:
        annotations = {
            "read_only_hint": getattr(ann, "read_only_hint", None),
            "destructive_hint": getattr(ann, "destructive_hint", None),
        }
    return {
        "name": str(tool.name),
        "description": getattr(tool, "description", None),
        "input_schema": dict(getattr(tool, "input_schema", None) or {}),
        "annotations": annotations,
    }


class FragmentWriter:
    """Appends one JSON line per call (and one ``tools`` header per server) to the trace dir."""

    def __init__(self, trace_dir: Path, server: str, pid: int | None = None) -> None:
        self.trace_dir = trace_dir
        self.server = server
        self.path = trace_dir / f"{server}-{pid or os.getpid()}.jsonl"
        self.blobs = trace_dir / "blobs"
        self._tools_written = False

    def write_tools(self, tools: list[Any]) -> None:
        if self._tools_written:
            return
        self._tools_written = True
        self._append(
            {
                "kind": "tools",
                "ts": iso(utcnow()),
                "server": self.server,
                "tools": [tool_to_dict(t) for t in tools],
            }
        )

    def write_call(
        self, tool: str, args: dict[str, Any], result: Any, *, duration_ms: int, schema_hash: str
    ) -> None:
        value, kind = normalize_result(result)
        if len(dumps_compact(value)) > MAX_INLINE_RESULT:
            sha = sha256_hex(dumps_compact(value))
            write_json(self.blobs / f"{sha}.json", value)
            value = {"$ref": f"blobs/{sha}.json"}
        self._append(
            {
                "kind": "call",
                "ts": iso(utcnow()),
                "server": self.server,
                "tool": tool,
                "args": args,
                "result": value,
                "result_kind": kind,
                "is_error": bool(getattr(result, "is_error", False)),
                "duration_ms": duration_ms,
                "schema_hash": schema_hash,
            }
        )

    def _append(self, line: dict[str, Any]) -> None:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(dumps_compact(line) + "\n")


async def run_proxy(
    name: str, upstream: list[str], trace_dir: Path, *, extra_env: dict[str, str] | None = None
) -> None:
    """Serve stdio, forward ``tools/list`` and ``tools/call`` verbatim, log every call."""
    from mcp import Client, StdioServerParameters
    from mcp.server import Server, ServerRequestContext
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        PaginatedRequestParams,
        TextContent,
    )

    if not upstream:
        raise AmberError(
            "no upstream command given", hint="amber proxy --name shop -- uv run server.py"
        )
    writer = FragmentWriter(trace_dir, name)
    hashes: dict[str, str] = {}
    params = StdioServerParameters(
        command=upstream[0], args=list(upstream[1:]), env={**os.environ, **(extra_env or {})}
    )
    async with Client(params) as client:

        async def refresh_tools() -> ListToolsResult:
            res = await client.list_tools()
            writer.write_tools(list(res.tools))
            for t in res.tools:
                hashes[t.name] = schema_hash(dict(t.input_schema or {}))
            return res

        async def on_list_tools(
            ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None
        ) -> ListToolsResult:
            res = await refresh_tools()
            return ListToolsResult(tools=res.tools, next_cursor=res.next_cursor)

        async def on_call_tool(
            ctx: ServerRequestContext[Any], params: CallToolRequestParams
        ) -> CallToolResult:
            if params.name not in hashes:
                await refresh_tools()
            t0 = time.monotonic()
            try:
                res = await client.call_tool(params.name, params.arguments or {})
            except Exception as e:  # upstream failures become tool errors, not protocol errors
                res = CallToolResult(
                    content=[TextContent(type="text", text=f"{type(e).__name__}: {e}")],
                    is_error=True,
                )
            duration_ms = int((time.monotonic() - t0) * 1000)
            writer.write_call(
                params.name,
                dict(params.arguments or {}),
                res,
                duration_ms=duration_ms,
                schema_hash=hashes.get(params.name, ""),
            )
            return res

        server: Server[Any] = Server(
            "amber-proxy", on_list_tools=on_list_tools, on_call_tool=on_call_tool
        )
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())


# --- import -----------------------------------------------------------------


def read_fragments(
    trace_dir: Path, *, since: datetime | None = None
) -> tuple[list[dict[str, Any]], dict[str, ToolSchema]]:
    """All call fragments (sorted by ``ts``, then file order) and the tool schemas seen."""
    calls: list[tuple[str, int, dict[str, Any]]] = []
    tools: dict[str, ToolSchema] = {}
    order = 0
    for file in sorted(trace_dir.glob("*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = cast(dict[str, Any], loads(line))
            kind = obj.get("kind", "call")
            if kind == "tools":
                for t in cast(list[dict[str, Any]], obj.get("tools") or []):
                    schema = ToolSchema(
                        server=str(obj["server"]),
                        name=str(t["name"]),
                        input_schema=dict(t.get("input_schema") or {}),
                        effect=tool_effect(t.get("annotations")),
                    )
                    tools[schema.canonical] = schema
                continue
            ts = str(obj.get("ts") or "")
            if since is not None and ts and datetime.fromisoformat(ts) < since:
                continue
            calls.append((ts, order, obj))
            order += 1
    calls.sort(key=lambda c: (c[0], c[1]))
    return [c[2] for c in calls], tools


def import_proxy(
    trace_dir: Path,
    *,
    workflow: str,
    prompt: str = "",
    inputs: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    status: Status | None = None,
    since: datetime | None = None,
    trace_id: str | None = None,
) -> Trace:
    """Fold proxy fragments across servers into one ``mcp-proxy`` trace (no thoughts)."""
    if not trace_dir.is_dir():
        raise AmberError(
            f"no proxy directory {trace_dir}", hint="Run the agent through `amber proxy` first."
        )
    fragments, tools = read_fragments(trace_dir, since=since)
    if not fragments:
        raise AmberError(
            f"no proxy fragments in {trace_dir}", hint="Did the host actually call any tools?"
        )
    events: list[ToolCall] = []
    for i, frag in enumerate(fragments):
        canonical = f"{frag['server']}.{frag['tool']}"
        if canonical not in tools:
            tools[canonical] = ToolSchema(server=str(frag["server"]), name=str(frag["tool"]))
        result = frag.get("result")
        if isinstance(result, dict) and "$ref" in result:
            result = loads(
                (trace_dir / str(cast(dict[str, Any], result)["$ref"])).read_text(encoding="utf-8")
            )
        kind = cast(ResultKind, frag.get("result_kind") or ("empty" if result is None else "json"))
        ts = frag.get("ts")
        events.append(
            ToolCall(
                idx=i,
                call_id=f"px_{i + 1:03d}",
                tool=canonical,
                args=dict(frag.get("args") or {}),
                result=result,
                result_kind=kind,
                is_error=bool(frag.get("is_error", False)),
                started_at=datetime.fromisoformat(ts) if isinstance(ts, str) and ts else None,
                duration_ms=frag.get("duration_ms"),
            )
        )
    first = events[0].started_at
    return Trace(
        id=trace_id or new_id("t"),
        workflow=workflow,
        created_at=first or utcnow(),
        adapter="mcp-proxy",
        harness={"name": "mcp-proxy"},
        task_prompt=prompt,
        inputs=dict(inputs or {}),
        tools=sorted(tools.values(), key=lambda t: t.canonical),
        events=list(events),
        outcome=Outcome(status=status or "unknown"),
        origin="import",
        tags=list(tags or []),
    )
