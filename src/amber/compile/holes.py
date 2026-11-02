"""Typed holes and their verification against the recordings (``docs/04-compiler.md`` §D1).

* ``transform`` — ``def transform_step<idx>(inputs, r0, …) -> dict``; accepted only if the
  function reproduces every recorded value (floats ``abs_tol=1e-9``; lists of dicts compared
  order-insensitively with a warning);
* ``judgment`` — ``PROMPT_step<idx>`` + ``Step<idx>Out`` (one field per unknown leaf plus
  ``confidence``); accepted if the schema validates every recorded value, the prompt is ≤ 1200
  chars and references only available context keys, and leaks no tool names or trace ids;
* ``gate`` — ``Gate<idx>Out(go: bool, reason: str)`` + prompt for an ``unknown`` predicate.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from amber.align.models import Skeleton, StepSpec
from amber.compile.manifest import FieldSpec, FieldType
from amber.compile.sandbox import AmberError, call_with_timeout, load_function
from amber.trace.canonical import canonical_json
from amber.trace.jsonpath import MISSING, get_path
from amber.trace.models import ToolCall, Trace

HoleKind = Literal["transform", "judgment", "gate"]
PROMPT_MAX_CHARS = 1200
SHRINK_ITEMS = 20
SHRINK_STR = 500
SHRINK_BYTES = 8 * 1024
_KEY_RE = re.compile(r"\{(\w+)\}|`(\w+)`")


@dataclass
class HoleExample:
    trace_id: str
    params: dict[str, Any] = field(default_factory=dict[str, Any])
    context: dict[str, Any] = field(default_factory=dict[str, Any])
    expected: dict[str, Any] = field(default_factory=dict[str, Any])
    thoughts: str = ""


@dataclass
class Hole:
    id: str
    kind: HoleKind
    step_idx: int
    tool: str
    fields: dict[str, str]  # field name → arg leaf path ("" for gate fields)
    params: list[str]  # transform parameter names
    context_keys: list[str]
    examples: list[HoleExample]
    expected_types: dict[str, FieldType]

    @property
    def function_name(self) -> str:
        return f"transform_step{self.step_idx}"

    @property
    def schema_name(self) -> str:
        return f"Gate{self.step_idx}Out" if self.kind == "gate" else f"Step{self.step_idx}Out"

    @property
    def prompt_name(self) -> str:
        return (
            f"PROMPT_gate{self.step_idx}" if self.kind == "gate" else f"PROMPT_step{self.step_idx}"
        )

    @property
    def llm_step_id(self) -> str:
        return f"gate{self.step_idx}" if self.kind == "gate" else f"step{self.step_idx}"

    @property
    def signature(self) -> str:
        params = ", ".join(
            f"{p}: Any" if p != "inputs" else "inputs: dict[str, Any]" for p in self.params
        )
        return f"def {self.function_name}({params}) -> dict[str, Any]"


@dataclass
class VerifyReport:
    ok: bool
    details: list[str] = field(default_factory=list[str])
    mismatches: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    notes: list[str] = field(default_factory=list[str])

    def summary(self) -> str:
        return "; ".join(self.details) if self.details else ("ok" if self.ok else "failed")


# --- naming and shrinking -----------------------------------------------------


def field_name(path: str) -> str:
    """``$.filter.since`` → ``filter_since``; ``$.labels[0]`` → ``labels_0``."""
    key = path[2:] if path.startswith("$.") else path.lstrip("$")
    key = re.sub(r"\[(\d+)\]", r"_\1", key).replace(".", "_").replace("-", "_")
    key = re.sub(r"[^A-Za-z0-9_]", "_", key) or "value"
    if key[0].isdigit():
        key = "v_" + key
    return key


def infer_field_type(values: list[Any]) -> FieldType:
    if values and all(isinstance(v, bool) for v in values):
        return "bool"
    if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "int"
    if values and all(isinstance(v, int | float) and not isinstance(v, bool) for v in values):
        return "float"
    if values and all(
        isinstance(v, list) and all(isinstance(x, str) for x in cast(list[Any], v)) for v in values
    ):
        return "list[str]"
    return "str"


def shrink(value: Any, *, max_items: int = SHRINK_ITEMS, max_str: int = SHRINK_STR) -> Any:
    """Truncate lists to *max_items* (with a ``...+N`` marker) and strings to *max_str* chars."""
    if isinstance(value, str):
        return value if len(value) <= max_str else value[:max_str] + "…"
    if isinstance(value, list):
        items = cast(list[Any], value)
        out = [shrink(v, max_items=max_items, max_str=max_str) for v in items[:max_items]]
        if len(items) > max_items:
            out.append(f"...+{len(items) - max_items}")
        return out
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        return {k: shrink(v, max_items=max_items, max_str=max_str) for k, v in d.items()}
    return value


def shrink_to_bytes(value: Any, limit: int = SHRINK_BYTES) -> Any:
    out = shrink(value)
    items, chars = SHRINK_ITEMS, SHRINK_STR
    while len(canonical_json(out)) > limit and items > 1:
        items, chars = max(1, items // 2), max(40, chars // 2)
        out = shrink(value, max_items=items, max_str=chars)
    return out


# --- collection ---------------------------------------------------------------


def _call(trace: Trace, event_idx: int) -> ToolCall:
    call = trace.events[event_idx]
    assert isinstance(call, ToolCall)
    return call


def transform_params(step: StepSpec) -> list[str]:
    return ["inputs", *[f"r{k}" for k in range(step.idx)]]


def context_keys(step: StepSpec, skeleton: Skeleton) -> list[str]:
    cols = set(step.context_hint)
    cols.update(s.idx for s in skeleton.steps if s.idx < step.idx and s.observe)
    return ["inputs", *[f"r{k}" for k in sorted(cols)]]


def _available(trace: Trace, skeleton: Skeleton, upto: int) -> dict[str, Any]:
    values: dict[str, Any] = {"inputs": dict(trace.inputs)}
    for s in skeleton.steps:
        if s.idx >= upto:
            break
        values[f"r{s.idx}"] = (
            _call(trace, s.calls[trace.id]).result if trace.id in s.calls else None
        )
    return values


def collect_holes(skeleton: Skeleton, traces: list[Trace]) -> list[Hole]:
    """One hole per transform/judgment step plus one gate per unknown-predicate step."""
    by_id = {t.id: t for t in traces}
    holes: list[Hole] = []
    for step in skeleton.steps:
        if step.conditional and step.predicate is not None and step.predicate.kind == "unknown":
            holes.append(_gate_hole(step, skeleton, by_id))
        unknown = step.unknown_leaves()
        if not unknown:
            continue
        for kind in ("transform", "judgment"):
            paths = [p for p, prov in unknown.items() if prov.hole == kind]
            if paths:
                holes.append(_value_hole(kind, step, skeleton, by_id, paths))
    return holes


def _value_hole(
    kind: str, step: StepSpec, skeleton: Skeleton, traces: dict[str, Trace], paths: list[str]
) -> Hole:
    fields: dict[str, str] = {}
    for path in paths:
        name = field_name(path)
        while name in fields:
            name += "_"
        fields[name] = path
    examples: list[HoleExample] = []
    per_field: dict[str, list[Any]] = {n: [] for n in fields}
    params = transform_params(step)
    keys = context_keys(step, skeleton)
    order = {tid: i for i, tid in enumerate(skeleton.traces)}
    for tid, event_idx in sorted(step.calls.items(), key=lambda kv: order.get(kv[0], 0)):
        trace = traces[tid]
        call = _call(trace, event_idx)
        expected: dict[str, Any] = {}
        for name, path in fields.items():
            value = get_path(call.args, path)
            expected[name] = None if value is MISSING else value
            per_field[name].append(expected[name])
        available = _available(trace, skeleton, step.idx)
        examples.append(
            HoleExample(
                trace_id=tid,
                params={p: available.get(p) for p in params},
                context={k: available.get(k) for k in keys},
                expected=expected,
                thoughts=step.thoughts.get(tid, ""),
            )
        )
    hole_kind: HoleKind = "transform" if kind == "transform" else "judgment"
    return Hole(
        id=f"{hole_kind}_step{step.idx}",
        kind=hole_kind,
        step_idx=step.idx,
        tool=step.tool,
        fields=fields,
        params=params,
        context_keys=keys,
        examples=examples,
        expected_types={n: infer_field_type(v) for n, v in per_field.items()},
    )


def _gate_hole(step: StepSpec, skeleton: Skeleton, traces: dict[str, Trace]) -> Hole:
    keys = context_keys(step, skeleton)
    examples: list[HoleExample] = []
    for tid, trace in traces.items():
        available = _available(trace, skeleton, step.idx)
        examples.append(
            HoleExample(
                trace_id=tid,
                context={k: available.get(k) for k in keys},
                expected={"go": tid in step.calls},
                thoughts=step.thoughts.get(tid, ""),
            )
        )
    return Hole(
        id=f"gate_step{step.idx}",
        kind="gate",
        step_idx=step.idx,
        tool=step.tool,
        fields={"go": "", "reason": ""},
        params=[],
        context_keys=keys,
        examples=examples,
        expected_types={"go": "bool", "reason": "str"},
    )


def downgrade_to_judgment(hole: Hole) -> Hole:
    """A transform the model could not write becomes a judgment with the same fields."""
    return Hole(
        id=f"judgment_step{hole.step_idx}",
        kind="judgment",
        step_idx=hole.step_idx,
        tool=hole.tool,
        fields=dict(hole.fields),
        params=[],
        context_keys=["inputs", *[p for p in hole.params if p != "inputs"]],
        examples=[
            HoleExample(
                trace_id=ex.trace_id,
                context={
                    k: ex.params.get(k)
                    for k in ["inputs", *[p for p in hole.params if p != "inputs"]]
                },
                expected=dict(ex.expected),
                thoughts=ex.thoughts,
            )
            for ex in hole.examples
        ],
        expected_types=dict(hole.expected_types),
    )


# --- schemas ------------------------------------------------------------------


_PY_TYPES: dict[FieldType, Any] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list[str]": list[str],
}


_PLACEHOLDERS: dict[FieldType, Any] = {
    "str": "",
    "int": 0,
    "float": 0.0,
    "bool": False,
    "list[str]": [],
}


def _placeholder(f: FieldSpec) -> Any:
    return f.enum[0] if f.enum else _PLACEHOLDERS[f.type]


def build_judgment_model(name: str, fields: list[FieldSpec]) -> type[BaseModel]:
    """Pydantic model (``extra="forbid"``) from field specs, with ``confidence`` added."""
    definitions: dict[str, Any] = {}
    for f in fields:
        py_type: Any = _PY_TYPES[f.type]
        if f.enum:
            py_type = Literal[tuple(f.enum)]  # type: ignore[valid-type]
        definitions[f.name] = (py_type, Field(description=f.description))
    if "confidence" not in definitions:
        definitions["confidence"] = (float, Field(ge=0, le=1, description="0..1"))
    return create_model(name, __config__=ConfigDict(extra="forbid"), **definitions)


# --- verification -------------------------------------------------------------


def values_equal(expected: Any, actual: Any, notes: list[str], *, path: str = "$") -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual or (expected == actual and type(expected) is type(actual))
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return math.isclose(float(expected), float(actual), abs_tol=1e-9, rel_tol=0.0)
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.rstrip() == actual.rstrip()
    if isinstance(expected, list) and isinstance(actual, list):
        e_list, a_list = cast(list[Any], expected), cast(list[Any], actual)
        if len(e_list) != len(a_list):
            return False
        if all(
            values_equal(e, a, notes, path=f"{path}[{i}]")
            for i, (e, a) in enumerate(zip(e_list, a_list, strict=True))
        ):
            return True
        if (
            e_list
            and all(isinstance(x, dict) for x in e_list)
            and sorted(e_list, key=canonical_json) == sorted(a_list, key=canonical_json)
        ):
            notes.append(f"{path}: list order differs from the recording (accepted)")
            return True
        return False
    if isinstance(expected, dict) and isinstance(actual, dict):
        e_dict, a_dict = cast(dict[str, Any], expected), cast(dict[str, Any], actual)
        if set(e_dict) != set(a_dict):
            return False
        return all(values_equal(e_dict[k], a_dict[k], notes, path=f"{path}.{k}") for k in e_dict)
    return expected == actual


def verify_transform(hole: Hole, source: str) -> VerifyReport:
    report = VerifyReport(ok=True)
    try:
        fn = load_function(source, hole.function_name)
    except AmberError as e:
        return VerifyReport(ok=False, details=[e.message])
    for ex in hole.examples:
        entry: dict[str, Any] = {"trace": ex.trace_id, "expected": ex.expected}
        try:
            actual = call_with_timeout(fn, dict(ex.params))
        except AmberError as e:
            entry["exception"] = e.message
            report.mismatches.append(entry)
            report.details.append(f"{ex.trace_id}: {e.message}")
            continue
        except Exception as e:  # the transform raised
            entry["exception"] = f"{type(e).__name__}: {e}"
            report.mismatches.append(entry)
            report.details.append(f"{ex.trace_id}: {type(e).__name__}: {e}")
            continue
        if not isinstance(actual, dict):
            entry["actual"] = actual
            report.mismatches.append(entry)
            report.details.append(
                f"{ex.trace_id}: returned {type(actual).__name__}, expected a dict"
            )
            continue
        actual_d = cast(dict[str, Any], actual)
        entry["actual"] = actual_d
        bad = [
            name
            for name in hole.fields
            if name not in actual_d
            or not values_equal(ex.expected[name], actual_d[name], report.notes, path=name)
        ]
        if bad:
            report.mismatches.append(entry)
            report.details.append(f"{ex.trace_id}: mismatch at {', '.join(bad)}")
    report.ok = not report.mismatches
    return report


def _referenced_keys(prompt: str) -> set[str]:
    return {a or b for a, b in _KEY_RE.findall(prompt)}


def verify_judgment(
    hole: Hole,
    prompt: str,
    fields: list[FieldSpec],
    *,
    forbidden_terms: list[str] = (),  # type: ignore[assignment]
) -> VerifyReport:
    report = VerifyReport(ok=True)
    expected_names = set(hole.fields)
    given = {f.name for f in fields if f.name != "confidence"}
    if given != expected_names:
        report.details.append(
            f"fields must be exactly {sorted(expected_names)}, got {sorted(given)}"
        )
    if len(prompt) > PROMPT_MAX_CHARS:
        report.details.append(f"prompt is {len(prompt)} chars (max {PROMPT_MAX_CHARS})")
    if not prompt.strip():
        report.details.append("prompt is empty")
    # the declared inputs are reachable as `inputs.<name>`; a prompt that names one (`product`)
    # refers to available data (a live compile lost a repair round to this)
    input_names: set[str] = set()
    for ex in hole.examples:
        declared = ex.context.get("inputs")
        if isinstance(declared, dict):
            input_names.update(str(k) for k in cast(dict[str, Any], declared))
    unknown_keys = _referenced_keys(prompt) - set(hole.context_keys) - input_names
    if unknown_keys:
        report.details.append(f"prompt references unavailable context keys {sorted(unknown_keys)}")
    for term in forbidden_terms:
        if term and term in prompt:
            report.details.append(f"prompt leaks {term!r}")
    if report.details:
        report.ok = False
        return report
    try:
        model = build_judgment_model(hole.schema_name, fields)
    except Exception as e:
        return VerifyReport(ok=False, details=[f"schema cannot be built: {e}"])
    for ex in hole.examples:
        # a gate's ``reason`` (and any field a recording cannot contain) gets a type-correct
        # stand-in: requiring it made every gate fail ("recorded value rejected at reason")
        unrecorded = {
            f.name: _placeholder(f)
            for f in fields
            if f.name not in ex.expected and f.name != "confidence"
        }
        try:
            model.model_validate({**unrecorded, **ex.expected, "confidence": 1.0})
        except ValidationError as e:
            first = e.errors()[0]
            loc = ".".join(str(p) for p in first["loc"])
            report.mismatches.append(
                {"trace": ex.trace_id, "expected": ex.expected, "error": first["msg"]}
            )
            report.details.append(
                f"{ex.trace_id}: recorded value rejected at {loc}: {first['msg']}"
            )
    report.ok = not report.mismatches
    return report
