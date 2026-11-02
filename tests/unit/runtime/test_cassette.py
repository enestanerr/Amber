"""Cassette divergence cases (docs/08-testing.md P5): wrong tool, wrong arg, extra call,
missing call, unordered mode, volatile masking on both sides, recorded llm validation,
resumed-trace prefix preload."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from amber.compile.manifest import Budgets, FieldSpec, LlmStep, Manifest
from amber.runtime.backends.cassette import CassetteBackend, ReplayDivergence
from amber.trace.models import ToolCall, Trace
from tests.unit.align.conftest import mk_trace


def _manifest(trace: Trace, **extra: Any) -> Manifest:
    base: dict[str, Any] = {
        "workflow": trace.workflow,
        "version": 1,
        "created_at": datetime(2026, 11, 2, tzinfo=UTC),
        "source_traces": [trace.id],
        "skeleton_hash": "x",
        "budgets": Budgets(max_calls=10, max_llm_tokens=100, wall_clock_s=10),
        "trace_calls": {trace.id: [c.idx for c in trace.calls]},
    }
    base.update(extra)
    return Manifest.model_validate(base)


def _trace() -> Trace:
    return mk_trace(
        "t0",
        [
            ("shop.week", {"week": "W42"}, {"start": "2026-10-12"}),
            ("shop.list", {"since": "2026-10-12", "ts": "11:00"}, [{"id": 1}]),
            ("slack.post", {"text": "hello"}, {"ok": True}),
        ],
    )


def test_happy_path_returns_recorded_results_and_tracks_consumption() -> None:
    t = _trace()
    ctx = CassetteBackend(t, _manifest(t))
    assert ctx.tool("shop.week", week="W42") == {"start": "2026-10-12"}
    r = ctx.tool("shop.list", since="2026-10-12", ts="11:00")
    assert r == [{"id": 1}]
    r.append("mutated")  # deep copy: the cassette is not affected
    assert ctx.expected[1].result == [{"id": 1}]
    assert ctx.leftover and ctx.consumed == 2
    ctx.tool("slack.post", text="hello")
    assert ctx.leftover == [] and ctx.consumed == 3
    assert ctx.now() == t.created_at
    ctx.log("x")
    assert ctx.logs == ["x"]


def test_wrong_tool() -> None:
    t = _trace()
    ctx = CassetteBackend(t, _manifest(t))
    with pytest.raises(ReplayDivergence) as info:
        ctx.tool("shop.list", since="2026-10-12", ts="11:00")
    d = info.value
    assert d.step_idx == 0 and d.expected == ("shop.week", {"week": "W42"})
    assert (
        "order differs" in (d.hint or "")
        and "-tool: shop.week" in d.diff
        and "+tool: shop.list" in d.diff
    )


def test_wrong_arg_hint_names_the_path() -> None:
    t = _trace()
    ctx = CassetteBackend(t, _manifest(t))
    with pytest.raises(ReplayDivergence) as info:
        ctx.tool("shop.week", week="W43")
    assert "argument differs at $.week" in (info.value.hint or "")
    assert info.value.exit_code == 3


def test_extra_call_after_end() -> None:
    t = _trace()
    ctx = CassetteBackend(t, _manifest(t))
    for call in t.calls:
        ctx.tool(call.tool, **call.args)
    with pytest.raises(ReplayDivergence, match="<end of recording>") as info:
        ctx.tool("shop.extra")
    assert "conditional predicate" in (info.value.hint or "")


def test_volatile_paths_are_masked_on_both_sides() -> None:
    t = _trace()
    m = _manifest(t, volatile_paths={1: ["$.ts"]})
    ctx = CassetteBackend(t, m)
    ctx.tool("shop.week", week="W42")
    assert ctx.tool("shop.list", since="2026-10-12", ts="17:45") == [{"id": 1}]
    with pytest.raises(ReplayDivergence):
        ctx.tool("slack.post", text="hello  ", extra=1)


def test_unordered_mode_matches_any_pending_call() -> None:
    t = _trace()
    ctx = CassetteBackend(t, _manifest(t), strict_order=False)
    assert ctx.tool("slack.post", text="hello") == {"ok": True}
    assert ctx.tool("shop.week", week="W42") == {"start": "2026-10-12"}
    with pytest.raises(ReplayDivergence) as info:
        ctx.tool("shop.nope")
    assert "unordered" in (info.value.hint or "")
    assert [c.tool for c in ctx.leftover] == ["shop.list"]


def test_pruned_calls_are_skipped_via_trace_calls() -> None:
    t = mk_trace(
        "t0",
        [
            ("shop.list", {}, [1]),
            ("builtin.Read", {"file_path": "x"}, "txt"),
            ("slack.post", {"text": "hi"}, {"ok": 1}),
        ],
    )
    kept = [t.calls[0].idx, t.calls[2].idx]
    ctx = CassetteBackend(t, _manifest(t, trace_calls={"t0": kept}))
    assert [c.tool for c in ctx.expected] == ["shop.list", "slack.post"]
    ctx.tool("shop.list")
    ctx.tool("slack.post", text="hi")
    assert ctx.leftover == []


class SummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    confidence: float


class GateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    go: bool
    reason: str
    confidence: float


def test_recorded_llm_values_and_validation() -> None:
    t = _trace()
    llm = LlmStep(
        id="step2",
        step_idx=2,
        schema_name="Step2Out",
        fields=[FieldSpec(name="text")],
        context_keys=["inputs", "r1"],
        field_paths={"text": "$.text"},
    )
    gate = LlmStep(
        id="gate2", step_idx=2, kind="gate", schema_name="Gate2Out", context_keys=["inputs"]
    )
    m = _manifest(t, llm_steps=[llm, gate])
    seen: list[str] = []
    ctx = CassetteBackend(t, m, on_llm=lambda sid, *_: seen.append(sid))
    out = ctx.llm("step2", SummaryOut, "prompt", {"inputs": {}, "r1": [1]})
    assert out.text == "hello" and out.confidence == 1.0 and seen == ["step2"]
    g = ctx.llm("gate2", GateOut, "p", {"inputs": {}})
    assert g.go is True and g.reason == "recorded"
    with pytest.raises(ReplayDivergence, match="unexpected context keys"):
        ctx.llm("step2", SummaryOut, "prompt", {"inputs": {}, "r9": 1})
    with pytest.raises(ReplayDivergence, match="not in the manifest"):
        ctx.llm("step7", SummaryOut, "prompt", {})

    class Narrow(BaseModel):
        model_config = ConfigDict(extra="forbid")
        text: int
        confidence: float

    with pytest.raises(ReplayDivergence, match="does not satisfy Narrow"):
        ctx.llm("step2", Narrow, "prompt", {"inputs": {}})
    assert len(ctx.llm_calls) == 2


def test_resumed_trace_preloads_prefix_calls() -> None:
    prefix = [
        ToolCall(
            idx=0,
            call_id="p0",
            tool="shop.week",
            args={"week": "W42"},
            result={"start": "s"},
            result_kind="json",
        )
    ]
    tail = mk_trace("t_resumed", [("slack.post", {"text": "hi"}, {"ok": 1})])
    resumed = tail.model_copy(
        update={"prefix_calls": prefix, "resumed_from_step": 1, "origin": "fallback"}
    )
    resumed.tools.append(prefix[0] and tail.tools[0])  # keep invariants trivial
    ctx = CassetteBackend(
        resumed, _manifest(resumed, trace_calls={"t_resumed": [-1, tail.calls[0].idx]})
    )
    assert [c.tool for c in ctx.expected] == ["shop.week", "slack.post"]
    assert ctx.tool("shop.week", week="W42") == {"start": "s"}
    assert ctx.tool("slack.post", text="hi") == {"ok": 1}
    assert ctx.leftover == []
