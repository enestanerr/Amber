"""Trace model invariants and schema hashing (docs/02-trace-format.md §1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from amber.trace.models import Outcome, ToolCall, ToolSchema, Trace, TraceInvariantError, Usage
from amber.util.hashing import digest


def _trace(**overrides: object) -> Trace:
    base: dict[str, object] = {
        "id": "t_x",
        "workflow": "wf",
        "created_at": datetime(2026, 11, 2, tzinfo=UTC),
        "adapter": "mcp-proxy",
        "tools": [ToolSchema(server="shop", name="ping")],
        "events": [
            ToolCall(idx=0, call_id="c1", tool="shop.ping", args={}, result=1, result_kind="json")
        ],
        "outcome": Outcome(status="success"),
    }
    base.update(overrides)
    return Trace.model_validate(base)


def test_tool_schema_fills_canonical_and_hash() -> None:
    t = ToolSchema(server="shop", name="list_orders", input_schema={"type": "object"})
    assert t.canonical == "shop.list_orders"
    assert t.hash == digest({"type": "object"})
    assert len(t.hash) == 16


def test_fixture_traces_validate(weekly_traces: list[Trace]) -> None:
    assert [len(t.calls) for t in weekly_traces] == [5, 6, 5]
    assert all(t.outcome.status == "success" for t in weekly_traces)
    assert weekly_traces[1].calls[4].tool == "builtin.Read"


def test_idx_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="idx is 5"):
        _trace(events=[ToolCall(idx=5, call_id="c1", tool="shop.ping", args={})])


def test_unknown_tool_rejected() -> None:
    with pytest.raises(ValidationError, match="without a ToolSchema"):
        _trace(events=[ToolCall(idx=0, call_id="c1", tool="shop.nope", args={})])


def test_duplicate_call_id_rejected() -> None:
    events = [
        ToolCall(idx=0, call_id="c1", tool="shop.ping", args={}),
        ToolCall(idx=1, call_id="c1", tool="shop.ping", args={}),
    ]
    with pytest.raises(ValidationError, match="duplicate call_id"):
        _trace(events=events)


def test_hash_is_always_recomputed() -> None:
    stale = ToolSchema(server="shop", name="ping", hash="deadbeefdeadbeef")
    assert stale.hash == digest({})  # a stored hash never wins over the schema
    _trace(tools=[stale]).check()


def test_check_is_callable_directly(t1: Trace) -> None:
    t1.check()
    t1.events[0].idx = 7
    with pytest.raises(TraceInvariantError):
        t1.check()


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        ToolCall(idx=0, call_id="c", tool="a.b", args={}, bogus=1)  # type: ignore[call-arg]


def test_usage_addition_and_total() -> None:
    u = Usage(input_tokens=1, cache_read_tokens=2, cache_creation_tokens=3, output_tokens=4)
    s = u + Usage(input_tokens=10)
    assert s.total_input == 16 and s.output_tokens == 4


def test_json_roundtrip(t1: Trace) -> None:
    again = Trace.model_validate_json(t1.model_dump_json())
    assert again == t1
