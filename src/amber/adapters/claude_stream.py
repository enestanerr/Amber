"""``claude-stream`` adapter: wrap ``claude -p … --output-format stream-json --verbose``.

Also serves ``amber record --from-stdin`` (NDJSON piped in, used by tests and CI).
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from amber.adapters.base import RecordRequest
from amber.adapters.claude_cli_flags import VERSION, inputs_suffix, prepare_claude_argv
from amber.adapters.claude_messages import ClaudeMessageMapper, parse_stream_line
from amber.errors import AmberError
from amber.trace.models import Status, ToolCall, Trace
from amber.util.logging import get_logger
from amber.util.time import new_id, utcnow

log = get_logger("adapters.claude_stream")

STREAM_LIMIT = 16 * 1024 * 1024  # a single tool result line may be large
STDERR_TAIL_LINES = 40


@dataclass
class StreamRecording:
    trace: Trace
    exit_code: int
    denied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stderr_tail: str = ""


def feed_lines(mapper: ClaudeMessageMapper, lines: Iterable[str]) -> int:
    """Feed NDJSON lines into *mapper*; returns the number of parsed events."""
    n = 0
    for line in lines:
        obj = parse_stream_line(line)
        if obj is not None:
            mapper.feed(obj)
            n += 1
    return n


def record_from_lines(
    request: RecordRequest,
    lines: Iterable[str],
    *,
    task_prompt: str = "",
    trace_id: str | None = None,
    created_at: datetime | None = None,
    on_call: Callable[[ToolCall], None] | None = None,
) -> StreamRecording:
    """Build a trace from already-captured stream-json (no subprocess, no timestamps)."""
    mapper = ClaudeMessageMapper(
        workflow=request.workflow,
        adapter="claude-stream",
        task_prompt=task_prompt,
        inputs=request.inputs,
        tags=request.tags,
        on_call=on_call,
    )
    feed_lines(mapper, lines)
    status: Status | None = request.status_override
    if status is None and not mapper.result_seen:
        status = "failure"
    trace = mapper.build(
        trace_id=trace_id or new_id("t"), created_at=created_at or utcnow(), status_override=status
    )
    return StreamRecording(trace, 0, mapper.denied, mapper.warnings)


def resolve_claude(executable: str) -> str:
    path = shutil.which(executable)
    if path is None:
        raise AmberError(
            f"{executable!r} was not found on PATH",
            hint="Install Claude Code (https://claude.com/claude-code) and log in, or record"
            " another harness through `amber proxy`.",
        )
    return path


async def claude_version(exe: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, VERSION, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (OSError, TimeoutError):
        return None
    text = out.decode("utf-8", "replace").strip()
    return text.split()[0] if text else None


async def _drain_tail(stream: asyncio.StreamReader | None) -> str:
    if stream is None:
        return ""
    tail: list[str] = []
    while True:
        raw = await stream.readline()
        if not raw:
            break
        tail.append(raw.decode("utf-8", "replace").rstrip())
        if len(tail) > STDERR_TAIL_LINES:
            del tail[0]
    return "\n".join(tail)


async def run_claude(
    request: RecordRequest,
    *,
    on_call: Callable[[ToolCall], None] | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> StreamRecording:
    """Spawn ``claude``, stream its stdout into the mapper, return the recording."""
    prepared = prepare_claude_argv(
        request.agent_command,
        mcp_config=request.mcp_config,
        allow=request.allow,
        permission_mode=request.permission_mode,
        prompt_suffix=inputs_suffix(request.inputs) if request.inject_inputs else None,
    )
    exe = resolve_claude(prepared.argv[0])
    mapper = ClaudeMessageMapper(
        workflow=request.workflow,
        adapter="claude-stream",
        task_prompt=prepared.prompt,
        inputs=request.inputs,
        tags=request.tags,
        clock=clock,
        on_call=on_call,
    )
    version = await claude_version(exe)
    if version:
        mapper.harness["version"] = version
    started = clock()
    log.debug("spawning %s", prepared.argv)
    proc = await asyncio.create_subprocess_exec(
        exe,
        *prepared.argv[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=request.cwd,
        limit=STREAM_LIMIT,
    )
    stderr_task = asyncio.create_task(_drain_tail(proc.stderr))
    assert proc.stdout is not None
    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            obj = parse_stream_line(raw.decode("utf-8", "replace"))
            if obj is not None:
                mapper.feed(obj)
    except AmberError:
        proc.kill()
        await proc.wait()
        stderr_task.cancel()
        raise
    exit_code = await proc.wait()
    stderr_tail = await stderr_task
    status: Status | None = request.status_override
    if status is None and (exit_code != 0 or not mapper.result_seen):
        status = "failure"
    trace = mapper.build(trace_id=new_id("t"), created_at=started, status_override=status)
    return StreamRecording(trace, exit_code, mapper.denied, mapper.warnings, stderr_tail)


class ClaudeStreamAdapter:
    name = "claude-stream"

    async def record(self, request: RecordRequest) -> Trace:
        return (await run_claude(request)).trace
