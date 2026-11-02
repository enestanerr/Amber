"""Step classification (``docs/04-compiler.md`` §A4) and inferred inputs (§A6).

Each ``Unknown`` leaf is assigned a hole kind:

* ``judgment`` — a string ≥ 24 chars, an enum-like short string (≤ 3 distinct values that
  repeat across traces), or any string when the preceding thoughts contain decision cues;
* ``transform`` — numbers, booleans, date/ISO strings, lists, dicts;
* short free strings that merely vary across traces are promoted to **inferred inputs**.

Optional steps become ``conditional`` with a data predicate derived from an earlier core
result when one separates the present/absent traces, else an ``unknown`` (gate) predicate.
"""

from __future__ import annotations

import re
from typing import Any

from amber.align.models import InputSpec, Predicate, Provenance, StepSpec
from amber.align.provenance import call_at, leaf_key
from amber.trace.jsonpath import MISSING, get_path, iter_leaves
from amber.trace.models import Trace

JUDGMENT_MIN_CHARS = 24
ENUM_MAX_DISTINCT = 3
INFERRED_MAX_CHARS = 64
DECISION_CUES = re.compile(r"decide|choose|classify|summar|judg|seems|looks like|I think", re.I)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


def is_date_like(value: str) -> bool:
    return bool(DATE_RE.match(value.strip()))


def _enum_like(values: list[Any]) -> bool:
    distinct = {leaf_key(v) for v in values}
    return 1 < len(distinct) <= ENUM_MAX_DISTINCT and len(distinct) < len(values)


def _leaf_values(step: StepSpec, traces: dict[str, Trace], path: str) -> list[Any]:
    out: list[Any] = []
    for tid, event_idx in step.calls.items():
        value = get_path(call_at(traces[tid], event_idx).args, path)
        if value is not MISSING:
            out.append(value)
    return out


def _promote_input(path: str, values: list[Any], inputs: dict[str, InputSpec]) -> str:
    base = path.rsplit(".", 1)[-1].split("[", 1)[0] or "value"
    base = re.sub(r"[^A-Za-z0-9_]", "_", base)
    if base[0].isdigit():
        base = "in_" + base
    name = base
    n = 2
    while name in inputs:
        name = f"{base}_{n}"
        n += 1
    inputs[name] = InputSpec(type="string", source="inferred", examples=list(values))
    return name


def classify_step(
    step: StepSpec, traces: dict[str, Trace], inputs: dict[str, InputSpec], warnings: list[str]
) -> None:
    cue = any(DECISION_CUES.search(text) for text in step.thoughts.values())
    judgment = transform = False
    for path, prov in list(step.args.items()):
        if prov.kind != "unknown":
            continue
        values = _leaf_values(step, traces, path)
        sample = values[0] if values else None
        if isinstance(sample, str):
            if is_date_like(sample):
                prov.hole = "transform"
                transform = True
            elif (
                len(sample) >= JUDGMENT_MIN_CHARS
                or cue
                or _enum_like(values)
                or prov.large
                or len(sample) > INFERRED_MAX_CHARS
            ):
                prov.hole = "judgment"
                judgment = True
            elif len(values) > 1:
                name = _promote_input(path, values, inputs)
                step.args[path] = Provenance(kind="input", name=name)
                warnings.append(f"inferred_input: {name} from step {step.idx} {step.tool} {path}")
            else:
                prov.hole = "judgment"
                judgment = True
        else:
            prov.hole = "transform"
            transform = True
    step.kind = "judgment" if judgment else ("transform-needed" if transform else "deterministic")


def derive_predicate(step: StepSpec, steps: list[StepSpec], traces: dict[str, Trace]) -> Predicate:
    """Find an earlier core result leaf that separates present and absent traces."""
    all_ids = list(traces)
    present = set(step.present_in)
    absent = [t for t in all_ids if t not in present]
    if not absent:
        return Predicate(kind="unknown")
    for earlier in steps:
        if earlier.idx >= step.idx or len(earlier.calls) < len(all_ids):
            continue
        leaves: dict[str, dict[str, Any]] = {}
        for tid, event_idx in earlier.calls.items():
            for path, value in iter_leaves(call_at(traces[tid], event_idx).result):
                leaves.setdefault(path, {})[tid] = value
        for path in sorted(leaves, key=lambda p: (len(p), p)):
            per = leaves[path]
            if len(per) < len(all_ids):
                continue
            p_vals = [per[t] for t in all_ids if t in present]
            a_vals = [per[t] for t in absent]
            pred = _separating_op(p_vals, a_vals)
            if pred is not None:
                op, value = pred
                return Predicate(kind="data", col=earlier.idx, path=path, op=op, value=value)
    return Predicate(kind="unknown")


def _separating_op(present: list[Any], absent: list[Any]) -> tuple[Any, Any] | None:
    def empty(v: Any) -> bool:
        return v in ("", [], {}, None)

    if all(not empty(v) for v in present) and all(empty(v) for v in absent):
        return "non-empty", None
    p_keys = {leaf_key(v) for v in present}
    a_keys = {leaf_key(v) for v in absent}
    if len(p_keys) == 1 and not (p_keys & a_keys):
        return "==", present[0]
    nums_p = [v for v in present if isinstance(v, int | float) and not isinstance(v, bool)]
    nums_a = [v for v in absent if isinstance(v, int | float) and not isinstance(v, bool)]
    if len(nums_p) == len(present) and len(nums_a) == len(absent) and nums_p and nums_a:
        if min(nums_p) > max(nums_a):
            return ">", max(nums_a)
        if max(nums_p) < min(nums_a):
            return "<", min(nums_a)
    if len(a_keys) == 1 and not (p_keys & a_keys):
        return "!=", absent[0]
    return None


def classify_steps(
    steps: list[StepSpec],
    traces: dict[str, Trace],
    inputs: dict[str, InputSpec],
    warnings: list[str],
) -> None:
    for step in steps:
        classify_step(step, traces, inputs, warnings)
        if len(step.present_in) < len(traces):
            step.conditional = True
            step.predicate = derive_predicate(step, steps, traces)
