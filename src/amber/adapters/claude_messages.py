"""Shared mapper: Claude message dicts → trace events (``docs/02-trace-format.md`` §3 A1/A2/A3).

All three Claude adapters (stream-json, transcripts, Agent SDK) feed the same dict shapes:
``{"type": "system"|"assistant"|"user"|"result", ...}``.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from amber.errors import AmberError
from amber.trace.canonical import (
    BUILTIN_SERVER,
    builtin_effect,
    canonical_tool_name,
    normalize_result,
)
from amber.trace.models import (
    AdapterName,
    Event,
    Note,
    Origin,
    Outcome,
    Status,
    Thought,
    ToolCall,
    ToolSchema,
    Trace,
    Usage,
)
from amber.util.logging import get_logger

log = get_logger("adapters.claude")

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_DENIED_RE = re.compile(r"permission|not allowed|denied", re.IGNORECASE)


class PermissionPromptError(AmberError):
    """A ``permission_request`` event appeared: the run would block on a prompt."""

    exit_code = 1


def strip_ansi(line: str) -> str:
    return _ANSI_RE.sub("", line)


def parse_stream_line(line: str) -> dict[str, Any] | None:
    """One NDJSON line → dict, or ``None`` for blank / non-JSON lines (debug-logged)."""
    text = strip_ansi(line).strip()
    if not text:
        return None
    try:
        obj: Any = json.loads(text)
    except ValueError:
        log.debug("skipping non-JSON line: %.80s", text)
        return None
    if not isinstance(obj, dict):
        log.debug("skipping non-object line: %.80s", text)
        return None
    return cast(dict[str, Any], obj)


def parse_claude_tool_name(name: str) -> tuple[str, str]:
    """``mcp__shop__list_orders`` → ``("shop", "list_orders")``; anything else → builtin."""
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            return parts[1], parts[2]
        return "mcp", name[len("mcp__") :]
    return BUILTIN_SERVER, name


def usage_from_payload(payload: dict[str, Any] | None) -> Usage:
    """Map Claude usage keys (``cache_read_input_tokens`` …) onto :class:`Usage`."""
    if not payload:
        return Usage()
    return Usage(
        input_tokens=int(payload.get("input_tokens") or 0),
        cache_read_tokens=int(payload.get("cache_read_input_tokens") or 0),
        cache_creation_tokens=int(payload.get("cache_creation_input_tokens") or 0),
        output_tokens=int(payload.get("output_tokens") or 0),
    )


def _blocks(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    return [cast(dict[str, Any], b) for b in cast(list[Any], content) if isinstance(b, dict)]


class ClaudeMessageMapper:
    """Accumulates events; :meth:`build` returns the :class:`Trace`."""

    def __init__(
        self,
        *,
        workflow: str,
        adapter: AdapterName,
        task_prompt: str = "",
        inputs: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        origin: Origin = "record",
        clock: Callable[[], datetime] | None = None,
        on_call: Callable[[ToolCall], None] | None = None,
    ) -> None:
        self.workflow = workflow
        self.adapter: AdapterName = adapter
        self.task_prompt = task_prompt
        self.inputs: dict[str, Any] = dict(inputs or {})
        self.tags = list(tags or [])
        self.origin: Origin = origin
        self.clock = clock
        self.on_call = on_call
        self.events: list[Event] = []
        self.tools: dict[str, ToolSchema] = {}
        self.harness: dict[str, str] = {"name": "claude-code"}
        self.session_id: str | None = None
        self.usage_sum = Usage()
        self.result_usage: Usage | None = None
        self.result_seen = False
        self.status: Status = "unknown"
        self.final_text = ""
        self.cost_usd: float | None = None
        self.duration_ms: int | None = None
        self.num_turns: int | None = None
        self.denied: list[str] = []
        self.warnings: list[str] = []
        self._pending: dict[str, tuple[ToolCall, float]] = {}
        self._last_text = ""

    # -- dispatch ------------------------------------------------------------
    def feed(self, obj: dict[str, Any]) -> None:
        kind = obj.get("type")
        if kind == "system":
            subtype = obj.get("subtype")
            if subtype == "init":
                self.feed_init(obj)
            elif subtype == "compact_boundary":
                self.note("system", "compaction")
            else:
                log.debug("ignoring system event %s", subtype)
        elif kind == "assistant":
            message = obj.get("message")
            self.feed_assistant(
                cast(dict[str, Any], message) if isinstance(message, dict) else {},
                parent_tool_use_id=obj.get("parent_tool_use_id"),
            )
        elif kind == "user":
            message = obj.get("message")
            self.feed_user(cast(dict[str, Any], message) if isinstance(message, dict) else {})
        elif kind == "result":
            self.feed_result(obj)
        elif kind == "permission_request":
            raise PermissionPromptError(
                "the agent asked for permission; recordings must run unattended",
                hint="Pre-approve tools with `--allow 'mcp__<server>__*'` or "
                "`--permission-mode acceptEdits`.",
            )
        else:
            log.debug("ignoring event type %s", kind)

    # -- pieces --------------------------------------------------------------
    def feed_init(self, obj: dict[str, Any]) -> None:
        self.session_id = obj.get("session_id") or self.session_id
        if obj.get("model"):
            self.harness["model"] = str(obj["model"])
        if obj.get("version"):
            self.harness["version"] = str(obj["version"])
        tools = obj.get("tools")
        if isinstance(tools, list):
            for name in cast(list[Any], tools):
                if isinstance(name, str):
                    self._stub_tool(name)

    def feed_assistant(
        self,
        message: dict[str, Any],
        *,
        parent_tool_use_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        if message.get("model") and "model" not in self.harness:
            self.harness["model"] = str(message["model"])
        usage = message.get("usage")
        self.usage_sum = self.usage_sum + usage_from_payload(
            cast(dict[str, Any], usage) if isinstance(usage, dict) else None
        )
        content = message.get("content")
        if isinstance(content, str):
            self._thought(content)
            return
        for block in _blocks(content):
            btype = block.get("type")
            if btype == "text":
                self._thought(str(block.get("text") or ""))
            elif btype == "tool_use":
                self._open_call(block, parent_tool_use_id, timestamp)

    def feed_user(self, message: dict[str, Any], *, timestamp: datetime | None = None) -> None:
        content = message.get("content")
        if isinstance(content, str):
            if not self.task_prompt:
                self.task_prompt = content
            self.note("user", content)
            return
        for block in _blocks(content):
            if block.get("type") == "tool_result":
                self._close_call(block)
            elif block.get("type") == "text":
                text = str(block.get("text") or "")
                if not self.task_prompt:
                    self.task_prompt = text
                self.note("user", text)

    def feed_result(self, obj: dict[str, Any]) -> None:
        self.result_seen = True
        ok = obj.get("subtype") == "success" and not obj.get("is_error", False)
        self.status = "success" if ok else "failure"
        result = obj.get("result")
        self.final_text = result if isinstance(result, str) else (self._last_text or "")
        usage = obj.get("usage")
        if isinstance(usage, dict):
            self.result_usage = usage_from_payload(cast(dict[str, Any], usage))
        self.cost_usd = obj.get("total_cost_usd")
        self.duration_ms = obj.get("duration_ms")
        self.num_turns = obj.get("num_turns")
        self.session_id = obj.get("session_id") or self.session_id

    def note(self, source: str, text: str) -> None:
        if source == "user":
            self.events.append(Note(idx=len(self.events), source="user", text=text))
        elif source == "system":
            self.events.append(Note(idx=len(self.events), source="system", text=text))
        else:
            self.events.append(Note(idx=len(self.events), source="adapter", text=text))

    # -- internals -----------------------------------------------------------
    def _thought(self, text: str) -> None:
        if text.strip():
            self._last_text = text
            self.events.append(Thought(idx=len(self.events), text=text))

    def _stub_tool(self, raw_name: str) -> ToolSchema:
        server, tool = parse_claude_tool_name(raw_name)
        canonical = canonical_tool_name(server, tool)
        if canonical not in self.tools:
            effect = builtin_effect(tool) if server == BUILTIN_SERVER else "unknown"
            self.tools[canonical] = ToolSchema(server=server, name=tool, effect=effect)
        return self.tools[canonical]

    def _open_call(
        self, block: dict[str, Any], parent: str | None, timestamp: datetime | None
    ) -> None:
        schema = self._stub_tool(str(block.get("name") or "unknown"))
        call_id = str(block.get("id") or f"call_{len(self.events)}")
        raw_args = block.get("input")
        args = cast(dict[str, Any], raw_args) if isinstance(raw_args, dict) else {}
        call = ToolCall(
            idx=len(self.events),
            call_id=call_id,
            parent_call_id=parent,
            tool=schema.canonical,
            args=dict(args),
            started_at=timestamp or (self.clock() if self.clock else None),
        )
        self.events.append(call)
        self._pending[call_id] = (call, time.monotonic())
        if self.on_call:
            self.on_call(call)

    def _close_call(self, block: dict[str, Any]) -> None:
        call_id = str(block.get("tool_use_id") or "")
        entry = self._pending.pop(call_id, None)
        if entry is None:
            log.debug("tool_result for unknown call %s", call_id)
            return
        call, t0 = entry
        value, kind = normalize_result(block.get("content"))
        call.result = value
        call.result_kind = kind
        call.is_error = bool(block.get("is_error", False))
        if self.clock:
            call.duration_ms = int((time.monotonic() - t0) * 1000)
        if call.is_error and isinstance(value, str) and _DENIED_RE.search(value):
            self.denied.append(call.tool)

    # -- output --------------------------------------------------------------
    def build(
        self, *, trace_id: str, created_at: datetime, status_override: Status | None = None
    ) -> Trace:
        """Assemble the trace. Unclosed calls stay with an empty result and a warning."""
        for call, _ in self._pending.values():
            self.warnings.append(f"call {call.call_id} ({call.tool}) never received a result")
        for event in self.events:
            if isinstance(event, ToolCall) and event.tool not in self.tools:
                server, _, tool = event.tool.partition(".")
                self.tools[event.tool] = ToolSchema(server=server, name=tool)
        status: Status = status_override or self.status
        if not self.result_seen and not self.final_text:
            self.final_text = self._last_text  # transcripts: last assistant text
        outcome = Outcome(
            status=status,
            final_text=self.final_text,
            usage=self.result_usage or self.usage_sum,
            cost_usd=self.cost_usd,
            duration_ms=self.duration_ms,
            num_turns=self.num_turns,
        )
        return Trace(
            id=trace_id,
            workflow=self.workflow,
            created_at=created_at,
            adapter=self.adapter,
            harness=dict(self.harness),
            task_prompt=self.task_prompt,
            inputs=dict(self.inputs),
            tools=sorted(self.tools.values(), key=lambda t: t.canonical),
            events=list(self.events),
            outcome=outcome,
            origin=self.origin,
            session_id=self.session_id,
            tags=list(self.tags),
        )
