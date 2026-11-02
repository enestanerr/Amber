"""Canonical trace models, ``schema_version`` 1 (``docs/02-trace-format.md`` §1).

Invariants enforced by :meth:`Trace.check` (also run on validation):

* ``events[i].idx == i``;
* every ``ToolCall.tool`` has a matching ``ToolSchema.canonical`` in ``tools``;
* ``call_id`` is unique within a trace;
* ``ToolSchema.hash`` equals ``sha256(canonical(input_schema))[:16]``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from amber.trace.canonical import Effect, ResultKind, canonical_tool_name
from amber.util.hashing import digest

AdapterName = Literal["claude-stream", "claude-transcript", "agent-sdk", "mcp-proxy"]
Origin = Literal["record", "import", "fallback"]
Status = Literal["success", "failure", "unknown"]


class TraceInvariantError(ValueError):
    """A trace violates one of the documented invariants."""


def schema_hash(input_schema: dict[str, Any]) -> str:
    """``sha256(canonical_json(input_schema))[:16]``."""
    return digest(input_schema, length=16)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(_Model):
    input_tokens: int = 0  # non-cached input
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens + other.cache_creation_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class ToolSchema(_Model):
    server: str  # "shop", "slack", "builtin"
    name: str  # "list_orders", "Bash"
    canonical: str = ""  # "shop.list_orders"; filled from server + name when empty
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    effect: Effect = "unknown"
    hash: str = ""  # filled when empty; checked when given

    @model_validator(mode="after")
    def _fill_derived(self) -> ToolSchema:
        if not self.canonical:
            self.canonical = canonical_tool_name(self.server, self.name)
        if not self.hash:
            self.hash = schema_hash(self.input_schema)
        return self


class ToolCall(_Model):
    kind: Literal["tool_call"] = "tool_call"
    idx: int
    call_id: str
    parent_call_id: str | None = None
    tool: str  # canonical "server.tool"
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    result_kind: ResultKind = "empty"
    is_error: bool = False
    started_at: datetime | None = None
    duration_ms: int | None = None


class Thought(_Model):
    kind: Literal["thought"] = "thought"
    idx: int
    text: str


class Note(_Model):
    kind: Literal["note"] = "note"
    idx: int
    source: Literal["user", "system", "adapter"]
    text: str


Event = Annotated[ToolCall | Thought | Note, Field(discriminator="kind")]


class Outcome(_Model):
    status: Status = "unknown"
    final_text: str = ""
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None


class Trace(_Model):
    schema_version: Literal[1] = 1
    id: str
    workflow: str
    created_at: datetime
    adapter: AdapterName
    harness: dict[str, str] = Field(default_factory=dict)
    task_prompt: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    tools: list[ToolSchema] = Field(default_factory=list[ToolSchema])
    events: list[Event]
    outcome: Outcome
    origin: Origin = "record"
    resumed_from_step: int | None = None
    prefix_calls: list[ToolCall] = Field(default_factory=list[ToolCall])
    session_id: str | None = None
    tags: list[str] = Field(default_factory=list)

    @property
    def calls(self) -> list[ToolCall]:
        """Tool calls in event order."""
        return [e for e in self.events if isinstance(e, ToolCall)]

    @property
    def thoughts(self) -> list[Thought]:
        return [e for e in self.events if isinstance(e, Thought)]

    def tool_schema(self, canonical: str) -> ToolSchema | None:
        for t in self.tools:
            if t.canonical == canonical:
                return t
        return None

    def check(self) -> None:
        """Raise :class:`TraceInvariantError` listing every violated invariant."""
        problems: list[str] = []
        for i, event in enumerate(self.events):
            if event.idx != i:
                problems.append(f"events[{i}].idx is {event.idx}")
        known = {t.canonical for t in self.tools}
        seen: set[str] = set()
        for call in self.calls:
            if call.tool not in known:
                problems.append(f"call {call.call_id!r} uses {call.tool!r} without a ToolSchema")
            if call.call_id in seen:
                problems.append(f"duplicate call_id {call.call_id!r}")
            seen.add(call.call_id)
        for tool in self.tools:
            expected = schema_hash(tool.input_schema)
            if tool.hash != expected:
                problems.append(f"tool {tool.canonical}: hash {tool.hash} != {expected}")
        if problems:
            raise TraceInvariantError("; ".join(problems))

    @model_validator(mode="after")
    def _validate_invariants(self) -> Trace:
        self.check()
        return self
