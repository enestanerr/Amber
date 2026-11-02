"""``amber record <workflow> [options] -- claude -p "<task>" [flags]``."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from amber.adapters.base import RecordRequest
from amber.adapters.claude_cli_flags import passthrough_args
from amber.adapters.claude_stream import StreamRecording, record_from_lines, run_claude
from amber.adapters.enrich import apply_tool_schemas, fetch_tool_schemas
from amber.cli._context import open_store
from amber.cli._inputs import parse_inputs, parse_status
from amber.cli._output import JsonOpt, get_output, stderr
from amber.trace.jsonpath import iter_leaves
from amber.trace.models import ToolCall, Trace


def record(
    ctx: typer.Context,
    workflow: Annotated[str, typer.Argument(help="Workflow name, e.g. weekly-report.")],
    agent: Annotated[list[str] | None, typer.Argument(help="Agent command after `--`.")] = None,
    input_: Annotated[
        list[str] | None, typer.Option("--input", help="Declared input k=v (repeatable).")
    ] = None,
    mcp: Annotated[
        Path | None, typer.Option("--mcp", help="MCP config JSON passed to the agent.")
    ] = None,
    allow: Annotated[
        list[str] | None, typer.Option("--allow", help="Tool allow pattern (repeatable).")
    ] = None,
    permission_mode: Annotated[str | None, typer.Option("--permission-mode")] = None,
    sdk: Annotated[
        bool, typer.Option("--sdk", help="Use the Agent SDK adapter (extra [sdk]).")
    ] = False,
    from_stdin: Annotated[
        bool, typer.Option("--from-stdin", help="Read stream-json from stdin.")
    ] = False,
    prompt: Annotated[
        str | None, typer.Option("--prompt", help="Task prompt (with --from-stdin).")
    ] = None,
    tag: Annotated[
        list[str] | None, typer.Option("--tag", help="Tag on the trace (repeatable).")
    ] = None,
    status: Annotated[str | None, typer.Option("--status", help="Override outcome status.")] = None,
    no_inject_inputs: Annotated[bool, typer.Option("--no-inject-inputs")] = False,
    json_: JsonOpt = False,
) -> None:
    """Record one agent run as a trace."""
    out = get_output(ctx, json_)
    store = open_store(ctx)
    request = RecordRequest(
        workflow=workflow,
        agent_command=list(agent or []),
        inputs=parse_inputs(input_ or []),
        mcp_config=mcp,
        allow=list(allow or []),
        permission_mode=permission_mode,
        tags=list(tag or []),
        status_override=parse_status(status),
        inject_inputs=not no_inject_inputs,
    )

    def progress(call: ToolCall) -> None:
        stderr.print(f"  [dim]#{call.idx:<3}[/] {call.tool}", highlight=False)

    if from_stdin:
        rec = record_from_lines(request, sys.stdin, task_prompt=prompt or "")
    elif sdk:
        from amber.adapters.agent_sdk import record_sdk

        rec = StreamRecording(asyncio.run(record_sdk(request, on_call=progress)), 0)
    else:
        rec = asyncio.run(run_claude(request, on_call=progress))
        if mcp is not None:
            rec.trace = _enrich(rec, mcp)

    extra = passthrough_args(list(agent or [])) if not (from_stdin or sdk) else []
    if extra:  # e.g. --model / --setting-sources: the fallback relaunches the agent with them
        rec.trace.harness["agent_args"] = json.dumps(extra)
    if mcp is not None:
        rec.trace.harness["mcp_config"] = str(mcp.resolve())
    if allow:
        rec.trace.harness["allow"] = ",".join(allow)
    if permission_mode:
        rec.trace.harness["permission_mode"] = permission_mode
    path = store.save(rec.trace)
    if out.json_mode:
        out.json(rec.trace)
    else:
        out.print(render_summary(rec.trace))
        for w in rec.warnings:
            stderr.print(f"[yellow]warning:[/] {w}")
        if rec.denied:
            stderr.print(
                f"[yellow]warning:[/] {len(rec.denied)} tool calls were denied; "
                "re-record with --allow 'mcp__<server>__*'"
            )
        if rec.stderr_tail and rec.trace.outcome.status == "failure":
            stderr.print("[dim]claude stderr (tail):[/]\n" + rec.stderr_tail)
        out.print(f"[dim]saved {path}[/]")
    if rec.trace.outcome.status == "failure":
        raise typer.Exit(1)


def judgment_candidates(trace: Trace) -> list[str]:
    """String args ≥ 24 chars that are neither inputs nor copied from an earlier result."""
    seen: set[str] = {str(v) for v in trace.inputs.values()}
    found: list[str] = []
    for call in trace.calls:
        for path, value in iter_leaves(call.args):
            if isinstance(value, str) and len(value) >= 24 and value not in seen:
                found.append(f"{call.tool}{path[1:]}")
        for _, value in iter_leaves(call.result):
            if isinstance(value, str):
                seen.add(value)
    return found


def render_summary(trace: Trace) -> str:
    calls = trace.calls
    by_server: dict[str, int] = {}
    for c in calls:
        by_server[c.tool.split(".", 1)[0]] = by_server.get(c.tool.split(".", 1)[0], 0) + 1
    servers = " · ".join(f"{s} {n}" for s, n in sorted(by_server.items()))
    cands = judgment_candidates(trace)
    u = trace.outcome.usage
    cost = f" · ${trace.outcome.cost_usd:.3f}" if trace.outcome.cost_usd is not None else ""
    secs = f" · {trace.outcome.duration_ms / 1000:.1f} s" if trace.outcome.duration_ms else ""
    lines = [
        f"Recorded {trace.workflow} → [bold]{trace.id}[/]  ({trace.outcome.status})",
        f"  tool calls      {len(calls):<4}({servers})",
        f"  judgment cands  {len(cands):<4}({', '.join(cands) if cands else '-'})",
        f"  tokens          in {u.total_input:,} (cached {u.cache_read_tokens:,})"
        f" · out {u.output_tokens:,}{cost}{secs}",
        "Next: record 1-2 more runs with different inputs, "
        f"then `amber align {trace.workflow} --show`",
    ]
    return "\n".join(lines)


def _enrich(rec: StreamRecording, mcp: Path) -> Trace:
    """Replace stub tool schemas with the servers' real ones (best effort)."""
    try:
        schemas = asyncio.run(fetch_tool_schemas(mcp))
    except Exception as e:  # enrichment must never lose a recording
        rec.warnings.append(f"could not fetch tool schemas from {mcp}: {e}")
        return rec.trace
    return apply_tool_schemas(rec.trace, schemas)
