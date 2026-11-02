"""Synthesizer with the recorded backend: fills, repair loop, downgrade, failure."""

from __future__ import annotations

import json
from typing import Any

import pytest

from amber.align.models import Skeleton
from amber.align.skeleton import build_skeleton
from amber.compile.holes import collect_holes
from amber.compile.synthesizer import (
    CompileError,
    JudgmentFill,
    TransformFill,
    synthesize,
    user_message,
)
from amber.models.recorded import RecordedBackend
from amber.trace.models import Trace, Usage
from tests.unit.align.conftest import mk_trace
from tests.unit.compile.conftest import FIXTURES


def _limit_traces() -> list[Trace]:
    return [
        mk_trace(f"t{i}", [("shop.count", {}, {"count": n}), ("shop.top", {"limit": n * 2}, [])])
        for i, n in enumerate([2, 5, 7])
    ]


class Recording:
    """Wrap a backend and keep the user messages it saw."""

    def __init__(self, inner: RecordedBackend) -> None:
        self.inner = inner
        self.messages: list[dict[str, Any]] = []
        self.name = "recording"

    async def complete(
        self, schema: Any, system: str, user: str, *, model: str, max_tokens: int
    ) -> Any:
        self.messages.append(json.loads(user))
        return await self.inner.complete(schema, system, user, model=model, max_tokens=max_tokens)


async def test_weekly_holes_are_filled(
    weekly_skeleton: Skeleton, weekly_traces: list[Trace]
) -> None:
    holes = collect_holes(weekly_skeleton, weekly_traces)
    backend = RecordedBackend(directory=FIXTURES / "model_fills")
    warnings: list[str] = []
    outcomes = await synthesize(holes, weekly_skeleton, backend, model="m", warnings=warnings)
    assert [(o.hole.id, o.accepted, o.attempts) for o in outcomes] == [
        ("transform_step3", True, 1),
        ("judgment_step4", True, 1),
    ]
    assert isinstance(outcomes[0].fill, TransformFill) and isinstance(
        outcomes[1].fill, JudgmentFill
    )
    assert warnings == [] and backend.calls == [("transform_step3", 0), ("judgment_step4", 0)]
    assert outcomes[0].usage.input_tokens > 0


async def test_repair_loop_feeds_back_the_mismatch() -> None:
    traces = _limit_traces()
    skeleton = build_skeleton(traces)
    holes = collect_holes(skeleton, traces)
    assert [h.id for h in holes] == ["transform_step1"]
    backend = Recording(RecordedBackend(directory=FIXTURES / "model_fills"))
    outcomes = await synthesize(holes, skeleton, backend, model="m", warnings=[])
    assert outcomes[0].accepted and outcomes[0].attempts == 2
    first, second = backend.messages
    assert first["attempt"] == 0 and "previous_attempt" not in first
    assert second["attempt"] == 1 and second["previous_attempt"]["python_source"]
    assert second["mismatch"]["cases"][0]["trace"] == "t0"
    assert (
        "expected" in second["mismatch"]["cases"][0] and "actual" in second["mismatch"]["cases"][0]
    )


async def test_transform_is_downgraded_to_judgment() -> None:
    traces = _limit_traces()
    skeleton = build_skeleton(traces)
    holes = collect_holes(skeleton, traces)
    wrong = {
        "python_source": "def transform_step1(inputs, r0):\n    return {'limit': 1}\n",
        "rationale": "nope",
    }
    fills = {
        "transform_step1": [wrong, wrong],
        "judgment_step1": [
            {
                "prompt": "Choose the limit from the count in `r0`.",
                "fields": [{"name": "limit", "type": "int", "description": "limit"}],
                "rationale": "limit follows the count",
            }
        ],
    }
    warnings: list[str] = []
    outcomes = await synthesize(
        holes, skeleton, RecordedBackend(fills), model="m", max_repair=1, warnings=warnings
    )
    o = outcomes[0]
    assert o.accepted and o.downgraded and o.hole.id == "judgment_step1"
    assert (
        skeleton.steps[1].kind == "judgment"
        and skeleton.steps[1].args["$.limit"].hole == "judgment"
    )
    assert warnings and warnings[0].startswith("transform_downgraded")
    assert o.attempts == 1 and o.usage.input_tokens > 0


async def test_judgment_failure_raises_compile_error() -> None:
    traces = [
        mk_trace(f"t{i}", [("shop.get", {}, {"x": 1}), ("slack.post", {"text": t}, {"ok": 1})])
        for i, t in enumerate(
            ["a long enough judgment text one", "a long enough judgment text two"]
        )
    ]
    skeleton = build_skeleton(traces)
    holes = collect_holes(skeleton, traces)
    bad = {
        "prompt": "Use `r9` which does not exist.",
        "fields": [{"name": "text", "type": "str"}],
        "rationale": "x",
    }
    with pytest.raises(CompileError, match="judgment_step1"):
        await synthesize(
            holes,
            skeleton,
            RecordedBackend({"judgment_step1": [bad]}),
            model="m",
            max_repair=1,
            warnings=[],
        )


def test_user_message_shape(weekly_skeleton: Skeleton, weekly_traces: list[Trace]) -> None:
    holes = collect_holes(weekly_skeleton, weekly_traces)
    t = json.loads(user_message(holes[0], attempt=0, previous=None, report=None))
    assert t["hole_id"] == "transform_step3" and t["kind"] == "transform"
    assert t["signature"].startswith("def transform_step3(") and t["produces"] == {"as_of": "str"}
    assert len(t["examples"]) == 3 and t["examples"][0]["expected"] == {"as_of": "2026-10-11"}
    assert len(json.dumps(t["examples"][0]["params"]["r1"])) < 9000
    j = json.loads(user_message(holes[1], attempt=0, previous=None, report=None))
    assert j["context_keys"] == ["inputs", "r0", "r1", "r2", "r3"]
    assert j["schema_name"] == "Step4Out"
    assert "agent_thoughts" in j["examples"][0] and isinstance(j["rules"], list)
    assert Usage().total_input == 0
