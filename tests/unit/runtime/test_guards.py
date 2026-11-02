"""Guard trigger table (docs/05-runtime.md §3) and the result-shape validator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel

from amber.compile.manifest import Budgets, Manifest, StepSummary, ToolEntry
from amber.runtime.guards import PROCEED, GuardEvent, Guards, GuardStop, shape_problems
from amber.trace.models import Usage


def _manifest(**extra: Any) -> Manifest:
    base: dict[str, Any] = {
        "workflow": "wf",
        "version": 1,
        "created_at": datetime(2026, 11, 2, tzinfo=UTC),
        "source_traces": ["t1"],
        "skeleton_hash": "x",
        "tools": [
            ToolEntry(canonical="shop.list", server="shop", name="list", effect="read"),
            ToolEntry(canonical="slack.post", server="slack", name="post", effect="write"),
            ToolEntry(canonical="shop.mystery", server="shop", name="mystery", effect="unknown"),
        ],
        "steps": [
            StepSummary(idx=0, tool="shop.list", kind="deterministic"),
            StepSummary(idx=1, tool="slack.post", kind="judgment", effect="write"),
        ],
        "budgets": Budgets(max_calls=3, max_llm_tokens=50, wall_clock_s=600),
        "result_schemas": {
            0: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "total": {"type": "number"}},
                    "required": ["id", "total"],
                },
            }
        },
        "placeholder_results": {1: {"ok": True, "ts": "123.4"}},
        "volatile_paths": {1: ["$.ts"]},
    }
    base.update(extra)
    return Manifest.model_validate(base)


def test_allowlist_and_budget() -> None:
    g = Guards(_manifest())
    with pytest.raises(GuardStop) as info:
        g.before_call(0, "shop.nope", {})
    assert info.value.kind == "allowlist" and info.value.exit_code == 4
    assert g.before_call(0, "shop.list", {}) is PROCEED
    assert g.before_call(0, "shop.list", {}) is PROCEED
    assert g.before_call(0, "shop.list", {}) is PROCEED
    with pytest.raises(GuardStop, match="call budget"):
        g.before_call(0, "shop.list", {})


def test_write_effect_dry_run_placeholder_and_unknown_is_write() -> None:
    g = Guards(_manifest(), allow_writes=False)
    placeholder = g.before_call(1, "slack.post", {"text": "x"})
    assert placeholder == {"ok": True, "ts": "<volatile>"}
    assert g.events[-1].guard == "write_effect" and g.events[-1].severity == "info"
    assert g.before_call(2, "shop.mystery", {}) is None  # unknown effect: dry-run, no placeholder
    allowed = Guards(_manifest(), allow_writes=True)
    assert allowed.before_call(1, "slack.post", {"text": "x"}) is PROCEED


def test_result_shape_hard_soft() -> None:
    g = Guards(_manifest())
    g.after_call(0, "shop.list", [{"id": "o1", "total": 3.5}], is_error=False)
    assert g.events == []
    g.after_call(0, "shop.list", [{"id": "o1", "total": 3, "extra": 1}], is_error=False)
    assert g.events[-1].severity == "soft" and "extra key" in g.events[-1].detail
    g.after_call(0, "shop.list", [], is_error=False)
    assert g.events[-1].severity == "soft" and "empty list" in g.events[-1].detail
    with pytest.raises(GuardStop) as info:
        g.after_call(0, "shop.list", [{"id": "o1", "amount": 3.5}], is_error=False)
    assert info.value.kind == "result_shape" and "missing key 'total'" in info.value.detail
    with pytest.raises(GuardStop, match="type"):
        g.after_call(0, "shop.list", {"not": "a list"}, is_error=False)


def test_error_guard() -> None:
    g = Guards(_manifest())
    with pytest.raises(GuardStop) as info:
        g.after_call(0, "shop.list", None, is_error=True, error_text="boom")
    assert info.value.kind == "error" and "boom" in info.value.detail


class Out(BaseModel):
    text: str
    confidence: float


def test_llm_guards() -> None:
    g = Guards(_manifest(), min_confidence=0.5)
    g.after_llm(1, Out(text="a", confidence=0.9), Usage(input_tokens=10, output_tokens=5))
    assert g.events == []
    g.after_llm(1, Out(text="a", confidence=0.2), Usage(input_tokens=10, output_tokens=5))
    assert g.events[-1].guard == "llm_output" and g.events[-1].severity == "soft"
    with pytest.raises(GuardStop, match="llm token budget"):
        g.after_llm(1, Out(text="a", confidence=0.9), Usage(input_tokens=30, output_tokens=5))
    stop = g.llm_invalid(1, "bad json")
    assert stop.kind == "llm_output" and "validation" in stop.detail


def test_schema_drift_hard_stops_before_running() -> None:
    g = Guards(_manifest())
    g.drift([GuardEvent("schema_drift", "soft", "description changed", 0)])
    assert len(g.events) == 1
    with pytest.raises(GuardStop) as info:
        g.drift([GuardEvent("schema_drift", "hard", "tool missing", 0)])
    assert info.value.kind == "schema_drift"


def test_wall_clock_budget() -> None:
    g = Guards(_manifest(), wall_clock_s=0.0)
    with pytest.raises(GuardStop, match="wall-clock"):
        g.before_call(0, "shop.list", {})


@pytest.mark.parametrize(
    ("schema", "value", "expected"),
    [
        ({"type": "integer"}, 3, []),
        ({"type": "number"}, 3, []),
        ({"type": "string"}, 3, [("hard", "$: type integer, recorded string")]),
        ({"anyOf": [{"type": "string"}, {"type": "null"}]}, None, []),
        (
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
            {"a": 1},
            [("hard", "$.a: type integer, recorded string")],
        ),
    ],
)
def test_shape_problems(
    schema: dict[str, Any], value: Any, expected: list[tuple[str, str]]
) -> None:
    assert shape_problems(schema, value) == expected
