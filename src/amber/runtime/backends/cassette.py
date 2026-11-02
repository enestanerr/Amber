"""Cassette backend: replay a program against one recorded trace (``docs/04-compiler.md`` §E1).

Every ``ctx.tool`` call must match the next expected call (tool name + canonical args with
volatile paths masked on both sides); the recorded result is returned. ``ctx.llm`` is served
from the recorded unknown values of that step; ``ctx.now`` is the trace's creation time.
"""

from __future__ import annotations

import copy
import difflib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar, cast

from pydantic import BaseModel, ValidationError

from amber.compile.manifest import LlmStep, Manifest
from amber.errors import AmberError
from amber.trace.canonical import canonical_json, canonicalize_args
from amber.trace.jsonpath import MISSING, get_path
from amber.trace.models import ToolCall, Trace
from amber.util.json import dumps_canonical

T = TypeVar("T", bound=BaseModel)
DIFF_MAX_LINES = 40


class ReplayDivergence(AmberError):
    """The program made a call the recording did not have at this position (or vice versa)."""

    exit_code = 3

    def __init__(
        self,
        step_idx: int,
        expected: tuple[str, dict[str, Any]] | None,
        actual: tuple[str, dict[str, Any]] | None,
        *,
        hint: str,
        message: str | None = None,
    ) -> None:
        self.step_idx = step_idx
        self.expected = expected
        self.actual = actual
        self.diff = unified_diff(expected, actual)
        exp = f"{expected[0]}" if expected else "<end of recording>"
        act = f"{actual[0]}" if actual else "<no call>"
        super().__init__(
            message or f"replay diverged at call {step_idx}: expected {exp}, got {act}",
            hint=hint,
        )


def unified_diff(
    expected: tuple[str, dict[str, Any]] | None, actual: tuple[str, dict[str, Any]] | None
) -> str:
    def render(side: tuple[str, dict[str, Any]] | None) -> list[str]:
        if side is None:
            return []
        return [f"tool: {side[0]}", *dumps_canonical(side[1]).splitlines()]

    lines = list(
        difflib.unified_diff(render(expected), render(actual), "recorded", "program", lineterm="")
    )
    if len(lines) > DIFF_MAX_LINES:
        lines = [*lines[:DIFF_MAX_LINES], "..."]
    return "\n".join(lines)


@dataclass
class ExpectedCall:
    tool: str
    args: dict[str, Any]  # canonical, masked
    result: Any
    event_idx: int
    consumed: bool = False


@dataclass
class LlmRecord:
    step_id: str
    value: dict[str, Any]
    context_keys: list[str]


@dataclass
class CassetteBackend:
    """Sync ``Context`` implementation over one trace."""

    trace: Trace
    manifest: Manifest | None = None
    strict_order: bool = True
    on_llm: Callable[[str, type[BaseModel], str, dict[str, Any], BaseModel], None] | None = None
    expected: list[ExpectedCall] = field(default_factory=list[ExpectedCall])
    pos: int = 0
    llm_calls: list[LlmRecord] = field(default_factory=list[LlmRecord])
    logs: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        self.expected = self._build_expected()

    # -- construction ------------------------------------------------------
    def _kept_calls(self) -> list[ToolCall]:
        """Calls the program is expected to make: the kept steps' calls (pruned ones skipped)."""
        calls = [c for c in self.trace.calls]
        if self.manifest is not None and self.trace.id in self.manifest.trace_calls:
            wanted = set(self.manifest.trace_calls[self.trace.id])
            calls = [c for c in calls if c.idx in wanted]
        return calls

    def _volatile_for(self, position: int) -> list[str]:
        if self.manifest is None:
            return []
        # positions map 1:1 to kept steps when the trace is a source trace
        prefix = len(self.trace.prefix_calls)
        step_idx = position - prefix if position >= prefix else -1
        if step_idx < 0:
            return []
        return list(self.manifest.volatile_paths.get(step_idx, []))

    def _build_expected(self) -> list[ExpectedCall]:
        out: list[ExpectedCall] = []
        for call in [*self.trace.prefix_calls, *self._kept_calls()]:
            position = len(out)
            out.append(
                ExpectedCall(
                    tool=call.tool,
                    args=canonicalize_args(call.args, self._volatile_for(position)),
                    result=call.result,
                    event_idx=call.idx,
                )
            )
        return out

    # -- Context -----------------------------------------------------------
    def tool(self, name: str, /, **args: Any) -> Any:
        masked = canonicalize_args(args, self._volatile_for(self.pos))
        actual = (name, masked)
        if self.strict_order:
            if self.pos >= len(self.expected):
                raise ReplayDivergence(
                    self.pos,
                    None,
                    actual,
                    hint="program made a call the recording did not; check the conditional "
                    "predicate",
                )
            head = self.expected[self.pos]
            if (head.tool, head.args) != actual:
                raise ReplayDivergence(
                    self.pos, (head.tool, head.args), actual, hint=_hint(head, actual)
                )
            head.consumed = True
            self.pos += 1
            return copy.deepcopy(head.result)
        for i, cand in enumerate(self.expected):
            if not cand.consumed and (cand.tool, cand.args) == actual:
                cand.consumed = True
                self.pos = max(self.pos, i + 1)
                return copy.deepcopy(cand.result)
        pending = next((c for c in self.expected if not c.consumed), None)
        raise ReplayDivergence(
            self.pos,
            (pending.tool, pending.args) if pending else None,
            actual,
            hint="no pending recorded call matches this one (unordered mode)",
        )

    def llm(self, step_id: str, schema: type[T], prompt: str, context: dict[str, Any]) -> T:
        spec = self.manifest.llm_step(step_id) if self.manifest is not None else None
        if spec is None:
            raise ReplayDivergence(
                self.pos,
                None,
                (f"llm:{step_id}", {}),
                message=f"llm step {step_id!r} is not in the manifest",
                hint="recompile the workflow so program.py and manifest.json agree",
            )
        extra = set(context) - set(spec.context_keys)
        if extra:
            raise ReplayDivergence(
                self.pos,
                None,
                (f"llm:{step_id}", {"context": sorted(context)}),
                message=f"llm step {step_id!r} received unexpected context keys {sorted(extra)}",
                hint="the program's context dict must only use keys listed in the manifest",
            )
        value = self.recorded_llm_value(spec)
        try:
            parsed = schema.model_validate(value)
        except ValidationError as e:
            first = e.errors()[0]
            raise ReplayDivergence(
                self.pos,
                None,
                (f"llm:{step_id}", value),
                message=f"recorded value for llm step {step_id!r} does not satisfy "
                f"{schema.__name__}: {'.'.join(str(p) for p in first['loc'])}: {first['msg']}",
                hint="the judgment schema must accept every recorded value; recompile",
            ) from e
        self.llm_calls.append(LlmRecord(step_id, value, list(spec.context_keys)))
        if self.on_llm is not None:
            self.on_llm(step_id, schema, prompt, context, parsed)
        return parsed

    def recorded_llm_value(self, spec: LlmStep) -> dict[str, Any]:
        """Recorded unknown leaves of the step in this trace (gate: whether the step ran)."""
        call = self._call_for_step(spec.step_idx)
        if spec.kind == "gate":
            return {"go": call is not None, "reason": "recorded", "confidence": 1.0}
        value: dict[str, Any] = {"confidence": 1.0}
        for name, path in spec.field_paths.items():
            leaf = get_path(call.args, path) if call is not None else MISSING
            value[name] = None if leaf is MISSING else leaf
        return value

    def _call_for_step(self, step_idx: int) -> ToolCall | None:
        if self.manifest is None or self.trace.id not in self.manifest.trace_calls:
            calls = self.trace.calls
            return calls[step_idx] if step_idx < len(calls) else None
        wanted = self.manifest.trace_calls[self.trace.id]
        if step_idx >= len(wanted):
            return None
        event_idx = wanted[step_idx]
        if event_idx < 0:
            return None
        event = self.trace.events[event_idx]
        return event if isinstance(event, ToolCall) else None

    def now(self) -> datetime:
        return self.trace.created_at

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    # -- after the run -----------------------------------------------------
    @property
    def leftover(self) -> list[ExpectedCall]:
        return [c for c in self.expected if not c.consumed]

    @property
    def consumed(self) -> int:
        return sum(1 for c in self.expected if c.consumed)


def _hint(head: ExpectedCall, actual: tuple[str, dict[str, Any]]) -> str:
    if head.tool != actual[0]:
        return "program order differs from recording; check the skeleton step order"
    exp_leaves = dict(_leaves(head.args))
    act_leaves = dict(_leaves(actual[1]))
    changed = sorted(
        set(exp_leaves) ^ set(act_leaves)
        | {p for p in exp_leaves if exp_leaves[p] != act_leaves.get(p)}
    )
    where = changed[0] if changed else "?"
    return (
        f"same tool, argument differs at {where}; if that value comes from an earlier result "
        "it may be volatile — consider adding it to volatile_paths"
    )


def _leaves(obj: Any) -> list[tuple[str, str]]:
    from amber.trace.jsonpath import iter_leaves

    return [(p, canonical_json(v)) for p, v in iter_leaves(cast(Any, obj))]
