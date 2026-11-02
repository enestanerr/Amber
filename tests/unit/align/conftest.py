from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from amber.trace.models import Outcome, Thought, ToolCall, ToolSchema, Trace
from amber.util.json import read_json

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "traces" / "weekly-report"

Call = tuple[str, dict[str, Any], Any]


def mk_trace(
    tid: str,
    calls: Iterable[Call],
    *,
    inputs: dict[str, Any] | None = None,
    thoughts: dict[int, str] | None = None,
    effects: dict[str, str] | None = None,
    errors: Iterable[int] = (),
    workflow: str = "wf",
) -> Trace:
    """Build a small synthetic trace: ``calls`` are ``(tool, args, result)`` in order;
    ``thoughts`` maps call position → thought text emitted right before that call."""
    events: list[Thought | ToolCall] = []
    tools: dict[str, ToolSchema] = {}
    errs = set(errors)
    for pos, (tool, args, result) in enumerate(calls):
        if thoughts and pos in thoughts:
            events.append(Thought(idx=len(events), text=thoughts[pos]))
        server, _, name = tool.partition(".")
        effect = (effects or {}).get(tool, "unknown")
        tools.setdefault(tool, ToolSchema(server=server, name=name, effect=effect))  # type: ignore[arg-type]
        kind = "empty" if result is None else ("text" if isinstance(result, str) else "json")
        events.append(
            ToolCall(
                idx=len(events),
                call_id=f"{tid}_c{pos}",
                tool=tool,
                args=args,
                result=result,
                result_kind=kind,  # type: ignore[arg-type]
                is_error=pos in errs,
            )
        )
    return Trace(
        id=tid,
        workflow=workflow,
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
        adapter="mcp-proxy",
        inputs=dict(inputs or {}),
        tools=list(tools.values()),
        events=list(events),
        outcome=Outcome(status="success"),
    )


@pytest.fixture
def weekly_traces() -> list[Trace]:
    return [Trace.model_validate(read_json(FIXTURES / f"t{i}.json")) for i in (1, 2, 3)]
