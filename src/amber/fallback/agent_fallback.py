"""Agent fallback (``docs/05-runtime.md`` §5.1): build the resume prompt, relaunch the agent
through the ``claude-stream`` adapter with the recorded MCP config, and store the result as a
``fallback`` trace whose events start with the compiled run's completed calls.

This is the only module under ``fallback/`` that knows about Claude.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from amber.adapters.base import RecordRequest
from amber.adapters.claude_stream import StreamRecording, run_claude
from amber.compile.manifest import Manifest
from amber.fallback.proposal import FallbackProposal
from amber.trace.models import Note, Status, ToolCall, ToolSchema, Trace
from amber.util.json import dumps_compact

_ENV = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    autoescape=False,
)
PREVIEW = 160


def _preview(value: Any, width: int = PREVIEW) -> str:
    text = dumps_compact(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def render_resume_prompt(
    *,
    workflow: str,
    inputs: dict[str, Any],
    completed: list[ToolCall],
    step_idx: int,
    tool: str,
    stop_detail: str,
    task_prompt: str,
    allow_writes: bool,
) -> str:
    return _ENV.get_template("resume_prompt.md.j2").render(
        workflow=workflow,
        inputs_json=dumps_compact(inputs),
        completed=[
            {
                "idx": i + 1,
                "tool": c.tool,
                "args_preview": _preview(c.args, 80),
                "result_preview": _preview(c.result),
            }
            for i, c in enumerate(completed)
        ],
        step_idx=step_idx + 1,
        tool=tool,
        stop_detail=stop_detail,
        task_prompt=task_prompt,
        allow_writes=allow_writes,
    )


def build_request(
    manifest: Manifest,
    proposal: FallbackProposal,
    *,
    inputs: dict[str, Any],
    completed: list[ToolCall],
    task_prompt: str,
    allow_writes: bool,
    cwd: Path | None,
) -> RecordRequest:
    """``RecordRequest`` that relaunches the agent the way the source runs were recorded."""
    recording = manifest.recording
    mcp_config = Path(recording["mcp_config"]) if recording.get("mcp_config") else None
    allow = [a for a in recording.get("allow", "").split(",") if a]
    prompt = render_resume_prompt(
        workflow=manifest.workflow,
        inputs=inputs,
        completed=completed,
        step_idx=proposal.step_idx,
        tool=proposal.tool,
        stop_detail=proposal.stop_detail,
        task_prompt=task_prompt,
        allow_writes=allow_writes,
    )
    return RecordRequest(
        workflow=manifest.workflow,
        agent_command=["claude", "-p", prompt],
        inputs={k: str(v) for k, v in inputs.items()},
        mcp_config=mcp_config,
        allow=allow,
        permission_mode=recording.get("permission_mode"),
        tags=["fallback"],
        inject_inputs=False,
        cwd=cwd,
    )


def splice_prefix(trace: Trace, prefix: list[ToolCall], resumed_from_step: int) -> Trace:
    """Return the trace with the compiled run's completed calls spliced in front of its events,
    so align/verify see one complete run (``prefix_calls`` keeps the raw list as well)."""
    events: list[Any] = [
        Note(
            idx=0,
            source="adapter",
            text=f"resumed from step {resumed_from_step + 1}: the first {len(prefix)} call(s) "
            "were executed by the compiled program before the guard stopped it",
        )
    ]
    tools = {t.canonical: t for t in trace.tools}
    for i, call in enumerate(prefix):
        events.append(
            call.model_copy(update={"idx": len(events), "call_id": f"prefix_{i}_{call.call_id}"})
        )
        if call.tool not in tools:
            server, _, name = call.tool.partition(".")
            tools[call.tool] = ToolSchema(server=server, name=name)
    for event in trace.events:
        events.append(event.model_copy(update={"idx": len(events)}))
    return trace.model_copy(
        update={
            "events": events,
            "tools": list(tools.values()),
            "origin": "fallback",
            "resumed_from_step": resumed_from_step,
            "prefix_calls": list(prefix),
            "tags": sorted({*trace.tags, "fallback"}),
        }
    )


async def record_fallback(
    request: RecordRequest, *, resumed_from_step: int, prefix: list[ToolCall]
) -> StreamRecording:
    """Run the agent and return the recording with the spliced fallback trace."""
    rec = await run_claude(request)
    status: Status = rec.trace.outcome.status
    trace = splice_prefix(rec.trace, prefix, resumed_from_step)
    trace.outcome.status = status
    rec.trace = trace
    return rec
