"""``agent-sdk`` adapter (optional extra ``[sdk]``): run ``claude_agent_sdk.query`` in-process
and feed its message dataclasses, converted to the stream-json dict shapes, to the shared
mapper. The SDK is imported lazily; conversion works on duck-typed objects so it is testable
without the SDK.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from amber.adapters.base import RecordRequest
from amber.adapters.claude_cli_flags import inputs_suffix, prepare_claude_argv
from amber.adapters.claude_messages import ClaudeMessageMapper
from amber.errors import AmberError
from amber.trace.models import Status, ToolCall, Trace
from amber.util.time import new_id, utcnow


def block_to_dict(block: Any) -> dict[str, Any] | None:
    name = type(block).__name__
    if name == "TextBlock":
        return {"type": "text", "text": block.text}
    if name == "ToolUseBlock":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if name == "ToolResultBlock":
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": bool(getattr(block, "is_error", False)),
        }
    if name == "ThinkingBlock":
        return {"type": "thinking"}
    if isinstance(block, dict):
        return cast(dict[str, Any], block)
    return None


def _content(blocks: Any) -> Any:
    if isinstance(blocks, str):
        return blocks
    out: list[dict[str, Any]] = []
    for b in cast(list[Any], blocks or []):
        d = block_to_dict(b)
        if d is not None:
            out.append(d)
    return out


def sdk_message_to_dict(msg: Any) -> dict[str, Any] | None:
    """SDK dataclass → stream-json-shaped dict (``None`` for ignored message kinds)."""
    name = type(msg).__name__
    if name == "AssistantMessage":
        return {
            "type": "assistant",
            "parent_tool_use_id": getattr(msg, "parent_tool_use_id", None),
            "message": {
                "content": _content(msg.content),
                "model": getattr(msg, "model", None),
                "usage": getattr(msg, "usage", None),
            },
        }
    if name == "UserMessage":
        return {
            "type": "user",
            "parent_tool_use_id": getattr(msg, "parent_tool_use_id", None),
            "message": {"content": _content(msg.content)},
        }
    if name == "SystemMessage":
        data = getattr(msg, "data", None) or {}
        return {"type": "system", "subtype": msg.subtype, **cast(dict[str, Any], data)}
    if name == "ResultMessage":
        return {
            "type": "result",
            "subtype": msg.subtype,
            "is_error": bool(getattr(msg, "is_error", False)),
            "result": getattr(msg, "result", None),
            "session_id": getattr(msg, "session_id", None),
            "duration_ms": getattr(msg, "duration_ms", None),
            "num_turns": getattr(msg, "num_turns", None),
            "total_cost_usd": getattr(msg, "total_cost_usd", None),
            "usage": getattr(msg, "usage", None),
        }
    return None


def load_mcp_servers(path: Any) -> dict[str, Any]:
    """Read a Claude ``--mcp-config`` JSON file into the SDK's ``mcp_servers`` mapping."""
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data.get("mcpServers") or {})


async def record_sdk(
    request: RecordRequest, *, on_call: Callable[[ToolCall], None] | None = None
) -> Trace:
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError as e:
        raise AmberError(
            "claude-agent-sdk is not installed",
            hint="Install the extra: uv pip install 'amber-compiler[sdk]' (needs the claude CLI).",
        ) from e
    prepared = prepare_claude_argv(
        request.agent_command,
        prompt_suffix=inputs_suffix(request.inputs) if request.inject_inputs else None,
    )
    options = ClaudeAgentOptions(
        cwd=str(request.cwd) if request.cwd else None,
        mcp_servers=load_mcp_servers(request.mcp_config),
        allowed_tools=list(request.allow),
        permission_mode=cast(Any, request.permission_mode) if request.permission_mode else None,
    )
    mapper = ClaudeMessageMapper(
        workflow=request.workflow,
        adapter="agent-sdk",
        task_prompt=prepared.prompt,
        inputs=request.inputs,
        tags=request.tags,
        clock=utcnow,
        on_call=on_call,
    )
    started = utcnow()
    async for msg in query(prompt=prepared.prompt, options=options):
        d = sdk_message_to_dict(msg)
        if d is not None:
            mapper.feed(d)
    status: Status | None = request.status_override
    if status is None and not mapper.result_seen:
        status = "failure"
    return mapper.build(trace_id=new_id("t"), created_at=started, status_override=status)


class AgentSdkAdapter:
    name = "agent-sdk"

    async def record(self, request: RecordRequest) -> Trace:
        return await record_sdk(request)
